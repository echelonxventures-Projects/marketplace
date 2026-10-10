"""U08 — Order & Transaction service."""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Iterable, Optional

from marketplace.kernel import AuthorizationContext, IdempotencyStore, Money, generate
from marketplace.kernel.errors import (
    DuplicateRequestError,
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.money import sum_money, zero
from marketplace.universes._base import U08
from marketplace.universes.u08_order.entities import (
    Cart,
    CartLine,
    Cancellation,
    CheckoutEntity,
    LineSnapshot,
    Order,
    OrderEntity,
    OrderSnapshot,
    Quote,
)


class OrderService:
    def __init__(self) -> None:
        self.carts: dict[str, Cart] = {}
        self.orders: dict[str, Order] = {}
        self.cancellations: list[Cancellation] = []
        self.quotes: dict[str, Quote] = {}
        self._idempotency = IdempotencyStore()
        self._lock = threading.RLock()

    # -- cart (REQ-ORD-001) ----------------------------------------------

    def open_cart(self, buyer_id: str, tenant_id: str) -> Cart:
        cart = Cart(cart_id=generate("cart"), buyer_id=buyer_id, tenant_id=tenant_id)
        self.carts[cart.cart_id] = cart
        return cart

    def add_to_cart(
        self, cart_id: str, offer_id: str, product_id: str, seller_id: str, quantity: int,
        *, variant_id: Optional[str] = None,
    ) -> Cart:
        if quantity <= 0:
            raise ValidationError("Cart quantity must be positive")
        cart = self._require_cart(cart_id)
        line = next((l for l in cart.lines if l.offer_id == offer_id), None)
        if line is None:
            cart.lines.append(
                CartLine(offer_id, product_id, variant_id, seller_id, quantity, U08.now())
            )
        else:
            line.quantity += quantity
        cart.version += 1
        return cart

    def merge_carts(self, guest_cart_id: str, account_cart_id: str) -> Cart:
        """Guest cart merged into the account cart on login."""
        guest = self._require_cart(guest_cart_id)
        account = self._require_cart(account_cart_id)
        for line in guest.lines:
            existing = next((l for l in account.lines if l.offer_id == line.offer_id), None)
            if existing is None:
                account.lines.append(line)
            else:
                existing.quantity += line.quantity
        guest.status = "merged"
        account.version += 1
        return account

    def apply_coupons(self, cart_id: str, coupon_codes: Iterable[str]) -> Cart:
        cart = self._require_cart(cart_id)
        cart.coupon_codes = list(coupon_codes)
        cart.version += 1
        return cart

    # -- order (REQ-ORD-004, REQ-ORD-005) ---------------------------------

    def place_order(
        self,
        *,
        order_id: Optional[str],
        buyer_id: str,
        tenant_id: str,
        cart_id: str,
        snapshot: OrderSnapshot,
        correlation_id: str,
        idempotency_key: str,
        payment_correlation_id: Optional[str] = None,
    ) -> Order:
        """Create a durable, idempotent order commitment with an immutable snapshot."""

        def create() -> Order:
            with self._lock:
                order = Order(
                    order_id=order_id or generate("order", tenant_id[:4]),
                    buyer_id=buyer_id,
                    tenant_id=tenant_id,
                    cart_id=cart_id,
                    snapshot=snapshot,
                    placed_at=U08.now(),
                    correlation_id=correlation_id,
                    payment_correlation_id=payment_correlation_id,
                )
                if order.order_id in self.orders:
                    raise DuplicateRequestError(idempotency_key, "order already exists")
                entity = OrderEntity(order.order_id)
                entity.transition_to(
                    "confirmed", actor="order.service", actor_roles=frozenset({"order.service"}),
                    reason="checkout confirmed with payment authorization", recorded_at=U08.now(),
                )
                order.status = entity.state
                for line in snapshot.lines:
                    order.seller_suborders.setdefault(line.seller_id, []).append(line.line_id)
                self.orders[order.order_id] = order
                cart = self.carts.get(cart_id)
                if cart is not None:
                    cart.status = "converted"
                return order

        return self._idempotency.execute(idempotency_key, create)

    def transition_order(
        self, order_id: str, to_state: str, *, actor: str, actor_roles: Iterable[str], reason: str
    ) -> Order:
        with self._lock:
            order = self._require_order(order_id)
            entity = OrderEntity(order_id)
            entity.state = order.status
            entity.transition_to(
                to_state, actor=actor, actor_roles=actor_roles,
                reason=reason, recorded_at=U08.now(),
            )
            order.status = entity.state
            order.version += 1
            return order

    def cancel_order(
        self, order_id: str, *, actor: str, actor_roles: Iterable[str], reason: str, line_ids: Optional[list[str]] = None
    ) -> Order:
        with self._lock:
            order = self._require_order(order_id)
            if order.status in {"shipped", "delivered", "closed"}:
                raise IllegalTransitionError(
                    order_id, order.status, "cancelled",
                    OrderEntity.machine.legal_targets(order.status),
                )
            entity = OrderEntity(order_id)
            entity.state = order.status
            entity.transition_to(
                "cancelled", actor=actor, actor_roles=actor_roles,
                reason=reason, recorded_at=U08.now(),
            )
            order.status = entity.state
            order.cancellation_reason = reason
            order.version += 1
            self.cancellations.append(
                Cancellation(
                    cancellation_id=generate("evidence"),
                    order_id=order_id,
                    line_ids=line_ids or [],
                    reason=reason,
                    cancelled_at=U08.now(),
                    actor=actor,
                )
            )
            return order

    def timeline(self, order_id: str) -> list[dict]:
        """REQ-ORD-009: the end-to-end transaction timeline."""
        order = self._require_order(order_id)
        timeline = [
            {"at": order.placed_at, "kind": "order_placed", "order_id": order.order_id,
             "correlation_id": order.correlation_id}
        ]
        if order.payment_correlation_id:
            timeline.append(
                {"at": order.placed_at, "kind": "payment_authorized", "order_id": order.order_id,
                 "payment_correlation_id": order.payment_correlation_id}
            )
        timeline.extend(self.cancellations and [
            {"at": c.cancelled_at, "kind": "cancelled", "order_id": order.order_id,
             "reason": c.reason, "actor": c.actor}
            for c in self.cancellations if c.order_id == order_id
        ] or [])
        return sorted(timeline, key=lambda e: e["at"])

    # -- quotes (REQ-ORD-003) ---------------------------------------------

    def issue_quote(
        self, authz: AuthorizationContext, buyer_id: str, seller_id: str, lines: list[dict],
        currency: str, valid_until: str,
    ) -> Quote:
        authz.requires_role("quote.writer")
        quote = Quote(
            quote_id=generate("checkout"),
            buyer_id=buyer_id,
            seller_id=seller_id,
            lines=lines,
            currency=currency,
            valid_until=valid_until,
        )
        self.quotes[quote.quote_id] = quote
        return quote

    def accept_quote(self, authz: AuthorizationContext, quote_id: str) -> Quote:
        quote = self.quotes.get(quote_id)
        if quote is None:
            raise NotFoundError(f"Quote {quote_id} not found")
        if quote.valid_until < U08.now():
            quote.status = "expired"
            raise ValidationError(f"Quote {quote_id} has expired")
        if authz.actor_id != quote.buyer_id and "quote.approver" not in authz.roles:
            raise ValidationError("Only the quoted buyer or an approver may accept a quote")
        quote.status = "accepted"
        return quote

    # -- invariants --------------------------------------------------------

    def assert_snapshot_matches(
        self, order: Order, evaluation_total: Money, evaluation_id: str
    ) -> None:
        """INV-01: the committed snapshot must equal the authoritative evaluation."""
        snapshot = order.snapshot
        if snapshot is None:
            raise InvariantViolationError(
                "INV-01", f"Order {order.order_id} has no commercial snapshot"
            )
        if snapshot.price_evaluation_id != evaluation_id:
            raise InvariantViolationError(
                "INV-01",
                f"Order snapshot references evaluation {snapshot.price_evaluation_id}, "
                f"not the authoritative {evaluation_id}",
            )
        snapshot_total = Money.of(snapshot.totals["grand_total"], snapshot.currency)
        if snapshot_total != evaluation_total:
            raise InvariantViolationError(
                "INV-01",
                f"Order snapshot total {snapshot_total} differs from evaluation total "
                f"{evaluation_total}",
                details={
                    "snapshot_total": str(snapshot_total),
                    "evaluation_total": str(evaluation_total),
                },
            )

    def _require_cart(self, cart_id: str) -> Cart:
        cart = self.carts.get(cart_id)
        if cart is None:
            raise NotFoundError(f"Cart {cart_id} not found")
        return cart

    def _require_order(self, order_id: str) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise NotFoundError(f"Order {order_id} not found")
        return order


U08.owns(
    "cart", "Mutable collection of intended purchases; explicitly not a confirmed order",
    "U08.OrderService.open_cart", read_projections=["U11.cart_projection"],
)
U08.owns("checkout_attempt", "Validated attempt to create a purchase transaction", "U08.OrderService.place_order")
U08.owns("order", "Durable commercial commitment and its lifecycle", "U08.OrderService.place_order", read_projections=["U10.fulfillment_order", "U09.payment_intent"])
U08.owns("order_line", "Immutable line-level commercial commitment within an order", "U08.OrderService.place_order")
U08.owns("cancellation", "Order cancellation record with actor and reason", "U08.OrderService.cancel_order")
U08.owns("quote", "B2B quote that may be contractually binding by policy", "U08.OrderService.issue_quote")
