"""U13 Trust & Safety: risk cases, enforcement, disputes and appeals."""

import pytest

from marketplace.kernel import AuthorizationContext
from marketplace.kernel.errors import IllegalTransitionError, NotFoundError, PolicyViolationError, ValidationError
from marketplace.universes.u11_u15.services import TrustService

pytestmark = pytest.mark.unit

U13_NOW = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def service():
    return TrustService()


@pytest.fixture
def filer():
    return AuthorizationContext(
        actor_id="buyer-1",
        actor_type="person",
        # The dispute machine permits an appeal only with the buyer/seller role.
        roles=frozenset({"dispute.filer", "buyer"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def approver():
    return AuthorizationContext(
        actor_id="trust-approver",
        actor_type="person",
        roles=frozenset({"trust.approver"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def admin():
    return AuthorizationContext(
        actor_id="trust-admin",
        actor_type="person",
        roles=frozenset({"trust.admin"}),
        tenant_id="tenant-A",
    )


class TestRiskCases:
    def test_open_risk_case(self, service):
        case = service.open_risk_case("listing", "L-1", ["price_anomaly", "new_seller"])
        assert case.status == "open"
        assert case.signals == ["price_anomaly", "new_seller"]
        assert case.case_id in service.risk_cases

    def test_risk_case_records_model_version(self, service):
        case = service.open_risk_case("transaction", "T-1", ["velocity"], model_version="risk-v2")
        assert case.model_version == "risk-v2"


class TestEnforcement:
    def test_enforce_records_actor_and_rule(self, service, admin):
        action = service.enforce(admin, "RULE-01", "listing", "L-1", "listing_blocked", "prohibited item")
        assert action.kind == "listing_blocked"
        assert action.actor == "trust-admin"
        assert action.rule_id == "RULE-01"
        assert action.reversed_at is None

    def test_unknown_kind_rejected(self, service, admin):
        with pytest.raises(ValidationError):
            service.enforce(admin, "R", "listing", "L", "nuke_from_orbit", "x")

    def test_admin_role_required(self, service, filer):
        with pytest.raises(Exception):
            service.enforce(filer, "R", "listing", "L", "listing_blocked", "x")


class TestDisputes:
    def _in_review(self, service, filer):
        dispute = service.file_dispute(filer, "ORD-1", "x", "buyer-1")
        service.submit_evidence(filer, dispute.dispute_id, "screenshot", "ref")
        from marketplace.universes.u11_u15.services import DisputeEntity

        entity = DisputeEntity(dispute.dispute_id)
        entity.state = dispute.status
        entity.transition_to(
            "in_review", actor="trust.agent", actor_roles=frozenset({"trust.agent"}),
            reason="review started", recorded_at=U13_NOW,
        )
        dispute.status = "in_review"
        return dispute

    def test_file_and_submit_evidence(self, service, filer):
        dispute = service.file_dispute(filer, "ORD-1", "Item not as described", "buyer-1")
        item = service.submit_evidence(filer, dispute.dispute_id, "screenshot", "ref-1")
        assert item in dispute.evidence
        assert dispute.status == "filed"

    def test_unknown_evidence_kind_rejected(self, service, filer):
        dispute = service.file_dispute(filer, "ORD-1", "x", "buyer-1")
        with pytest.raises(ValidationError):
            service.submit_evidence(filer, dispute.dispute_id, "forged_document", "ref")

    def test_evidence_on_unknown_dispute_raises(self, service, filer):
        with pytest.raises(NotFoundError):
            service.submit_evidence(filer, "nope", "screenshot", "ref")

    def test_decide_requires_evidence(self, service, filer, approver):
        dispute = service.file_dispute(filer, "ORD-1", "x", "buyer-1")
        from marketplace.universes.u11_u15.services import DisputeEntity

        entity = DisputeEntity(dispute.dispute_id)
        entity.state = dispute.status
        entity.transition_to(
            "in_review", actor="trust.agent", actor_roles=frozenset({"trust.agent"}),
            reason="review started", recorded_at=U13_NOW,
        )
        dispute.status = "in_review"
        with pytest.raises(PolicyViolationError):
            service.decide(approver, dispute.dispute_id, "refund")

    def test_decide_records_decision_and_window(self, service, filer, approver):
        dispute = self._in_review(service, filer)
        decided = service.decide(approver, dispute.dispute_id, "refund")
        assert decided.status == "decided"
        assert decided.decision == "refund"
        assert decided.decided_by == "trust-approver"
        assert decided.appeal_window_until is not None

    def test_decide_requires_approver_role(self, service, filer):
        dispute = service.file_dispute(filer, "ORD-1", "x", "buyer-1")
        with pytest.raises(Exception):
            service.decide(filer, dispute.dispute_id, "refund")


class TestAppeals:
    def _decided(self, service, filer, approver):
        dispute = service.file_dispute(filer, "ORD-1", "x", "buyer-1")
        service.submit_evidence(filer, dispute.dispute_id, "screenshot", "ref")
        from marketplace.universes.u11_u15.services import DisputeEntity

        entity = DisputeEntity(dispute.dispute_id)
        entity.state = dispute.status
        entity.transition_to(
            "in_review", actor="trust.agent", actor_roles=frozenset({"trust.agent"}),
            reason="review started", recorded_at=U13_NOW,
        )
        dispute.status = "in_review"
        return service.decide(approver, dispute.dispute_id, "refund")

    def test_appeal_within_window(self, service, filer, approver):
        dispute = self._decided(service, filer, approver)
        appealed = service.appeal(filer, dispute.dispute_id)
        assert appealed.status == "appealed"

    def test_cannot_appeal_undecided_dispute(self, service, filer):
        dispute = service.file_dispute(filer, "ORD-1", "x", "buyer-1")
        with pytest.raises(IllegalTransitionError):
            service.appeal(filer, dispute.dispute_id)

    def test_appeal_after_window_closed(self, service, filer, approver):
        dispute = self._decided(service, filer, approver)
        dispute.appeal_window_until = "2000-01-01T00:00:00+00:00"
        with pytest.raises(PolicyViolationError):
            service.appeal(filer, dispute.dispute_id)

    def test_unknown_dispute_appeal_raises(self, service, filer):
        with pytest.raises(NotFoundError):
            service.appeal(filer, "nope")


class TestU13ConceptOwnership:
    def test_concepts_owned(self):
        from marketplace.kernel.registry import CONCEPTS

        owned = {c.concept for c in CONCEPTS.all() if c.owner_universe == "U13"}
        assert {"risk_case", "moderation_decision", "dispute_case", "enforcement_action"} <= owned
