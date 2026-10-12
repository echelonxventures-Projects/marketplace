"""U12 FDVI: review, learning and corrective action (Spec 030 Module 15; FR-043..045)."""

from decimal import Decimal

import pytest

from marketplace.kernel import Money
from marketplace.kernel.errors import (
    AuthorizationError,
    NotFoundError,
    PolicyViolationError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.entities import (
    DecisionEvaluation,
    ValueRealizationRecord,
)
from marketplace.universes.u12_intelligence.review import (
    CorrectiveActionProposal,
    ReviewFinding,
    ReviewPolicy,
    ReviewService,
)

pytestmark = pytest.mark.unit


def evaluation(net=Decimal("100")):
    return DecisionEvaluation(
        evaluation_id="ev-1",
        case_id="case-1",
        alternative_ref="alt-1",
        evidence_snapshot_ref="snap-1",
        objective_snapshot_refs=[],
        constraint_snapshot_refs=[],
        financial_effects={} if net is None else {"net": Money.of(net, "USD")},
    )


def realization(value=Decimal("60")):
    return ValueRealizationRecord(
        realization_id="real-1",
        case_id="case-1",
        value_hypothesis_ref="hyp-1",
        baseline_snapshot_ref="base-1",
        observed_value=Money.of(value, "USD"),
    )


class TestReviewComparison:
    def test_material_gap_produces_finding(self, authz):
        service = ReviewService()
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=realization(Decimal("60")),
        )
        assert review.findings
        finding = service.findings[review.findings[0]]
        assert finding.code == "value.gap"
        assert "40.0%" in finding.description
        assert finding.severity == "high"  # 40% >= 20%

    def test_small_gap_produces_no_finding(self, authz):
        service = ReviewService()
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=realization(Decimal("97")),
        )
        assert review.findings == []
        assert review.outcome_assessment == "no material deviation detected"

    def test_custom_materiality_threshold(self, authz):
        service = ReviewService(ReviewPolicy(material_gap_threshold=Decimal("0.01")))
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=realization(Decimal("97")),
        )
        assert review.findings

    def test_currency_mismatch_flagged_not_converted(self, authz):
        mismatched = ValueRealizationRecord(
            realization_id="real-2",
            case_id="case-1",
            value_hypothesis_ref="hyp-1",
            baseline_snapshot_ref="base-1",
            observed_value=Money.of(Decimal("60"), "EUR"),
        )
        service = ReviewService()
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=mismatched,
        )
        finding = service.findings[review.findings[0]]
        assert finding.code == "currency.mismatch"
        assert finding.severity == "high"

    def test_missing_expected_and_observed_rejected(self, authz):
        service = ReviewService()
        with pytest.raises(ValidationError):
            service.compare(
                authz,
                case_id="case-1",
                review_type="outcome",
                review_trigger="periodic",
            )

    def test_no_financial_effects_produces_no_finding(self, authz):
        service = ReviewService()
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="periodic",
            review_trigger="scheduled",
            evaluation=evaluation(net=None),
            realization=realization(Decimal("60")),
        )
        assert review.findings == []

    def test_reviewer_role_required(self, other_tenant_authz):
        service = ReviewService()
        with pytest.raises(Exception):
            service.compare(
                other_tenant_authz,
                case_id="case-1",
                review_type="outcome",
                review_trigger="periodic",
                evaluation=evaluation(),
            )

    def test_assessment_ordering_by_severity(self, authz):
        service = ReviewService()
        critical = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=realization(Decimal("60")),
        )
        assert "material deviation" in critical.outcome_assessment


class TestFindings:
    def test_invalid_severity_rejected(self, authz):
        service = ReviewService()
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=realization(Decimal("60")),
        )
        bad = ReviewFinding(finding_id="", code="x", description="d", severity="catastrophic")
        with pytest.raises(ValidationError):
            service.add_finding(authz, review.review_id, bad)

    def test_add_finding_links_to_review(self, authz):
        service = ReviewService()
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=realization(Decimal("60")),
        )
        before = len(review.findings)
        finding = ReviewFinding(finding_id="", code="drift.signal", description="d")
        added = service.add_finding(authz, review.review_id, finding)
        assert len(review.findings) == before + 1
        assert service.findings[added.finding_id] is added


class TestCorrectiveAction:
    def _review(self, authz):
        service = ReviewService()
        review = service.compare(
            authz,
            case_id="case-1",
            review_type="outcome",
            review_trigger="periodic",
            evaluation=evaluation(),
            realization=realization(Decimal("60")),
        )
        return service, review

    def test_proposal_created_and_linked(self, authz):
        service, review = self._review(authz)
        finding_id = review.findings[0]
        proposal = service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain and revalidate",
            rationale="drift detected",
            finding_refs=[finding_id],
        )
        assert proposal.status == "proposed"
        assert proposal.proposal_id in review.corrective_action_refs
        assert service.proposals_for_review(review.review_id) == [proposal]

    def test_invalid_target_kind_rejected(self, authz):
        service, review = self._review(authz)
        with pytest.raises(ValidationError):
            service.propose_corrective_action(
                authz,
                review_id=review.review_id,
                case_id="case-1",
                target_kind="database",
                target_ref="x",
                target_owner_ref="o",
                requested_change="c",
                rationale="r",
            )

    def test_missing_owner_rejected(self, authz):
        service, review = self._review(authz)
        with pytest.raises(ValidationError):
            service.propose_corrective_action(
                authz,
                review_id=review.review_id,
                case_id="case-1",
                target_kind="policy",
                target_ref="p",
                target_owner_ref="",
                requested_change="c",
                rationale="r",
            )

    def test_complete_blocked_until_material_finding_routed(self, authz):
        service, review = self._review(authz)
        with pytest.raises(PolicyViolationError):
            service.complete(authz, review.review_id)

    def test_complete_allowed_once_proposal_routed(self, authz):
        service, review = self._review(authz)
        service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain",
            rationale="drift",
            finding_refs=[review.findings[0]],
        )
        completed = service.complete(authz, review.review_id)
        assert completed.review_status == "completed"

    def test_only_owner_may_accept(self, authz):
        service, review = self._review(authz)
        proposal = service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain",
            rationale="drift",
        )
        with pytest.raises(AuthorizationError):
            service.accept(authz, proposal.proposal_id)

    def test_accept_then_implement_lifecycle(self, authz):
        service, review = self._review(authz)
        proposal = service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain",
            rationale="drift",
        )
        owner_authz = _actor("model-owner")
        accepted = service.accept(owner_authz, proposal.proposal_id, note="ok")
        assert accepted.status == "accepted"
        implemented = service.mark_implemented(
            owner_authz, proposal.proposal_id, implemented_at="2026-01-02T00:00:00+00:00"
        )
        assert implemented.status == "implemented"

    def test_reject_requires_reason(self, authz):
        service, review = self._review(authz)
        proposal = service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain",
            rationale="drift",
        )
        owner_authz = _actor("model-owner")
        with pytest.raises(ValidationError):
            service.reject(owner_authz, proposal.proposal_id, reason="")
        rejected = service.reject(owner_authz, proposal.proposal_id, reason="not valid")
        assert rejected.status == "rejected"

    def test_cannot_accept_twice(self, authz):
        service, review = self._review(authz)
        proposal = service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain",
            rationale="drift",
        )
        owner_authz = _actor("model-owner")
        service.accept(owner_authz, proposal.proposal_id)
        with pytest.raises(ValidationError):
            service.accept(owner_authz, proposal.proposal_id)

    def test_withdraw_only_by_reviewer(self, authz):
        service, review = self._review(authz)
        proposal = service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain",
            rationale="drift",
        )
        with pytest.raises(AuthorizationError):
            service.withdraw(_actor("someone-else"), proposal.proposal_id)
        withdrawn = service.withdraw(authz, proposal.proposal_id)
        assert withdrawn.status == "withdrawn"

    def test_cannot_withdraw_accepted_proposal(self, authz):
        service, review = self._review(authz)
        proposal = service.propose_corrective_action(
            authz,
            review_id=review.review_id,
            case_id="case-1",
            target_kind="model",
            target_ref="model-v1",
            target_owner_ref="model-owner",
            requested_change="retrain",
            rationale="drift",
        )
        service.accept(_actor("model-owner"), proposal.proposal_id)
        with pytest.raises(ValidationError):
            service.withdraw(authz, proposal.proposal_id)

    def test_unknown_proposal_raises(self, authz):
        service, review = self._review(authz)
        with pytest.raises(NotFoundError):
            service.get_proposal("nope")
        with pytest.raises(NotFoundError):
            service.complete(authz, "nope")


class TestNoSelfAppliedProposal:
    def _proposal(self, **over):
        base = dict(
            proposal_id="p1",
            review_id="r1",
            case_id="case-1",
            target_kind="policy",
            target_ref="pol-1",
            target_owner_ref="policy-owner",
            requested_change="amend",
            rationale="r",
            status="implemented",
            accepted_by="policy-owner",
        )
        base.update(over)
        return CorrectiveActionProposal(**base)

    def test_owner_applied_is_allowed(self):
        service = ReviewService()
        assert service.assert_no_self_applied_proposal(self._proposal()) is None

    def test_missing_owner_application_is_a_violation(self):
        service = ReviewService()
        with pytest.raises(Exception):
            service.assert_no_self_applied_proposal(
                self._proposal(accepted_by="reviewer-1")
            )

    def test_non_policy_target_is_not_blocked(self):
        service = ReviewService()
        assert (
            service.assert_no_self_applied_proposal(
                self._proposal(target_kind="model", accepted_by="reviewer-1")
            )
            is None
        )


class TestReviewConceptOwnership:
    def test_concept_owned_by_u12(self):
        from marketplace.kernel.registry import CONCEPTS

        assert any(c.concept == "corrective_action_proposal" for c in CONCEPTS.all())


def _actor(actor_id, roles=frozenset({"decision.reviewer"})):
    from marketplace.kernel import AuthorizationContext

    return AuthorizationContext(
        actor_id=actor_id,
        actor_type="person",
        roles=roles,
        tenant_id="tenant-A",
    )
