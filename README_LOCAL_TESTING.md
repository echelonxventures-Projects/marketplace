# UCOS Marketplace — Local Testing Guide

The platform ships with a **stdlib-only HTTP server** so you can exercise the
whole marketplace locally: catalog, pricing, inventory, offers, cart, checkout,
payments, ledger, search and governance traceability. No third-party dependency
is added — the server is built on `http.server`, preserving the platform's
zero-runtime-dependency property.

## Start the server

```bash
python3 server.py                    # http://127.0.0.1:8000, seeded demo dataset
python3 server.py --port 9000        # custom port
python3 server.py --host 0.0.0.0     # listen on all interfaces
python3 server.py --no-seed          # empty platform, no demo data
```

All state is **in memory**. Restarting the server resets everything and re-seeds
the demo dataset. The startup banner prints the live URLs and credentials.

## Demo credentials

Three personas are seeded in tenant `tenant-A`. Send an API key as a bearer
token, or log in to receive one:

| Persona | API key (bearer)              | Login (subject / password)          |
|---------|-------------------------------|-------------------------------------|
| admin   | `mk_demo_admin`               | `admin@marketplace.local` / `admin-pass-1`   |
| seller  | `mk_demo_seller`              | `seller@marketplace.local` / `seller-pass-1` |
| buyer   | `mk_demo_buyer`               | `buyer@marketplace.local` / `buyer-pass-1`   |

```bash
# Either use the API key directly:
curl -H 'Authorization: Bearer mk_demo_buyer' http://127.0.0.1:8000/api/catalog/products

# Or log in for a bearer token:
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"subject": "buyer@marketplace.local", "password": "buyer-pass-1"}'
```

Introspect any token with `GET /api/auth/whoami`. Credentials are demo-only and
are deliberately published by `GET /api/demo/credentials`.

## Demo dataset

Three published ACME products, each with a live offer and stocked inventory,
plus a seller with a signed contract, a `PCT10` promotion, a `SAVE10` coupon and
a balanced ledger:

```bash
curl http://127.0.0.1:8000/api/demo/state
curl http://127.0.0.1:8000/api/health
```

## The quick win: one-shot checkout

`POST /api/checkout` runs the full critical path in one call — reserve stock,
place an order on an immutable priced snapshot, authorize and capture payment,
post to the (balanced) ledger, and commit inventory. It is idempotent on the
`correlation_id`.

```bash
curl -X POST http://127.0.0.1:8000/api/checkout \
  -H 'Authorization: Bearer mk_demo_buyer' \
  -H 'Content-Type: application/json' \
  -d '{"offer_id": "<offer_id from /api/offers>", "quantity": 2}'
```

## Endpoint reference

Discovery: `GET /api/openapi.json` lists every registered route.

Browse and discovery endpoints (catalog, offers, public prices, stock
availability, search, health, demo state) are open. **Everything that reads or
writes tenant-scoped commerce data requires an authenticated caller in the
tenant**: a missing token is rejected (`403`), never treated as unrestricted.
A buyer (`consumer`) sees only their own orders and payment intents;
operational roles (`admin`, `seller`) read tenant-wide.

| Area | Method & path | Notes |
|---|---|---|
| Auth | `POST /api/auth/login` | `{subject, password}` → bearer token |
| Auth | `GET /api/auth/whoami` | introspect the current token |
| Catalog | `GET /api/catalog/products` | list |
| Catalog | `GET /api/catalog/products/{id}` | one, with its offers |
| Catalog | `POST /api/catalog/products` | `catalog.writer` |
| Catalog | `POST /api/catalog/products/{id}/submit` | `catalog.writer` |
| Catalog | `POST /api/catalog/products/{id}/publish` | `catalog.approver` |
| Catalog | `GET /api/catalog/taxonomy` · `GET /api/catalog/brands` | |
| Pricing | `POST /api/pricing/pricebooks` | `pricing.writer` |
| Pricing | `POST /api/pricing/pricebooks/{id}/prices` | `pricing.writer` |
| Pricing | `GET /api/pricing/prices/{product_id}?quantity=N` | unit + total |
| Pricing | `POST /api/pricing/evaluate` | full contextual breakdown |
| Pricing | `POST /api/pricing/promotions` · `POST /api/pricing/coupons` | `promotion.writer` |
| Inventory | `POST /api/inventory/positions` | `inventory.writer` |
| Inventory | `POST /api/inventory/positions/{id}/link-offer` | `inventory.writer` |
| Inventory | `GET /api/inventory/availability/{product_id}` | |
| Inventory | `POST /api/inventory/reserve` · `POST /api/inventory/reservations/{id}/commit` | authenticated |
| Offers | `GET /api/offers` · `GET /api/offers/eligible?product_id=` | |
| Offers | `POST /api/offers/listings` | `seller` |
| Offers | `POST /api/offers/listings/{id}/submit` | |
| Offers | `POST /api/offers/listings/{id}/publish` | `merch.approver` |
| Offers | `POST /api/offers` · `POST /api/offers/{id}/activate` | `seller` |
| Orders | `POST /api/orders/carts` · `POST /api/orders/carts/{id}/lines` | authenticated |
| Orders | `POST /api/orders` · `GET /api/orders` · `GET /api/orders/{id}` | authenticated; consumer sees own |
| Payments | `POST /api/payments/authorize` | provider `demo` is pre-registered |
| Payments | `POST /api/payments/intents/{id}/capture` · `GET /api/payments/intents` | authenticated; consumer sees own |
| Ledger | `POST /api/ledger/entries` · `GET /api/ledger/trial-balance` | `pricing.writer` to post; authenticated to read |
| Checkout | `POST /api/checkout` | one-shot critical path, authenticated |
| Search | `GET /api/search?q=` | over seeded products |
| Governance | `GET /api/governance/concepts` | 78 canonical concepts, one owner each |
| Governance | `GET /api/governance/requirements` | 50/50 implemented, 0 gaps |
| Governance | `GET /api/governance/invariants` | 60 invariants |
| Governance | `GET /api/governance/audit/{universe_id}` | tamper-evident audit chain |
| Governance | `GET /api/governance/provenance/{target_id}` | who changed what, why |

## Error contract

Errors use the canonical kernel contract, mapped to HTTP status:

```json
{ "code": "INVARIANT_VIOLATION", "message": "[INV-02] ...", "details": {}, "retryable": false }
```

Common statuses: `400` validation, `401` authentication, `403` authorization,
`404` not found, `409` illegal transition / oversell / duplicate, `503`
dependency unavailable (retryable).

## Things worth trying

- **Oversell is rejected, not clamped**: `POST /api/checkout` with a large
  quantity returns `409 INVARIANT_VIOLATION [INV-02]`.
- **Duplicate checkout is a no-op**: repeat a call with the same
  `correlation_id`; stock and ledger are unchanged.
- **Authorization fails closed**: the buyer key cannot publish a product
  (`403`), and a missing token is rejected rather than treated as unrestricted.
  Anonymous calls to `/api/orders`, `/api/payments/intents`,
  `/api/ledger/trial-balance`, `/api/checkout` and the inventory mutations are
  all rejected.
- **Tenant isolation**: a token for `tenant-A` cannot read another tenant's
  resources.
- **A buyer sees only their own records**: `GET /api/orders` and
  `GET /api/payments/intents` return only the consumer's own rows; reading
  another buyer's order by id is a `404`, and the `admin`/`seller` keys see the
  tenant-wide view.
- **Ledger always balances**: every checkout posts a double-entry record, and
  the balance assertion runs as part of the request.

## Scope

This is the U15 adapter surface: a **local testing adapter**, not a production
deployment. It keeps the honest deferral recorded in the implementation ledger —
there is still no container, CI pipeline, durable persistence or secrets
management. Production authorization remains out of scope here.
