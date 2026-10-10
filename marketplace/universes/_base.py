"""Shared base for all universes.

Each universe is a bounded context that owns canonical concepts (GOV-02).
This module gives universes a uniform way to declare ownership, register
capabilities and expose their public service surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from marketplace.kernel import (
    AuditChain,
    AuthorizationContext,
    Clock,
    ConceptRecord,
    EventBus,
    IdempotencyStore,
    InMemoryStore,
    ProvenanceRecord,
    registry,
)


@dataclass
class Universe:
    """A bounded context owning a set of canonical concepts."""

    universe_id: str
    name: str
    description: str
    store: InMemoryStore = field(default_factory=InMemoryStore)
    audit: AuditChain = field(default_factory=AuditChain)
    clock: Clock = field(default_factory=Clock)
    bus: EventBus = field(default_factory=EventBus)
    idempotency: IdempotencyStore = field(default_factory=IdempotencyStore)
    concepts: list[str] = field(default_factory=list)

    def owns(self, concept: str, description: str, write_path: str, *, read_projections: Iterable[str] = ()) -> ConceptRecord:
        self.concepts.append(concept)
        record = ConceptRecord(
            concept=concept,
            owner_universe=self.universe_id,
            description=description,
            identifier_prefix=concept.split("_")[0],
            write_path=write_path,
            read_projections=list(read_projections),
            source=f"{self.universe_id} {self.name}",
        )
        return registry.CONCEPTS.register(record)

    def now(self) -> str:
        return self.clock()

    def provenance(
        self,
        authz: AuthorizationContext,
        action: str,
        target_type: str,
        target_id: str,
        reason: str,
        source: str,
        correlation_id: str | None = None,
        version_before: int | None = None,
        version_after: int | None = None,
    ) -> ProvenanceRecord:
        entry = self.audit.record(
            actor=authz.actor_id,
            authority=authz.actor_type,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            source=source,
            recorded_at=self.now(),
            correlation_id=correlation_id,
            version_before=version_before,
            version_after=version_after,
        )
        return ProvenanceRecord(
            record_id=entry["digest"],
            actor=entry["actor"],
            authority=entry["authority"],
            action=entry["action"],
            target_type=entry["target_type"],
            target_id=entry["target_id"],
            reason=entry["reason"],
            source=entry["source"],
            recorded_at=entry["recorded_at"],
            version_before=version_before,
            version_after=version_after,
            correlation_id=correlation_id,
            signature=entry["digest"],
        )


# Canonical universe declarations (registered once, imported everywhere)
U01 = Universe("U01", "Identity & Access", "Party, identity, organization, authentication, authorization, consent, delegation")
U02 = Universe("U02", "Universal Commerce", "Business models, trade relationships, channels, commercial transaction semantics")
U03 = Universe("U03", "Product & Service Knowledge", "Product/service master, variants, brands, attributes, taxonomy, media")
U04 = Universe("U04", "Offer & Merchandising", "Listing, seller offer, assortment, bundle, campaign, merchandising placement")
U05 = Universe("U05", "Pricing & Taxation", "Price books, price evaluation, discount, tax, duty, fee, pricing policy")
U06 = Universe("U06", "Inventory & Supply", "Stock, reservation, availability, replenishment, procurement supply")
U07 = Universe("U07", "Marketplace & Seller", "Seller profile, onboarding case, seller contract, commission, seller performance")
U08 = Universe("U08", "Order & Transaction", "Cart, checkout attempt, quote acceptance, order, cancellation, transaction state")
U09 = Universe("U09", "Financial Commerce", "Payment operation, ledger, fee allocation, refund, payable, settlement, reconciliation")
U10 = Universe("U10", "Fulfillment & Logistics", "Fulfillment instruction, warehouse execution, shipment, carrier, delivery proof")
U11 = Universe("U11", "Customer Experience", "Search experience, preferences, wishlist, loyalty, review, communication, support")
U12 = Universe("U12", "Intelligence & AI", "Feature definitions, model/agent versions, recommendations, forecasts, decision records")
U13 = Universe("U13", "Trust & Safety", "Risk case, moderation decision, authenticity evidence, dispute case, enforcement")
U14 = Universe("U14", "Governance & Compliance", "Policy, consent rule, retention, regulatory mapping, audit and certification evidence")
U15 = Universe("U15", "Platform & Operations", "API/event contracts, workflow definitions, adapters, SLOs, runbooks, deployment records")

ALL_UNIVERSES: dict[str, Universe] = {
    u.universe_id: u for u in (
        U01, U02, U03, U04, U05, U06, U07, U08, U09, U10, U11, U12, U13, U14, U15
    )
}


def universe(universe_id: str) -> Universe:
    from marketplace.kernel.errors import NotFoundError

    u = ALL_UNIVERSES.get(universe_id)
    if u is None:
        raise NotFoundError(f"Unknown universe {universe_id!r}")
    return u
