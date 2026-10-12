"""Authorization, tenant isolation, audit integrity and fail-safe behaviour.

Covers LAW-ARCH-07: an operation that cannot establish authorization does not
proceed, and dependency unavailability is never a reason to bypass it.
"""

import pytest

from marketplace.kernel import AuthorizationContext, FailingAuthorizationResolver
from marketplace.kernel.authorization import AuditChain, Delegation
from marketplace.kernel.errors import AuthorizationError, TenantIsolationError

pytestmark = pytest.mark.security


class TestAuthorizationContext:
    def test_requires_role_passes_when_held(self):
        ctx = AuthorizationContext("a", "person", frozenset({"admin"}), "t")
        assert ctx.requires_role("admin") is ctx

    def test_requires_role_fails_when_missing(self):
        ctx = AuthorizationContext("a", "person", frozenset({"user"}), "t")
        with pytest.raises(AuthorizationError):
            ctx.requires_role("admin")

    def test_requires_scope_fails_when_missing(self):
        ctx = AuthorizationContext("a", "person", frozenset(), "t", frozenset({"read"}))
        with pytest.raises(AuthorizationError):
            ctx.requires_scope("write")

    def test_same_tenant_passes(self):
        ctx = AuthorizationContext("a", "person", frozenset(), "t")
        assert ctx.same_tenant("t") is ctx

    def test_cross_tenant_denied(self):
        ctx = AuthorizationContext("a", "person", frozenset(), "tenant-A")
        with pytest.raises(TenantIsolationError):
            ctx.same_tenant("tenant-B")

    def test_context_is_immutable(self):
        ctx = AuthorizationContext("a", "person", frozenset(), "t")
        with pytest.raises(Exception):
            ctx.actor_id = "other"

    def test_to_dict_redacts_nothing_but_is_explicit(self):
        d = AuthorizationContext("a", "person", frozenset({"r"}), "t").to_dict()
        assert d["actor_id"] == "a" and d["roles"] == ["r"]


class TestFailSafeResolver:
    """A down authorization dependency must fail closed, never open."""

    def test_resolve_denies_when_unavailable(self):
        with pytest.raises(AuthorizationError):
            FailingAuthorizationResolver().resolve("any-token")

    def test_validate_delegation_denies_when_unavailable(self):
        with pytest.raises(AuthorizationError):
            FailingAuthorizationResolver().validate_delegation("d", "act", {})


class TestDelegation:
    def make(self, **over):
        base = dict(
            delegation_id="d1",
            principal="boss",
            delegate="assistant",
            scopes=["approve.refund"],
            validity_start="2026-01-01T00:00:00+00:00",
            validity_end="2026-12-31T00:00:00+00:00",
        )
        base.update(over)
        return Delegation(**base)

    def test_active_within_window(self):
        assert self.make().active_at("2026-06-01T00:00:00+00:00")

    def test_inactive_before_window(self):
        assert not self.make().active_at("2025-01-01T00:00:00+00:00")

    def test_inactive_after_window(self):
        assert not self.make().active_at("2027-01-01T00:00:00+00:00")

    def test_revocation_ends_authority(self):
        d = self.make()
        d.revoke("2026-06-01T00:00:00+00:00")
        assert not d.active_at("2026-06-02T00:00:00+00:00")

    def test_scope_coverage(self):
        d = self.make()
        assert d.covers("approve.refund")
        assert not d.covers("approve.payout")

    def test_transaction_limit_enforced(self):
        d = self.make(transaction_limits={"amount": 100})
        assert d.within_limits("amount", 100)
        assert not d.within_limits("amount", 101)

    def test_limit_absent_means_unlimited(self):
        assert self.make().within_limits("anything", 10**9)

    def test_non_revokable_delegation(self):
        d = self.make(revokable=False)
        with pytest.raises(AuthorizationError):
            d.revoke("2026-06-01T00:00:00+00:00")


class TestAuditChain:
    """LAW-ARCH-04: the audit log is append-only and tamper-evident."""

    def record(self, **over):
        base = dict(
            actor="alice",
            authority="role:admin",
            action="update",
            target_type="price",
            target_id="p1",
            reason="price change",
            source="U05",
            recorded_at="2026-01-01T00:00:00+00:00",
        )
        base.update(over)
        return base

    def test_chain_starts_at_genesis(self):
        chain = AuditChain()
        chain.record(**self.record())
        assert chain.entries()[0]["previous_digest"] == "GENESIS"

    def test_entries_chain(self):
        chain = AuditChain()
        first = chain.record(**self.record())
        second = chain.record(**self.record(target_id="p2"))
        assert second["previous_digest"] == first["digest"]

    def test_intact_chain_verifies(self):
        chain = AuditChain()
        chain.record(**self.record())
        chain.record(**self.record(target_id="p2"))
        assert chain.verify() == []

    def test_tampered_entry_is_detected(self):
        chain = AuditChain()
        chain.record(**self.record())
        chain.record(**self.record(target_id="p2"))
        chain._entries[0]["reason"] = "fabricated"
        assert chain.verify() != []

    def test_broken_chain_link_detected(self):
        """Removing the head entry orphans the rest of the chain."""
        chain = AuditChain()
        chain.record(**self.record())
        chain.record(**self.record(target_id="p2"))
        chain._entries.pop(0)
        assert chain.verify() != []

    def test_nothing_to_verify_on_empty_chain(self):
        assert AuditChain().verify() == []

    def test_length_and_target_filter(self):
        chain = AuditChain()
        chain.record(**self.record())
        chain.record(**self.record(target_id="p2"))
        assert len(chain) == 2
        assert len(chain.for_target("p2")) == 1
