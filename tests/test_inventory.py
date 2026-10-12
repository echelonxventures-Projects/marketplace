"""U06 Inventory & Supply: INV-02 oversell prevention, idempotent reservation, TTL."""

from decimal import Decimal

import pytest

from marketplace.kernel.errors import NotFoundError, OversellError, ValidationError
from marketplace.universes.u06_inventory.service import (
    InventoryPolicy,
    InventoryService,
)

pytestmark = pytest.mark.invariant


@pytest.fixture
def inventory():
    return InventoryService()


def position(inventory, authz, **over):
    base = dict(
        product_id="sku-1",
        owner_id="seller-1",
        location_id="wh-1",
        on_hand=10,
    )
    base.update(over)
    return inventory.create_position(authz, **base)


class TestPositions:
    def test_create_position(self, inventory, authz):
        pos = position(inventory, authz)
        assert pos.on_hand == 10
        assert pos.allocatable == 10

    def test_negative_on_hand_rejected(self, inventory, authz):
        with pytest.raises(ValidationError):
            position(inventory, authz, on_hand=-1)

    def test_requires_writer_role(self, inventory, other_tenant_authz):
        with pytest.raises(Exception):
            inventory.create_position(other_tenant_authz, "sku", "s", "wh")

    def test_safety_stock_reduces_allocatable(self, inventory, authz):
        pos = position(inventory, authz, policy=InventoryPolicy(safety_stock=4))
        assert pos.allocatable == 6

    def test_allocatable_never_negative(self, inventory, authz):
        pos = position(inventory, authz, on_hand=2, policy=InventoryPolicy(safety_stock=10))
        assert pos.allocatable == 0

    def test_link_offer_and_lookup(self, inventory, authz):
        pos = position(inventory, authz)
        inventory.link_offer("offer-1", pos.position_id)
        assert inventory.position_for_offer("offer-1").position_id == pos.position_id

    def test_unlinked_offer_returns_none(self, inventory):
        assert inventory.position_for_offer("nope") is None


class TestReservations:
    def reserve(self, inventory, authz, **over):
        pos = position(inventory, authz)
        inventory.link_offer("offer-1", pos.position_id)
        base = dict(
            offer_id="offer-1",
            quantity=2,
            correlation_id="cart-1",
            idempotency_key="key-1",
        )
        base.update(over)
        return inventory.reserve(**base)

    def test_reserve_decrements_allocatable(self, inventory, authz):
        res = self.reserve(inventory, authz)
        assert res.quantity == 2
        assert res.state == "reserved"
        assert inventory.position_for_offer("offer-1").allocatable == 8
        assert inventory.position_for_offer("offer-1").reserved == 2

    def test_zero_quantity_rejected(self, inventory, authz):
        with pytest.raises(ValidationError):
            self.reserve(inventory, authz, quantity=0)

    def test_oversell_prevented(self, inventory, authz):
        with pytest.raises(OversellError):
            self.reserve(inventory, authz, quantity=99)

    def test_oversell_buffer_allows_controlled_oversell(self, inventory, authz):
        pos = position(inventory, authz, policy=InventoryPolicy(allow_oversell=True, oversell_buffer_pct=Decimal("0.5")))
        inventory.link_offer("offer-2", pos.position_id)
        # allocatable 10 + buffer 5 = 15 max
        res = inventory.reserve(offer_id="offer-2", quantity=15, correlation_id="c", idempotency_key="k")
        assert res.quantity == 15

    def test_oversell_buffer_exceeded_denied(self, inventory, authz):
        pos = position(
            inventory, authz, policy=InventoryPolicy(allow_oversell=True, oversell_buffer_pct=Decimal("0.5"))
        )
        inventory.link_offer("offer-3", pos.position_id)
        with pytest.raises(OversellError):
            inventory.reserve(offer_id="offer-3", quantity=16, correlation_id="c", idempotency_key="k")

    def test_idempotent_reservation(self, inventory, authz):
        pos = position(inventory, authz)
        inventory.link_offer("offer-1", pos.position_id)
        first = inventory.reserve(
            offer_id="offer-1", quantity=2, correlation_id="cart-1", idempotency_key="key-1"
        )
        second = inventory.reserve(
            offer_id="offer-1", quantity=2, correlation_id="cart-1", idempotency_key="key-1"
        )
        assert first.reservation_id == second.reservation_id
        assert pos.reserved == 2

    def test_missing_offer_position(self, inventory, authz):
        with pytest.raises(NotFoundError):
            inventory.reserve(
                offer_id="unlinked", quantity=1, correlation_id="c", idempotency_key="k"
            )

    def test_commit_consumes_stock(self, inventory, authz):
        res = self.reserve(inventory, authz)
        committed = inventory.commit(res.reservation_id, correlation_id="cart-1")
        pos = inventory.position_for_offer("offer-1")
        assert committed.state == "committed"
        assert pos.on_hand == 8
        assert pos.reserved == 0

    def test_release_returns_stock(self, inventory, authz):
        res = self.reserve(inventory, authz)
        released = inventory.release(res.reservation_id, reason="cancelled")
        assert released.state == "released"
        assert inventory.position_for_offer("offer-1").allocatable == 10


class TestReservationExpiry:
    """Regression: ``expires_at`` must be set so stale reservations can expire."""

    def test_reserve_sets_expiry_from_ttl(self, inventory, authz):
        pos = position(inventory, authz)
        inventory.link_offer("offer-1", pos.position_id)
        res = inventory.reserve(
            offer_id="offer-1", quantity=1, correlation_id="c",
            idempotency_key="k", ttl_seconds=300,
        )
        assert res.expires_at != ""
        assert res.expires_at > res.created_at

    def test_reserve_uses_policy_ttl_by_default(self, inventory, authz):
        pos = position(inventory, authz, policy=InventoryPolicy(reservation_ttl_seconds=600))
        inventory.link_offer("offer-1", pos.position_id)
        res = inventory.reserve(
            offer_id="offer-1", quantity=1, correlation_id="c", idempotency_key="k"
        )
        assert res.expires_at != ""

    def test_expire_stale_reservations(self, inventory, authz):
        pos = position(inventory, authz)
        inventory.link_offer("offer-1", pos.position_id)
        res = inventory.reserve(
            offer_id="offer-1", quantity=3, correlation_id="c",
            idempotency_key="k", ttl_seconds=60,
        )
        # the sweep uses a strict inequality, so it must be called after expiry
        from datetime import datetime, timedelta

        later = (
            datetime.fromisoformat(res.expires_at.replace("Z", "+00:00")) + timedelta(seconds=1)
        ).isoformat()
        expired = inventory.expire_stale_reservations(later)
        assert len(expired) == 1
        assert expired[0].reservation_id == res.reservation_id
        assert expired[0].state == "expired"
        # stock returned to the pool
        assert inventory.position_for_offer("offer-1").allocatable == 10

    def test_non_stale_reservations_are_kept(self, inventory, authz):
        pos = position(inventory, authz)
        inventory.link_offer("offer-1", pos.position_id)
        res = inventory.reserve(
            offer_id="offer-1", quantity=3, correlation_id="c",
            idempotency_key="k", ttl_seconds=3600,
        )
        assert inventory.expire_stale_reservations(res.created_at) == []
        assert inventory.reservations[res.reservation_id].state == "reserved"


class TestAvailabilityProjection:
    """LAW-ARCH-06: availability is a computed projection, never a write authority."""

    def test_availability_aggregates_positions(self, inventory, authz):
        position(inventory, authz, location_id="wh-1", on_hand=4)
        position(inventory, authz, location_id="wh-2", on_hand=6)
        av = inventory.availability("sku-1")
        assert av["on_hand"] == 10
        assert av["available_to_promise"] == 10
        assert len(av["positions"]) == 2

    def test_availability_excludes_reserved(self, inventory, authz):
        pos = position(inventory, authz, on_hand=10)
        inventory.link_offer("offer-1", pos.position_id)
        inventory.reserve(offer_id="offer-1", quantity=4, correlation_id="c", idempotency_key="k")
        av = inventory.availability("sku-1")
        assert av["available_to_promise"] == 6

    def test_unknown_product_is_empty(self, inventory):
        av = inventory.availability("ghost")
        assert av["on_hand"] == 0
        assert av["positions"] == []


class TestFeedUpdates:
    def test_feed_update_records_observation(self, inventory, authz):
        pos = position(inventory, authz)
        update = inventory.apply_feed_update(
            authz, pos.position_id, "supplier-feed", 2, 12,
        )
        assert update.observed_on_hand == 12
        assert update.source == "supplier-feed"
