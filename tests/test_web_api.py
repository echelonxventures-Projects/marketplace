"""Tests for the local test server: seeding, auth, routing and the API surface.

These hit the handlers through the router (no socket), which is enough to prove
the adapter wiring is correct. The runtime is re-seeded per test class so the
checkout flow can mutate state without leaking across tests.
"""

import pytest

from marketplace.kernel.registry import CONCEPTS, REQUIREMENTS
from marketplace.web import build_runtime
from marketplace.web.api import router
from marketplace.web.router import Request, Response


@pytest.fixture(scope="module")
def runtime():
    return build_runtime()


def call(runtime, method, path, *, token=None, body=None, query=None):
    """Dispatch a request through the router exactly as the server would."""
    request = Request(
        method=method,
        path=path,
        params={},
        query=query or {},
        body=body,
        headers={},
        token=token,
    )
    request.runtime = runtime
    if token:
        request.authz = runtime.resolve(runtime.token_for(token) or token)
    resolved = router.resolve(method, path)
    assert resolved is not None, f"no route for {method} {path}"
    handler, params = resolved
    request.params = params
    return handler(request)


@pytest.mark.unit
class TestSeeding:
    def test_seeds_personas_with_credentials(self, runtime):
        personas = {p["key"]: p for p in runtime.seed_report.personas}
        assert set(personas) == {"admin", "seller", "buyer"}
        for persona in personas.values():
            assert persona["identity_id"]
            assert persona["api_key"]
            assert persona["password"]

    def test_seeded_products_have_live_offers_and_stock(self, runtime):
        products = runtime.seed_report.products
        assert len(products) == 3
        for product in products:
            offer = runtime.offers.offers[product["offer_id"]]
            assert offer.status == "active"
            position = runtime.inventory.positions[product["position_id"]]
            assert position.on_hand > 0

    def test_api_keys_resolve_to_their_persona(self, runtime):
        for key, expected in (
            ("mk_demo_admin", "admin@marketplace.local"),
            ("mk_demo_seller", "seller@marketplace.local"),
            ("mk_demo_buyer", "buyer@marketplace.local"),
        ):
            authz = runtime.resolve(runtime.token_for(key))
            identity = runtime.identity.identities[authz.actor_id]
            assert identity["subject"] == expected

    def test_seeded_ledger_balances(self, runtime):
        runtime.ledger.assert_all_journals_balanced()

    def test_governance_registries_populated(self, runtime):
        assert len(CONCEPTS.all()) >= 78
        implemented = [r for r in REQUIREMENTS.all() if r.status == "implemented"]
        assert len(implemented) >= 50


@pytest.mark.unit
class TestRouter:
    def test_matches_literal_and_param_segments(self):
        assert router.resolve("GET", "/api/health")[0] is not None
        handler, params = router.resolve("GET", "/api/orders/ORD-1")
        assert params == {"order_id": "ORD-1"}

    def test_method_must_match(self):
        assert router.resolve("DELETE", "/api/health") is None

    def test_unknown_route_is_none(self):
        assert router.resolve("GET", "/api/does-not-exist") is None


@pytest.mark.unit
class TestAuth:
    def test_login_returns_a_working_token(self, runtime):
        response = call(
            runtime,
            "POST",
            "/api/auth/login",
            body={"subject": "buyer@marketplace.local", "password": "buyer-pass-1"},
        )
        token = response.payload["token"]
        assert response.payload["token_type"] == "Bearer"
        authz = runtime.resolve(token)
        assert authz.actor_id == response.payload["identity_id"]

    def test_wrong_password_is_rejected(self, runtime):
        from marketplace.kernel.errors import AuthenticationError

        with pytest.raises(AuthenticationError):
            call(
                runtime,
                "POST",
                "/api/auth/login",
                body={"subject": "buyer@marketplace.local", "password": "nope"},
            )

    def test_whoami_reports_roles(self, runtime):
        response = call(
            runtime, "GET", "/api/auth/whoami", token="mk_demo_seller"
        )
        assert response.payload["subject"] == "seller@marketplace.local"
        assert "catalog.writer" in response.payload["roles"]

    def test_unknown_key_is_rejected(self, runtime):
        from marketplace.kernel.errors import AuthenticationError

        with pytest.raises(AuthenticationError):
            runtime.resolve("mk_does_not_exist")


@pytest.mark.unit
class TestAuthorizationEnforcement:
    def test_buyer_cannot_publish_a_product(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        product_id = runtime.seed_report.products[0]["product_id"]
        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                f"/api/catalog/products/{product_id}/publish",
                token="mk_demo_buyer",
            )

    def test_missing_token_is_denied_not_anonymous(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                "/api/pricing/promotions",
                body={"code": "X", "kind": "percent", "value": "5"},
            )


@pytest.mark.unit
class TestCatalogRead:
    def test_list_products(self, runtime):
        response = call(runtime, "GET", "/api/catalog/products", token="mk_demo_buyer")
        assert response.payload["count"] == 3

    def test_get_one_product_with_its_offers(self, runtime):
        product = runtime.seed_report.products[0]
        response = call(
            runtime, "GET", f"/api/catalog/products/{product['product_id']}"
        )
        assert response.payload["product_id"] == product["product_id"]
        assert response.payload["status"] == "published"
        assert any(o["offer_id"] == product["offer_id"] for o in response.payload["offers"])

    def test_unknown_product_is_not_found(self, runtime):
        from marketplace.kernel.errors import NotFoundError

        with pytest.raises(NotFoundError):
            call(runtime, "GET", "/api/catalog/products/PRD-nope")


@pytest.mark.unit
class TestPricing:
    def test_unit_price_and_total(self, runtime):
        product = runtime.seed_report.products[0]
        response = call(
            runtime,
            "GET",
            f"/api/pricing/prices/{product['product_id']}",
            query={"quantity": "2"},
        )
        assert response.payload["unit_price"]["amount"] == product["price"]
        assert response.payload["total"]["amount"] == str(
            round(float(product["price"]) * 2, 2)
        )

    def test_evaluate_returns_a_breakdown(self, runtime):
        product = runtime.seed_report.products[0]
        response = call(
            runtime,
            "POST",
            "/api/pricing/evaluate",
            body={"product_id": product["product_id"], "quantity": 2},
        )
        assert response.payload["currency"] == "USD"
        assert response.payload["total"]["amount"] == str(
            round(float(product["price"]) * 2, 2)
        )

    def test_malformed_promotion_value_is_a_validation_error(self, runtime):
        from marketplace.kernel.errors import ValidationError

        with pytest.raises(ValidationError):
            call(
                runtime,
                "POST",
                "/api/pricing/promotions",
                token="mk_demo_admin",
                body={"code": "BAD", "kind": "percent", "value": "not-a-number"},
            )


@pytest.mark.unit
class TestCheckout:
    def _first_offer(self, runtime):
        return runtime.seed_report.products[0]["offer_id"]

    def test_checkout_runs_the_full_critical_path(self, runtime):
        offer_id = self._first_offer(runtime)
        response = call(
            runtime,
            "POST",
            "/api/checkout",
            token="mk_demo_buyer",
            body={"offer_id": offer_id, "quantity": 2, "correlation_id": "test-co-1"},
        )
        trace = response.payload
        assert trace["order"]["status"] == "confirmed"
        assert trace["payment"]["status"] == "captured"
        assert trace["inventory"]["reservation_state"] == "committed"
        assert trace["inventory"]["on_hand"] == 98
        assert trace["ledger"]["CASH"] == "USD 299.98"

    def test_repeated_checkout_is_idempotent(self, runtime):
        offer_id = self._first_offer(runtime)
        body = {"offer_id": offer_id, "quantity": 2, "correlation_id": "test-co-2"}
        first = call(runtime, "POST", "/api/checkout", token="mk_demo_buyer", body=body).payload
        second = call(runtime, "POST", "/api/checkout", token="mk_demo_buyer", body=body).payload
        assert first["order"]["order_id"] == second["order"]["order_id"]
        assert first["inventory"]["on_hand"] == second["inventory"]["on_hand"]
        assert first["ledger"]["CASH"] == second["ledger"]["CASH"]

    def test_oversell_is_rejected(self, runtime):
        from marketplace.kernel.errors import InvariantViolationError

        offer_id = self._first_offer(runtime)
        with pytest.raises(InvariantViolationError):
            call(
                runtime,
                "POST",
                "/api/checkout",
                token="mk_demo_buyer",
                body={"offer_id": offer_id, "quantity": 99999},
            )


@pytest.mark.unit
class TestGovernance:
    def test_concepts_list(self, runtime):
        response = call(
            runtime, "GET", "/api/governance/concepts", token="mk_demo_admin"
        )
        owners = {c["owner_universe"] for c in response.payload["concepts"]}
        assert len(owners) == 15

    def test_requirements_have_no_gaps(self, runtime):
        response = call(
            runtime, "GET", "/api/governance/requirements", token="mk_demo_admin"
        )
        assert response.payload["gaps"] == []
        assert response.payload["untraced"] == []

    def test_audit_chain_is_intact(self, runtime):
        response = call(
            runtime, "GET", "/api/governance/audit/U03", token="mk_demo_admin"
        )
        assert response.payload["chain_intact"] is True
        assert response.payload["entries"] > 0

    def test_provenance_for_a_seeded_product(self, runtime):
        product_id = runtime.seed_report.products[0]["product_id"]
        response = call(
            runtime,
            "GET",
            f"/api/governance/provenance/{product_id}",
            token="mk_demo_admin",
        )
        assert response.payload["target_id"] == product_id
        assert response.payload["records"]


@pytest.mark.unit
class TestSearch:
    def test_search_finds_seeded_products(self, runtime):
        response = call(runtime, "GET", "/api/search", query={"q": "headphones"})
        assert response.payload["count"] >= 1

    def test_empty_query_returns_no_hits(self, runtime):
        response = call(runtime, "GET", "/api/search")
        assert response.payload["count"] == 0


@pytest.mark.unit
class TestSerialization:
    def test_money_serializes_to_amount_and_currency(self):
        from decimal import Decimal

        from marketplace.kernel import Money

        from marketplace.web.router import serialize

        assert serialize(Money.of(Decimal("10.5"), "USD")) == {
            "amount": "10.50",
            "currency": "USD",
        }

    def test_dataclass_and_nested_containers(self):
        from dataclasses import dataclass

        from marketplace.web.router import serialize

        @dataclass
        class Inner:
            value: float

        @dataclass
        class Outer:
            items: list
            tags: frozenset

        out = serialize(Outer(items=[Inner(1.5)], tags=frozenset({"b", "a"})))
        assert out == {"items": [{"value": 1.5}], "tags": ["a", "b"]}

    def test_response_helpers(self):
        assert Response.ok({"a": 1}).payload == {"a": 1}
        assert Response.created(x=1).payload == {"result": None, "x": 1}


@pytest.mark.unit
class TestAccessControl:
    """The adapter must fail closed: commerce data is never served to an
    anonymous caller, and a buyer sees only their own records in their tenant.
    """

    def _first_offer(self, runtime):
        return runtime.seed_report.products[0]["offer_id"]

    def _second_buyer(self, runtime):
        """A second consumer in the seeded tenant (registered once, then reused)."""

        key = "mk_demo_buyer2"
        if runtime.token_for(key) is not None:
            return key
        admin = runtime.resolve(runtime.token_for("mk_demo_admin"))
        party = runtime.identity.register_party(
            admin,
            party_type="person",
            legal_name="Second Buyer",
            tenant_id=runtime.tenant_id,
            display_name="Second Buyer",
        )
        identity_id = runtime.identity.register_identity(
            admin, party.party_id, "buyer2@marketplace.local"
        )
        runtime.identity.enroll_credential(identity_id, "buyer2-pass-1")
        runtime.remember_token(
            key,
            runtime.mint_token(identity_id, runtime.tenant_id, ["consumer"]),
        )
        return key

    def test_anonymous_cannot_read_orders(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        with pytest.raises(AuthorizationError):
            call(runtime, "GET", "/api/orders")

    def test_anonymous_cannot_read_payment_intents(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        with pytest.raises(AuthorizationError):
            call(runtime, "GET", "/api/payments/intents")

    def test_anonymous_cannot_read_the_ledger(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        with pytest.raises(AuthorizationError):
            call(runtime, "GET", "/api/ledger/trial-balance")

    def test_anonymous_cannot_read_another_order(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        order_id = next(iter(runtime.orders.orders), "ORD-none")
        with pytest.raises(AuthorizationError):
            call(runtime, "GET", f"/api/orders/{order_id}")

    def test_anonymous_cannot_checkout(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                "/api/checkout",
                body={"offer_id": self._first_offer(runtime), "quantity": 1},
            )

    def test_anonymous_cannot_reserve_stock(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                "/api/inventory/reserve",
                body={"offer_id": self._first_offer(runtime), "quantity": 1},
            )

    def test_anonymous_cannot_commit_a_reservation(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        reservation_id = next(iter(runtime.inventory.reservations), "RSV-none")
        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                f"/api/inventory/reservations/{reservation_id}/commit",
            )

    def test_anonymous_cannot_link_an_offer(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        position_id = runtime.seed_report.products[0]["position_id"]
        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                f"/api/inventory/positions/{position_id}/link-offer",
                body={"offer_id": self._first_offer(runtime)},
            )

    def test_anonymous_cannot_open_a_cart_or_place_an_order(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        for path in ("/api/orders/carts", "/api/orders"):
            with pytest.raises(AuthorizationError):
                call(runtime, "POST", path, body={})

    def test_anonymous_cannot_authorize_or_capture_a_payment(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                "/api/payments/authorize",
                body={"order_id": "ORD-none", "amount": "1.00"},
            )
        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                "/api/payments/intents/PAY-none/capture",
                body={},
            )

    def test_a_buyer_cannot_link_an_offer(self, runtime):
        from marketplace.kernel.errors import AuthorizationError

        position_id = runtime.seed_report.products[0]["position_id"]
        with pytest.raises(AuthorizationError):
            call(
                runtime,
                "POST",
                f"/api/inventory/positions/{position_id}/link-offer",
                token="mk_demo_buyer",
                body={"offer_id": self._first_offer(runtime)},
            )

    def test_a_buyer_sees_only_their_own_orders(self, runtime):
        key = self._second_buyer(runtime)
        call(
            runtime,
            "POST",
            "/api/checkout",
            token="mk_demo_buyer",
            body={
                "offer_id": self._first_offer(runtime),
                "quantity": 1,
                "correlation_id": "ac-own-1",
            },
        )
        buyer = runtime.resolve(runtime.token_for("mk_demo_buyer"))
        response = call(runtime, "GET", "/api/orders", token="mk_demo_buyer")
        assert response.payload["count"] >= 1
        assert all(o["buyer_id"] == buyer.actor_id for o in response.payload["orders"])
        # A second consumer in the same tenant sees none of them.
        other = call(runtime, "GET", "/api/orders", token=key)
        assert other.payload["count"] == 0

    def test_a_buyer_cannot_read_another_buyers_order(self, runtime):
        from marketplace.kernel.errors import NotFoundError

        key = self._second_buyer(runtime)
        response = call(
            runtime,
            "POST",
            "/api/checkout",
            token="mk_demo_buyer",
            body={
                "offer_id": self._first_offer(runtime),
                "quantity": 1,
                "correlation_id": "ac-own-2",
            },
        )
        order_id = response.payload["order"]["order_id"]
        with pytest.raises(NotFoundError):
            call(runtime, "GET", f"/api/orders/{order_id}", token=key)

    def test_a_buyer_cannot_see_another_buyers_payment_intent(self, runtime):
        key = self._second_buyer(runtime)
        call(
            runtime,
            "POST",
            "/api/checkout",
            token="mk_demo_buyer",
            body={
                "offer_id": self._first_offer(runtime),
                "quantity": 1,
                "correlation_id": "ac-own-3",
            },
        )
        other = call(runtime, "GET", "/api/payments/intents", token=key)
        assert other.payload["count"] == 0

    def test_operational_roles_see_tenant_wide_orders(self, runtime):
        call(
            runtime,
            "POST",
            "/api/checkout",
            token="mk_demo_buyer",
            body={
                "offer_id": self._first_offer(runtime),
                "quantity": 1,
                "correlation_id": "ac-own-4",
            },
        )
        for key in ("mk_demo_admin", "mk_demo_seller"):
            response = call(runtime, "GET", "/api/orders", token=key)
            assert response.payload["count"] >= 1

    def test_a_foreign_tenant_token_is_isolated(self, runtime):
        from marketplace.kernel.errors import TenantIsolationError

        buyer = runtime.resolve(runtime.token_for("mk_demo_buyer"))
        foreign = runtime.mint_token(buyer.actor_id, "tenant-B", ["consumer"])
        for path in ("/api/orders", "/api/payments/intents", "/api/ledger/trial-balance"):
            with pytest.raises(TenantIsolationError):
                call(runtime, "GET", path, token=foreign)

    def test_every_commerce_endpoint_requires_an_authenticated_caller(self):
        """A guard against the whole class of defect: every route that reads or
        writes tenant-scoped commerce data must resolve a principal, so a handler
        added later cannot silently serve anonymous callers again.
        """

        import inspect

        from marketplace.web import api

        commerce_prefixes = (
            "/api/orders",
            "/api/payments",
            "/api/ledger",
            "/api/checkout",
            "/api/inventory/positions",
            "/api/inventory/reserve",
            "/api/inventory/reservations",
        )
        public_prefixes = (
            "/api/inventory/availability",  # public stock lookup
        )
        checked = 0
        for method, path in api.router.routes():
            if not any(path.startswith(p) for p in commerce_prefixes):
                continue
            if any(path.startswith(p) for p in public_prefixes):
                continue
            handler = api.router.resolve(method, path)
            assert handler is not None, f"unresolvable route {method} {path}"
            source = inspect.getsource(handler[0])
            assert "_principal(" in source, (
                f"{method} {path} does not resolve an authenticated principal "
                "(LAW-ARCH-07: a missing token must never be unrestricted)"
            )
            checked += 1
        assert checked >= 15, f"expected to check the commerce routes, found {checked}"
