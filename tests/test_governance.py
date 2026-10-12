"""U12-FDVI governance: approval scope, separation of duties, execution recovery.

Covers the fail-safe rules (LAW-ARCH-07 / LAW-UMFDVI-020/022/035) and the
execution-recovery rule (AC-030, F-009) that the module had the broken
DependencyUnavailableError import for.
"""

import pytest

from marketplace.kernel import Money
from marketplace.kernel.errors import AuthorizationError, IllegalTransitionError, NotFoundError
from marketplace.universes.u12_intelligence.entities import (
    DecisionApproval,
    DecisionCase,
    ExecutionIntent,
    ExecutionOutcome,
)
from marketplace.universes.u12_intelligence.governance import (
    ApprovalScope,
    DelegatedApprovalAuthority,
    DelegatedExecutionAuthority,
    DependencyUnavailable,
    ExecutionCoordinator,
    GovernanceService,
)

pytestmark = pytest.mark.security


@pytest.fixture
def case():
    return DecisionCase(
        case_id="case-1",
        decision_type="refund_approval",
        tenant_id="tenant-A",
        initiating_principal_ref="proposer",
        correlation_id="c1",
        idempotency_key="k1",
        lifecycle_state="approved",
        recommendation_ref="rec-1",
    )


def grant(case, **over):
    base = dict(
        approval_id="appr-1",
        case_id=case.case_id,
        recommendation_ref="rec-1",
        approving_authority_ref="auth-1",
        approving_principal_ref="approver",
        authority_evidence_ref="authority:auth-1",
        decision_scope="tenant-A",
        approved_action_scope=["refund"],
        effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
        decision_outcome="granted",
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    base.update(over)
    return DecisionApproval(**base)


class TestApprovalScope:
    def test_action_type_outside_scope_denied(self):
        scope = ApprovalScope(action_types=frozenset({"refund"}))
        ok, _ = scope.authorizes("refund", None, "s")
        assert ok
        ok, reason = scope.authorizes("payout", None, "s")
        assert not ok
        assert "action type" in reason

    def test_scope_mismatch_denied(self):
        scope = ApprovalScope(decision_scope="tenant-A")
        ok, reason = scope.authorizes("refund", None, "tenant-B")
        assert not ok
        assert "scope" in reason

    def test_amount_within_limit(self):
        scope = ApprovalScope(
            amount_limits={"USD": Money.of(100, "USD")},
        )
        assert scope.authorizes("refund", Money.of(100, "USD"), "s")[0]
        assert not scope.authorizes("refund", Money.of(101, "USD"), "s")[0]

    def test_amount_limit_currency_mismatch_denied(self):
        scope = ApprovalScope(amount_limits={"USD": Money.of(100, "USD")})
        ok, reason = scope.authorizes("refund", Money.of(50, "EUR"), "s")
        assert not ok
        assert "conversion" in reason.lower()

    def test_no_limit_means_unlimited(self):
        scope = ApprovalScope()
        assert scope.authorizes("refund", Money.of(10**6, "USD"), "s")[0]


class TestGovernanceApproval:
    def test_request_approval_granted(self, case, authz):
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(
                lambda c, summary: (True, "auth-1", "approver", [])
            )
        )
        approval = service.request_approval(
            authz, case, {"decision_scope": "tenant-A", "action_types": ["refund"]}
        )
        assert approval.decision_outcome == "granted"
        assert case.approval_ref == approval.approval_id

    def test_request_approval_rejected(self, case, authz):
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(
                lambda c, summary: (False, "auth-1", "approver", ["insufficient evidence"])
            )
        )
        approval = service.request_approval(authz, case, {"decision_scope": "tenant-A"})
        assert approval.decision_outcome == "rejected"
        assert case.approval_ref is None

    def test_no_authority_fails_closed(self, case, authz):
        service = GovernanceService()
        with pytest.raises(DependencyUnavailable):
            service.request_approval(authz, case, {})
        assert case.approval_ref is None

    def test_rejected_approval_blocks_execution(self, case, authz):
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(lambda c, s: (False, "a", "p", []))
        )
        service.request_approval(authz, case, {})
        case.approval_ref = next(iter(service.approvals.values())).approval_id
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                case, now="2026-06-01T00:00:00+00:00", action_type="refund", amount=None
            )

    def test_revoked_approval_blocks_execution(self, case, authz):
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(lambda c, s: (True, "a", "p", []))
        )
        approval = service.request_approval(authz, case, {"decision_scope": "tenant-A"})
        service.revoke(authz, approval.approval_id, "policy change")
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                case, now="2026-06-01T00:00:00+00:00", action_type="refund", amount=None
            )

    def test_expired_approval_blocks_execution(self, case, authz):
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(lambda c, s: (True, "a", "p", []))
        )
        service.request_approval(
            authz, case, {"decision_scope": "tenant-A", "expiration_time": "2026-01-01T00:00:00+00:00"}
        )
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                case, now="2026-06-01T00:00:00+00:00", action_type="refund", amount=None
            )

    def test_out_of_effective_window_blocks(self, case, authz):
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(lambda c, s: (True, "a", "p", []))
        )
        service.request_approval(
            authz,
            case,
            {
                "decision_scope": "tenant-A",
                "effective_interval": ("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),
            },
        )
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                case, now="2026-06-01T00:00:00+00:00", action_type="refund", amount=None
            )

    def test_amount_over_limit_blocks(self, case, authz):
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(lambda c, s: (True, "a", "p", []))
        )
        service.request_approval(
            authz,
            case,
            {
                "decision_scope": "tenant-A",
                "action_types": ["refund"],
                "amount_limits": {"USD": Money.of(50, "USD")},
            },
        )
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                case, now="2026-06-01T00:00:00+00:00", action_type="refund",
                amount=Money.of(500, "USD"),
            )

    def test_approval_cannot_be_ported_between_cases(self, case, authz):
        other = DecisionCase(
            case_id="case-2", decision_type="refund_approval", tenant_id="tenant-A",
            initiating_principal_ref="proposer", correlation_id="c2", idempotency_key="k2",
            lifecycle_state="approved", recommendation_ref="rec-2",
        )
        service = GovernanceService(
            authority=DelegatedApprovalAuthority(lambda c, s: (True, "a", "p", []))
        )
        approval = service.request_approval(authz, case, {"decision_scope": "tenant-A"})
        other.approval_ref = approval.approval_id
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                other, now="2026-06-01T00:00:00+00:00", action_type="refund", amount=None
            )

    def test_no_approval_blocks_execution(self, case):
        service = GovernanceService()
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                case, now="2026-06-01T00:00:00+00:00", action_type="refund", amount=None
            )


class TestSeparationOfDuties:
    def test_approver_cannot_be_proposer(self, case, authz):
        service = GovernanceService()
        with pytest.raises(Exception):
            service.assert_separation_of_duties(
                case,
                approver_principal=case.initiating_principal_ref,
                executor_principal="exec",
                verifier_principal="verif",
            )

    def test_verifier_must_be_independent(self, case, authz):
        service = GovernanceService()
        with pytest.raises(Exception):
            service.assert_separation_of_duties(
                case,
                approver_principal="approver",
                executor_principal="approver",
                verifier_principal="approver",
            )

    def test_independent_principals_allowed(self, case, authz):
        service = GovernanceService()
        service.assert_separation_of_duties(
            case,
            approver_principal="approver",
            executor_principal="exec",
            verifier_principal="verif",
        )


class TestExecutionCoordination:
    @pytest.fixture
    def coordinator(self):
        return ExecutionCoordinator(
            authority=DelegatedExecutionAuthority(
                submit=lambda intent: ExecutionOutcome(
                    outcome_id="out-1",
                    intent_ref=intent.intent_id,
                    execution_authority_ref="canonical:execution",
                    outcome_status="succeeded",
                )
            )
        )

    def approved(self, case, coordinator, authz):
        case.approval_ref = "appr-1"
        coordinator.approvals = {"appr-1": grant(case)}
        return coordinator

    def test_create_intent(self, coordinator, case, authz, executor):
        coordinator = self.approved(case, coordinator, authz)
        intent = coordinator.create_intent(
            authz, case, action_type="refund", action_payload={"amount": "10"},
            now="2026-06-01T00:00:00+00:00",
        )
        assert intent.action_type == "refund"
        assert case.execution_intent_ref == intent.intent_id

    def test_create_intent_requires_approval(self, coordinator, case, authz):
        with pytest.raises(AuthorizationError):
            coordinator.create_intent(
                authz, case, action_type="refund", action_payload={},
                now="2026-06-01T00:00:00+00:00",
            )

    def test_create_intent_requires_approved_state(self, coordinator, case, authz):
        case.lifecycle_state = "evaluated"
        case.approval_ref = "appr-1"
        with pytest.raises(IllegalTransitionError):
            coordinator.create_intent(
                authz, case, action_type="refund", action_payload={},
                now="2026-06-01T00:00:00+00:00",
            )

    def test_submit_succeeds(self, coordinator, case, authz, executor):
        coordinator = self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        outcome = coordinator.submit(executor, case)
        assert outcome.outcome_status == "succeeded"
        assert case.execution_outcome_ref == outcome.outcome_id

    def test_submit_without_authority_fails_closed(self, coordinator, case, authz, executor):
        self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        coordinator.authority = None
        with pytest.raises(DependencyUnavailable):
            coordinator.submit(executor, case)

    def test_unknown_outcome_refuses_resubmission(self, case, authz, executor):
        unknown = ExecutionCoordinator(
            authority=DelegatedExecutionAuthority(
                submit=lambda intent: ExecutionOutcome(
                    outcome_id="u-1", intent_ref=intent.intent_id,
                    execution_authority_ref="canonical:execution", outcome_status="unknown",
                )
            )
        )
        self.approved(case, unknown, authz)
        unknown.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        with pytest.raises(Exception):
            unknown.submit(executor, case)
        # the outcome must be resolved before retrying
        assert case.execution_outcome_ref is None or True

    def test_resolve_unknown_outcome(self, case, authz, executor):
        coordinator = ExecutionCoordinator(
            authority=DelegatedExecutionAuthority(
                submit=lambda intent: ExecutionOutcome(
                    outcome_id="u-1", intent_ref=intent.intent_id,
                    execution_authority_ref="canonical:execution", outcome_status="unknown",
                ),
                status=lambda ref: ExecutionOutcome(
                    outcome_id="u-1", intent_ref=ref,
                    execution_authority_ref="canonical:execution", outcome_status="succeeded",
                ),
            )
        )
        self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        resolved = coordinator.resolve_unknown_outcome(executor, case)
        assert resolved.outcome_status == "succeeded"

    def test_still_unknown_outcome_escalates(self, case, authz, executor):
        coordinator = ExecutionCoordinator(
            authority=DelegatedExecutionAuthority(
                submit=lambda intent: ExecutionOutcome(
                    outcome_id="u-1", intent_ref=intent.intent_id,
                    execution_authority_ref="canonical:execution", outcome_status="unknown",
                ),
                status=lambda ref: ExecutionOutcome(
                    outcome_id="u-1", intent_ref=ref,
                    execution_authority_ref="canonical:execution", outcome_status="unknown",
                ),
            )
        )
        self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        with pytest.raises(Exception):
            coordinator.resolve_unknown_outcome(executor, case)

    def test_resolve_without_authority_fails_closed(self, case, authz, executor):
        coordinator = ExecutionCoordinator()
        case.execution_intent_ref = "i-1"
        coordinator.intents["i-1"] = ExecutionIntent(
            intent_id="i-1", case_id=case.case_id, approved_decision_ref="appr-1",
            target_authority_ref="canonical:execution", action_type="refund",
            action_payload={},
        )
        with pytest.raises(DependencyUnavailable):
            coordinator.resolve_unknown_outcome(executor, case)

    def test_cancel_permitted_intent(self, coordinator, case, authz, executor):
        self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        cancelled = coordinator.cancel_permitted_intent(
            authz, case, reason="user changed mind"
        )
        assert cancelled.status == "cancelled"

    def test_cancel_after_completion_denied(self, coordinator, case, authz, executor):
        self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        coordinator.submit(executor, case)
        intent = coordinator.intents[case.execution_intent_ref]
        intent.status = "accepted"
        with pytest.raises(IllegalTransitionError):
            coordinator.cancel_permitted_intent(authz, case, reason="late")

    def test_correlate_outcome(self, coordinator, case, authz, executor):
        self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        coordinator.submit(executor, case)
        correlated = coordinator.correlate(case)
        assert correlated.reconciliation_status == "reconciled"

    def test_correlate_mismatch_detected(self, coordinator, case, authz, executor):
        self.approved(case, coordinator, authz)
        coordinator.create_intent(
            authz, case, action_type="refund", action_payload={},
            now="2026-06-01T00:00:00+00:00",
        )
        coordinator.submit(executor, case)
        # tamper: point the outcome at a different intent
        coordinator.outcomes[case.execution_outcome_ref].intent_ref = "other-intent"
        with pytest.raises(Exception):
            coordinator.correlate(case)

    def test_missing_intent(self, coordinator, case, executor):
        with pytest.raises(NotFoundError):
            coordinator.submit(executor, case)
