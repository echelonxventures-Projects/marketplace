"""U12-FDVI domain entities — the universal decision ontology (Spec 030 §4).

Every entity carries a canonical identifier and records provenance. References
are typed so that a DecisionAlternative cannot be confused with a
DecisionEvaluation, and expected values are never stored in realized-value
fields (LAW-UMFDVI-018).

Money-bearing fields use the canonical ``kernel.Money``; there is no float
arithmetic anywhere in the decision path (NFR-024/025, ADR-FDVI-003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from marketplace.kernel import Money, Stateful
from marketplace.kernel.state import StateMachine, Transition, register_machine

# ---------------------------------------------------------------------------
# Lifecycle (Spec 030 §10)
# ---------------------------------------------------------------------------

# A reduced, fully-specified subset of the §10.1 state list covering the
# mandatory path, the failure branches and the recovery branches. Every arc
# names the role that may initiate it (§10.2).
DECISION_MACHINE = register_machine(
    StateMachine(
        name="fdvi.decision",
        concept="decision_case",
        initial_state="draft",
        transitions=(
            # context stage
            Transition("draft", "context_pending", frozenset({"decision.requester"}), event="DecisionCaseCreated"),
            Transition("context_pending", "context_validating", frozenset({"decision.service"}), event="DecisionContextValidating"),
            Transition("context_validating", "context_validated", frozenset({"decision.service"}), event="DecisionContextValidated"),
            Transition("context_validating", "context_rejected", frozenset({"decision.service"}), event="DecisionContextRejected", terminal=True),
            # evidence stage
            Transition("context_validated", "evidence_pending", frozenset({"decision.service"}), event="DecisionEvidenceValidating"),
            Transition("evidence_pending", "evidence_validated", frozenset({"decision.service"}), event="DecisionEvidenceValidated"),
            Transition("evidence_pending", "evidence_stale", frozenset({"decision.service"}), event="DecisionEvidenceInvalidated"),
            Transition("evidence_stale", "evidence_pending", frozenset({"decision.service"}), event="DecisionEvidenceValidating"),
            # constraints stage
            Transition("evidence_validated", "constraints_resolving", frozenset({"decision.service"}), event="DecisionConstraintsResolving"),
            Transition("constraints_resolving", "constraints_validated", frozenset({"decision.service"}), event="DecisionConstraintsResolved"),
            Transition("constraints_resolving", "constraint_conflict", frozenset({"decision.service"}), event="DecisionConstraintConflictDetected"),
            Transition("constraint_conflict", "constraints_resolving", frozenset({"policy.owner"}), event="DecisionConstraintsResolving"),
            # alternatives + evaluation
            Transition("constraints_validated", "alternatives_generating", frozenset({"decision.service"}), event="DecisionAlternativesGenerating"),
            Transition("alternatives_generating", "alternatives_ready", frozenset({"decision.service"}), event="DecisionAlternativesGenerated"),
            Transition("alternatives_ready", "evaluating", frozenset({"decision.service"}), event="DecisionAlternativeEvaluated"),
            Transition("evaluating", "evaluated", frozenset({"decision.service"}), event="DecisionAlternativeEvaluated"),
            Transition("alternatives_ready", "infeasible", frozenset({"decision.service"}), event="DecisionAlternativeRejected", terminal=True),
            # optimization
            Transition("evaluated", "optimizing", frozenset({"decision.service"}), event="OptimizationRunStarted"),
            Transition("optimizing", "optimized", frozenset({"decision.service"}), event="OptimizationRunCompleted"),
            Transition("optimizing", "optimization_failed", frozenset({"decision.service"}), event="OptimizationRunFailed"),
            Transition("optimization_failed", "alternatives_generating", frozenset({"decision.owner"}), event="DecisionAlternativesGenerating"),
            # recommendation + approval
            Transition("optimized", "recommended", frozenset({"decision.service"}), event="DecisionRecommendationCreated"),
            Transition("evaluated", "recommended", frozenset({"decision.service"}), event="DecisionRecommendationCreated"),
            Transition("recommended", "approval_pending", frozenset({"decision.service"}), event="DecisionApprovalRequested"),
            Transition("approval_pending", "approved", frozenset({"decision.approver"}), event="DecisionApprovalGranted"),
            Transition("approval_pending", "rejected", frozenset({"decision.approver"}), event="DecisionApprovalRejected", terminal=True),
            Transition("recommended", "expired", frozenset({"decision.service"}), event="DecisionRecommendationExpired", terminal=True),
            Transition("approved", "expired", frozenset({"decision.service"}), event="DecisionApprovalExpired", terminal=True),
            Transition("approval_pending", "expired", frozenset({"decision.service"}), event="DecisionApprovalExpired", terminal=True),
            # execution coordination
            Transition("approved", "execution_pending", frozenset({"decision.service"}), event="ExecutionIntentCreated"),
            Transition("execution_pending", "execution_requested", frozenset({"execution.service"}), event="ExecutionIntentSubmitted"),
            Transition("execution_requested", "execution_succeeded", frozenset({"execution.authority"}), event="ExecutionOutcomeConfirmed"),
            Transition("execution_requested", "execution_failed", frozenset({"execution.authority"}), event="ExecutionOutcomeFailed", terminal=True),
            Transition("execution_requested", "execution_outcome_unknown", frozenset({"execution.authority"}), event="ExecutionOutcomeUnknown"),
            Transition("execution_outcome_unknown", "execution_requested", frozenset({"execution.service"}), event="ExecutionIntentSubmitted"),
            Transition("execution_outcome_unknown", "execution_failed", frozenset({"execution.authority"}), event="ExecutionOutcomeFailed", terminal=True),
            # outcome + value
            Transition("execution_succeeded", "outcome_measuring", frozenset({"decision.service"}), event="DecisionOutcomeMeasured"),
            Transition("execution_succeeded", "outcome_measurement_pending", frozenset({"decision.service"}), event="DecisionOutcomeMeasured"),
            Transition("outcome_measurement_pending", "outcome_measuring", frozenset({"decision.service"}), event="DecisionOutcomeMeasured"),
            Transition("outcome_measuring", "outcome_measured", frozenset({"decision.service"}), event="DecisionOutcomeMeasured"),
            Transition("outcome_measured", "value_attribution_pending", frozenset({"decision.service"}), event="ValueAttributionEstimated"),
            Transition("value_attribution_pending", "value_attributed", frozenset({"decision.service"}), event="ValueRealizationRecorded"),
            # review + terminal
            Transition("value_attributed", "under_review", frozenset({"decision.reviewer"}), event="DecisionReviewRequested"),
            Transition("under_review", "completed", frozenset({"decision.reviewer"}), event="DecisionReviewCompleted", terminal=True),
            Transition("value_attributed", "completed", frozenset({"decision.owner"}), event="DecisionReviewCompleted", terminal=True),
            # lifecycle
            Transition("draft", "cancelled", frozenset({"decision.requester"}), event="DecisionCaseCancelled", terminal=True),
            Transition("context_pending", "cancelled", frozenset({"decision.requester"}), event="DecisionCaseCancelled", terminal=True),
            Transition("recommended", "superseded", frozenset({"decision.owner"}), event="DecisionSuperseded", terminal=True),
            Transition("approved", "superseded", frozenset({"decision.owner"}), event="DecisionSuperseded", terminal=True),
        ),
        terminal_states=frozenset({
            "completed", "cancelled", "superseded", "rejected", "expired",
            "context_rejected", "infeasible", "execution_failed",
        }),
    )
)


class DecisionCaseEntity(Stateful):
    machine = DECISION_MACHINE

    def __init__(self, case_id: str) -> None:
        super().__init__(case_id)
        self.case_id = case_id


# ---------------------------------------------------------------------------
# A. DecisionContext
# ---------------------------------------------------------------------------


@dataclass
class DecisionContext:
    context_id: str
    case_id: str
    organizational_scope: str
    commercial_scope: str
    temporal_scope: str
    geographic_scope: str
    regulatory_scope: str
    tenant_id: str
    applicable_policy_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    context_version: int = 1
    validity_status: str = "draft"  # draft | validated | rejected
    created_at: str = ""
    missing: list[str] = field(default_factory=list)
    provenance_refs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# B. DecisionObjective
# ---------------------------------------------------------------------------


@dataclass
class DecisionObjective:
    objective_id: str
    code: str
    objective_type: str
    owner_ref: str
    definition_ref: str
    desired_direction: str  # maximize | minimize | target | reduce | maintain
    measurement_definition_ref: str
    target_value: Optional[Decimal]
    target_interval: Optional[tuple[Decimal, Decimal]]
    time_horizon: str
    priority: int
    mandatory: bool
    unit: str
    currency: Optional[str] = None
    stakeholder_scope: str = ""
    constraint_refs: list[str] = field(default_factory=list)
    version: int = 1


# ---------------------------------------------------------------------------
# C / D. DecisionCase and DecisionAlternative
# ---------------------------------------------------------------------------


@dataclass
class DecisionCase:
    case_id: str
    decision_type: str
    tenant_id: str
    initiating_principal_ref: str
    correlation_id: str
    idempotency_key: str
    context_ref: Optional[str] = None
    objective_refs: list[str] = field(default_factory=list)
    constraint_set_ref: Optional[str] = None
    evidence_bundle_ref: Optional[str] = None
    alternatives_ref: list[str] = field(default_factory=list)
    recommendation_ref: Optional[str] = None
    approval_ref: Optional[str] = None
    execution_intent_ref: Optional[str] = None
    execution_outcome_ref: Optional[str] = None
    value_realization_ref: Optional[str] = None
    review_ref: Optional[str] = None
    lifecycle_state: str = "draft"
    version: int = 1
    supersedes_ref: Optional[str] = None
    superseded_by_ref: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class DecisionAlternative:
    alternative_id: str
    case_id: str
    alternative_type: str
    action_intent: str
    required_preconditions: list[str] = field(default_factory=list)
    expected_effects: dict = field(default_factory=dict)
    estimated_costs: dict[str, Money] = field(default_factory=dict)
    estimated_benefits: dict[str, Money] = field(default_factory=dict)
    risk_refs: list[str] = field(default_factory=list)
    dependency_refs: list[str] = field(default_factory=list)
    reversibility_class: str = "partially_reversible"
    feasibility_status: str = "unknown"  # feasible | infeasible | unknown | unverified
    feasibility_reasons: list[str] = field(default_factory=list)
    evaluation_result_ref: Optional[str] = None
    version: int = 1


# ---------------------------------------------------------------------------
# E. DecisionConstraint
# ---------------------------------------------------------------------------


@dataclass
class DecisionConstraint:
    constraint_id: str
    code: str
    constraint_type: str  # legal | contractual | security | financial | operational | policy | risk | fairness
    semantic_definition_ref: str
    owner_ref: str
    mandatory: bool
    scope: str
    evaluation_method_ref: str
    effective_interval: tuple[str, str]
    source_policy_ref: str
    exception_policy_ref: Optional[str] = None
    version: int = 1


# ---------------------------------------------------------------------------
# F. DecisionEvaluation
# ---------------------------------------------------------------------------


@dataclass
class DecisionEvaluation:
    evaluation_id: str
    case_id: str
    alternative_ref: str
    evidence_snapshot_ref: str
    objective_snapshot_refs: list[str]
    constraint_snapshot_refs: list[str]
    model_refs: list[str] = field(default_factory=list)
    policy_refs: list[str] = field(default_factory=list)
    evaluation_method_ref: str = ""
    feasibility_result: str = "unknown"
    feasibility_reasons: list[str] = field(default_factory=list)
    objective_values: dict[str, Decimal] = field(default_factory=dict)
    financial_effects: dict[str, Money] = field(default_factory=dict)
    uncertainty_summary: dict = field(default_factory=dict)
    explanation_ref: Optional[str] = None
    computed_at: str = ""
    evaluation_version: int = 1


# ---------------------------------------------------------------------------
# G. OptimizationRun
# ---------------------------------------------------------------------------


@dataclass
class OptimizationRun:
    run_id: str
    case_id: str
    objective_set_ref: str
    constraint_set_ref: str
    search_method_ref: str
    algorithm_version: str
    resource_budget: dict = field(default_factory=dict)
    termination_reason: str = ""  # completed | resource_limit | cancelled | no_feasible | failed
    candidate_count: int = 0
    feasible_candidate_count: int = 0
    solution_status: str = "unknown"  # optimal | feasible | heuristic | infeasible | unknown
    optimality_claim: str = "unknown"  # proven_optimal | approximately_optimal | heuristic | incomplete | infeasible | unknown
    limitation_summary: list[str] = field(default_factory=list)
    selected_alternative_ref: Optional[str] = None
    started_at: str = ""
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# H. DecisionRecommendation
# ---------------------------------------------------------------------------


@dataclass
class DecisionRecommendation:
    recommendation_id: str
    case_id: str
    recommended_alternative_ref: str
    supporting_evaluation_refs: list[str] = field(default_factory=list)
    expected_outcome_summary: dict = field(default_factory=dict)
    confidence_summary: dict = field(default_factory=dict)
    material_tradeoffs: list[str] = field(default_factory=list)
    rejected_alternative_refs: list[str] = field(default_factory=list)
    rejection_reasons: dict[str, str] = field(default_factory=dict)
    recommendation_reason: str = ""
    expiration_time: str = ""
    review_requirements: list[str] = field(default_factory=list)
    status: str = "active"  # active | expired | superseded | revalidated
    created_at: str = ""

    def is_expired(self, now: str) -> bool:
        return bool(self.expiration_time) and now > self.expiration_time


# ---------------------------------------------------------------------------
# I. DecisionApproval
# ---------------------------------------------------------------------------


@dataclass
class DecisionApproval:
    approval_id: str
    case_id: str
    recommendation_ref: str
    approving_authority_ref: str
    approving_principal_ref: str
    authority_evidence_ref: str
    decision_scope: str
    approved_action_scope: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    effective_interval: tuple[str, str] = ("", "")
    expiration_time: str = ""
    decision_outcome: str = "pending"  # pending | granted | rejected | revoked | expired
    amount_limits: dict[str, Money] = field(default_factory=dict)
    recorded_at: str = ""


# ---------------------------------------------------------------------------
# J / K. ExecutionIntent and ExecutionOutcome
# ---------------------------------------------------------------------------


@dataclass
class ExecutionIntent:
    intent_id: str
    case_id: str
    approved_decision_ref: str
    target_authority_ref: str
    action_type: str
    action_payload: dict
    precondition_refs: list[str] = field(default_factory=list)
    authorization_ref: Optional[str] = None
    idempotency_key: str = ""
    requested_at: str = ""
    status: str = "pending"  # pending | submitted | accepted | superseded | cancelled


@dataclass
class ExecutionOutcome:
    outcome_id: str
    intent_ref: str
    execution_authority_ref: str
    external_execution_ref: Optional[str] = None
    outcome_status: str = "unknown"  # succeeded | failed | unknown
    authoritative_evidence_refs: list[str] = field(default_factory=list)
    failure_reason: Optional[str] = None
    occurred_at: str = ""
    recorded_at: str = ""
    reconciliation_status: str = "unreconciled"  # unreconciled | reconciled


# ---------------------------------------------------------------------------
# L / M. ValueHypothesis and ValueRealizationRecord
# ---------------------------------------------------------------------------


@dataclass
class ValueHypothesis:
    hypothesis_id: str
    case_id: str
    hypothesis_statement: str
    baseline_definition_ref: str
    target_outcome_definition_ref: str
    measurement_method_ref: str
    assumptions: list[str] = field(default_factory=list)
    confounder_refs: list[str] = field(default_factory=list)
    measurement_window: tuple[str, str] = ("", "")
    attribution_method_ref: Optional[str] = None
    owner_ref: str = ""
    status: str = "not_defined"


@dataclass
class BaselineDefinition:
    baseline_id: str
    case_id: str
    method: str  # historical | budget | matched_comparison | randomized | difference_in_differences | counterfactual | expert_review
    metric: str
    unit: str
    currency: Optional[str]
    value: Decimal
    measurement_window: tuple[str, str]
    evidence_refs: list[str] = field(default_factory=list)
    approved_by: Optional[str] = None
    locked_at: Optional[str] = None


@dataclass
class ValueRealizationRecord:
    realization_id: str
    case_id: str
    value_hypothesis_ref: str
    baseline_snapshot_ref: str
    observed_outcome_refs: list[str] = field(default_factory=list)
    observed_value: Optional[Money] = None
    gross_effect_estimate: Optional[Money] = None
    incremental_effect_estimate: Optional[Money] = None
    attributable_effect_estimate: Optional[Money] = None
    cost_of_action: Optional[Money] = None
    net_effect_estimate: Optional[Money] = None
    uncertainty_summary: dict = field(default_factory=dict)
    attribution_method_ref: str = ""
    attribution_category: str = "insufficient_evidence"
    confidence_level: str = "low"
    measurement_window: tuple[str, str] = ("", "")
    reviewer_ref: Optional[str] = None
    status: str = "not_defined"


# ---------------------------------------------------------------------------
# N. DecisionReview
# ---------------------------------------------------------------------------


@dataclass
class DecisionReview:
    review_id: str
    case_id: str
    review_type: str  # outcome | policy_deviation | drift | dispute | periodic
    review_trigger: str
    evidence_refs: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    policy_deviations: list[str] = field(default_factory=list)
    outcome_assessment: str = ""
    corrective_action_refs: list[str] = field(default_factory=list)
    reviewer_ref: str = ""
    review_status: str = "open"  # open | completed
    created_at: str = ""


# ---------------------------------------------------------------------------
# O. DecisionModelVersion
# ---------------------------------------------------------------------------


@dataclass
class DecisionModelVersion:
    model_version_id: str
    model_type: str  # solver | evaluator | forecaster | classifier | heuristic
    semantic_purpose: str
    implementation_ref: str
    version: str
    validation_status: str = "pending"  # pending | validated | failed | retired
    applicable_context: str = ""
    limitations: list[str] = field(default_factory=list)
    approval_ref: Optional[str] = None
    effective_interval: tuple[str, str] = ("", "")
    retirement_status: str = "active"  # active | retired | review_required


# ---------------------------------------------------------------------------
# P. DecisionEvidence
# ---------------------------------------------------------------------------


@dataclass
class DecisionEvidence:
    evidence_id: str
    evidence_type: str
    source_authority_ref: str
    source_artifact_ref: str
    source_version: int
    evidence_status: str = "valid"  # valid | stale | invalid | disputed | estimated
    provenance: str = ""
    observation_time: str = ""
    ingestion_time: str = ""
    validity_interval: tuple[str, str] = ("", "")
    freshness_status: str = "current"  # current | stale | unknown
    integrity_metadata: dict = field(default_factory=dict)
    access_classification: str = "internal"  # public | internal | personal | sensitive | financial
    payload: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Q. DecisionPolicySnapshot
# ---------------------------------------------------------------------------


@dataclass
class DecisionPolicySnapshot:
    snapshot_id: str
    case_id: str
    policy_refs: list[str] = field(default_factory=list)
    policy_versions: dict[str, int] = field(default_factory=dict)
    authority_refs: list[str] = field(default_factory=list)
    evaluation_time: str = ""
    scope: str = ""
    precedence_resolution: list[str] = field(default_factory=list)
    conflict_status: str = "none"  # none | detected | resolved | escalated


# Concept ownership (GOV-02): U12 is the single write authority for the
# decision-intelligence concepts introduced by Spec 030 (ADR-FDVI-001).
from marketplace.universes._base import U12  # noqa: E402

U12.owns(
    "decision_case",
    "Consequential decision lifecycle: context, evidence, constraints, "
    "alternatives, recommendation, approval, execution and outcome",
    "U12.GovernanceService.create_intent",
    read_projections=["U14.policy", "U01.delegation"],
)
U12.owns(
    "decision_constraint",
    "Mandatory or soft constraint resolved from authoritative policy sources",
    "U12.ConstraintRegistry.register",
    read_projections=["U14.policy"],
)
