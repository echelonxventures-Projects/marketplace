"""U08 — Order & Transaction.

Canonical concepts owned: cart, checkout_attempt, quote, order, order_line,
cancellation, order_snapshot.

REQ-ORD-001..010. Enforces INV-01 (a confirmed order references the exact
accepted offer/terms plus a versioned price/tax/fulfillment snapshot) and the
checkout sequence of master spec §13.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional

from marketplace.kernel import AuthorizationContext, Money, Stateful, generate
from marketplace.kernel.errors import (
    DuplicateRequestError,
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.money import sum_money, zero
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U08

# ---------------------------------------------------------------------------
# State machines (§7.1)
# ---------------------------------------------------------------------------

CHECKOUT_MACHINE = register_machine(
    StateMachine(
        name="checkout.attempt",
        concept="checkout_attempt",
        initial_state="started",
        transitions=(
            Transition("started", "validating", frozenset({"buyer", "checkout.service"}), event="checkout.validating"),
            Transition("validating", "payment_pending", frozenset({"checkout.service"}), event="checkout.payment_pending"),
            Transition("validating", "failed", frozenset({"checkout.service"}), event="checkout.failed", terminal=True),
            Transition("payment_pending", "confirmed", frozenset({"checkout.service"}), event="checkout.confirmed", terminal=True),
            Transition("payment_pending", "failed", frozenset({"checkout.service"}), event="checkout.payment_failed", terminal=True),
            Transition("payment_pending", "abandoned", frozenset({"buyer", "checkout.service"}), event="checkout.abandoned", terminal=True),
        ),
        terminal_states=frozenset({"confirmed", "failed", "abandoned"}),
    )
)

ORDER_MACHINE = register_machine(
    StateMachine(
        name="order.lifecycle",
        concept="order",
        initial_state="pending",
        transitions=(
            Transition("pending", "confirmed", frozenset({"order.service"}), event="order.confirmed"),
            Transition("pending", "cancelled", frozenset({"buyer", "order.service"}), event="order.cancelled", terminal=True),
            Transition("confirmed", "processing", frozenset({"fulfillment.service"}), event="order.processing"),
            Transition("confirmed", "cancelled", frozenset({"buyer", "order.service"}), event="order.cancelled", terminal=True),
            Transition("processing", "shipped", frozenset({"fulfillment.service"}), event="order.shipped"),
            Transition("processing", "cancelled", frozenset({"order.service"}), event="order.cancelled", terminal=True),
            Transition("shipped", "delivered", frozenset({"fulfillment.service"}), event="order.delivered", terminal=True),
            Transition("shipped", "returned", frozenset({"returns.service"}), event="order.returned"),
            Transition("returned", "closed", frozenset({"returns.service"}), event="order.closed", terminal=True),
            Transition("delivered", "closed", frozenset({"order.service"}), event="order.closed", terminal=True),
            Transition("returned", "disputed", frozenset({"trust.service"}), event="order.disputed"),
            Transition("disputed", "closed", frozenset({"trust.service"}), event="order.closed", terminal=True),
        ),
        terminal_states=frozenset({"cancelled", "delivered", "closed"}),
    )
)


class CheckoutEntity(Stateful):
    machine = CHECKOUT_MACHINE

    def __init__(self, checkout_id: str) -> None:
        super().__init__(checkout_id)
        self.checkout_id = checkout_id


class OrderEntity(Stateful):
    machine = ORDER_MACHINE

    def __init__(self, order_id: str) -> None:
        super().__init__(order_id)
        self.order_id = order_id


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class CartLine:
    offer_id: str
    product_id: str
    variant_id: Optional[str]
    seller_id: str
    quantity: int
    added_at: str


@dataclass
class Cart:
    """REQ-ORD-001: mutable intent. Explicitly not a confirmed order."""

    cart_id: str
    buyer_id: str
    tenant_id: str
    lines: list[CartLine] = field(default_factory=list)
    status: str = "active"  # active | merged | converted | abandoned
    coupon_codes: list[str] = field(default_factory=list)
    version: int = 0


@dataclass
class LineSnapshot:
    """The immutable commercial terms of one line at commitment (INV-01)."""

    line_id: str
    offer_id: str
    product_id: str
    variant_id: Optional[str]
    seller_id: str
    quantity: int
    unit_price: Money
    fulfillment_model: str
    lead_time_days: int
    components: list[dict]
    line_total: Money


@dataclass
class OrderSnapshot:
    """The immutable commercial snapshot of the whole order (LAW-ARCH-05)."""

    currency: str
    lines: list[LineSnapshot]
    totals: dict[str, str]
    price_evaluation_id: str
    policy_versions: dict[str, int]
    offer_versions: dict[str, int]
    snapshot_at: str


@dataclass
class Order:
    """A durable commercial commitment (master spec §4.1: not a shipment or payment)."""

    order_id: str
    buyer_id: str
    tenant_id: str
    cart_id: Optional[str]
    status: str = "pending"
    snapshot: Optional[OrderSnapshot] = None
    seller_suborders: dict[str, list[str]] = field(default_factory=dict)
    placed_at: str = ""
    correlation_id: str = ""
    cancellation_reason: Optional[str] = None
    payment_correlation_id: Optional[str] = None
    version: int = 0


@dataclass
class Cancellation:
    cancellation_id: str
    order_id: str
    line_ids: list[str]
    reason: str
    cancelled_at: str
    actor: str


@dataclass
class Quote:
    """REQ-ORD-003: a B2B quote that may be contractually binding by policy."""

    quote_id: str
    buyer_id: str
    seller_id: str
    lines: list[dict]
    currency: str
    valid_until: str
    status: str = "open"  # open | accepted | expired | rejected
