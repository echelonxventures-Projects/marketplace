"""U12-FDVI review, learning and corrective action (Spec 030 Module 15; FR-043..045).

Module 15 responsibilities:

* compare expected and observed results;
* identify decision errors and failed assumptions;
* detect model or policy drift;
* create corrective action proposals;
* recommend model or policy review;
* route material findings to authorized owners.

The module MUST NOT automatically alter production policies or authorization
boundaries (Spec 030 §7.1 Module 15). A corrective action is therefore a
*governed proposal*: it records what should change and routes it to the owner
who is authorized to change it. No proposal is ever applied in place.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from marketplace.kernel import AuthorizationContext, Money, generate
from marketplace.kernel.errors import (
    AuthorizationError,
    InvariantViolationError,
    NotFoundError,
    PolicyViolationError,
    ValidationError,
)
from marketplace.universes._base import U12
from marketplace.universes.u12_intelligence.entities import (
    DecisionEvaluation,
    DecisionReview,
    ValueRealizationRecord,
)

# The severity of a finding, per Spec 030 §13 review semantics.
SEVERITIES = frozenset({"low", "medium", "high", "critical"})

# What kind of thing the proposal asks its owner to change.
TARGET_KINDS = frozenset(
    {
        "model",  # retrain, revalidate or retire a model version
        "policy",  # amend a constraint, objective or approval policy
        "constraint",  # correct a constraint definition or its effective interval
        "objective",  # correct measurement semantics or targets
        "process",  # change how a decision stage is performed
        "evidence",  # repair or refresh an evidence source
    }
)

# Where a proposal stands in its governed lifecycle.
PROPOSAL_STATUSES = frozenset(
    {
        "proposed",  # created, awaiting the target owner
        "accepted",  # the owner accepted it for remediation
        "rejected",  # the owner declined it, with a recorded reason
        "implemented",  # the owner reports the change is live
        "withdrawn",  # the proposer withdrew it
    }
)


@dataclass
class ReviewFinding:
    """One discrepancy between expectation and observation."""

    finding_id: str
    code: str
    description: str
    severity: str = "medium"  # low | medium | high | critical
    expected: str = ""
    observed: str = ""
    evidence_ref: str = ""
    assumption_ref: str = ""  # the failed assumption, when one is implicated
    model_ref: str = ""  # the implicated model version, for drift findings
    owner_ref: str = ""


@dataclass
class CorrectiveActionProposal:
    """A governed request to change a model, policy, constraint or process.

    Created by review, routed to the owner, never self-applied. The proposal
    records the gap and the requested change; the owner decides.
    """

    proposal_id: str
    review_id: str
    case_id: str
    target_kind: str  # model | policy | constraint | objective | process | evidence
    target_ref: str  # the canonical id of the artifact to change
    target_owner_ref: str  # the only principal authorized to apply it
    requested_change: str
    rationale: str
    severity: str = "medium"
    status: str = "proposed"
    finding_refs: list[str] = field(default_factory=list)
    accepted_by: str = ""
    acceptance_note: str = ""
    implemented_at: str = ""
    created_at: str = ""


@dataclass
class ReviewPolicy:
    """Which deviations are severe enough to require a review (AC-038)."""

    material_gap_threshold: Decimal = Decimal("0.05")
    require_finding_for_critical: bool = True


class ReviewService:
    """Module 15: compare expectation to observation and open governed reviews."""

    def __init__(self, policy: Optional[ReviewPolicy] = None) -> None:
        self.policy = policy or ReviewPolicy()
        self.reviews: dict[str, DecisionReview] = {}
        self.findings: dict[str, ReviewFinding] = {}
        self._proposals: dict[str, CorrectiveActionProposal] = {}
        self._lock = threading.RLock()

    def compare(
        self,
        authz: AuthorizationContext,
        *,
        case_id: str,
        review_type: str,
        review_trigger: str,
        evaluation: Optional[DecisionEvaluation] = None,
        realization: Optional[ValueRealizationRecord] = None,
        evidence_refs: Optional[list[str]] = None,
        reviewer_ref: str = "",
    ) -> DecisionReview:
        """FR-043/044: compare expected and observed outcomes and open a review."""
        authz.requires_role("decision.reviewer")
        if evaluation is None and realization is None:
            raise ValidationError(
                "A review comparison requires either the evaluation record "
                "(expected) or the value realization record (observed)"
            )
        findings: list[ReviewFinding] = []
        if evaluation is not None and realization is not None:
            findings.extend(
                self._financial_findings(case_id, evaluation, realization)
            )
        review = DecisionReview(
            review_id=generate("evidence"),
            case_id=case_id,
            review_type=review_type,
            review_trigger=review_trigger,
            evidence_refs=list(evidence_refs or []),
            findings=[f.finding_id for f in findings],
            policy_deviations=[],
            outcome_assessment=self._assess(findings),
            corrective_action_refs=[],
            reviewer_ref=reviewer_ref or authz.actor_id,
            review_status="open",
            created_at=U12.now(),
        )
        with self._lock:
            self.reviews[review.review_id] = review
            for finding in findings:
                self.findings[finding.finding_id] = finding
        return review

    def _financial_findings(
        self,
        case_id: str,
        evaluation: DecisionEvaluation,
        realization: ValueRealizationRecord,
    ) -> list[ReviewFinding]:
        """FR-044: compare expected financial effects against realized value."""
        findings: list[ReviewFinding] = []
        expected_total = self._expected_total(evaluation)
        observed = realization.observed_value
        if expected_total is None or observed is None:
            return findings
        if expected_total.currency != observed.currency:
            findings.append(
                ReviewFinding(
                    finding_id=generate("evidence"),
                    code="currency.mismatch",
                    description=(
                        "The expected and observed values are in different "
                        "currencies; the comparison requires an approved "
                        "conversion before any variance is reported"
                    ),
                    severity="high",
                    expected=str(expected_total),
                    observed=str(observed),
                )
            )
            return findings
        gap = observed.amount - expected_total.amount
        if expected_total.amount != 0:
            relative = abs(gap) / abs(expected_total.amount)
            if relative >= self.policy.material_gap_threshold:
                findings.append(
                    ReviewFinding(
                        finding_id=generate("evidence"),
                        code="value.gap",
                        description=(
                            f"Observed value differs from the expected value by "
                            f"{relative:.1%}, above the material gap threshold"
                        ),
                        severity="high" if relative >= Decimal("0.2") else "medium",
                        expected=str(expected_total),
                        observed=str(observed),
                        evidence_ref=realization.realization_id,
                    )
                )
        return findings

    @staticmethod
    def _expected_total(evaluation: DecisionEvaluation) -> Optional[Money]:
        effects = list(evaluation.financial_effects.values())
        if not effects:
            return None
        from marketplace.kernel.money import sum_money

        return sum_money(effects)

    @staticmethod
    def _assess(findings: list[ReviewFinding]) -> str:
        if not findings:
            return "no material deviation detected"
        severities = {f.severity for f in findings}
        if "critical" in severities:
            return "critical deviation; corrective action required"
        if "high" in severities:
            return "material deviation; corrective action recommended"
        return "minor deviation recorded"

    def add_finding(
        self, authz: AuthorizationContext, review_id: str, finding: ReviewFinding
    ) -> ReviewFinding:
        """Attach a finding (for example a drift signal) to an open review."""
        authz.requires_role("decision.reviewer")
        review = self._require_review(review_id)
        if finding.severity not in SEVERITIES:
            raise ValidationError(
                f"Finding severity {finding.severity!r} is not one of {sorted(SEVERITIES)}"
            )
        finding.finding_id = finding.finding_id or generate("evidence")
        with self._lock:
            self.findings[finding.finding_id] = finding
            if finding.finding_id not in review.findings:
                review.findings.append(finding.finding_id)
        return finding

    def complete(self, authz: AuthorizationContext, review_id: str) -> DecisionReview:
        """Close a review. Material findings must first be routed to an owner."""
        authz.requires_role("decision.reviewer")
        review = self._require_review(review_id)
        unactioned = [
            self.findings[f].code
            for f in review.findings
            if f in self.findings
            and self.findings[f].severity in {"high", "critical"}
            and not self._has_proposal(review_id, self.findings[f].finding_id)
        ]
        if unactioned:
            raise PolicyViolationError(
                "Material findings must be routed to their owner before the "
                "review closes (FR-045); unactioned: "
                f"{unactioned}",
                details={"review_id": review_id, "unactioned": unactioned},
            )
        review.review_status = "completed"
        return review

    def _has_proposal(self, review_id: str, finding_id: str) -> bool:
        return any(
            p.review_id == review_id and finding_id in p.finding_refs
            for p in self._proposals.values()
        )

    def _require_review(self, review_id: str) -> DecisionReview:
        review = self.reviews.get(review_id)
        if review is None:
            raise NotFoundError(f"Decision review {review_id} not found")
        return review

    # -- corrective action proposals (FR-045) ------------------------------

    def propose_corrective_action(
        self,
        authz: AuthorizationContext,
        *,
        review_id: str,
        case_id: str,
        target_kind: str,
        target_ref: str,
        target_owner_ref: str,
        requested_change: str,
        rationale: str,
        severity: str = "medium",
        finding_refs: Optional[list[str]] = None,
    ) -> CorrectiveActionProposal:
        """FR-045: create a governed corrective action proposal.

        The proposal is routed to ``target_owner_ref``. Review never applies the
        change itself, so a policy or model cannot be altered by the learning
        loop (Spec 030 §7.1 Module 15).
        """
        authz.requires_role("decision.reviewer")
        review = self._require_review(review_id)
        if target_kind not in TARGET_KINDS:
            raise ValidationError(
                f"Corrective action target kind {target_kind!r} is not one of "
                f"{sorted(TARGET_KINDS)}"
            )
        if severity not in SEVERITIES:
            raise ValidationError(
                f"Severity {severity!r} is not one of {sorted(SEVERITIES)}"
            )
        if not target_owner_ref:
            raise ValidationError(
                "A corrective action must name the owner authorized to apply it; "
                "it cannot be routed to nobody"
            )
        proposal = CorrectiveActionProposal(
            proposal_id=generate("evidence"),
            review_id=review_id,
            case_id=case_id,
            target_kind=target_kind,
            target_ref=target_ref,
            target_owner_ref=target_owner_ref,
            requested_change=requested_change,
            rationale=rationale,
            severity=severity,
            status="proposed",
            finding_refs=list(finding_refs or []),
            created_at=U12.now(),
        )
        with self._lock:
            self._proposals[proposal.proposal_id] = proposal
            review.corrective_action_refs.append(proposal.proposal_id)
        U12.provenance(
            authz,
            "propose_corrective_action",
            "corrective_action_proposal",
            proposal.proposal_id,
            reason=rationale,
            source="U12.ReviewService.propose_corrective_action",
            correlation_id=case_id,
        )
        return proposal

    def get_proposal(self, proposal_id: str) -> CorrectiveActionProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise NotFoundError(f"Corrective action proposal {proposal_id} not found")
        return proposal

    def proposals_for_review(self, review_id: str) -> list[CorrectiveActionProposal]:
        return [p for p in self._proposals.values() if p.review_id == review_id]

    def accept(
        self,
        authz: AuthorizationContext,
        proposal_id: str,
        *,
        note: str = "",
    ) -> CorrectiveActionProposal:
        """The target owner accepts a proposal for remediation.

        Only the named owner may accept; the proposer cannot self-apply it,
        which keeps proposal and remediation in separate hands (LAW-UMFDVI-033).
        """
        proposal = self.get_proposal(proposal_id)
        if authz.actor_id != proposal.target_owner_ref:
            raise AuthorizationError(
                f"Corrective action {proposal_id} is owned by "
                f"{proposal.target_owner_ref!r}; {authz.actor_id!r} may not "
                "accept it"
            )
        if proposal.status != "proposed":
            raise ValidationError(
                f"Proposal {proposal_id} is {proposal.status!r}, not 'proposed'"
            )
        proposal.status = "accepted"
        proposal.accepted_by = authz.actor_id
        proposal.acceptance_note = note
        return proposal

    def reject(
        self,
        authz: AuthorizationContext,
        proposal_id: str,
        *,
        reason: str,
    ) -> CorrectiveActionProposal:
        """The target owner declines a proposal, recording why (GOV-08)."""
        proposal = self.get_proposal(proposal_id)
        if authz.actor_id != proposal.target_owner_ref:
            raise AuthorizationError(
                f"Corrective action {proposal_id} is owned by "
                f"{proposal.target_owner_ref!r}; {authz.actor_id!r} may not "
                "reject it"
            )
        if proposal.status != "proposed":
            raise ValidationError(
                f"Proposal {proposal_id} is {proposal.status!r}, not 'proposed'"
            )
        if not reason:
            raise ValidationError("Rejecting a corrective action requires a reason")
        proposal.status = "rejected"
        proposal.accepted_by = authz.actor_id
        proposal.acceptance_note = reason
        return proposal

    def mark_implemented(
        self,
        authz: AuthorizationContext,
        proposal_id: str,
        *,
        implemented_at: str,
    ) -> CorrectiveActionProposal:
        """The owner reports the accepted change is live."""
        proposal = self.get_proposal(proposal_id)
        if authz.actor_id != proposal.target_owner_ref:
            raise AuthorizationError(
                f"Corrective action {proposal_id} is owned by "
                f"{proposal.target_owner_ref!r}; {authz.actor_id!r} may not "
                "mark it implemented"
            )
        if proposal.status != "accepted":
            raise ValidationError(
                f"Proposal {proposal_id} is {proposal.status!r}; only an accepted "
                "proposal can be marked implemented"
            )
        if not implemented_at:
            raise ValidationError("Implementation requires a timestamp")
        proposal.status = "implemented"
        proposal.implemented_at = implemented_at
        return proposal

    def withdraw(
        self, authz: AuthorizationContext, proposal_id: str
    ) -> CorrectiveActionProposal:
        """The original proposer withdraws a proposal before it is accepted."""
        proposal = self.get_proposal(proposal_id)
        review = self._require_review(proposal.review_id)
        if authz.actor_id != review.reviewer_ref:
            raise AuthorizationError(
                f"Only the reviewer of {proposal.review_id!r} may withdraw its "
                "proposals"
            )
        if proposal.status not in {"proposed", "rejected"}:
            raise ValidationError(
                f"Proposal {proposal_id} is {proposal.status!r}; only a proposed "
                "or rejected proposal can be withdrawn"
            )
        proposal.status = "withdrawn"
        return proposal

    def assert_no_self_applied_proposal(
        self, proposal: CorrectiveActionProposal
    ) -> None:
        """The learning loop never mutates production policy in place."""
        if proposal.status == "implemented" and proposal.target_kind in {
            "policy",
            "constraint",
        }:
            # Implementation is reported by the owner; the record itself must
            # still show who applied it, and it must not be the reviewer.
            if proposal.accepted_by and proposal.accepted_by == proposal.target_owner_ref:
                return
            raise InvariantViolationError(
                "LAW-UMFDVI-024",
                f"Proposal {proposal.proposal_id} reports an implemented policy "
                "change without a recorded owner; learning must not alter "
                "production policy unattended",
                details={"proposal_id": proposal.proposal_id},
            )


# Concept ownership (GOV-02): U12 is the single write authority for the
# corrective-action concept introduced by Spec 030 Module 15.
U12.owns(
    "corrective_action_proposal",
    "A governed request to change a model, policy, constraint or process, "
    "routed to the artifact's owner and never self-applied by review",
    "U12.ReviewService.propose_corrective_action",
    read_projections=["U14.policy", "U12.model_registration"],
)
