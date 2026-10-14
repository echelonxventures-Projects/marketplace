"""Demo dataset and credentials for local testing.

Seeds one tenant with three personas (admin, seller, buyer), a small electronics
catalog with live offers, priced stock, a promotion with a coupon, seller
onboarding and contract, and a balanced ledger. Everything is created through
the canonical universe services under a system ``AuthorizationContext``, so the
seeded state is exactly what the API would produce if you typed every call by
hand.

Credentials are demo-only and are deliberately printed by ``/api/demo/state``
and on server startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from marketplace.kernel import AuthorizationContext, Money

from .runtime import DEFAULT_CURRENCY, DEFAULT_TENANT

NOW = "2026-01-01T00:00:00+00:00"
UNTIL = "2027-01-01T00:00:00+00:00"

SYSTEM_ROLES = frozenset(
    {
        "party.writer",
        "party.admin",
        "consent.admin",
        "delegator",
        "taxonomy.writer",
        "schema.publisher",
        "catalog.writer",
        "catalog.approver",
        "catalog.admin",
        "seller.recruiter",
        "onboarding.agent",
        "onboarding.approver",
        "contracts.writer",
        "pricing.writer",
        "promotion.writer",
        "inventory.writer",
        "inventory.feed",
        "merch.approver",
        "merch.admin",
        "trust.admin",
        "seller",
    }
)

SELLER_ROLES = frozenset(
    {
        "party.writer",
        "taxonomy.writer",
        "schema.publisher",
        "catalog.writer",
        "catalog.approver",
        "seller",
        "merch.approver",
        "pricing.writer",
        "promotion.writer",
        "inventory.writer",
        "inventory.feed",
    }
)

BUYER_ROLES = frozenset({"consumer"})


@dataclass
class Persona:
    """A demo principal: login subject, password and the API key minted for it."""

    key: str
    subject: str
    password: str
    display_name: str
    party_type: str
    roles: frozenset[str]
    tenant_id: str = DEFAULT_TENANT
    party_id: Optional[str] = None
    identity_id: Optional[str] = None
    api_key: Optional[str] = None

    def to_public_dict(self) -> dict:
        return {
            "key": self.key,
            "subject": self.subject,
            "password": self.password,
            "display_name": self.display_name,
            "tenant_id": self.tenant_id,
            "roles": sorted(self.roles),
            "party_id": self.party_id,
            "identity_id": self.identity_id,
            "api_key": self.api_key,
        }


PERSONAS: list[Persona] = [
    Persona(
        key="admin",
        subject="admin@marketplace.local",
        password="admin-pass-1",
        display_name="Platform Admin",
        party_type="person",
        roles=SYSTEM_ROLES,
    ),
    Persona(
        key="seller",
        subject="seller@marketplace.local",
        password="seller-pass-1",
        display_name="ACME Audio Merchant",
        party_type="person",
        roles=SELLER_ROLES,
    ),
    Persona(
        key="buyer",
        subject="buyer@marketplace.local",
        password="buyer-pass-1",
        display_name="Demo Shopper",
        party_type="person",
        roles=BUYER_ROLES,
    ),
]

# API keys are stable across restarts (they encode the identity, not a secret),
# so documentation can point at them directly.
API_KEYS: dict[str, str] = {
    "admin": "mk_demo_admin",
    "seller": "mk_demo_seller",
    "buyer": "mk_demo_buyer",
}


@dataclass
class SeedReport:
    personas: list[dict] = field(default_factory=list)
    tenant_id: str = DEFAULT_TENANT
    currency: str = DEFAULT_CURRENCY
    taxonomy: dict[str, str] = field(default_factory=dict)
    brand: Optional[dict] = None
    family: Optional[dict] = None
    seller: Optional[dict] = None
    pricebook: Optional[dict] = None
    promotion: Optional[dict] = None
    coupon_code: Optional[str] = None
    products: list[dict] = field(default_factory=list)
    ledger_accounts: list[str] = field(default_factory=list)
    journal_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "currency": self.currency,
            "personas": self.personas,
            "taxonomy": self.taxonomy,
            "brand": self.brand,
            "family": self.family,
            "seller": self.seller,
            "pricebook": self.pricebook,
            "promotion": self.promotion,
            "coupon_code": self.coupon_code,
            "products": self.products,
            "ledger_accounts": self.ledger_accounts,
            "journal_id": self.journal_id,
        }


CATALOG = [
    {
        "title": "ACME Wireless Headphones",
        "description": "Over-ear wireless headphones with active noise cancellation.",
        "taxonomy": "audio",
        "product_type": "physical",
        "price": "149.99",
        "on_hand": 100,
        "attributes": {"color": "black", "weight_grams": 250, "wireless": True},
    },
    {
        "title": "ACME Bluetooth Speaker",
        "description": "Portable speaker, 20-hour battery, water resistant.",
        "taxonomy": "audio",
        "product_type": "physical",
        "price": "89.50",
        "on_hand": 60,
        "attributes": {"color": "blue", "weight_grams": 1200, "wireless": True},
    },
    {
        "title": "ACME USB-C Cable",
        "description": "Braided USB-C to USB-C cable, 2 m, 100 W.",
        "taxonomy": "accessories",
        "product_type": "physical",
        "price": "12.00",
        "on_hand": 400,
        "attributes": {"color": "white", "weight_grams": 45, "wireless": False},
    },
]


def seed_demo(runtime) -> SeedReport:
    """Create the demo dataset and credentials on a fresh runtime."""

    from marketplace.kernel.money import zero
    from marketplace.universes.u06_inventory.service import InventoryPolicy
    from marketplace.universes.u07_seller.service import SellerService  # noqa: F401

    report = SeedReport()
    system = AuthorizationContext(
        actor_id="system.seed",
        actor_type="service",
        roles=SYSTEM_ROLES,
        tenant_id=DEFAULT_TENANT,
    )
    seller_actor = AuthorizationContext(
        actor_id="seller@marketplace.local",
        actor_type="person",
        roles=SELLER_ROLES,
        tenant_id=DEFAULT_TENANT,
    )

    identity = runtime.identity
    catalog = runtime.catalog
    pricing = runtime.pricing
    inventory = runtime.inventory
    offers = runtime.offers
    sellers = runtime.sellers
    experience = runtime.experience
    ledger = runtime.ledger

    # -- 1. personas ------------------------------------------------------

    for persona in PERSONAS:
        party = identity.register_party(
            system,
            party_type=persona.party_type,
            legal_name=persona.display_name,
            tenant_id=persona.tenant_id,
            display_name=persona.display_name,
        )
        persona.party_id = party.party_id
        identity_id = identity.register_identity(system, party.party_id, persona.subject)
        persona.identity_id = identity_id
        identity.enroll_credential(identity_id, persona.password)
        token = runtime.mint_token(
            identity_id, persona.tenant_id, sorted(persona.roles)
        )
        runtime.remember_token(API_KEYS[persona.key], token)
        persona.api_key = API_KEYS[persona.key]
        report.personas.append(persona.to_public_dict())

    # -- 2. taxonomy, brand, family, attribute schema ---------------------

    electronics = catalog.define_taxonomy(system, code="electronics", label="Electronics")
    audio = catalog.define_taxonomy(
        system, code="audio", label="Audio", parent_code="electronics"
    )
    accessories = catalog.define_taxonomy(
        system, code="accessories", label="Accessories", parent_code="electronics"
    )
    report.taxonomy = {
        "electronics": electronics.node_id,
        "audio": audio.node_id,
        "accessories": accessories.node_id,
    }

    from marketplace.universes.u03_catalog.service import AttributeDefinition

    attributes = [
        AttributeDefinition("a-color", "color", "Colour", "text"),
        AttributeDefinition("a-weight", "weight_grams", "Weight (g)", "number"),
        AttributeDefinition("a-wireless", "wireless", "Wireless", "boolean"),
    ]
    # Attribute schemas are published per taxonomy node, so every node a product
    # can land in needs its own (versioned) schema.
    for taxonomy_code in ("electronics", "audio", "accessories"):
        catalog.publish_attribute_schema(system, taxonomy_code, attributes)

    brand = catalog.register_brand(system, code="ACME", label="ACME")
    report.brand = {"brand_id": brand.brand_id, "code": brand.code, "label": brand.label}
    family = catalog.register_family(system, code="audio-gear", label="Audio Gear")
    report.family = {
        "family_id": family.family_id,
        "code": family.code,
        "label": family.label,
    }

    # -- 3. seller, onboarding and contract -------------------------------

    org_party = identity.register_party(
        system,
        party_type="organization",
        legal_name="ACME Audio Inc.",
        tenant_id=DEFAULT_TENANT,
    )
    org = identity.register_organization(system, org_party.party_id, "ACME Audio Inc.")
    seller = sellers.register_seller(
        system, party_id=org_party.party_id, display_name="ACME Audio"
    )
    case = sellers.open_onboarding(system, seller.seller_id)
    evidence = sellers.submit_evidence(
        system, case.case_id, "business_registry", "registry:ACME-AUDIO"
    )
    tax_evidence = sellers.submit_evidence(
        system, case.case_id, "tax_id", "tax:US-12345"
    )
    sellers.record_verification_result(system, case.case_id, evidence.evidence_id, passed=True, note="registry verified")
    sellers.record_verification_result(system, case.case_id, tax_evidence.evidence_id, passed=True, note="tax id verified")
    sellers.complete_verification(system, case.case_id)
    contract = sellers.sign_contract(
        system,
        case.case_id,
        territories=["*"],
        channels=["web"],
        commission_rate=Decimal("0.10"),
        currency=DEFAULT_CURRENCY,
        valid_from=NOW,
        valid_until=UNTIL,
    )
    report.seller = {
        "seller_id": seller.seller_id,
        "party_id": org_party.party_id,
        "org_id": org.org_id,
        "display_name": seller.display_name,
        "case_id": case.case_id,
        "contract_id": contract.contract_id,
        "commission_rate": "0.10",
    }

    # -- 4. pricebook + promotion + coupon --------------------------------

    book = pricing.create_pricebook(
        system, code="retail", currency=DEFAULT_CURRENCY, precedence=100
    )
    report.pricebook = {
        "pricebook_id": book.pricebook_id,
        "code": book.code,
        "currency": book.currency,
        "precedence": book.precedence,
    }
    promotion = pricing.create_promotion(
        system,
        code="PCT10",
        kind="percent",
        value=Decimal("10"),
        currency=DEFAULT_CURRENCY,
        valid_from=NOW,
        valid_until=UNTIL,
        funded_by="platform",
        stacking_group="percent-off",
    )
    report.promotion = {
        "promotion_id": promotion.promotion_id,
        "code": promotion.code,
        "kind": promotion.kind,
        "value": "10",
    }
    pricing.issue_coupon(system, promotion.promotion_id, "SAVE10")
    report.coupon_code = "SAVE10"

    # -- 5. products: create -> submit -> publish, then price, listing,
    #         offer, inventory and search index --------------------------

    for index, item in enumerate(CATALOG, start=1):
        product = catalog.create_product(
            system,
            product_type=item["product_type"],
            family_code="audio-gear",
            title=item["title"],
            description=item["description"],
            taxonomy_code=item["taxonomy"],
            attributes=item["attributes"],
            brand_code="ACME",
        )
        catalog.submit_for_publication(system, product.product_id)
        catalog.publish(system, product.product_id)

        pricing.set_price(
            system,
            book.pricebook_id,
            product.product_id,
            Money.of(item["price"], DEFAULT_CURRENCY),
            valid_from=NOW,
            valid_until=UNTIL,
        )

        listing = offers.create_listing(
            seller_actor,
            seller_id=seller.seller_id,
            product_id=product.product_id,
            title=item["title"],
            taxonomy_node_id=report.taxonomy[item["taxonomy"]],
            tenant_id=DEFAULT_TENANT,
        )
        offers.submit_listing(seller_actor, listing.listing_id)
        offers.publish_listing(seller_actor, listing.listing_id)

        from marketplace.universes.u04_offer.service import OfferTerms

        offer = offers.create_offer(
            seller_actor,
            listing.listing_id,
            product.product_id,
            variant_id=None,
            condition="new",
            list_price=Money.of(item["price"], DEFAULT_CURRENCY),
            terms=OfferTerms(
                fulfillment_model="seller_fulfilled",
                lead_time_days=2,
                return_window_days=30,
            ),
            valid_from=NOW,
            valid_until=UNTIL,
        )
        offers.activate_offer(seller_actor, offer.offer_id)

        position = inventory.create_position(
            system,
            product_id=product.product_id,
            owner_id=seller.seller_id,
            location_id="wh-1",
            on_hand=item["on_hand"],
            condition="new",
            policy=InventoryPolicy(safety_stock=max(5, item["on_hand"] // 10)),
        )
        inventory.link_offer(offer.offer_id, position.position_id)

        experience.index_product(
            product.product_id, item["title"], item["attributes"], product.version
        )

        report.products.append(
            {
                "index": index,
                "product_id": product.product_id,
                "title": item["title"],
                "taxonomy": item["taxonomy"],
                "price": item["price"],
                "currency": DEFAULT_CURRENCY,
                "listing_id": listing.listing_id,
                "offer_id": offer.offer_id,
                "position_id": position.position_id,
                "on_hand": item["on_hand"],
            }
        )

    # -- 6. ledger: a balanced chart of accounts --------------------------

    for account_id, code, label, kind in (
        ("cash", "CASH", "Cash", "asset"),
        ("revenue", "REVENUE", "Sales revenue", "revenue"),
        ("seller_payable", "PAYABLE", "Seller payables", "liability"),
    ):
        ledger.open_account(account_id, code, label, kind, DEFAULT_CURRENCY)
        report.ledger_accounts.append(account_id)
    ledger.open_journal("sales", "Sales journal", DEFAULT_CURRENCY)
    report.journal_id = "sales"

    # Opening balance so the cash account is funded before any sale.
    ledger.post(
        journal_id="sales",
        description="opening-balance",
        debit_account_id="cash",
        credit_account_id="revenue",
        amount=zero(DEFAULT_CURRENCY),
        source="seed",
        correlation_id="seed-opening",
    )
    ledger.assert_all_journals_balanced()

    return report
