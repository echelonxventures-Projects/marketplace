"""U04 — Offer & Merchandising.

Canonical concepts owned: listing, offer, assortment, bundle, campaign.

REQ-MER-001, REQ-MER-002. An Offer is a seller's commercial proposal and is
deliberately distinct from Product (master), Listing (presentation) and Price
(evaluated result) — master spec §4.1, GAP-0001 closure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from marketplace.kernel import (
    AuthorizationContext,
    Money,
    Stateful,
    generate,
)
from marketplace.kernel.errors import (
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U04

# ---------------------------------------------------------------------------
# State machines
# ---------------------------------------------------------------------------

LISTING_MACHINE = register_machine(
    StateMachine(
        name="listing.lifecycle",
        concept="listing",
        initial_state="draft",
        transitions=(
            Transition("draft", "pending", frozenset({"seller", "merch.writer"}), event="listing.submitted"),
            Transition("pending", "live", frozenset({"merch.approver"}), event="listing.published"),
            Transition("pending", "rejected", frozenset({"merch.approver"}), event="listing.rejected"),
            Transition("rejected", "draft", frozenset({"seller", "merch.writer"}), event="listing.resubmitted"),
            Transition("live", "unpublished", frozenset({"seller", "merch.admin"}), event="listing.unpublished"),
            Transition("unpublished", "live", frozenset({"merch.approver"}), event="listing.republished"),
            Transition("live", "blocked", frozenset({"trust.admin"}), event="listing.blocked"),
            Transition("blocked", "live", frozenset({"trust.admin"}), event="listing.unblocked"),
            Transition("unpublished", "archived", frozenset({"merch.admin"}), event="listing.archived", terminal=True),
        ),
        terminal_states=frozenset({"archived"}),
    )
)

OFFER_MACHINE = register_machine(
    StateMachine(
        name="offer.validity",
        concept="offer",
        initial_state="proposed",
        transitions=(
            Transition("proposed", "active", frozenset({"seller", "merch.approver"}), event="offer.activated"),
            Transition("active", "suspended", frozenset({"seller", "merch.admin"}), event="offer.suspended"),
            Transition("suspended", "active", frozenset({"merch.approver"}), event="offer.resumed"),
            Transition("active", "expired", frozenset({"system"}), event="offer.expired", terminal=True),
            Transition("suspended", "expired", frozenset({"system"}), terminal=True),
            Transition("active", "withdrawn", frozenset({"seller"}), event="offer.withdrawn", terminal=True),
            Transition("proposed", "rejected", frozenset({"merch.approver"}), terminal=True),
        ),
        terminal_states=frozenset({"expired", "withdrawn", "rejected"}),
    )
)


class ListingEntity(Stateful):
    machine = LISTING_MACHINE

    def __init__(self, listing_id: str) -> None:
        super().__init__(listing_id)
        self.listing_id = listing_id


class OfferEntity(Stateful):
    machine = OFFER_MACHINE

    def __init__(self, offer_id: str) -> None:
        super().__init__(offer_id)
        self.offer_id = offer_id


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class Listing:
    """Marketplace presentation of a product or service (not the product master)."""

    listing_id: str
    seller_id: str
    product_id: str
    title: str
    taxonomy_node_id: str
    tenant_id: str = ""
    status: str = "draft"
    version: int = 0
    locale: str = "en"
    content_overlays: dict[str, str] = field(default_factory=dict)


@dataclass
class OfferTerms:
    """The seller's commercial conditions attached to an offer."""

    fulfillment_model: str  # seller_fulfilled | platform_fulfilled | dropship | digital | service
    lead_time_days: int = 0
    return_window_days: int = 30
    minimum_order_quantity: int = 1
    maximum_order_quantity: Optional[int] = None
    allowed_buyer_classes: frozenset[str] = field(default_factory=lambda: frozenset({"consumer"}))
    territories: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))
    channels: frozenset[str] = field(default_factory=lambda: frozenset({"web"}))


@dataclass
class Offer:
    """A commercial proposal made by a seller under specified conditions.

    REQ-CAT-002: distinct from Product, Listing, Inventory and evaluated Price.
    """

    offer_id: str
    listing_id: str
    seller_id: str
    product_id: str
    variant_id: Optional[str]
    condition: str  # new | refurbished | used | open_box
    list_price: Money
    currency: str
    terms: OfferTerms
    valid_from: str
    valid_until: str
    status: str = "proposed"
    inventory_position_id: Optional[str] = None
    version: int = 0


@dataclass
class Assortment:
    assortment_id: str
    seller_id: str
    name: str
    offer_ids: list[str] = field(default_factory=list)


@dataclass
class Bundle:
    """REQ-MER-002: a bundle of offers with explicit component attribution."""

    bundle_id: str
    seller_id: str
    name: str
    components: list[tuple[str, int]] = field(default_factory=list)  # (offer_id, quantity)

    def total_list_price(self, offers: dict[str, Offer]) -> Money:
        if not self.components:
            raise ValidationError("Bundle has no components")
        total = None
        for offer_id, quantity in self.components:
            offer = offers[offer_id]
            line = offer.list_price * quantity
            total = line if total is None else total + line
        return total  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Offer service
# ---------------------------------------------------------------------------


class OfferService:
    def __init__(self) -> None:
        self.listings: dict[str, Listing] = {}
        self.offers: dict[str, Offer] = {}
        self.assortments: dict[str, Assortment] = {}
        self.bundles: dict[str, Bundle] = {}

    # -- listing ----------------------------------------------------------

    def create_listing(
        self,
        authz: AuthorizationContext,
        seller_id: str,
        product_id: str,
        title: str,
        taxonomy_node_id: str,
        *,
        tenant_id: Optional[str] = None,
        overlays: Optional[dict[str, str]] = None,
    ) -> Listing:
        # The seller's tenant is authoritative for isolation (REQ-IAM-008).
        # OfferService cannot see U07 seller profiles, so the caller states the
        # seller's tenant and the actor is checked against it.
        authz.requires_role("seller").same_tenant(tenant_id if tenant_id is not None else authz.tenant_id)
        listing = Listing(
            listing_id=generate("listing"),
            seller_id=seller_id,
            product_id=product_id,
            title=title,
            taxonomy_node_id=taxonomy_node_id,
            tenant_id=authz.tenant_id,
            content_overlays=dict(overlays or {}),
        )
        self.listings[listing.listing_id] = listing
        U04.provenance(
            authz, "create_listing", "listing", listing.listing_id,
            reason=f"listing for product {product_id}", source="U04.OfferService",
        )
        return listing

    def submit_listing(self, authz: AuthorizationContext, listing_id: str) -> Listing:
        listing = self._require_listing(listing_id)
        entity = ListingEntity(listing_id)
        entity.state = listing.status
        entity.transition_to(
            "pending", actor=authz.actor_id, actor_roles=authz.roles,
            reason="seller submission", recorded_at=U04.now(),
        )
        listing.status = entity.state
        listing.version += 1
        return listing

    def publish_listing(self, authz: AuthorizationContext, listing_id: str) -> Listing:
        authz.requires_role("merch.approver")
        listing = self._require_listing(listing_id)
        entity = ListingEntity(listing_id)
        entity.state = listing.status
        entity.transition_to(
            "live", actor=authz.actor_id, actor_roles=authz.roles,
            reason="marketplace approval", recorded_at=U04.now(),
        )
        listing.status = entity.state
        listing.version += 1
        return listing

    def unpublish_listing(self, authz: AuthorizationContext, listing_id: str) -> Listing:
        listing = self._require_listing(listing_id)
        entity = ListingEntity(listing_id)
        entity.state = listing.status
        entity.transition_to(
            "unpublished", actor=authz.actor_id, actor_roles=authz.roles,
            reason="seller or operator withdrawal", recorded_at=U04.now(),
        )
        listing.status = entity.state
        return listing

    # -- offer ------------------------------------------------------------

    def create_offer(
        self,
        authz: AuthorizationContext,
        listing_id: str,
        product_id: str,
        *,
        variant_id: Optional[str],
        condition: str,
        list_price: Money,
        terms: OfferTerms,
        valid_from: str,
        valid_until: str,
        inventory_position_id: Optional[str] = None,
    ) -> Offer:
        authz.requires_role("seller")
        listing = self._require_listing(listing_id)
        if listing.product_id != product_id:
            raise ValidationError(
                "An offer must reference the product of its listing (REQ-CAT-002 boundary)"
            )
        if condition not in {"new", "refurbished", "used", "open_box"}:
            raise ValidationError(f"Unknown offer condition {condition!r}")
        if valid_until <= valid_from:
            raise ValidationError("Offer validity period is empty")
        if list_price.is_negative():
            raise ValidationError("Offer list price cannot be negative")
        offer = Offer(
            offer_id=generate("offer"),
            listing_id=listing_id,
            seller_id=listing.seller_id,
            product_id=product_id,
            variant_id=variant_id,
            condition=condition,
            list_price=list_price,
            currency=list_price.currency.code,
            terms=terms,
            valid_from=valid_from,
            valid_until=valid_until,
            inventory_position_id=inventory_position_id,
        )
        self.offers[offer.offer_id] = offer
        U04.provenance(
            authz, "create_offer", "offer", offer.offer_id,
            reason=f"offer on listing {listing_id}", source="U04.OfferService",
        )
        return offer

    def activate_offer(self, authz: AuthorizationContext, offer_id: str) -> Offer:
        offer = self._require_offer(offer_id)
        entity = OfferEntity(offer_id)
        entity.state = offer.status
        entity.transition_to(
            "active", actor=authz.actor_id, actor_roles=authz.roles,
            reason="offer activation", recorded_at=U04.now(),
        )
        offer.status = entity.state
        offer.version += 1
        return offer

    def suspend_offer(self, authz: AuthorizationContext, offer_id: str, reason: str) -> Offer:
        offer = self._require_offer(offer_id)
        entity = OfferEntity(offer_id)
        entity.state = offer.status
        entity.transition_to(
            "suspended", actor=authz.actor_id, actor_roles=authz.roles,
            reason=reason, recorded_at=U04.now(),
        )
        offer.status = entity.state
        return offer

    def withdraw_offer(self, authz: AuthorizationContext, offer_id: str) -> Offer:
        offer = self._require_offer(offer_id)
        entity = OfferEntity(offer_id)
        entity.state = offer.status
        entity.transition_to(
            "withdrawn", actor=authz.actor_id, actor_roles=authz.roles,
            reason="seller withdrawal", recorded_at=U04.now(),
        )
        offer.status = entity.state
        return offer

    def expire_stale_offers(self, at: str) -> list[Offer]:
        expired: list[Offer] = []
        for offer in self.offers.values():
            if offer.status == "active" and offer.valid_until < at:
                entity = OfferEntity(offer.offer_id)
                entity.state = offer.status
                entity.transition_to(
                    "expired", actor="system", actor_roles=frozenset({"system"}),
                    reason="validity period elapsed", recorded_at=at,
                )
                offer.status = entity.state
                expired.append(offer)
        return expired

    def eligible_offers(
        self,
        product_id: str,
        *,
        at: str,
        buyer_class: str = "consumer",
        territory: str = "*",
        channel: str = "web",
    ) -> list[Offer]:
        """Active offers for a product that the buyer is allowed to purchase."""
        return [
            offer
            for offer in self.offers.values()
            if offer.product_id == product_id
            and offer.status == "active"
            and offer.valid_from <= at <= offer.valid_until
            and buyer_class in offer.terms.allowed_buyer_classes
            and (territory in offer.terms.territories or "*" in offer.terms.territories)
            and channel in offer.terms.channels
        ]

    def validate_offer(self, offer: Offer, *, at: str, quantity: int) -> Offer:
        """ Preconditions used at checkout (REQ-ORD-002). """
        if offer.status != "active":
            raise IllegalTransitionError(
                offer.offer_id, offer.status, "purchased", OfferEntity.machine.legal_targets(offer.status)
            )
        if not (offer.valid_from <= at <= offer.valid_until):
            raise ValidationError(
                f"Offer {offer.offer_id} is not valid at {at}; "
                f"validity is [{offer.valid_from}, {offer.valid_until}]"
            )
        if quantity < offer.terms.minimum_order_quantity:
            raise ValidationError(
                f"Quantity {quantity} below offer minimum "
                f"{offer.terms.minimum_order_quantity}"
            )
        if offer.terms.maximum_order_quantity is not None and quantity > offer.terms.maximum_order_quantity:
            raise ValidationError(
                f"Quantity {quantity} exceeds offer maximum "
                f"{offer.terms.maximum_order_quantity}"
            )
        return offer

    def create_bundle(
        self,
        authz: AuthorizationContext,
        seller_id: str,
        name: str,
        components: list[tuple[str, int]],
    ) -> Bundle:
        authz.requires_role("seller")
        if not components:
            raise ValidationError("A bundle requires at least one component")
        seen: set[str] = set()
        for offer_id, quantity in components:
            if quantity <= 0:
                raise ValidationError("Bundle component quantities must be positive")
            if offer_id in seen:
                raise ValidationError(f"Bundle lists offer {offer_id} twice; use quantity instead")
            seen.add(offer_id)
            offer = self._require_offer(offer_id)
            if offer.seller_id != seller_id:
                raise InvariantViolationError(
                    "GOV-05",
                    "Bundle components must belong to the same seller; "
                    "cross-seller bundles require a governed campaign",
                )
        bundle = Bundle(
            bundle_id=generate("offer"), seller_id=seller_id, name=name, components=components
        )
        self.bundles[bundle.bundle_id] = bundle
        return bundle

    def add_assortment(
        self,
        authz: AuthorizationContext,
        seller_id: str,
        name: str,
        offer_ids: list[str],
    ) -> Assortment:
        """REQ-MER-001: a seller-defined grouping of that seller's own offers."""
        authz.requires_role("seller")
        if not offer_ids:
            raise ValidationError("An assortment requires at least one offer")
        seen: set[str] = set()
        for offer_id in offer_ids:
            if offer_id in seen:
                raise ValidationError(f"Assortment lists offer {offer_id} twice")
            seen.add(offer_id)
            offer = self._require_offer(offer_id)
            if offer.seller_id != seller_id:
                raise InvariantViolationError(
                    "GOV-05",
                    "Assortment offers must belong to the same seller; "
                    "cross-seller groupings require a governed campaign",
                )
        assortment = Assortment(
            assortment_id=generate("offer"), seller_id=seller_id, name=name, offer_ids=list(offer_ids)
        )
        self.assortments[assortment.assortment_id] = assortment
        U04.provenance(
            authz, "add_assortment", "assortment", assortment.assortment_id,
            reason=f"assortment of {len(offer_ids)} offers", source="U04.OfferService",
        )
        return assortment

    def _require_listing(self, listing_id: str) -> Listing:
        listing = self.listings.get(listing_id)
        if listing is None:
            raise NotFoundError(f"Listing {listing_id} not found")
        return listing

    def _require_offer(self, offer_id: str) -> Offer:
        offer = self.offers.get(offer_id)
        if offer is None:
            raise NotFoundError(f"Offer {offer_id} not found")
        return offer


# Concept ownership declarations (GOV-02).
U04.owns("listing", "Marketplace presentation of a product; references product identity", "U04.OfferService.create_listing", read_projections=["U11.search_projection"])
U04.owns("offer", "Seller proposal: condition, eligibility and fulfillment terms; distinct from product and evaluated price", "U04.OfferService.create_offer", read_projections=["U05.price_evaluation", "U08.cart"])
U04.owns("assortment", "Seller-defined grouping of offers", "U04.OfferService.add_assortment")
U04.owns("bundle", "Composition of offers with explicit component attribution", "U04.OfferService.create_bundle")
