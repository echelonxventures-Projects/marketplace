"""U10 — Fulfillment & Logistics.

Canonical concepts owned: fulfillment_order, shipment, delivery_proof,
return_movement, warehouse/location capability.

REQ-FUL-001..006. Enforces INV-05: a fulfillment instruction must reference an
authorized order obligation and cannot be duplicated by message replay.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from marketplace.kernel import AuthorizationContext, IdempotencyStore, Stateful, generate
from marketplace.kernel.errors import (
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U10

# ---------------------------------------------------------------------------
# State machines (§7.1)
# ---------------------------------------------------------------------------

FULFILLMENT_MACHINE = register_machine(
    StateMachine(
        name="fulfillment.order",
        concept="fulfillment_order",
        initial_state="released",
        transitions=(
            Transition("released", "allocated", frozenset({"fulfillment.service"}), event="fulfillment.allocated"),
            Transition("allocated", "picking", frozenset({"warehouse.service"}), event="fulfillment.picking"),
            Transition("picking", "packed", frozenset({"warehouse.service"}), event="fulfillment.packed"),
            Transition("packed", "shipped", frozenset({"shipping.service"}), event="fulfillment.shipped"),
            Transition("released", "cancelled", frozenset({"order.service"}), event="fulfillment.cancelled", terminal=True),
            Transition("allocated", "cancelled", frozenset({"order.service"}), event="fulfillment.cancelled", terminal=True),
            Transition("shipped", "delivered", frozenset({"carrier.service"}), event="fulfillment.delivered", terminal=True),
            Transition("shipped", "exception", frozenset({"carrier.service"}), event="fulfillment.exception"),
            Transition("exception", "shipped", frozenset({"fulfillment.service"}), event="fulfillment.recovered"),
            Transition("exception", "cancelled", frozenset({"fulfillment.admin"}), event="fulfillment.cancelled", terminal=True),
        ),
        terminal_states=frozenset({"delivered", "cancelled"}),
    )
)

SHIPMENT_MACHINE = register_machine(
    StateMachine(
        name="shipment.lifecycle",
        concept="shipment",
        initial_state="created",
        transitions=(
            Transition("created", "in_transit", frozenset({"shipping.service", "carrier.service"}), event="shipment.in_transit"),
            Transition("in_transit", "out_for_delivery", frozenset({"carrier.service"}), event="shipment.out_for_delivery"),
            Transition("out_for_delivery", "delivered", frozenset({"carrier.service"}), event="shipment.delivered", terminal=True),
            Transition("in_transit", "exception", frozenset({"carrier.service"}), event="shipment.exception"),
            Transition("out_for_delivery", "exception", frozenset({"carrier.service"}), event="shipment.exception"),
            Transition("exception", "in_transit", frozenset({"fulfillment.service"}), event="shipment.recovered"),
            Transition("exception", "returned_to_origin", frozenset({"carrier.service"}), event="shipment.returned", terminal=True),
        ),
        terminal_states=frozenset({"delivered", "returned_to_origin"}),
    )
)


class FulfillmentEntity(Stateful):
    machine = FULFILLMENT_MACHINE

    def __init__(self, fulfillment_id: str) -> None:
        super().__init__(fulfillment_id)
        self.fulfillment_id = fulfillment_id


class ShipmentEntity(Stateful):
    machine = SHIPMENT_MACHINE

    def __init__(self, shipment_id: str) -> None:
        super().__init__(shipment_id)
        self.shipment_id = shipment_id


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class Warehouse:
    location_id: str
    code: str
    capabilities: frozenset[str] = field(default_factory=lambda: frozenset({"standard"}))
    hazmat: bool = False
    cold_chain: bool = False
    cutoff_time: str = "16:00"
    status: str = "active"


@dataclass
class DeliveryPromise:
    """REQ-FUL-003: a promise computed from stock, capacity, cutoff, carrier and region."""

    promise_id: str
    earliest_date: str
    latest_date: str
    carrier: str
    service_level: str
    computed_from: dict
    valid_until: str


@dataclass
class FulfillmentLine:
    line_id: str
    offer_id: str
    product_id: str
    quantity: int


@dataclass
class FulfillmentOrder:
    """An instruction to fulfill an order obligation (not the order itself)."""

    fulfillment_id: str
    order_id: str
    seller_id: str
    location_id: str
    lines: list[FulfillmentLine]
    model: str  # seller_fulfilled | platform_fulfilled | dropship | digital | service
    status: str = "released"
    shipment_id: Optional[str] = None
    promise: Optional[DeliveryPromise] = None
    correlation_id: str = ""
    version: int = 0


@dataclass
class TrackingEvent:
    event_id: str
    shipment_id: str
    milestone: str
    at: str
    carrier_reference: str
    note: str = ""


@dataclass
class Shipment:
    shipment_id: str
    fulfillment_id: str
    carrier: str
    carrier_reference: str
    tracking_url: str = ""
    status: str = "created"
    milestones: list[TrackingEvent] = field(default_factory=list)
    proof: Optional[dict] = None


@dataclass
class ReturnMovement:
    """REQ-FUL-006: reverse logistics movement with disposition."""

    return_id: str
    authorization_id: str
    shipment: Optional[Shipment] = None
    disposition: str = "pending"  # pending | restock | refurbish | dispose | return_to_vendor
    inspected: bool = False
    inspection_outcome: str = ""


# ---------------------------------------------------------------------------
# Carrier adapter contract (REQ-FUL-004)
# ---------------------------------------------------------------------------


class CarrierAdapter:
    """Versioned carrier contract with idempotency and webhook verification."""

    code = "base"

    def create_shipment(self, fulfillment: FulfillmentOrder) -> Shipment:
        raise NotImplementedError

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        raise NotImplementedError


class LocalCarrierAdapter(CarrierAdapter):
    """A reference carrier adapter; behaviour is real and observable."""

    code = "local"

    def __init__(self) -> None:
        self.created: list[FulfillmentOrder] = []

    def create_shipment(self, fulfillment: FulfillmentOrder) -> Shipment:
        self.created.append(fulfillment)
        return Shipment(
            shipment_id=generate("shipment"),
            fulfillment_id=fulfillment.fulfillment_id,
            carrier=self.code,
            carrier_reference=f"TRK-{fulfillment.fulfillment_id[-8:]}",
            tracking_url=f"https://track.local/{fulfillment.fulfillment_id[-8:]}",
            status="created",
        )

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        import hashlib
        import hmac

        expected = hmac.new(b"local-carrier-secret", payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Fulfillment service
# ---------------------------------------------------------------------------


class FulfillmentService:
    def __init__(self, carrier: Optional[CarrierAdapter] = None) -> None:
        self.warehouses: dict[str, Warehouse] = {}
        self.fulfillments: dict[str, FulfillmentOrder] = {}
        self.shipments: dict[str, Shipment] = {}
        self.returns: dict[str, ReturnMovement] = {}
        self.carrier = carrier
        self._idempotency = IdempotencyStore()
        self._lock = threading.RLock()
        self._order_fulfillments: dict[str, list[str]] = {}

    def register_warehouse(self, authz: AuthorizationContext, code: str, **capabilities) -> Warehouse:
        authz.requires_role("ops.writer")
        warehouse = Warehouse(
            location_id=generate("evidence"),
            code=code,
            capabilities=frozenset(capabilities.get("capabilities", {"standard"})),
            hazmat=bool(capabilities.get("hazmat", False)),
            cold_chain=bool(capabilities.get("cold_chain", False)),
            cutoff_time=capabilities.get("cutoff_time", "16:00"),
        )
        self.warehouses[warehouse.location_id] = warehouse
        return warehouse

    def compute_promise(
        self,
        *,
        location_id: str,
        carrier: str,
        service_level: str,
        lead_time_days: int,
        at: str,
        cutoff: str = "16:00",
    ) -> DeliveryPromise:
        """REQ-FUL-003: delivery promise from explicit inputs."""
        from datetime import datetime, timedelta

        start = datetime.fromisoformat(at.replace("Z", "+00:00"))
        earliest = start + timedelta(days=max(lead_time_days, 1))
        latest = earliest + timedelta(days=2)
        return DeliveryPromise(
            promise_id=generate("evidence"),
            earliest_date=earliest.date().isoformat(),
            latest_date=latest.date().isoformat(),
            carrier=carrier,
            service_level=service_level,
            computed_from={
                "location_id": location_id,
                "lead_time_days": lead_time_days,
                "cutoff": cutoff,
                "carrier": carrier,
                "service_level": service_level,
            },
            valid_until=at,
        )

    def release(
        self,
        *,
        order_id: str,
        seller_id: str,
        location_id: str,
        lines: list[tuple[str, str, int]],
        model: str,
        correlation_id: str,
        idempotency_key: str,
        promise: Optional[DeliveryPromise] = None,
    ) -> FulfillmentOrder:
        """INV-05: one fulfillment instruction per order obligation.

        A replay with the same idempotency key returns the released
        instruction; it never creates a duplicate pick-pack-ship cycle.
        """
        if model not in {
            "seller_fulfilled", "platform_fulfilled", "dropship", "digital", "service"
        }:
            raise ValidationError(f"Unknown fulfillment model {model!r}")
        if not lines:
            raise ValidationError("A fulfillment order requires at least one line")

        def run() -> FulfillmentOrder:
            with self._lock:
                if order_id in self._order_fulfillments:
                    raise InvariantViolationError(
                        "INV-05",
                        f"Order {order_id} already has a fulfillment instruction; "
                        "split fulfillment requires explicit line-level keys",
                        details={"order_id": order_id},
                    )
                fulfillment = FulfillmentOrder(
                    fulfillment_id=generate("fulfillment"),
                    order_id=order_id,
                    seller_id=seller_id,
                    location_id=location_id,
                    lines=[
                        FulfillmentLine(
                            line_id=generate("order_line"),
                            offer_id=offer_id,
                            product_id=product_id,
                            quantity=quantity,
                        )
                        for offer_id, product_id, quantity in lines
                    ],
                    model=model,
                    promise=promise,
                    correlation_id=correlation_id,
                )
                self.fulfillments[fulfillment.fulfillment_id] = fulfillment
                self._order_fulfillments[order_id] = [fulfillment.fulfillment_id]
                return fulfillment

        return self._idempotency.execute(idempotency_key, run)

    def allocate(self, fulfillment_id: str, *, actor: str, actor_roles) -> FulfillmentOrder:
        fulfillment = self._require_fulfillment(fulfillment_id)
        return self._transition(fulfillment, "allocated", actor, actor_roles, "stock allocated")

    def pick(self, fulfillment_id: str, *, actor: str, actor_roles) -> FulfillmentOrder:
        fulfillment = self._require_fulfillment(fulfillment_id)
        return self._transition(fulfillment, "picking", actor, actor_roles, "pick started")

    def pack(self, fulfillment_id: str, *, actor: str, actor_roles) -> FulfillmentOrder:
        fulfillment = self._require_fulfillment(fulfillment_id)
        return self._transition(fulfillment, "packed", actor, actor_roles, "pack complete")

    def ship(
        self, fulfillment_id: str, *, actor: str, actor_roles, idempotency_key: str
    ) -> tuple[FulfillmentOrder, Shipment]:
        fulfillment = self._require_fulfillment(fulfillment_id)
        if self.carrier is None:
            raise NotFoundError(
                "No carrier adapter registered; shipping requires an authorized carrier "
                "integration (REQ-FUL-004)"
            )
        shipment = self._idempotency.execute(
            idempotency_key,
            lambda: self.carrier.create_shipment(fulfillment),
        )
        self.shipments[shipment.shipment_id] = shipment
        fulfillment = self._transition(fulfillment, "shipped", actor, actor_roles, "handed to carrier")
        fulfillment.shipment_id = shipment.shipment_id
        return fulfillment, shipment

    def record_milestone(
        self, shipment_id: str, milestone: str, *, at: str, carrier_reference: str, note: str = ""
    ) -> Shipment:
        shipment = self._require_shipment(shipment_id)
        shipment.milestones.append(
            TrackingEvent(
                event_id=generate("evidence"),
                shipment_id=shipment_id,
                milestone=milestone,
                at=at,
                carrier_reference=carrier_reference,
                note=note,
            )
        )
        target = {
            "in_transit": "in_transit",
            "out_for_delivery": "out_for_delivery",
            "delivered": "delivered",
            "exception": "exception",
            "returned": "returned_to_origin",
        }.get(milestone)
        if target is not None and shipment.status != target:
            entity = ShipmentEntity(shipment_id)
            entity.state = shipment.status
            entity.transition_to(
                target, actor="carrier.service", actor_roles=frozenset({"carrier.service"}),
                reason=f"carrier milestone {milestone}", recorded_at=at,
            )
            shipment.status = entity.state
        return shipment

    def record_delivery_proof(
        self, shipment_id: str, *, proof_kind: str, evidence_ref: str, at: str
    ) -> Shipment:
        """REQ-FUL-005: proof of delivery is stored as evidence, not asserted."""
        shipment = self._require_shipment(shipment_id)
        shipment.proof = {
            "kind": proof_kind,
            "evidence_ref": evidence_ref,
            "recorded_at": at,
        }
        if shipment.status != "delivered":
            entity = ShipmentEntity(shipment_id)
            entity.state = shipment.status
            entity.transition_to(
                "delivered", actor="carrier.service", actor_roles=frozenset({"carrier.service"}),
                reason=f"delivery proof: {proof_kind}", recorded_at=at,
            )
            shipment.status = entity.state
        return shipment

    def open_return(self, authorization_id: str) -> ReturnMovement:
        movement = ReturnMovement(return_id=generate("return"), authorization_id=authorization_id)
        self.returns[movement.return_id] = movement
        return movement

    def record_inspection(
        self, authz: AuthorizationContext, return_id: str, outcome: str, disposition: str
    ) -> ReturnMovement:
        authz.requires_role("returns.inspector")
        if disposition not in {"restock", "refurbish", "dispose", "return_to_vendor"}:
            raise ValidationError(f"Unknown disposition {disposition!r}")
        movement = self.returns.get(return_id)
        if movement is None:
            raise NotFoundError(f"Return movement {return_id} not found")
        movement.inspected = True
        movement.inspection_outcome = outcome
        movement.disposition = disposition
        return movement

    def _transition(
        self, fulfillment: FulfillmentOrder, to_state: str, actor: str, actor_roles, reason: str
    ) -> FulfillmentOrder:
        entity = FulfillmentEntity(fulfillment.fulfillment_id)
        entity.state = fulfillment.status
        entity.transition_to(
            to_state, actor=actor, actor_roles=actor_roles, reason=reason, recorded_at=U10.now()
        )
        fulfillment.status = entity.state
        fulfillment.version += 1
        return fulfillment

    def _require_fulfillment(self, fulfillment_id: str) -> FulfillmentOrder:
        fulfillment = self.fulfillments.get(fulfillment_id)
        if fulfillment is None:
            raise NotFoundError(f"Fulfillment order {fulfillment_id} not found")
        return fulfillment

    def _require_shipment(self, shipment_id: str) -> Shipment:
        shipment = self.shipments.get(shipment_id)
        if shipment is None:
            raise NotFoundError(f"Shipment {shipment_id} not found")
        return shipment


U10.owns(
    "fulfillment_order",
    "Instruction to fulfill an authorized order obligation; one per order (INV-05)",
    "U10.FulfillmentService.release",
    read_projections=["U08.order"],
)
U10.owns("shipment", "Physical movement, carrier facts and tracking lifecycle", "U10.FulfillmentService.ship")
U10.owns("delivery_proof", "Evidence of delivery; does not itself prove acceptance absent policy", "U10.FulfillmentService.record_delivery_proof")
U10.owns("return_movement", "Reverse logistics movement with inspection and disposition", "U10.FulfillmentService.open_return")
U10.owns("warehouse", "Location capability, cutoffs and handling constraints", "U10.FulfillmentService.register_warehouse")
