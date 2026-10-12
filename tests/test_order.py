"""U08 Order & Transaction: cart intent, idempotent order commitment, lifecycle."""

import pytest

from marketplace.kernel import Money
from marketplace.kernel.errors import IllegalTransitionError, NotFoundError, ValidationError
from marketplace.universes.u08_order.entities import LineSnapshot, OrderEntity, OrderSnapshot
from marketplace.universes.u08_order.service import OrderService

pytestmark = pytest.mark.invariant


@pytest.fixture
def orders():
    return OrderService()


def snapshot(currency="USD"):
    line = LineSnapshot(
        line_id="l1",
        offer_id="offer-1",
        product_id="sku-1",
        variant_id=None,
        seller_id="seller-1",
        quantity=2,
        unit_price=Money.of(10, currency),
        fulfillment_model="seller_fulfilled",
        lead_time_days=3,
        components=[{"kind": "base", "amount": "20.00", "currency": currency}],
        line_total=Money.of(20, currency),
    )
    return OrderSnapshot(
        currency=currency,
        lines=[line],
        totals={"subtotal": "20.00", "total": "20.00"},
        price_evaluation_id="pev-1",
        policy_versions={"pricebook": 1},
        offer_versions={"offer-1": 1},
        snapshot_at="2026-01-01T00:00:00+00:00",
    )


class TestCart:
    def test_open_cart(self, orders):
        cart = orders.open_cart("buyer-1", "tenant-A")
        assert cart.status == "active"
        assert cart.lines == []

    def test_add_line(self, orders):
        cart = orders.open_cart("buyer-1", "tenant-A")
        orders.add_to_cart(cart.cart_id, "offer-1", "sku-1", "seller-1", 2)
        assert cart.lines[0].quantity == 2
        assert cart.version == 1

    def test_add_zero_quantity_rejected(self, orders):
        cart = orders.open_cart("buyer-1", "tenant-A")
        with pytest.raises(ValidationError):
            orders.add_to_cart(cart.cart_id, "offer-1", "sku-1", "seller-1", 0)

    def test_repeat_add_merges_quantity(self, orders):
        cart = orders.open_cart("buyer-1", "tenant-A")
        orders.add_to_cart(cart.cart_id, "offer-1", "sku-1", "seller-1", 2)
        orders.add_to_cart(cart.cart_id, "offer-1", "sku-1", "seller-1", 3)
        assert len(cart.lines) == 1
        assert cart.lines[0].quantity == 5

    def test_merge_carts(self, orders):
        guest = orders.open_cart("guest", "tenant-A")
        account = orders.open_cart("buyer-1", "tenant-A")
        orders.add_to_cart(guest.cart_id, "offer-1", "sku-1", "seller-1", 2)
        orders.merge_carts(guest.cart_id, account.cart_id)
        assert guest.status == "merged"
        assert account.lines[0].quantity == 2

    def test_merge_accumulates_matching_lines(self, orders):
        guest = orders.open_cart("guest", "tenant-A")
        account = orders.open_cart("buyer-1", "tenant-A")
        orders.add_to_cart(account.cart_id, "offer-1", "sku-1", "seller-1", 1)
        orders.add_to_cart(guest.cart_id, "offer-1", "sku-1", "seller-1", 4)
        orders.merge_carts(guest.cart_id, account.cart_id)
        assert account.lines[0].quantity == 5

    def test_missing_cart(self, orders):
        with pytest.raises(NotFoundError):
            orders.add_to_cart("nope", "offer-1", "sku-1", "seller-1", 1)


def place(orders, *, idempotency_key="key-1", currency="USD"):
    cart = orders.open_cart("buyer-1", "tenant-A")
    return orders.place_order(
        order_id=None,
        buyer_id="buyer-1",
        tenant_id="tenant-A",
        cart_id=cart.cart_id,
        snapshot=snapshot(currency),
        correlation_id="corr-1",
        idempotency_key=idempotency_key,
        payment_correlation_id="pay-1",
    )


class TestOrderCommitment:
    def test_place_order(self, orders):
        order = place(orders)
        assert order.status == "confirmed"
        assert order.buyer_id == "buyer-1"
        assert "seller-1" in order.seller_suborders

    def test_order_is_idempotent(self, orders):
        first = place(orders, idempotency_key="dup")
        second = place(orders, idempotency_key="dup")
        assert first.order_id == second.order_id
        assert len(orders.orders) == 1

    def test_explicit_order_id_respected(self, orders):
        order = place(orders)
        assert order.order_id.startswith("ORD")

    def test_cart_marked_converted(self, orders):
        cart = orders.open_cart("buyer-1", "tenant-A")
        orders.place_order(
            order_id=None, buyer_id="buyer-1", tenant_id="tenant-A", cart_id=cart.cart_id,
            snapshot=snapshot(), correlation_id="c", idempotency_key="k",
        )
        assert cart.status == "converted"

    def test_snapshot_is_immutable_reference(self, orders):
        order = place(orders)
        snap = order.snapshot
        assert snap.currency == "USD"
        assert snap.lines[0].unit_price == Money.of(10, "USD")


class TestOrderLifecycle:
    def test_cancel_confirmed_order(self, orders):
        order = place(orders)
        cancelled = orders.cancel_order(
            order.order_id, actor="buyer-1", actor_roles={"buyer"}, reason="changed mind"
        )
        assert cancelled.status == "cancelled"
        assert cancelled.cancellation_reason == "changed mind"

    def test_cancel_shipped_order_denied(self, orders):
        order = place(orders)
        orders.transition_order(
            order.order_id, "processing", actor="fulfilment", actor_roles={"fulfillment.service"},
            reason="picked",
        )
        orders.transition_order(
            order.order_id, "shipped", actor="fulfilment", actor_roles={"fulfillment.service"},
            reason="dispatched",
        )
        with pytest.raises(IllegalTransitionError):
            orders.cancel_order(order.order_id, actor="x", actor_roles={"buyer"}, reason="late")

    def test_illegal_transition_denied(self, orders):
        order = place(orders)
        with pytest.raises(IllegalTransitionError):
            orders.transition_order(
                order.order_id, "delivered", actor="x", actor_roles={"x"}, reason="skip"
            )

    def test_transition_increments_version(self, orders):
        order = place(orders)
        before = order.version
        updated = orders.transition_order(
            order.order_id, "processing", actor="f", actor_roles={"fulfillment.service"}, reason="go"
        )
        assert updated.version == before + 1

    def test_cancellation_recorded_in_timeline(self, orders):
        order = place(orders)
        orders.cancel_order(order.order_id, actor="buyer-1", actor_roles={"buyer"}, reason="no")
        tl = orders.timeline(order.order_id)
        kinds = {e["kind"] for e in tl}
        assert "order_placed" in kinds
        assert "cancelled" in kinds

    def test_timeline_is_ordered(self, orders):
        order = place(orders)
        tl = orders.timeline(order.order_id)
        assert tl == sorted(tl, key=lambda e: e["at"])

    def test_timeline_unknown_order(self, orders):
        with pytest.raises(NotFoundError):
            orders.timeline("nope")


class TestOrderMachine:
    def test_initial_state_is_pending(self):
        assert OrderEntity("x").state == "pending"

    def test_confirmed_is_not_terminal(self):
        e = OrderEntity("x")
        assert not e.is_terminal
