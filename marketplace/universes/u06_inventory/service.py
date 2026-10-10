"""U06 — Inventory & Supply.

Canonical concepts owned: inventory_position, reservation, availability,
replenishment_order, supplier.

REQ-SUP-001..005. Enforces INV-02: reservations cannot exceed the
policy-defined allocatable quantity and duplicate commands cannot double
allocate. Availability is a computed projection under explicit policy — never
a second write authority over stock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from marketplace.kernel import (
    AuthorizationContext,
    IdempotencyStore,
    MarketplaceError,
    Stateful,
    generate,
)
from marketplace.kernel.errors import (
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    OversellError,
    ValidationError,
)
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U06

# ---------------------------------------------------------------------------
# State machine: reservation lifecycle (§7.1 required machine)
# ---------------------------------------------------------------------------

RESERVATION_MACHINE = register_machine(
    StateMachine(
        name="inventory.reservation",
        concept="reservation",
        initial_state="requested",
        transitions=(
            Transition("requested", "reserved", frozenset({"checkout.service"}), event="reservation.confirmed"),
            Transition("requested", "released", frozenset({"checkout.service"}), event="reservation.released", terminal=True),
            Transition("reserved", "committed", frozenset({"order.service"}), event="reservation.committed", terminal=True),
            Transition("reserved", "released", frozenset({"checkout.service", "order.service"}), event="reservation.released", terminal=True),
            Transition("reserved", "expired", frozenset({"system"}), event="reservation.expired", terminal=True),
        ),
        terminal_states=frozenset({"committed", "released", "expired"}),
    )
)


class ReservationEntity(Stateful):
    machine = RESERVATION_MACHINE

    def __init__(self, reservation_id: str) -> None:
        super().__init__(reservation_id)
        self.reservation_id = reservation_id


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class InventoryPolicy:
    safety_stock: int = 0
    reservation_ttl_seconds: int = 900
    allow_oversell: bool = False
    oversell_buffer_pct: Decimal = Decimal("0")
    allocation_order: str = "fifo"  # fifo | fefo
    lot_tracking: bool = False


@dataclass
class InventoryPosition:
    """Stock by item, location, owner, condition and status (REQ-SUP-001).

    Only U06 may mutate quantity fields. Other universes consume the
    ``available`` projection.
    """

    position_id: str
    product_id: str
    variant_id: Optional[str]
    owner_id: str  # seller or platform warehouse id
    location_id: str
    condition: str = "new"
    on_hand: int = 0
    reserved: int = 0
    policy: InventoryPolicy = field(default_factory=InventoryPolicy)
    lots: list[dict] = field(default_factory=list)
    version: int = 0

    @property
    def allocatable(self) -> int:
        """Available-to-promise: on-hand minus reserved minus safety stock
        (REQ-SUP-002). Never negative."""
        return max(0, self.on_hand - self.reserved - self.policy.safety_stock)


@dataclass
class Reservation:
    reservation_id: str
    position_id: str
    offer_id: str
    quantity: int
    state: str = "requested"
    created_at: str = ""
    expires_at: str = ""
    correlation_id: str = ""


@dataclass
class FeedUpdate:
    """REQ-SUP-004: an inventory feed update with source precedence."""

    update_id: str
    position_id: str
    source: str
    source_precedence: int
    observed_on_hand: int
    received_at: str
    applied: bool = False
    discrepancy: Optional[str] = None


@dataclass
class ReplenishmentOrder:
    """REQ-SUP-005: replenishment / purchase planning."""

    order_id: str
    position_id: str
    supplier_id: str
    quantity: int
    lead_time_days: int
    status: str = "planned"


# ---------------------------------------------------------------------------
# Inventory service
# ---------------------------------------------------------------------------


class InventoryService:
    def __init__(self) -> None:
        self.positions: dict[str, InventoryPosition] = {}
        self.reservations: dict[str, Reservation] = {}
        self.feed_updates: list[FeedUpdate] = []
        self.replenishments: dict[str, ReplenishmentOrder] = {}
        self._lock = threading.RLock()
        self._idempotency = IdempotencyStore()
        self.position_by_offer: dict[str, str] = {}

    # -- positions --------------------------------------------------------

    def create_position(
        self,
        authz: AuthorizationContext,
        product_id: str,
        owner_id: str,
        location_id: str,
        *,
        variant_id: Optional[str] = None,
        on_hand: int = 0,
        condition: str = "new",
        policy: Optional[InventoryPolicy] = None,
    ) -> InventoryPosition:
        authz.requires_role("inventory.writer")
        if on_hand < 0:
            raise ValidationError("On-hand quantity cannot be negative")
        position = InventoryPosition(
            position_id=generate("inventory"),
            product_id=product_id,
            variant_id=variant_id,
            owner_id=owner_id,
            location_id=location_id,
            condition=condition,
            on_hand=on_hand,
            policy=policy or InventoryPolicy(),
        )
        self.positions[position.position_id] = position
        return position

    def link_offer(self, offer_id: str, position_id: str) -> None:
        position = self._require_position(position_id)
        self.position_by_offer[offer_id] = position.position_id

    def position_for_offer(self, offer_id: str) -> Optional[InventoryPosition]:
        position_id = self.position_by_offer.get(offer_id)
        if position_id is None:
            return None
        return self.positions.get(position_id)

    # -- availability projection (LAW-ARCH-06: derived, never authoritative)

    def availability(self, product_id: str) -> dict:
        """Computed availability across all positions for a product."""
        total_on_hand = 0
        total_allocatable = 0
        positions = []
        for position in self.positions.values():
            if position.product_id != product_id:
                continue
            total_on_hand += position.on_hand
            total_allocatable += position.allocatable
            positions.append(
                {
                    "position_id": position.position_id,
                    "location_id": position.location_id,
                    "on_hand": position.on_hand,
                    "allocatable": position.allocatable,
                    "source_version": position.version,
                }
            )
        return {
            "product_id": product_id,
            "on_hand": total_on_hand,
            "available_to_promise": total_allocatable,
            "positions": positions,
            "freshness_bound": U06.now(),
        }

    # -- reservations -----------------------------------------------------

    def reserve(
        self,
        *,
        offer_id: str,
        quantity: int,
        correlation_id: str,
        idempotency_key: str,
        ttl_seconds: Optional[int] = None,
    ) -> Reservation:
        """Reserve stock with INV-02 enforcement and duplicate protection.

        The whole operation runs under the position lock, so concurrent
        reservations cannot oversell. Repeated commands with the same
        idempotency key return the recorded reservation rather than allocating
        again.
        """
        if quantity <= 0:
            raise ValidationError("Reservation quantity must be positive")
        position = self.position_for_offer(offer_id)
        if position is None:
            raise NotFoundError(f"No inventory position linked to offer {offer_id}")
        return self.idempotency_execute(
            idempotency_key,
            lambda: self._reserve_locked(position, offer_id, quantity, correlation_id, ttl_seconds),
        )

    def idempotency_execute(self, key: str, operation):
        return self._idempotency.execute(key, operation)

    def _reserve_locked(
        self,
        position: InventoryPosition,
        offer_id: str,
        quantity: int,
        correlation_id: str,
        ttl_seconds: Optional[int],
    ) -> Reservation:
        with self._lock:
            allocatable = position.allocatable
            if quantity > allocatable:
                if not position.policy.allow_oversell:
                    raise OversellError(
                        f"Cannot reserve {quantity} of {position.product_id} at "
                        f"{position.location_id}; allocatable is {allocatable} "
                        f"(on_hand={position.on_hand}, reserved={position.reserved}, "
                        f"safety_stock={position.policy.safety_stock}). Controlled "
                        "oversell requires an explicit policy (REQ-SUP-003).",
                        details={
                            "position_id": position.position_id,
                            "requested": quantity,
                            "allocatable": allocatable,
                        },
                    )
                buffer = int(
                    position.on_hand * position.policy.oversell_buffer_pct
                )
                if quantity > allocatable + buffer:
                    raise OversellError(
                        "Controlled-oversell buffer exceeded",
                        details={
                            "position_id": position.position_id,
                            "requested": quantity,
                            "allocatable": allocatable,
                            "buffer": buffer,
                        },
                    )
            now = U06.now()
            ttl = ttl_seconds if ttl_seconds is not None else position.policy.reservation_ttl_seconds
            reservation = Reservation(
                reservation_id=generate("reservation"),
                position_id=position.position_id,
                offer_id=offer_id,
                quantity=quantity,
                state="reserved",
                created_at=now,
                correlation_id=correlation_id,
            )
            entity = ReservationEntity(reservation.reservation_id)
            entity.transition_to(
                "reserved", actor="checkout.service", actor_roles=frozenset({"checkout.service"}),
                reason=f"reserve {quantity} for {correlation_id}", recorded_at=now,
            )
            position.reserved += quantity
            position.version += 1
            self.reservations[reservation.reservation_id] = reservation
            return reservation

    def commit(self, reservation_id: str, *, correlation_id: str) -> Reservation:
        """Convert a reservation into consumed stock at order confirmation."""
        with self._lock:
            reservation = self._require_reservation(reservation_id)
            entity = ReservationEntity(reservation_id)
            entity.state = reservation.state
            entity.transition_to(
                "committed", actor="order.service", actor_roles=frozenset({"order.service"}),
                reason=f"order {correlation_id} confirmed", recorded_at=U06.now(),
            )
            position = self._require_position(reservation.position_id)
            if reservation.quantity > position.on_hand:
                raise OversellError(
                    f"Commit of {reservation.quantity} exceeds on-hand {position.on_hand}",
                    details={"position_id": position.position_id},
                )
            position.on_hand -= reservation.quantity
            position.reserved = max(0, position.reserved - reservation.quantity)
            position.version += 1
            reservation.state = "committed"
            return reservation

    def release(self, reservation_id: str, *, reason: str) -> Reservation:
        with self._lock:
            reservation = self._require_reservation(reservation_id)
            entity = ReservationEntity(reservation_id)
            entity.state = reservation.state
            entity.transition_to(
                "released", actor="checkout.service", actor_roles=frozenset({"checkout.service"}),
                reason=reason, recorded_at=U06.now(),
            )
            position = self._require_position(reservation.position_id)
            position.reserved = max(0, position.reserved - reservation.quantity)
            position.version += 1
            reservation.state = "released"
            return reservation

    def expire_stale_reservations(self, at: str) -> list[Reservation]:
        expired: list[Reservation] = []
        for reservation in list(self.reservations.values()):
            if reservation.state != "reserved":
                continue
            if reservation.expires_at and reservation.expires_at < at:
                self.release(reservation.reservation_id, reason="ttl elapsed")
                reservation.state = "expired"
                expired.append(reservation)
        return expired

    # -- feeds (REQ-SUP-004) -------------------------------------------------

    def apply_feed_update(
        self,
        authz: AuthorizationContext,
        position_id: str,
        source: str,
        source_precedence: int,
        observed_on_hand: int,
    ) -> FeedUpdate:
        authz.requires_role("inventory.feed")
        with self._lock:
            position = self._require_position(position_id)
            last_update = next(
                (u for u in reversed(self.feed_updates) if u.position_id == position_id), None
            )
            discrepancy = None
            if last_update is not None and last_update.source_precedence < source_precedence:
                if observed_on_hand != position.on_hand:
                    discrepancy = (
                        f"Lower-precedence source {source!r} disagrees with current "
                        f"on_hand {position.on_hand}; flagged for adjudication"
                    )
            elif last_update is None or source_precedence >= last_update.source_precedence:
                delta = observed_on_hand - position.on_hand
                position.on_hand = observed_on_hand
                position.version += 1
                discrepancy = f"applied delta {delta:+d}" if delta else None
            else:
                discrepancy = "stale feed update; not applied"
            update = FeedUpdate(
                update_id=generate("evidence"),
                position_id=position_id,
                source=source,
                source_precedence=source_precedence,
                observed_on_hand=observed_on_hand,
                received_at=U06.now(),
                applied=_update_applied(discrepancy),
                discrepancy=discrepancy,
            )
            self.feed_updates.append(update)
            return update

    # -- replenishment ------------------------------------------------------

    def plan_replenishment(
        self, authz: AuthorizationContext, position_id: str, supplier_id: str, quantity: int, lead_time_days: int
    ) -> ReplenishmentOrder:
        authz.requires_role("inventory.planner")
        if quantity <= 0:
            raise ValidationError("Replenishment quantity must be positive")
        order = ReplenishmentOrder(
            order_id=generate("inventory"),
            position_id=position_id,
            supplier_id=supplier_id,
            quantity=quantity,
            lead_time_days=lead_time_days,
        )
        self.replenishments[order.order_id] = order
        return order

    # -- invariants ---------------------------------------------------------

    def assert_no_negative_stock(self) -> None:
        broken = [
            p.position_id
            for p in self.positions.values()
            if p.on_hand < 0 or p.reserved < 0 or p.reserved > p.on_hand
        ]
        if broken:
            raise InvariantViolationError(
                "INV-02",
                "Inventory integrity violated: negative stock or over-reservation",
                details={"positions": broken},
            )

    def _require_position(self, position_id: str) -> InventoryPosition:
        position = self.positions.get(position_id)
        if position is None:
            raise NotFoundError(f"Inventory position {position_id} not found")
        return position

    def _require_reservation(self, reservation_id: str) -> Reservation:
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            raise NotFoundError(f"Reservation {reservation_id} not found")
        return reservation


def _update_applied(discrepancy: Optional[str]) -> bool:
    if discrepancy is None:
        return True
    return discrepancy.startswith("applied")


# Concept ownership declarations (GOV-02).
U06.owns("inventory_position", "Quantity and allocation by item, location, owner, condition and time", "U06.InventoryService.create_position", read_projections=["U04.offer", "U11.availability_projection"])
U06.owns("reservation", "Temporary or committed allocation of inventory or capacity", "U06.InventoryService.reserve")
U06.owns("availability_projection", "Computed available-to-promise under explicit policy; rebuildable", "U06.InventoryService.availability")
U06.owns("replenishment_order", "Replenishment plan with supplier and lead time", "U06.InventoryService.plan_replenishment")
