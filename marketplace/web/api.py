"""REST endpoint definitions.

Each handler is a thin adapter: it parses the request, resolves the
``AuthorizationContext`` from the bearer token, calls the canonical universe
service and returns the result. No domain logic lives here.

Public, no token: the landing page, ``/api/health``, ``/api/openapi.json``,
``/api/auth/login``, ``/api/demo/state``, ``/api/demo/credentials`` and the
browse endpoints (catalog, offers, public prices, stock availability, search).
Everything that reads or writes tenant-scoped commerce data — orders, payments,
the ledger, inventory mutations and checkout — requires an authenticated caller
in the runtime's tenant, and a buyer (``consumer``) sees only their own records.
A missing token is rejected, never treated as unrestricted (LAW-ARCH-07).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from marketplace.kernel import AuthorizationContext, Money
from marketplace.kernel.errors import NotFoundError, ValidationError
from marketplace.kernel.registry import CONCEPTS, INVARIANTS, REQUIREMENTS
from marketplace.universes import _base as universes
from marketplace.universes.u08_order.entities import LineSnapshot, OrderSnapshot

from .router import Request, Response, Router, serialize
from .runtime import Runtime

router = Router()

# Default validity window for demo resources (matches the seeded dataset).
UNTIL = "2027-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Landing page (browsable in a browser)
# ---------------------------------------------------------------------------


_LANDING_CSS = """
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
         max-width: 880px; margin: 2rem auto; padding: 0 1.2rem; }
  h1 { font-size: 1.6rem; margin-bottom: .2rem; }
  h2 { font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid #8883;
       padding-bottom: .3rem; }
  table { border-collapse: collapse; width: 100%; margin: .6rem 0; }
  th, td { border: 1px solid #8885; padding: .35rem .6rem; text-align: left; }
  th { background: #8881; }
  code, pre { font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
              background: #8881; padding: .12rem .35rem; border-radius: 4px; }
  pre { padding: .8rem; overflow-x: auto; }
  .muted { color: #888; }
  a { color: #2a6df4; }
  .key { font-weight: 600; }
</style>
"""


def _landing_page(base_url: str, runtime: Runtime) -> str:
    personas = runtime.seed_report.personas if runtime.seed_report else []
    products = runtime.seed_report.products if runtime.seed_report else []
    rows = "\n".join(
        f"<tr><td class='key'>{p['key']}</td><td><code>{p['api_key']}</code></td>"
        f"<td><code>{p['subject']}</code></td><td><code>{p['password']}</code></td>"
        f"<td>{', '.join(p['roles'][:3])}{', …' if len(p['roles']) > 3 else ''}</td></tr>"
        for p in personas
    )
    product_rows = "\n".join(
        f"<tr><td><code>{p['product_id'][:20]}…</code></td><td>{p['title']}</td>"
        f"<td>{p['price']} {p['currency']}</td><td><code>{p['offer_id'][:20]}…</code></td></tr>"
        for p in products
    )
    first_offer = products[0]["offer_id"] if products else "&lt;offer_id&gt;"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>UCOS Marketplace — local test server</title>{_LANDING_CSS}</head>
<body>
<h1>UCOS Marketplace <span class="muted">— local test server</span></h1>
<p class="muted">The whole marketplace domain, over a stdlib-only HTTP API.
State is in memory; restarting the server resets and re-seeds everything.</p>

<h2>Demo credentials</h2>
<p>Send an API key as <code>Authorization: Bearer &lt;api_key&gt;</code>, or
<a href="/api/demo/credentials">fetch them as JSON</a>.</p>
<table>
  <tr><th>Persona</th><th>API key</th><th>Login subject</th><th>Password</th><th>Roles</th></tr>
  {rows}
</table>

<h2>Seeded catalog</h2>
<table>
  <tr><th>Product</th><th>Title</th><th>Price</th><th>Offer</th></tr>
  {product_rows}
</table>

<h2>Quick start</h2>
<pre>curl {base_url}/api/health
curl -H 'Authorization: Bearer mk_demo_buyer' {base_url}/api/catalog/products

# one-shot checkout: reserve -&gt; order -&gt; pay -&gt; ledger -&gt; commit
curl -X POST -H 'Authorization: Bearer mk_demo_buyer' \\
  -H 'Content-Type: application/json' \\
  -d '{{"offer_id": "{first_offer}", "quantity": 2}}' \\
  {base_url}/api/checkout</pre>

<h2>Links</h2>
<table>
  <tr><th>Resource</th><th>URL</th></tr>
  <tr><td>OpenAPI (all routes)</td><td><a href="/api/openapi.json">/api/openapi.json</a></td></tr>
  <tr><td>Health</td><td><a href="/api/health">/api/health</a></td></tr>
  <tr><td>Demo state</td><td><a href="/api/demo/state">/api/demo/state</a></td></tr>
  <tr><td>Catalog</td><td><a href="/api/catalog/products">/api/catalog/products</a></td></tr>
  <tr><td>Offers</td><td><a href="/api/offers">/api/offers</a></td></tr>
  <tr><td>Search</td><td><a href="/api/search?q=cable">/api/search?q=cable</a></td></tr>
  <tr><td>Governance: concepts</td><td><a href="/api/governance/concepts">/api/governance/concepts</a></td></tr>
  <tr><td>Governance: requirements</td><td><a href="/api/governance/requirements">/api/governance/requirements</a></td></tr>
</table>

<p class="muted">Local testing adapter only — not a production deployment.
See <code>README_LOCAL_TESTING.md</code>.</p>
</body></html>"""


@router.get("/")
def landing(request: Request) -> Response:
    runtime = _rt(request)
    host = request.headers.get("Host", "127.0.0.1:8000")
    return Response.html(_landing_page(f"http://{host}", runtime))


def _rt(request: Request) -> Runtime:
    runtime: Optional[Runtime] = getattr(request, "runtime", None)
    if runtime is None:
        raise RuntimeError("runtime not attached to request")
    return runtime


def _authz(request: Request, *, roles: Optional[list[str]] = None) -> AuthorizationContext:
    """The caller's resolved authority.

    Unauthenticated callers get an anonymous context, which fails any required
    role check, so a missing token is rejected the same way as an insufficient
    one (LAW-ARCH-07: never bypass authorization because it is absent).
    """

    runtime = _rt(request)
    if request.authz is None:
        authz = AuthorizationContext(
            actor_id="anonymous",
            actor_type="person",
            roles=frozenset(),
            tenant_id=runtime.tenant_id,
        )
    else:
        authz = request.authz
    if roles:
        for role in roles:
            authz.requires_role(role)
    return authz


def _principal(request: Request, *, roles: Optional[list[str]] = None) -> AuthorizationContext:
    """An authenticated caller, in this runtime's tenant.

    Endpoints that expose tenant-scoped commerce data use this rather than
    ``_authz``: an anonymous caller is rejected outright instead of being handed
    an unrestricted view, so a missing token fails closed the same way an
    insufficient one does (LAW-ARCH-07).
    """

    authz = _authz(request, roles=roles)
    if authz.actor_id == "anonymous":
        from marketplace.kernel.errors import AuthorizationError

        raise AuthorizationError("An authenticated caller is required")
    authz.same_tenant(_rt(request).tenant_id)
    return authz


# The buyer persona's only authority; such a caller sees their own commerce
# records and nothing else (least privilege). Every other authenticated role is
# operational and may read tenant-wide records.
_CONSUMER_ROLES = frozenset({"consumer"})


def _is_consumer(authz: AuthorizationContext) -> bool:
    return not (authz.roles - _CONSUMER_ROLES)


def _decimal(value: Any, *, name: str = "value") -> Decimal:
    """Parse a decimal, rejecting malformed input as a 400 rather than a 500."""

    if value is None or value == "":
        raise ValidationError(f"{name} is required")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValidationError(f"{name} must be a number, got {value!r}") from exc


def _body(request: Request) -> dict:
    if request.body is None:
        return {}
    if not isinstance(request.body, dict):
        raise ValidationError("Request body must be a JSON object")
    return request.body


def _money(value: Any, currency: str) -> Money:
    if value is None:
        raise ValidationError("Amount is required")
    if isinstance(value, dict):
        return Money.from_dict({"amount": str(value.get("amount")), "currency": value.get("currency", currency)})
    return Money.of(value, currency)


def _now(request: Request) -> str:
    return universes.U03.now()


# ---------------------------------------------------------------------------
# Health, discovery and demo state (public)
# ---------------------------------------------------------------------------


@router.get("/api/health")
def health(request: Request) -> Response:
    runtime = _rt(request)
    return Response.ok(
        {
            "status": "ok",
            "service": "ucos-marketplace-local",
            "tenant": runtime.tenant_id,
            "currency": runtime.currency,
            "seeded": runtime.seed_report is not None,
            "products": len(runtime.catalog.products),
            "live_offers": len([o for o in runtime.offers.offers.values() if o.status == "active"]),
            "orders": len(runtime.orders.orders),
            "concepts": len(CONCEPTS.all()),
            "requirements": len(REQUIREMENTS.all()),
        }
    )


@router.get("/api/openapi.json")
def openapi(request: Request) -> Response:
    return Response.ok(_openapi_spec())


@router.get("/api/demo/state")
def demo_state(request: Request) -> Response:
    runtime = _rt(request)
    if runtime.seed_report is None:
        raise NotFoundError("Demo dataset is not seeded; start the server with --seed")
    return Response.ok(runtime.seed_report.to_dict())


@router.get("/api/demo/credentials")
def demo_credentials(request: Request) -> Response:
    runtime = _rt(request)
    if runtime.seed_report is None:
        raise NotFoundError("Demo dataset is not seeded")
    personas = runtime.seed_report.personas
    return Response.ok(
        {
            "note": (
                "Demo credentials for local testing only. Send an API key as "
                "`Authorization: Bearer <api_key>` or log in with the subject and "
                "password at POST /api/auth/login to receive a bearer token."
            ),
            "login_url": "/api/auth/login",
            "personas": [
                {
                    "key": p["key"],
                    "subject": p["subject"],
                    "password": p["password"],
                    "api_key": p["api_key"],
                    "roles": p["roles"],
                    "tenant_id": p["tenant_id"],
                }
                for p in personas
            ],
        }
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@router.post("/api/auth/login")
def auth_login(request: Request) -> Response:
    runtime = _rt(request)
    body = _body(request)
    subject = body.get("subject")
    password = body.get("password")
    tenant_id = body.get("tenant_id") or runtime.tenant_id
    if not subject or not password:
        raise ValidationError("subject and password are required")
    session = runtime.identity.authenticate(subject, password, tenant_id=tenant_id)
    roles = _persona_roles(runtime, session.identity_id)
    token = runtime.mint_token(session.identity_id, tenant_id, roles)
    return Response.ok(
        {
            "token": token,
            "token_type": "Bearer",
            "session_id": session.session_id,
            "identity_id": session.identity_id,
            "tenant_id": tenant_id,
            "roles": roles,
            "expires_at": session.expires_at,
        },
        note="Send this token as `Authorization: Bearer <token>`.",
    )


def _persona_roles(runtime: Runtime, identity_id: str) -> list[str]:
    """The role bundle for a seeded persona.

    Roles are carried in the token (the resolver's reference format), so login
    re-issues the persona's bundle rather than trusting the caller to name roles.
    """

    if runtime.seed_report:
        for persona in runtime.seed_report.personas:
            if persona.get("identity_id") == identity_id:
                return sorted(persona.get("roles", []))
    return ["consumer"]


@router.get("/api/auth/whoami")
def auth_whoami(request: Request) -> Response:
    authz = _authz(request)
    if authz.actor_id == "anonymous":
        return Response.ok({"authenticated": False, "actor_id": "anonymous"})
    identity_record = _rt(request).identity.identities.get(authz.actor_id, {})
    return Response.ok(
        {
            "authenticated": True,
            "identity_id": authz.actor_id,
            "party_id": identity_record.get("party_id"),
            "subject": identity_record.get("subject"),
            "actor_type": authz.actor_type,
            "tenant_id": authz.tenant_id,
            "roles": sorted(authz.roles),
            "scopes": sorted(authz.scopes),
        }
    )


# ---------------------------------------------------------------------------
# Catalog (U03)
# ---------------------------------------------------------------------------


@router.get("/api/catalog/products")
def catalog_list_products(request: Request) -> Response:
    runtime = _rt(request)
    products = [
        {
            "product_id": p.product_id,
            "title": p.current.title,
            "description": p.current.description,
            "product_type": p.product_type,
            "status": p.status,
            "version": p.version,
            "taxonomy": p.current.taxonomy_assignments,
            "brand_id": p.current.brand_id,
            "attributes": p.current.attributes,
            "variants": sorted(p.variants.keys()),
        }
        for p in runtime.catalog.products.values()
    ]
    return Response.ok({"count": len(products), "products": products})


@router.get("/api/catalog/products/{product_id}")
def catalog_get_product(request: Request) -> Response:
    runtime = _rt(request)
    product = runtime.catalog.products.get(request.params["product_id"])
    if product is None:
        raise NotFoundError(f"Product {request.params['product_id']} not found")
    offers = [
        {
            "offer_id": o.offer_id,
            "status": o.status,
            "list_price": o.list_price.to_dict(),
            "condition": o.condition,
            "valid_until": o.valid_until,
        }
        for o in runtime.offers.offers.values()
        if o.product_id == product.product_id
    ]
    return Response.ok(
        {
            "product_id": product.product_id,
            "title": product.current.title,
            "description": product.current.description,
            "product_type": product.product_type,
            "status": product.status,
            "version": product.version,
            "attributes": product.current.attributes,
            "taxonomy": product.current.taxonomy_assignments,
            "versions": [v.version for v in product.versions],
            "offers": offers,
        }
    )


@router.post("/api/catalog/products")
def catalog_create_product(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["catalog.writer"])
    body = _body(request)
    product = runtime.catalog.create_product(
        authz,
        product_type=body.get("product_type", "physical"),
        family_code=body.get("family_code"),
        title=body.get("title", "Untitled product"),
        description=body.get("description", ""),
        taxonomy_code=body.get("taxonomy_code", "electronics"),
        attributes=body.get("attributes", {}),
        brand_code=body.get("brand_code"),
        external_identifiers=body.get("external_identifiers"),
    )
    return Response.created({"product_id": product.product_id, "status": product.status})


@router.post("/api/catalog/products/{product_id}/submit")
def catalog_submit_product(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["catalog.writer"])
    entity = runtime.catalog.submit_for_publication(authz, request.params["product_id"])
    return Response.ok(
        {"product_id": request.params["product_id"], "status": entity.state}
    )


@router.post("/api/catalog/products/{product_id}/publish")
def catalog_publish_product(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["catalog.approver"])
    product = runtime.catalog.publish(authz, request.params["product_id"])
    return Response.ok({"product_id": product.product_id, "status": product.status})


@router.get("/api/catalog/taxonomy")
def catalog_taxonomy(request: Request) -> Response:
    runtime = _rt(request)
    nodes = [
        {
            "node_id": n.node_id,
            "code": n.code,
            "label": n.label,
            "parent_id": n.parent_id,
        }
        for n in runtime.catalog.schemas._nodes.values()
    ]
    return Response.ok({"count": len(nodes), "nodes": nodes})


@router.get("/api/catalog/brands")
def catalog_brands(request: Request) -> Response:
    runtime = _rt(request)
    brands = [
        {"brand_id": b.brand_id, "code": b.code, "label": b.label, "verified": b.verified}
        for b in runtime.catalog.brands.values()
    ]
    return Response.ok({"count": len(brands), "brands": brands})


# ---------------------------------------------------------------------------
# Pricing (U05)
# ---------------------------------------------------------------------------


@router.post("/api/pricing/pricebooks")
def pricing_create_pricebook(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["pricing.writer"])
    body = _body(request)
    book = runtime.pricing.create_pricebook(
        authz,
        code=body.get("code", "custom"),
        currency=body.get("currency", runtime.currency),
        precedence=int(body.get("precedence", 50)),
    )
    return Response.created(
        {
            "pricebook_id": book.pricebook_id,
            "code": book.code,
            "currency": book.currency,
        }
    )


@router.post("/api/pricing/pricebooks/{pricebook_id}/prices")
def pricing_set_price(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["pricing.writer"])
    body = _body(request)
    entry = runtime.pricing.set_price(
        authz,
        request.params["pricebook_id"],
        body.get("product_id"),
        _money(body.get("amount"), body.get("currency", runtime.currency)),
        valid_from=body.get("valid_from", _now(request)),
        valid_until=body.get("valid_until", UNTIL),
        variant_id=body.get("variant_id"),
        min_quantity=int(body.get("min_quantity", 1)),
        tier_code=body.get("tier_code", "standard"),
    )
    return Response.created({"pricebook_id": entry.pricebook_id, "product_id": entry.product_id})


@router.get("/api/pricing/prices/{product_id}")
def pricing_unit_price(request: Request) -> Response:
    runtime = _rt(request)
    product_id = request.params["product_id"]
    currency = request.query.get("currency", runtime.currency)
    quantity = int(request.query.get("quantity", 1))
    price, source = runtime.pricing.unit_price(
        product_id, currency, at=_now(request), quantity=quantity
    )
    return Response.ok(
        {
            "product_id": product_id,
            "unit_price": price.to_dict(),
            "quantity": quantity,
            "total": (price * quantity).to_dict(),
            "source": source,
        }
    )


@router.post("/api/pricing/evaluate")
def pricing_evaluate(request: Request) -> Response:
    runtime = _rt(request)
    _authz(request)
    body = _body(request)
    product_id = body.get("product_id")
    quantity = int(body.get("quantity", 1))
    currency = body.get("currency", runtime.currency)
    unit_price, _ = runtime.pricing.unit_price(product_id, currency, at=_now(request))
    evaluation = runtime.pricing.evaluate(
        offer_id=body.get("offer_id", ""),
        product_id=product_id,
        variant_id=body.get("variant_id"),
        quantity=quantity,
        currency=currency,
        at=_now(request),
        unit_price=unit_price,
        coupons=body.get("coupons", []),
        buyer_id=body.get("buyer_id", "anonymous"),
        jurisdiction=body.get("jurisdiction", ""),
        shipping_cost=_money(body["shipping_cost"], currency)
        if body.get("shipping_cost") is not None
        else None,
    )
    return Response.ok(serialize(evaluation))


@router.post("/api/pricing/promotions")
def pricing_create_promotion(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["promotion.writer"])
    body = _body(request)
    promotion = runtime.pricing.create_promotion(
        authz,
        code=body.get("code"),
        kind=body.get("kind", "percent"),
        value=_decimal(body.get("value")),
        currency=body.get("currency", runtime.currency),
        valid_from=body.get("valid_from", _now(request)),
        valid_until=body.get("valid_until", UNTIL),
        funded_by=body.get("funded_by", "platform"),
        stacking_group=body.get("stacking_group"),
    )
    return Response.created(
        {
            "promotion_id": promotion.promotion_id,
            "code": promotion.code,
            "kind": promotion.kind,
        }
    )


@router.post("/api/pricing/coupons")
def pricing_issue_coupon(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["promotion.writer"])
    body = _body(request)
    promotion = next(
        (p for p in runtime.pricing.promotions.values() if p.code == body.get("promotion_code")),
        None,
    )
    if promotion is None:
        raise NotFoundError(f"Promotion {body.get('promotion_code')!r} not found")
    coupon = runtime.pricing.issue_coupon(
        authz, promotion.promotion_id, body.get("code"), buyer_id=body.get("buyer_id")
    )
    return Response.created({"coupon_id": coupon.coupon_id, "code": coupon.code})


# ---------------------------------------------------------------------------
# Inventory (U06)
# ---------------------------------------------------------------------------


@router.post("/api/inventory/positions")
def inventory_create_position(request: Request) -> Response:
    runtime = _rt(request)
    authz = _principal(request, roles=["inventory.writer"])
    body = _body(request)
    position = runtime.inventory.create_position(
        authz,
        product_id=body.get("product_id"),
        owner_id=body.get("owner_id", authz.actor_id),
        location_id=body.get("location_id", "default"),
        variant_id=body.get("variant_id"),
        on_hand=int(body.get("on_hand", 0)),
        condition=body.get("condition", "new"),
    )
    return Response.created(
        {
            "position_id": position.position_id,
            "product_id": position.product_id,
            "on_hand": position.on_hand,
        }
    )


@router.post("/api/inventory/positions/{position_id}/link-offer")
def inventory_link_offer(request: Request) -> Response:
    runtime = _rt(request)
    _principal(request, roles=["inventory.writer"])
    body = _body(request)
    runtime.inventory.link_offer(body.get("offer_id"), request.params["position_id"])
    return Response.ok(
        {"position_id": request.params["position_id"], "offer_id": body.get("offer_id")}
    )


@router.get("/api/inventory/availability/{product_id}")
def inventory_availability(request: Request) -> Response:
    runtime = _rt(request)
    return Response.ok(runtime.inventory.availability(request.params["product_id"]))


@router.post("/api/inventory/reserve")
def inventory_reserve(request: Request) -> Response:
    runtime = _rt(request)
    _principal(request)
    body = _body(request)
    reservation = runtime.inventory.reserve(
        offer_id=body.get("offer_id"),
        quantity=int(body.get("quantity", 1)),
        correlation_id=body.get("correlation_id"),
        idempotency_key=body.get("idempotency_key"),
    )
    return Response.created(
        {
            "reservation_id": reservation.reservation_id,
            "offer_id": reservation.offer_id,
            "quantity": reservation.quantity,
            "state": reservation.state,
        }
    )


@router.post("/api/inventory/reservations/{reservation_id}/commit")
def inventory_commit(request: Request) -> Response:
    runtime = _rt(request)
    _principal(request)
    body = _body(request)
    reservation = runtime.inventory.commit(
        request.params["reservation_id"],
        correlation_id=body.get("correlation_id", ""),
    )
    return Response.ok(
        {
            "reservation_id": reservation.reservation_id,
            "state": reservation.state,
        }
    )


# ---------------------------------------------------------------------------
# Offers (U04)
# ---------------------------------------------------------------------------


@router.get("/api/offers")
def offers_list(request: Request) -> Response:
    runtime = _rt(request)
    offers = [
        {
            "offer_id": o.offer_id,
            "product_id": o.product_id,
            "seller_id": o.seller_id,
            "status": o.status,
            "condition": o.condition,
            "list_price": o.list_price.to_dict(),
            "valid_from": o.valid_from,
            "valid_until": o.valid_until,
            "inventory_position_id": o.inventory_position_id,
        }
        for o in runtime.offers.offers.values()
    ]
    return Response.ok({"count": len(offers), "offers": offers})


@router.get("/api/offers/eligible")
def offers_eligible(request: Request) -> Response:
    runtime = _rt(request)
    product_id = request.query.get("product_id")
    if not product_id:
        raise ValidationError("product_id query parameter is required")
    at = _now(request)
    matching = runtime.offers.eligible_offers(
        product_id,
        at=at,
        buyer_class=request.query.get("buyer_class", "consumer"),
        territory=request.query.get("territory", "*"),
        channel=request.query.get("channel", "web"),
    )
    offers = [
        {
            "offer_id": o.offer_id,
            "product_id": o.product_id,
            "list_price": o.list_price.to_dict(),
            "condition": o.condition,
            "lead_time_days": o.terms.lead_time_days,
        }
        for o in matching
    ]
    return Response.ok({"count": len(offers), "at": at, "offers": offers})


@router.post("/api/offers/listings")
def offers_create_listing(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["seller"])
    body = _body(request)
    listing = runtime.offers.create_listing(
        authz,
        seller_id=body.get("seller_id"),
        product_id=body.get("product_id"),
        title=body.get("title", "Untitled listing"),
        taxonomy_node_id=body.get("taxonomy_node_id", ""),
        tenant_id=body.get("tenant_id", authz.tenant_id),
        overlays=body.get("overlays"),
    )
    return Response.created(
        {
            "listing_id": listing.listing_id,
            "seller_id": listing.seller_id,
            "product_id": listing.product_id,
        }
    )


@router.post("/api/offers/listings/{listing_id}/submit")
def offers_submit_listing(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request)
    listing = runtime.offers.submit_listing(authz, request.params["listing_id"])
    return Response.ok({"listing_id": listing.listing_id, "status": listing.status})


@router.post("/api/offers/listings/{listing_id}/publish")
def offers_publish_listing(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["merch.approver"])
    listing = runtime.offers.publish_listing(authz, request.params["listing_id"])
    return Response.ok({"listing_id": listing.listing_id, "status": listing.status})


@router.post("/api/offers")
def offers_create_offer(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["seller"])
    body = _body(request)
    from marketplace.universes.u04_offer.service import OfferTerms

    offer = runtime.offers.create_offer(
        authz,
        body.get("listing_id"),
        body.get("product_id"),
        variant_id=body.get("variant_id"),
        condition=body.get("condition", "new"),
        list_price=_money(body.get("list_price"), body.get("currency", runtime.currency)),
        terms=OfferTerms(
            fulfillment_model=body.get("fulfillment_model", "seller_fulfilled"),
            lead_time_days=int(body.get("lead_time_days", 2)),
            return_window_days=int(body.get("return_window_days", 30)),
        ),
        valid_from=body.get("valid_from", _now(request)),
        valid_until=body.get("valid_until", UNTIL),
        inventory_position_id=body.get("inventory_position_id"),
    )
    return Response.created({"offer_id": offer.offer_id, "status": offer.status})


@router.post("/api/offers/{offer_id}/activate")
def offers_activate(request: Request) -> Response:
    runtime = _rt(request)
    authz = _authz(request, roles=["seller"])
    offer = runtime.offers.activate_offer(authz, request.params["offer_id"])
    return Response.ok({"offer_id": offer.offer_id, "status": offer.status})


# ---------------------------------------------------------------------------
# Orders & checkout (U08)
# ---------------------------------------------------------------------------


@router.post("/api/orders/carts")
def orders_open_cart(request: Request) -> Response:
    runtime = _rt(request)
    authz = _principal(request)
    body = _body(request)
    cart = runtime.orders.open_cart(
        body.get("buyer_id", authz.actor_id), authz.tenant_id
    )
    return Response.created({"cart_id": cart.cart_id, "buyer_id": cart.buyer_id})


@router.post("/api/orders/carts/{cart_id}/lines")
def orders_add_line(request: Request) -> Response:
    runtime = _rt(request)
    _principal(request)
    body = _body(request)
    cart = runtime.orders.add_to_cart(
        request.params["cart_id"],
        offer_id=body.get("offer_id"),
        product_id=body.get("product_id"),
        seller_id=body.get("seller_id"),
        quantity=int(body.get("quantity", 1)),
        variant_id=body.get("variant_id"),
    )
    return Response.ok(
        {
            "cart_id": cart.cart_id,
            "lines": [
                {
                    "offer_id": ln.offer_id,
                    "product_id": ln.product_id,
                    "quantity": ln.quantity,
                    "added_at": ln.added_at,
                }
                for ln in cart.lines
            ],
        }
    )


@router.get("/api/orders")
def orders_list(request: Request) -> Response:
    runtime = _rt(request)
    authz = _principal(request)
    orders = [
        {
            "order_id": o.order_id,
            "buyer_id": o.buyer_id,
            "status": o.status,
            "placed_at": o.placed_at,
            "correlation_id": o.correlation_id,
            "total": o.snapshot.totals,
            "currency": o.snapshot.currency,
            "lines": [
                {
                    "line_id": ln.line_id,
                    "product_id": ln.product_id,
                    "quantity": ln.quantity,
                    "unit_price": ln.unit_price.to_dict(),
                }
                for ln in o.snapshot.lines
            ],
        }
        for o in runtime.orders.orders.values()
        if not _is_consumer(authz) or o.buyer_id == authz.actor_id
    ]
    return Response.ok({"count": len(orders), "orders": orders})


@router.get("/api/orders/{order_id}")
def orders_get(request: Request) -> Response:
    runtime = _rt(request)
    authz = _principal(request)
    order = runtime.orders.orders.get(request.params["order_id"])
    # An order the caller may not read is reported as absent so a consumer
    # cannot enumerate other buyers' order ids.
    if order is None or (_is_consumer(authz) and order.buyer_id != authz.actor_id):
        raise NotFoundError(f"Order {request.params['order_id']} not found")
    return Response.ok(
        {
            "order_id": order.order_id,
            "buyer_id": order.buyer_id,
            "status": order.status,
            "placed_at": order.placed_at,
            "correlation_id": order.correlation_id,
            "snapshot": order.snapshot,
            "timeline": runtime.orders.timeline(order.order_id),
        }
    )


@router.post("/api/orders")
def orders_place(request: Request) -> Response:
    """Place an order from a cart.

    The immutable snapshot is built server-side from the cart lines by resolving
    each line's unit price through the pricing service, so the committed terms
    are the priced terms at checkout (INV-01).
    """

    runtime = _rt(request)
    authz = _principal(request)
    body = _body(request)
    cart_id = body.get("cart_id")
    cart = runtime.orders.carts.get(cart_id)
    if cart is None:
        raise NotFoundError(f"Cart {cart_id!r} not found")
    if not cart.lines:
        raise ValidationError("Cart is empty")
    correlation_id = body.get("correlation_id") or f"checkout-{cart_id}"

    lines: list[LineSnapshot] = []
    total = Money.of("0", runtime.currency)
    for index, line in enumerate(cart.lines, start=1):
        unit_price, _ = runtime.pricing.unit_price(
            line.product_id, runtime.currency, at=_now(request)
        )
        line_total = unit_price * line.quantity
        total = total + line_total
        lines.append(
            LineSnapshot(
                line_id=f"l{index}",
                offer_id=line.offer_id,
                product_id=line.product_id,
                variant_id=line.variant_id,
                seller_id=line.seller_id,
                quantity=line.quantity,
                unit_price=unit_price,
                fulfillment_model="seller_fulfilled",
                lead_time_days=2,
                components=[{"kind": "base", "amount": str(line_total.amount)}],
                line_total=line_total,
            )
        )
    snapshot = OrderSnapshot(
        currency=runtime.currency,
        lines=lines,
        totals={"total": str(total.amount), "currency": runtime.currency},
        price_evaluation_id="api-checkout",
        policy_versions={},
        offer_versions={ln.offer_id: 1 for ln in lines},
        snapshot_at=_now(request),
    )
    order = runtime.orders.place_order(
        order_id=None,
        buyer_id=body.get("buyer_id", cart.buyer_id),
        tenant_id=authz.tenant_id,
        cart_id=cart_id,
        snapshot=snapshot,
        correlation_id=correlation_id,
        idempotency_key=body.get("idempotency_key", correlation_id),
    )
    return Response.created(
        {
            "order_id": order.order_id,
            "status": order.status,
            "total": snapshot.totals,
            "correlation_id": correlation_id,
        }
    )


# ---------------------------------------------------------------------------
# Payments & ledger (U09)
# ---------------------------------------------------------------------------


@router.post("/api/payments/authorize")
def payments_authorize(request: Request) -> Response:
    runtime = _rt(request)
    authz = _principal(request)
    body = _body(request)
    intent = runtime.payments.authorize(
        order_id=body.get("order_id"),
        buyer_id=body.get("buyer_id", authz.actor_id),
        amount=_money(body.get("amount"), body.get("currency", runtime.currency)),
        method=body.get("method", "card"),
        provider_code=body.get("provider_code", "demo"),
        correlation_id=body.get("correlation_id", body.get("order_id")),
        idempotency_key=body.get("idempotency_key"),
    )
    return Response.created(
        {
            "intent_id": intent.intent_id,
            "order_id": intent.order_id,
            "status": intent.status,
            "amount": intent.amount.to_dict(),
        }
    )


@router.post("/api/payments/intents/{intent_id}/capture")
def payments_capture(request: Request) -> Response:
    runtime = _rt(request)
    _principal(request)
    body = _body(request)
    intent = runtime.payments.capture(
        request.params["intent_id"],
        amount=_money(body["amount"], runtime.currency) if body.get("amount") else None,
        idempotency_key=body.get("idempotency_key"),
    )
    return Response.ok(
        {
            "intent_id": intent.intent_id,
            "status": intent.status,
            "amount": intent.amount.to_dict(),
        }
    )


@router.get("/api/payments/intents")
def payments_intents(request: Request) -> Response:
    runtime = _rt(request)
    authz = _principal(request)
    intents = [
        {
            "intent_id": i.intent_id,
            "order_id": i.order_id,
            "buyer_id": i.buyer_id,
            "status": i.status,
            "amount": i.amount.to_dict(),
            "provider": i.provider,
        }
        for i in runtime.payments.intents.values()
        if not _is_consumer(authz) or i.buyer_id == authz.actor_id
    ]
    return Response.ok({"count": len(intents), "intents": intents})


@router.post("/api/ledger/entries")
def ledger_post(request: Request) -> Response:
    runtime = _rt(request)
    _principal(request, roles=["pricing.writer"])
    body = _body(request)
    entry = runtime.ledger.post(
        journal_id=body.get("journal_id", "sales"),
        description=body.get("description", "api-entry"),
        debit_account_id=body.get("debit_account_id"),
        credit_account_id=body.get("credit_account_id"),
        amount=_money(body.get("amount"), body.get("currency", runtime.currency)),
        source=body.get("source", "api"),
        correlation_id=body.get("correlation_id", "api"),
    )
    runtime.ledger.assert_all_journals_balanced()
    return Response.created(
        {
            "entry_id": entry.entry_id,
            "journal_id": entry.journal_id,
            "debit": entry.debit_account_id,
            "credit": entry.credit_account_id,
        }
    )


@router.get("/api/ledger/trial-balance")
def ledger_trial_balance(request: Request) -> Response:
    runtime = _rt(request)
    _principal(request)
    return Response.ok(runtime.ledger.trial_balance())


# ---------------------------------------------------------------------------
# One-shot checkout: reserve -> price -> order -> capture -> post -> commit
# ---------------------------------------------------------------------------


@router.post("/api/checkout")
def checkout(request: Request) -> Response:
    """Run the full critical path in one call and return the trace.

    This is the fastest way to see the whole platform work: stock is reserved,
    an order is placed on an immutable priced snapshot, payment is authorized
    and captured, the ledger is posted and balanced, and the reservation is
    committed so stock decrements exactly once.
    """

    runtime = _rt(request)
    authz = _principal(request)
    body = _body(request)
    offer_id = body.get("offer_id")
    quantity = int(body.get("quantity", 1))
    if not offer_id:
        raise ValidationError("offer_id is required")
    offer = runtime.offers.offers.get(offer_id)
    if offer is None:
        raise NotFoundError(f"Offer {offer_id!r} not found")
    correlation_id = body.get("correlation_id") or f"co-{offer_id}"
    buyer_id = body.get("buyer_id", authz.actor_id)
    trace: dict[str, Any] = {"correlation_id": correlation_id}

    # 1. reserve stock (fails closed on oversell, INV-02)
    reservation = runtime.inventory.reserve(
        offer_id=offer_id,
        quantity=quantity,
        correlation_id=correlation_id,
        idempotency_key=f"{correlation_id}-reserve",
    )
    trace["reservation"] = {
        "reservation_id": reservation.reservation_id,
        "quantity": reservation.quantity,
        "state": reservation.state,
    }

    # A committed reservation means this checkout already completed under this
    # correlation id. Re-running any financial step would double-count, so the
    # recorded outcome is returned unchanged (idempotent checkout, INV-007).
    if reservation.state == "committed":
        existing = next(
            (
                o
                for o in runtime.orders.orders.values()
                if o.correlation_id == correlation_id
            ),
            None,
        )
        trace["order"] = (
            {
                "order_id": existing.order_id,
                "status": existing.status,
                "total": existing.snapshot.totals,
            }
            if existing is not None
            else {"status": "already completed"}
        )
        position = runtime.inventory.position_for_offer(offer_id)
        trace["inventory"] = {
            "reservation_state": "committed",
            "on_hand": position.on_hand if position else None,
            "reserved": position.reserved if position else None,
        }
        trace["ledger"] = runtime.ledger.trial_balance()
        return Response.ok(trace, note="checkout already completed under this correlation id")

    # 2. resolve price and build the immutable snapshot
    unit_price, _ = runtime.pricing.unit_price(
        offer.product_id, offer.currency, at=_now(request)
    )
    line_total = unit_price * quantity
    line = LineSnapshot(
        line_id="l1",
        offer_id=offer_id,
        product_id=offer.product_id,
        variant_id=offer.variant_id,
        seller_id=offer.seller_id,
        quantity=quantity,
        unit_price=unit_price,
        fulfillment_model=offer.terms.fulfillment_model,
        lead_time_days=offer.terms.lead_time_days,
        components=[{"kind": "base", "amount": str(line_total.amount)}],
        line_total=line_total,
    )
    snapshot = OrderSnapshot(
        currency=offer.currency,
        lines=[line],
        totals={"total": str(line_total.amount), "currency": offer.currency},
        price_evaluation_id="api-checkout",
        policy_versions={},
        offer_versions={offer_id: offer.version},
        snapshot_at=_now(request),
    )

    # 3. place the order (idempotent on the correlation id)
    cart = runtime.orders.open_cart(buyer_id, authz.tenant_id)
    order = runtime.orders.place_order(
        order_id=None,
        buyer_id=buyer_id,
        tenant_id=authz.tenant_id,
        cart_id=cart.cart_id,
        snapshot=snapshot,
        correlation_id=correlation_id,
        idempotency_key=f"{correlation_id}-order",
        payment_correlation_id=f"{correlation_id}-pay",
    )
    trace["order"] = {
        "order_id": order.order_id,
        "status": order.status,
        "total": snapshot.totals,
    }

    # 4. authorize and capture payment
    intent = runtime.payments.authorize(
        order_id=order.order_id,
        buyer_id=buyer_id,
        amount=line_total,
        method=body.get("method", "card"),
        provider_code="demo",
        correlation_id=correlation_id,
        idempotency_key=f"{correlation_id}-pay",
    )
    captured = runtime.payments.capture(
        intent.intent_id, idempotency_key=f"{correlation_id}-capture"
    )
    trace["payment"] = {
        "intent_id": intent.intent_id,
        "status": captured.status,
        "amount": captured.amount.to_dict(),
    }

    # 5. post to the ledger; the balanced assertion is part of the transaction
    runtime.ledger.post(
        journal_id="sales",
        description=order.order_id,
        debit_account_id="cash",
        credit_account_id="revenue",
        amount=line_total,
        source="api.checkout",
        correlation_id=correlation_id,
    )
    runtime.ledger.assert_all_journals_balanced()
    trace["ledger"] = runtime.ledger.trial_balance()

    # 6. commit the reservation so stock is consumed exactly once
    committed = runtime.inventory.commit(
        reservation.reservation_id, correlation_id=correlation_id
    )
    position = runtime.inventory.position_for_offer(offer_id)
    trace["inventory"] = {
        "reservation_state": committed.state,
        "on_hand": position.on_hand if position else None,
        "reserved": position.reserved if position else None,
    }
    return Response.created(trace)


# ---------------------------------------------------------------------------
# Search (U11)
# ---------------------------------------------------------------------------


@router.get("/api/search")
def search(request: Request) -> Response:
    runtime = _rt(request)
    query = request.query.get("q", "")
    if not query:
        return Response.ok({"count": 0, "hits": [], "note": "add ?q=<terms>"})
    limit = int(request.query.get("limit", 20))
    hits = runtime.experience.search(query, limit=limit)
    return Response.ok({"count": len(hits), "query": query, "hits": hits})


# ---------------------------------------------------------------------------
# Governance & traceability (registry + audit)
# ---------------------------------------------------------------------------


@router.get("/api/governance/concepts")
def governance_concepts(request: Request) -> Response:
    _authz(request)
    concepts = [
        {
            "concept": c.concept,
            "owner_universe": c.owner_universe,
            "description": c.description,
            "identifier_prefix": c.identifier_prefix,
            "write_path": c.write_path,
            "read_projections": c.read_projections,
        }
        for c in CONCEPTS.all()
    ]
    return Response.ok({"count": len(concepts), "concepts": concepts})


@router.get("/api/governance/requirements")
def governance_requirements(request: Request) -> Response:
    _authz(request)
    requirements = [
        {
            "requirement_id": r.requirement_id,
            "domain": r.domain,
            "statement": r.statement,
            "status": r.status,
            "capability_ids": r.capability_ids,
            "test_ids": r.test_ids,
            "trace_complete": r.trace_complete(),
        }
        for r in REQUIREMENTS.all()
    ]
    return Response.ok(
        {
            "count": len(requirements),
            "gaps": [r.requirement_id for r in REQUIREMENTS.gaps()],
            "untraced": [r.requirement_id for r in REQUIREMENTS.untraced()],
            "requirements": requirements,
        }
    )


@router.get("/api/governance/invariants")
def governance_invariants(request: Request) -> Response:
    _authz(request)
    invariants = [
        {
            "invariant_id": i.invariant_id,
            "statement": i.statement,
            "test_ids": i.test_ids,
        }
        for i in INVARIANTS.all()
    ]
    return Response.ok({"count": len(invariants), "invariants": invariants})


@router.get("/api/governance/audit/{universe_id}")
def governance_audit(request: Request) -> Response:
    _rt(request)
    _authz(request)
    universe_id = request.params["universe_id"].upper()
    universe = universes.ALL_UNIVERSES.get(universe_id)
    if universe is None:
        raise NotFoundError(f"Unknown universe {universe_id!r}")
    entries = universe.audit.entries()
    broken = universe.audit.verify()
    return Response.ok(
        {
            "universe_id": universe_id,
            "name": universe.name,
            "entries": len(entries),
            "chain_intact": not broken,
            "broken_indices": broken,
            "records": entries,
        }
    )


@router.get("/api/governance/provenance/{target_id}")
def governance_provenance(request: Request) -> Response:
    _rt(request)
    _authz(request)
    target_id = request.params["target_id"]
    universe_id = request.query.get("universe", "").upper()
    records = []
    if universe_id:
        universe = universes.ALL_UNIVERSES.get(universe_id)
        if universe is None:
            raise NotFoundError(f"Unknown universe {universe_id!r}")
        records = universe.audit.for_target(target_id)
    else:
        for universe in universes.ALL_UNIVERSES.values():
            records.extend(universe.audit.for_target(target_id))
    if not records:
        raise NotFoundError(f"No provenance recorded for {target_id!r}")
    return Response.ok({"target_id": target_id, "records": records})


# ---------------------------------------------------------------------------
# OpenAPI discovery
# ---------------------------------------------------------------------------


def _openapi_spec() -> dict:
    paths: dict[str, Any] = {}
    for method, pattern in router.routes():
        path = pattern.replace("{", "{").replace("}", "}")
        operation_id = pattern.strip("/").replace("/", "_").replace("{", "").replace("}", "")
        paths.setdefault(path, {})[method.lower()] = {
            "operationId": operation_id,
            "summary": f"{method} {path}",
            "tags": [path.strip("/").split("/")[1] if len(path.strip("/").split("/")) > 1 else "root"],
        }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "UCOS Marketplace — local test API",
            "version": "1.0.0",
            "description": (
                "Reference HTTP surface over the UCOS marketplace domain kernel. "
                "Authenticate with a demo API key or a token from /api/auth/login."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": paths,
    }
