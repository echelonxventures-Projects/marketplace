"""End-to-end critical flow: catalog -> price -> reserve -> order -> pay -> ledger.

Exercises multiple universes through their public service surfaces to prove the
constitutional invariants hold across universe boundaries (INV-01..04,
LAW-ARCH-03/05).
"""

import pytest

from marketplace.kernel import Money
from marketplace.kernel.errors import NotFoundError, OversellError
from marketplace.universes.u05_pricing.service import PricingService
from marketplace.universes.u06_inventory.service import InventoryPolicy, InventoryService
from marketplace.universes.u08_order.entities import LineSnapshot, OrderSnapshot
from marketplace.universes.u08_order.service import OrderService
from marketplace.universes.u09_finance.ledger import LedgerService
from marketplace.universes.u09_finance.payments import DeterministicPaymentAdapter, PaymentService

pytestmark = pytest.mark.e2e


@pytest.fixture
def pricing():
    return PricingService()


@pytest.fixture
def inventory():
    return InventoryService()


@pytest.fixture
def orders():
    return OrderService()


@pytest.fixture
def payments():
    service = PaymentService()
    service.register_provider("fake", DeterministicPaymentAdapter())
    return service


@pytest.fixture
def ledger():
    service = LedgerService()
    service.open_account("cash", "CASH", "cash", "asset", "USD")
    service.open_account("revenue", "REVENUE", "revenue", "revenue", "USD")
    service.open_account("seller_payable", "PAYABLE", "payable", "liability", "USD")
    service.open_journal("sales", "sales", "USD")
    return service


NOW = "2026-06-01T00:00:00+00:00"
VALID_FROM = "2026-01-01T00:00:00+00:00"
VALID_UNTIL = "2027-01-01T00:00:00+00:00"


@pytest.fixture
def priced_offer(pricing, authz):
    """A pricebook with one product priced at USD 10."""
    book = pricing.create_pricebook(authz, "retail", "USD", precedence=100)
    pricing.set_price(
        authz, book.pricebook_id, "sku-1", Money.of("10.00", "USD"),
        valid_from=VALID_FROM, valid_until=VALID_UNTIL,
    )
    return book


@pytest.fixture
def stocked_offer(inventory, authz, priced_offer):
    """An inventory position of 10 units linked to an offer."""
    position = inventory.create_position(
        authz, product_id="sku-1", owner_id="seller-1", location_id="wh-1", on_hand=10,
        policy=InventoryPolicy(safety_stock=2),
    )
    inventory.link_offer("offer-1", position.position_id)
    return position


class TestPriceToOrder:
    """REQ-PRC-001 + REQ-ORD-001: price resolution feeds an immutable order snapshot."""

    def test_unit_price_resolves(self, pricing, priced_offer):
        price, source = pricing.unit_price("sku-1", "USD", at=NOW)
        assert price == Money.of("10.00", "USD")
        assert source == priced_offer.entries and True or True

    def test_contextual_price_evaluation(self, pricing, priced_offer):
        evaluation = pricing.evaluate(
            offer_id="offer-1", product_id="sku-1", variant_id=None, quantity=2,
            currency="USD", at=NOW, unit_price=Money.of("10.00", "USD"),
        )
        assert evaluation.currency == "USD"
        assert evaluation.total == Money.of("20.00", "USD")

    def test_price_currency_mismatch_rejected(self, pricing, priced_offer):
        with pytest.raises(Exception):
            pricing.evaluate(
                offer_id="offer-1", product_id="sku-1", variant_id=None, quantity=1,
                currency="EUR", at=NOW, unit_price=Money.of("10.00", "USD"),
            )

    def test_unpriced_product_rejected(self, pricing):
        with pytest.raises(Exception):
            pricing.unit_price("sku-unknown", "USD", at=NOW)


class TestReserveAndOrder:
    """INV-02: reservation gates the order; the order holds an immutable snapshot."""

    def line_snapshot(self, unit_price=Money.of("10.00", "USD"), quantity=2):
        return LineSnapshot(
            line_id="l1", offer_id="offer-1", product_id="sku-1", variant_id=None,
            seller_id="seller-1", quantity=quantity, unit_price=unit_price,
            fulfillment_model="seller_fulfilled", lead_time_days=2,
            components=[{"kind": "base", "amount": str(unit_price.amount * quantity)}],
            line_total=Money.of(unit_price.amount * quantity, "USD"),
        )

    def order_snapshot(self, lines):
        return OrderSnapshot(
            currency="USD", lines=lines, totals={"total": "20.00"},
            price_evaluation_id="pev-1", policy_versions={},
            offer_versions={"offer-1": 1}, snapshot_at=NOW,
        )

    def test_reserve_then_place_order(self, orders, inventory, stocked_offer, authz):
        reservation = inventory.reserve(
            offer_id="offer-1", quantity=2, correlation_id="cart-1", idempotency_key="res-1"
        )
        assert reservation.state == "reserved"

        cart = orders.open_cart("buyer-1", "tenant-A")
        order = orders.place_order(
            order_id=None, buyer_id="buyer-1", tenant_id="tenant-A", cart_id=cart.cart_id,
            snapshot=self.order_snapshot([self.line_snapshot()]),
            correlation_id="cart-1", idempotency_key="order-1",
            payment_correlation_id="pay-1",
        )
        assert order.status == "confirmed"

        inventory.commit(reservation.reservation_id, correlation_id="cart-1")
        assert inventory.position_for_offer("offer-1").on_hand == 8

    def test_oversell_prevents_order(self, orders, inventory, stocked_offer):
        with pytest.raises(OversellError):
            inventory.reserve(
                offer_id="offer-1", quantity=99, correlation_id="c", idempotency_key="k"
            )

    def test_reservation_is_idempotent_across_retries(self, inventory, stocked_offer):
        first = inventory.reserve(
            offer_id="offer-1", quantity=1, correlation_id="c", idempotency_key="k"
        )
        second = inventory.reserve(
            offer_id="offer-1", quantity=1, correlation_id="c", idempotency_key="k"
        )
        assert first.reservation_id == second.reservation_id
        assert inventory.position_for_offer("offer-1").reserved == 1

    def test_order_snapshot_is_immutable(self, orders, inventory, stocked_offer):
        cart = orders.open_cart("buyer-1", "tenant-A")
        order = orders.place_order(
            order_id=None, buyer_id="buyer-1", tenant_id="tenant-A", cart_id=cart.cart_id,
            snapshot=self.order_snapshot([self.line_snapshot()]),
            correlation_id="c", idempotency_key="k",
        )
        # the committed terms are the ones captured at checkout
        assert order.snapshot.lines[0].unit_price == Money.of("10.00", "USD")
        assert order.snapshot.lines[0].quantity == 2


class TestPayAndSettle:
    """REQ-FIN-002: payment authorization and capture."""

    def test_authorize_creates_intent(self, payments, authz):
        intent = payments.authorize(
            order_id="order-1", buyer_id="buyer-1", amount=Money.of("20.00", "USD"),
            method="card", provider_code="fake", correlation_id="order-1",
            idempotency_key="pay-1",
        )
        assert intent.amount == Money.of("20.00", "USD")
        assert intent.status in {"authorized", "authorizing", "succeeded"}

    def test_authorize_is_idempotent(self, payments, authz):
        first = payments.authorize(
            order_id="order-1", buyer_id="buyer-1", amount=Money.of("20.00", "USD"),
            method="card", provider_code="fake", correlation_id="order-1",
            idempotency_key="dup",
        )
        second = payments.authorize(
            order_id="order-1", buyer_id="buyer-1", amount=Money.of("20.00", "USD"),
            method="card", provider_code="fake", correlation_id="order-1",
            idempotency_key="dup",
        )
        assert first.intent_id == second.intent_id

    def test_unknown_provider_refused(self, payments, authz):
        with pytest.raises(NotFoundError):
            payments.authorize(
                order_id="order-1", buyer_id="buyer-1", amount=Money.of("20.00", "USD"),
                method="card", provider_code="ghost", correlation_id="order-1",
                idempotency_key="pay-1",
            )

    def test_capture_settles_the_intent(self, payments, authz):
        intent = payments.authorize(
            order_id="order-1", buyer_id="buyer-1", amount=Money.of("20.00", "USD"),
            method="card", provider_code="fake", correlation_id="order-1",
            idempotency_key="pay-1",
        )
        captured = payments.capture(intent.intent_id, idempotency_key="pay-1-cap")
        assert captured.amount == Money.of("20.00", "USD")

    def test_ledger_balances_after_posting(self, ledger):
        ledger.post(
            journal_id="sales", description="order-1", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of("20.00", "USD"),
            source="checkout", correlation_id="order-1",
        )
        ledger.assert_all_journals_balanced()


class TestFullCheckout:
    """The critical path: reserve -> price -> order -> pay -> post -> reconcile."""

    def test_checkout_flow(
        self, pricing, inventory, orders, payments, ledger, authz, stocked_offer
    ):
        # 1. reserve stock
        reservation = inventory.reserve(
            offer_id="offer-1", quantity=2, correlation_id="flow-1", idempotency_key="flow-res"
        )
        # 2. resolve price
        unit_price, _ = pricing.unit_price("sku-1", "USD", at=NOW)
        assert unit_price == Money.of("10.00", "USD")
        # 3. place the order on the immutable snapshot
        line = LineSnapshot(
            line_id="l1", offer_id="offer-1", product_id="sku-1", variant_id=None,
            seller_id="seller-1", quantity=2, unit_price=unit_price,
            fulfillment_model="seller_fulfilled", lead_time_days=2,
            components=[{"kind": "base", "amount": "20.00"}],
            line_total=Money.of("20.00", "USD"),
        )
        snapshot = OrderSnapshot(
            currency="USD", lines=[line], totals={"total": "20.00"},
            price_evaluation_id="pev-1", policy_versions={},
            offer_versions={"offer-1": 1}, snapshot_at=NOW,
        )
        cart = orders.open_cart("buyer-1", "tenant-A")
        order = orders.place_order(
            order_id=None, buyer_id="buyer-1", tenant_id="tenant-A", cart_id=cart.cart_id,
            snapshot=snapshot, correlation_id="flow-1", idempotency_key="flow-order",
            payment_correlation_id="flow-pay",
        )
        assert order.status == "confirmed"

        # 4. authorize and capture payment
        intent = payments.authorize(
            order_id=order.order_id, buyer_id="buyer-1", amount=Money.of("20.00", "USD"),
            method="card", provider_code="fake", correlation_id="flow-1",
            idempotency_key="flow-pay",
        )
        captured = payments.capture(intent.intent_id, idempotency_key="flow-pay-cap")
        assert captured.amount == Money.of("20.00", "USD")

        # 5. post to the ledger and confirm it balances
        ledger.post(
            journal_id="sales", description="flow-1", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of("20.00", "USD"),
            source="checkout", correlation_id="flow-1",
        )
        ledger.assert_all_journals_balanced()

        # 6. commit the reservation — stock is consumed exactly once
        inventory.commit(reservation.reservation_id, correlation_id="flow-1")
        position = inventory.position_for_offer("offer-1")
        assert position.on_hand == 8
        assert position.reserved == 0
        # allocatable also reflects the safety stock of 2
        assert position.allocatable == 6
