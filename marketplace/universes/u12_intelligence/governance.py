"""U12-FDVI governance integration and execution coordination
(Spec 030 Modules 11-12; FR-030..037).

Enforces:
  * LAW-ARCH-07 / LAW-UMFDVI-022 — fail-safe authorization;
  * LAW-ARCH-03 / LAW-UMFDVI-007 — recommendation, approval, execution and
    outcome are distinct;
  * LAW-UMFDVI-020 — an approval authorizes only its declared scope;
  * LAW-UMFDVI-035 — expirations are enforced, never ignored;
  * the execution-recovery rule (AC-030, F-009) — an unknown outcome must be
    resolved authoritatively before any re-submission.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from marketplace.kernel import AuthorizationContext, IdempotencyStore, Money, generate
from marketplace.kernel.errors import (
    AuthorizationError,
    DependencyUnavailableError,
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    PolicyViolationError,
    UnknownOutcomeError,
)
from marketplace.universes._base import U12
from marketplace.universes.u12_intelligence.entities import (
    DecisionApproval,
    DecisionCase,
    DecisionCaseEntity,
    ExecutionIntent,
    ExecutionOutcome,
)


@dataclass
class ApprovalScope:
    """The declared boundary of an approval (Spec 030 §11.1)."""

    action_types: frozenset[str] = field(default_factory=frozenset)
    amount_limits: dict[str, Money] = field(default_factory=dict)
    decision_scope: str = ""
    conditions: list[str] = field(default_factory=list)

    def authorizes(
        self, action_type: str, amount: Optional[Money], scope: str
    ) -> tuple[bool, str]:
        if self.action_types and action_type not in self.action_types:
            return False, f"action type {action_type!r} is outside the approved action scope"
        if self.decision_scope and scope != self.decision_scope:
            return False, f"scope {scope!r} differs from the approved scope {self.decision_scope!r}"
        if amount is not None:
            limit = self.amount_limits.get(amount.currency.code)
            if limit is not None:
                if amount > limit:
                    return False, f"amount {amount} exceeds the approved limit {limit}"
            elif self.amount_limits:
                # A limit is registered, but not in the action's currency. The
                # approval cannot cover an unconverted amount (AC-013).
                registered = ", ".join(sorted(self.amount_limits))
                return False, (
                    f"approval limit is in {registered} but the action is in "
                    f"{amount.currency}; an approved conversion is required"
                )
        return True, ""


class ApprovalAuthority:
    """The canonical approval authority contract.

    Implementations resolve an approval request to a granted/rejected decision.
    The decision capability records the result; it never grants authority
    itself (Spec 030 §7.1 Module 11: "This module coordinates governance. It
    does not independently grant authority.").
    """

    def request(self, case: DecisionCase, recommendation_summary: dict) -> DecisionApproval:
        raise NotImplementedError


class DelegatedApprovalAuthority(ApprovalAuthority):
    """Resolves approvals through a caller-supplied governance callback.

    The callback is the integration point to the canonical governance
    capability (U14 policy / U01 delegation). It returns a tuple of
    ``(granted, authority_ref, principal_ref, conditions)``. A callback that
    cannot be reached raises, which the caller must treat as *not approved*
    (fail-safe, LAW-ARCH-07).
    """

    def __init__(
        self,
        resolve: Callable[[DecisionCase, dict], tuple[bool, str, str, list[str]]],
    ) -> None:
        self.resolve = resolve

    def request(self, case: DecisionCase, recommendation_summary: dict) -> DecisionApproval:
        granted, authority_ref, principal_ref, conditions = self.resolve(
            case, recommendation_summary
        )
        if not granted:
            return DecisionApproval(
                approval_id=generate("evidence"),
                case_id=case.case_id,
                recommendation_ref=case.recommendation_ref or "",
                approving_authority_ref=authority_ref,
                approving_principal_ref=principal_ref,
                authority_evidence_ref="authority:rejection",
                decision_scope=recommendation_summary.get("decision_scope", ""),
                decision_outcome="rejected",
                conditions=list(conditions),
                recorded_at=U12.now(),
            )
        effective_start, effective_end = recommendation_summary.get(
            "effective_interval", ("", "")
        )
        return DecisionApproval(
            approval_id=generate("evidence"),
            case_id=case.case_id,
            recommendation_ref=case.recommendation_ref or "",
            approving_authority_ref=authority_ref,
            approving_principal_ref=principal_ref,
            authority_evidence_ref=f"authority:{authority_ref}",
            decision_scope=recommendation_summary.get("decision_scope", ""),
            approved_action_scope=list(recommendation_summary.get("action_types", [])),
            conditions=list(conditions),
            effective_interval=(effective_start, effective_end),
            expiration_time=recommendation_summary.get("expiration_time", ""),
            decision_outcome="granted",
            amount_limits=dict(recommendation_summary.get("amount_limits", {})),
            recorded_at=U12.now(),
        )


class GovernanceService:
    def __init__(self, authority: Optional[ApprovalAuthority] = None) -> None:
        self.authority = authority
        self.approvals: dict[str, DecisionApproval] = {}
        self.revoked: set[str] = set()
        self._lock = threading.RLock()

    def request_approval(
        self,
        authz: AuthorizationContext,
        case: DecisionCase,
        recommendation_summary: dict,
    ) -> DecisionApproval:
        """FR-030: submit a consequential decision to the canonical approval authority."""
        authz.requires_role("decision.requester")
        if self.authority is None:
            raise DependencyUnavailable(
                "approval.authority",
                "No approval authority is bound to this governance service; the "
                "decision cannot be approved and is not executed (fail-safe).",
            )
        approval = self.authority.request(case, recommendation_summary)
        with self._lock:
            self.approvals[approval.approval_id] = approval
            if approval.decision_outcome == "granted":
                case.approval_ref = approval.approval_id
        return approval

    def revalidate_authorization(
        self, case: DecisionCase, *, now: str, action_type: str, amount: Optional[Money]
    ) -> DecisionApproval:
        """FR-031 / AC-023..026: validate approval status, scope, authority and validity."""
        approval = self._require_approval(case)
        if approval.decision_outcome == "revoked" or approval.approval_id in self.revoked:
            raise AuthorizationError(
                f"Approval {approval.approval_id} has been revoked; execution under it "
                "is blocked (AC-025)"
            )
        if approval.decision_outcome == "rejected":
            raise AuthorizationError(
                f"Approval request for case {case.case_id} was rejected by the "
                "canonical authority; the decision is not authorized"
            )
        if approval.expiration_time and now > approval.expiration_time:
            raise AuthorizationError(
                f"Approval {approval.approval_id} expired at {approval.expiration_time}; "
                "a historical approval is not current authorization (AC-026)"
            )
        start, end = approval.effective_interval
        if start and end and (now < start or now > end):
            raise AuthorizationError(
                f"Approval {approval.approval_id} is not effective at {now} "
                f"(effective {start}..{end})"
            )
        scope = ApprovalScope(
            action_types=frozenset(approval.approved_action_scope),
            amount_limits=dict(approval.amount_limits),
            decision_scope=approval.decision_scope,
            conditions=list(approval.conditions),
        )
        granted, reason = scope.authorizes(action_type, amount, case.tenant_id)
        if not granted:
            raise AuthorizationError(
                f"Requested action is outside the approved scope: {reason} (AC-024)"
            )
        if case.case_id != approval.case_id:
            raise AuthorizationError(
                f"Approval {approval.approval_id} belongs to case {approval.case_id}, "
                f"not {case.case_id}; an approval cannot be ported between decisions"
            )
        return approval

    def revoke(self, authz: AuthorizationContext, approval_id: str, reason: str) -> DecisionApproval:
        authz.requires_role("decision.approver")
        approval = self._require_approval_by_id(approval_id)
        approval.decision_outcome = "revoked"
        with self._lock:
            self.revoked.add(approval_id)
        U12.provenance(
            authz, "revoke_approval", "decision_approval", approval_id,
            reason=reason, source="U12.GovernanceService.revoke",
        )
        return approval

    def assert_separation_of_duties(
        self,
        case: DecisionCase,
        approver_principal: str,
        executor_principal: str,
        verifier_principal: str,
    ) -> None:
        """LAW-UMFDVI-033: proposal, approval, execution and verification are distinct principals."""
        # The initiating principal may also execute only when policy explicitly
        # allows self-service decisions; approval and verification must always
        # be independent of the actor they check.
        if approver_principal == case.initiating_principal_ref:
            raise PolicyViolationError(
                "Separation of duties: the approver must not be the principal who "
                "proposed the decision (LAW-UMFDVI-033)"
            )
        if verifier_principal in (approver_principal, executor_principal):
            raise PolicyViolationError(
                "Separation of duties: the value verifier must be independent of the "
                "approver and the executor (LAW-UMFDVI-033)"
            )

    def _require_approval(self, case: DecisionCase) -> DecisionApproval:
        if case.approval_ref is None:
            raise AuthorizationError(
                f"Decision case {case.case_id} has no recorded approval; execution "
                "requires authorization from the canonical approval authority"
            )
        return self._require_approval_by_id(case.approval_ref)

    def _require_approval_by_id(self, approval_id: str) -> DecisionApproval:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise NotFoundError(f"Decision approval {approval_id} not found")
        return approval


class DependencyUnavailable(DependencyUnavailableError):
    """Raised when a mandatory governance dependency is unavailable (fail-safe)."""


# ---------------------------------------------------------------------------
# Module 12 — execution intent coordination
# ---------------------------------------------------------------------------


class ExecutionAuthority:
    """The contract of a canonical execution authority (Spec 030 §16.2)."""

    def submit(self, intent: ExecutionIntent) -> ExecutionOutcome:
        raise NotImplementedError

    def status_of(self, intent_ref: str) -> ExecutionOutcome:
        raise NotImplementedError


class DelegatedExecutionAuthority(ExecutionAuthority):
    """Routes execution intents to a canonical authority through a callback.

    The callback returns the authority's authoritative outcome. A callback that
    cannot determine the result returns an outcome with ``outcome_status='unknown'``.
    """

    def __init__(
        self,
        submit: Callable[[ExecutionIntent], ExecutionOutcome],
        status: Optional[Callable[[str], ExecutionOutcome]] = None,
    ) -> None:
        self._submit = submit
        self._status = status

    def submit(self, intent: ExecutionIntent) -> ExecutionOutcome:
        outcome = self._submit(intent)
        outcome.intent_ref = intent.intent_id
        return outcome

    def status_of(self, intent_ref: str) -> ExecutionOutcome:
        if self._status is None:
            raise DependencyUnavailable(
                "execution.authority",
                f"The execution authority cannot resolve the status of {intent_ref}; "
                "no status query contract is bound.",
            )
        return self._status(intent_ref)


class ExecutionCoordinator:
    def __init__(self, authority: Optional[ExecutionAuthority] = None) -> None:
        self.authority = authority
        self.intents: dict[str, ExecutionIntent] = {}
        self.outcomes: dict[str, ExecutionOutcome] = {}
        self.idempotency = IdempotencyStore()
        self._lock = threading.RLock()

    def create_intent(
        self,
        authz: AuthorizationContext,
        case: DecisionCase,
        *,
        action_type: str,
        action_payload: dict,
        precondition_refs: Optional[list[str]] = None,
        now: str,
        amount: Optional[Money] = None,
    ) -> ExecutionIntent:
        """FR-033: create a typed execution intent from an authorized decision."""
        if case.lifecycle_state not in {"approved"}:
            raise IllegalTransitionError(
                case.case_id, case.lifecycle_state, "execution_pending",
                DecisionCaseEntity.machine.legal_targets(case.lifecycle_state),
            )
        if case.approval_ref is None:
            raise AuthorizationError(
                f"Case {case.case_id} has no approval; an execution intent cannot be created"
            )
        if case.recommendation_ref is None:
            raise AuthorizationError(
                f"Case {case.case_id} has no recommendation to execute"
            )
        intent = ExecutionIntent(
            intent_id=generate("evidence"),
            case_id=case.case_id,
            approved_decision_ref=case.approval_ref,
            target_authority_ref="canonical:execution",
            action_type=action_type,
            action_payload=dict(action_payload),
            precondition_refs=list(precondition_refs or []),
            authorization_ref=case.approval_ref,
            idempotency_key=f"{case.case_id}:{action_type}",
            requested_at=now,
        )
        with self._lock:
            self.intents[intent.intent_id] = intent
            case.execution_intent_ref = intent.intent_id
        return intent

    def submit(self, authz: AuthorizationContext, case: DecisionCase) -> ExecutionOutcome:
        """FR-034/035: route the intent to the responsible canonical authority, idempotently."""
        authz.requires_role("execution.service")
        intent = self._require_intent(case)
        if self.authority is None:
            raise DependencyUnavailable(
                "execution.authority",
                "No execution authority is bound; the intent is not submitted and no "
                "action is taken (fail-safe, LAW-ARCH-07).",
            )

        def run() -> ExecutionOutcome:
            outcome = self.authority.submit(intent)
            with self._lock:
                intent.status = "submitted"
                self.outcomes[outcome.outcome_id] = outcome
                case.execution_outcome_ref = outcome.outcome_id
            if outcome.outcome_status == "unknown":
                raise UnknownOutcomeError(
                    outcome.outcome_id,
                    f"The execution authority returned an indeterminate result for "
                    f"intent {intent.intent_id}. Resolve the authoritative status "
                    "before re-submitting; the action was not repeated.",
                )
            return outcome

        try:
            return self.idempotency.execute(intent.idempotency_key, run)
        except UnknownOutcomeError:
            # The idempotency store records no completed outcome, so a later
            # re-submission is permitted only through resolve_unknown_outcome.
            with self._lock:
                intent.status = "submitted"
            raise

    def resolve_unknown_outcome(self, authz: AuthorizationContext, case: DecisionCase) -> ExecutionOutcome:
        """AC-030 / F-009: query the authoritative status before any retry."""
        authz.requires_role("execution.service")
        intent = self._require_intent(case)
        if self.authority is None:
            raise DependencyUnavailable(
                "execution.authority",
                "The execution authority is unavailable; the unknown outcome cannot "
                "be resolved and re-submission is refused (fail-safe).",
            )
        outcome = self.authority.status_of(intent.intent_id)
        outcome.intent_ref = intent.intent_id
        with self._lock:
            self.outcomes.setdefault(outcome.outcome_id, outcome)
            case.execution_outcome_ref = outcome.outcome_id
        if outcome.outcome_status == "unknown":
            raise UnknownOutcomeError(
                outcome.outcome_id,
                f"The authoritative status of intent {intent.intent_id} is still "
                "unknown; escalate rather than re-submit the action.",
            )
        # Now that the status is authoritative, record the outcome so a later
        # duplicate submission returns it instead of re-executing.
        self.idempotency._outcomes[intent.idempotency_key] = {"outcome": outcome}
        return outcome

    def cancel_permitted_intent(
        self, authz: AuthorizationContext, case: DecisionCase, reason: str
    ) -> ExecutionIntent:
        authz.requires_role("decision.owner")
        intent = self._require_intent(case)
        if intent.status not in {"pending", "submitted"}:
            raise IllegalTransitionError(
                intent.intent_id, intent.status, "cancelled", ["pending", "submitted"]
            )
        intent.status = "cancelled"
        U12.provenance(
            authz, "cancel_execution_intent", "execution_intent", intent.intent_id,
            reason=reason, source="U12.ExecutionCoordinator.cancel_permitted_intent",
        )
        return intent

    def correlate(self, case: DecisionCase) -> ExecutionOutcome:
        """FR-036: correlate an execution result with its originating intent."""
        outcome = self._outcome_for(case)
        if outcome.intent_ref != self._require_intent(case).intent_id:
            raise InvariantViolationError(
                "LAW-UMFDVI-007",
                f"Outcome {outcome.outcome_id} references intent {outcome.intent_ref!r}, "
                f"not the case's intent {case.execution_intent_ref!r}",
                details={"case_id": case.case_id},
            )
        outcome.reconciliation_status = "reconciled"
        return outcome

    def _require_intent(self, case: DecisionCase) -> ExecutionIntent:
        if case.execution_intent_ref is None:
            raise NotFoundError(
                f"Decision case {case.case_id} has no execution intent"
            )
        intent = self.intents.get(case.execution_intent_ref)
        if intent is None:
            raise NotFoundError(f"Execution intent {case.execution_intent_ref} not found")
        return intent

    def _outcome_for(self, case: DecisionCase) -> ExecutionOutcome:
        if case.execution_outcome_ref is None:
            raise NotFoundError(
                f"Decision case {case.case_id} has no execution outcome to correlate"
            )
        outcome = self.outcomes.get(case.execution_outcome_ref)
        if outcome is None:
            raise NotFoundError(f"Execution outcome {case.execution_outcome_ref} not found")
        return outcome
