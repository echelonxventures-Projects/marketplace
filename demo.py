#!/usr/bin/env python3
"""UCOS Omega — FDVI local demo.

Runs the full Financial Decision Governance, Optimization & Value Realization
Intelligence lifecycle end-to-end, in one process, with no network and no
credentials:

    register a decision -> objectives + constraints -> generate alternatives
    -> feasibility + financial evaluation -> scenarios + sensitivity
    -> recommendation -> approval -> execution -> value realization
    -> review + corrective action -> observability indicators

Every step prints the records it produced. Nothing here is mocked beyond the
two delegated authority callbacks, which are the documented integration points
to the canonical approval and execution capabilities (U14/U09): in a real
deployment those call the real authorities.

Run with:  python3 demo.py
"""

from __future__ import annotations

import textwrap
from decimal import Decimal
from typing import Optional

from marketplace.kernel import AuthorizationContext, Money
from marketplace.kernel.errors import IllegalTransitionError
from marketplace.universes._base import U12
from marketplace.universes.u12_intelligence.constraints import ConstraintRegistry
from marketplace.universes.u12_intelligence.entities import (
    DecisionAlternative,
    DecisionCase,
    DecisionCaseEntity,
    DecisionRecommendation,
    ExecutionOutcome,
    ValueRealizationRecord,
)
from marketplace.universes.u12_intelligence.evaluation import (
    EvaluationService,
    FeasibilityGate,
    ModelVersionRegistry,
)
from marketplace.universes.u12_intelligence.governance import (
    DelegatedApprovalAuthority,
    DelegatedExecutionAuthority,
    ExecutionCoordinator,
    GovernanceService,
)
from marketplace.universes.u12_intelligence.monitoring import ObservabilityService
from marketplace.universes.u12_intelligence.objectives import ObjectiveRegistry
from marketplace.universes.u12_intelligence.review import ReviewService
from marketplace.universes.u12_intelligence.scenarios import (
    ScenarioEvaluationService,
    ScenarioOutcome,
    ScenarioRegistry,
    SensitivityService,
)

NOW = "2026-01-01T00:00:00+00:00"
THEN = "2026-06-01T00:00:00+00:00"
CURRENCY = "USD"


# ---------------------------------------------------------------------------
# Actors — the demo's principals. Separation of duties (LAW-UMFDVI-033) means
# these must be distinct people: the proposer cannot approve their own request.
# ---------------------------------------------------------------------------
PROPOSER = AuthorizationContext(
    actor_id="ada.lovelace",
    actor_type="person",
    roles=frozenset(
        {
            "decision.requester",
            "decision.owner",
            "decision.service",
            "scenario.writer",
            "objective.writer",
            "constraint.writer",
            "model.governance",
            "observability.writer",
        }
    ),
    tenant_id="tenant-A",
)

APPROVER = AuthorizationContext(
    actor_id="grace.hopper",
    actor_type="person",
    roles=frozenset({"decision.approver"}),
    tenant_id="tenant-A",
)

EXECUTOR = AuthorizationContext(
    actor_id="checkout.service",
    actor_type="service",
    # Creating and submitting an intent needs decision.service and
    # execution.service; confirming the outcome is reserved for the execution
    # authority, which this delegated callback represents in the demo.
    roles=frozenset({"execution.service", "decision.service", "execution.authority"}),
    tenant_id="tenant-A",
)

REVIEWER = AuthorizationContext(
    actor_id="review.board",
    actor_type="person",
    roles=frozenset({"decision.reviewer", "observability.reader"}),
    tenant_id="tenant-A",
)

MODEL_OWNER = AuthorizationContext(
    actor_id="model.governance",
    actor_type="service",
    roles=frozenset({"decision.reviewer"}),
    tenant_id="tenant-A",
)


def banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def note(text: str) -> None:
    print(textwrap.fill(text, width=72, initial_indent="  ", subsequent_indent="  "))


def money(amount) -> Money:
    return Money.of(Decimal(str(amount)), CURRENCY)


def actor_with_roles(actor: AuthorizationContext, roles: frozenset[str]) -> AuthorizationContext:
    return AuthorizationContext(
        actor_id=actor.actor_id,
        actor_type=actor.actor_type,
        roles=roles,
        tenant_id=actor.tenant_id,
    )


def advance(entity: DecisionCaseEntity, to_state: str, actor: AuthorizationContext, event: str, case: Optional[DecisionCase] = None) -> None:
    """Move the decision lifecycle machine one step, printing the transition."""
    legal = entity.machine.legal_targets(entity.state)
    if to_state not in legal:
        raise IllegalTransitionError(entity.entity_id, entity.state, to_state, sorted(legal))
    from_state = entity.state
    entity.transition_to(
        to_state,
        actor=actor.actor_id,
        actor_roles=set(actor.roles),
        reason=event,
        recorded_at=U12.now(),
    )
    if case is not None:
        case.lifecycle_state = to_state
        case.updated_at = U12.now()
    print(f"    lifecycle: {from_state} -> {entity.state}  ({event})")


def main() -> None:
    banner("UCOS Omega — FDVI local demo")
    note(
        "A merchant wants a 10% discount on a product line. The platform must "
        "decide whether the discount is affordable under its margin policy, "
        "evaluate it under upside and downside demand scenarios, get human "
        "approval, execute it idempotently, then check whether the promised "
        "value actually materialized."
    )

    constraints = ConstraintRegistry()
    objectives = ObjectiveRegistry()
    models = ModelVersionRegistry()
    evaluation = EvaluationService(models)
    governance = GovernanceService(
        DelegatedApprovalAuthority(resolve_approval)
    )
    execution = ExecutionCoordinator(
        DelegatedExecutionAuthority(submit_discount, query_discount)
    )
    review = ReviewService()
    observability = ObservabilityService()

    # -- 1. Register the decision -----------------------------------------
    banner("1. Register the decision case (FR-001, FR-002)")
    case = DecisionCase(
        case_id="case-discount-q3",
        decision_type="margin_improvement",
        tenant_id="tenant-A",
        initiating_principal_ref=PROPOSER.actor_id,
        correlation_id="corr-001",
        idempotency_key="idem-001",
        created_at=NOW,
    )
    entity = DecisionCaseEntity(case.case_id)
    advance(entity, "context_pending", PROPOSER, "DecisionCaseCreated", case=case)
    advance(entity, "context_validating", PROPOSER, "DecisionContextValidating", case=case)
    note("Mandatory context assembled: tenant, commercial scope, temporal scope.")
    advance(entity, "context_validated", PROPOSER, "DecisionContextValidated", case=case)
    advance(entity, "evidence_pending", PROPOSER, "DecisionEvidenceValidating", case=case)
    advance(entity, "evidence_validated", PROPOSER, "DecisionEvidenceValidated", case=case)
    print(f"\n    case_id            {case.case_id}")
    print(f"    lifecycle_state    {entity.state}")

    # -- 2. Objectives and constraints -------------------------------------
    banner("2. Resolve objectives and constraints (FR-005..008)")
    objective = objectives.define(
        PROPOSER,
        code="net_margin",
        objective_type="financial",
        owner_ref="finance.team",
        definition_ref="def:net_margin",
        desired_direction="maximize",
        measurement_definition_ref="measure:net_margin",
        target_value=Decimal("0.18"),
        time_horizon="quarterly",
        unit="ratio",
        mandatory=True,
    )
    margin_floor = constraints.define(
        PROPOSER,
        code="margin_floor",
        constraint_type="financial",
        semantic_definition_ref="def:margin_floor",
        owner_ref="finance.team",
        mandatory=True,
        scope="tenant-A",
        evaluation_method_ref="eval:margin_floor",
        effective_interval=(NOW, THEN),
        source_policy_ref="policy:pricing-001",
    )
    # A floor evaluator is a governed function, not an ad-hoc lambda: the
    # constraint names its evaluation method, and the registry binds it.
    constraints.register_evaluator(
        "margin_floor",
        lambda candidate: (
            Decimal(str(candidate.get("blended_margin", 0))) >= Decimal("0.15"),
            "blended margin is below the 15% floor",
        ),
    )
    constraint_set = constraints.resolve_set(
        PROPOSER, case.case_id, [margin_floor.code], now=NOW
    )
    advance(entity, "constraints_resolving", PROPOSER, "DecisionConstraintsResolving", case=case)
    advance(entity, "constraints_validated", PROPOSER, "DecisionConstraintsResolved", case=case)
    print(f"\n    objective          {objective.code} (target {objective.target_value})")
    print(f"    mandatory budget   {margin_floor.code} @ >= 15%")
    print(f"    conflicts          {constraint_set.policy_snapshot.conflict_status}")

    # -- 3. Alternatives ----------------------------------------------------
    banner("3. Generate candidate alternatives (FR-012, FR-013)")
    advance(entity, "alternatives_generating", PROPOSER, "DecisionAlternativesGenerating", case=case)
    discount_10 = DecisionAlternative(
        alternative_id="alt-discount-10",
        case_id=case.case_id,
        alternative_type="pricing",
        action_intent="Apply a 10% line-item discount",
        required_preconditions=["context_validated", "evidence_validated"],
        dependency_refs=["pricing.service"],
    )
    discount_05 = DecisionAlternative(
        alternative_id="alt-discount-05",
        case_id=case.case_id,
        alternative_type="pricing",
        action_intent="Apply a 5% line-item discount",
        required_preconditions=["context_validated", "evidence_validated"],
        dependency_refs=["pricing.service"],
    )
    advance(entity, "alternatives_ready", PROPOSER, "DecisionAlternativesGenerated", case=case)
    for alt in (discount_10, discount_05):
        print(f"    {alt.alternative_id}: {alt.action_intent}")

    # -- 4. Feasibility and financial evaluation ----------------------------
    banner("4. Evaluate feasibility and financial effects (FR-014..018)")
    gate = FeasibilityGate(constraints)
    # The pricing dependency is confirmed available by the commerce platform.
    candidate = {
        "blended_margin": Decimal("0.182"),
        "dependency_available:pricing.service": True,
        "context_validated": True,
        "evidence_validated": True,
    }
    feasibility_10 = gate.evaluate(discount_10, constraint_set, candidate)
    feasibility_05 = gate.evaluate(discount_05, constraint_set, candidate)
    model = models.record(
        PROPOSER,
        _margin_model(),
    )
    eval_10 = evaluation.evaluate(
        PROPOSER,
        case_id=case.case_id,
        alternative=discount_10,
        evidence_snapshot_ref="snap-001",
        objective_snapshot_refs=[objective.objective_id],
        constraint_snapshot_refs=[constraint_set.set_id],
        model_refs=[model.model_version_id],
        policy_refs=["policy:pricing-001"],
        evaluation_method_ref="method:net_present_value",
        objective_values={"net_margin": Decimal("0.182"), "revenue_lift": Decimal("0.06")},
        financial_effects={
            "incremental_revenue": money("12000"),
            "discount_cost": money("-4800"),
        },
        uncertainty_summary={
            "disclosed": True,
            "demand_elasticity": Decimal("1.4"),
            "confidence": "medium",
        },
        computed_at=NOW,
        feasibility=feasibility_10,
    )
    eval_05 = evaluation.evaluate(
        PROPOSER,
        case_id=case.case_id,
        alternative=discount_05,
        evidence_snapshot_ref="snap-001",
        objective_snapshot_refs=[objective.objective_id],
        constraint_snapshot_refs=[constraint_set.set_id],
        model_refs=[model.model_version_id],
        policy_refs=["policy:pricing-001"],
        evaluation_method_ref="method:net_present_value",
        objective_values={"net_margin": Decimal("0.191"), "revenue_lift": Decimal("0.03")},
        financial_effects={
            "incremental_revenue": money("6000"),
            "discount_cost": money("-2400"),
        },
        uncertainty_summary={"disclosed": True, "confidence": "high"},
        computed_at=NOW,
        feasibility=feasibility_05,
    )
    advance(entity, "evaluating", PROPOSER, "DecisionAlternativeEvaluated", case=case)
    advance(entity, "evaluated", PROPOSER, "DecisionAlternativeEvaluated", case=case)
    for alt, feas, ev in ((discount_10, feasibility_10, eval_10), (discount_05, feasibility_05, eval_05)):
        net = evaluation.expected_net_value(
            {"incremental_revenue": ev.financial_effects["incremental_revenue"]},
            {"discount_cost": ev.financial_effects["discount_cost"]},
        )
        print(
            f"\n    {alt.alternative_id}\n"
            f"      feasibility       {feas.status}\n"
            f"      objective_values  {ev.objective_values}\n"
            f"      expected net      {net}"
        )

    # -- 5. Scenarios and sensitivity (the former FR-021/022 gap) ----------
    banner("5. Scenario and sensitivity evaluation (FR-021, FR-022)")
    scenarios = ScenarioRegistry()
    scenarios.define(
        PROPOSER,
        code="BASE",
        label="Baseline demand",
        description="Current demand assumptions hold",
        baseline=True,
    )
    scenarios.define(
        PROPOSER,
        code="DOWN",
        label="Downside demand",
        description="Elasticity is half of the estimate; margin compresses",
        assumption_overrides={"elasticity": "0.7", "blended_margin": "0.148"},
        downside=True,
    )
    scenarios.define(
        PROPOSER,
        code="UP",
        label="Upside demand",
        description="Elasticity exceeds the estimate",
        assumption_overrides={"elasticity": "2.0", "blended_margin": "0.210"},
    )
    scenario_service = ScenarioEvaluationService(scenarios)

    def scenario_evaluator(context, overrides) -> ScenarioOutcome:
        margin = Decimal(str(context.get("blended_margin", "0.182")))
        elasticity = Decimal(str(context.get("elasticity", "1.4")))
        return ScenarioOutcome(
            scenario_id="",
            alternative_id="",
            objective_values={"net_margin": margin, "revenue_lift": elasticity * Decimal("0.04")},
            financial_effects={"net": money(margin * Decimal("60000"))},
            feasibility_status="feasible" if margin >= Decimal("0.15") else "infeasible",
        )

    comparison = scenario_service.evaluate(
        PROPOSER,
        alternative=discount_10,
        base_context={},
        evaluator=scenario_evaluator,
    )
    scenario_service.assert_downside_evaluated(comparison)
    sensitivity = SensitivityService()
    report = sensitivity.analyze(
        PROPOSER,
        alternative=discount_10,
        probes={
            "elasticity": lambda name, delta: Decimal("1.4") * (Decimal(1) + delta) * Decimal("0.04"),
            "base_margin": lambda name, delta: (Decimal("0.182") + delta) * Decimal("0.04"),
        },
        objective_name="revenue_lift",
    )
    for outcome in comparison.outcomes:
        code = scenarios.get(outcome.scenario_id).code
        print(f"\n    scenario {code}")
        print(f"      objectives      {outcome.objective_values}")
        print(f"      feasibility     {outcome.feasibility_status}")
    print(f"\n    threshold effects   {comparison.threshold_effects or 'none'}")
    print(f"    recommendation flip {comparison.recommendation_changes or 'none'}")
    print(f"    material inputs     {report.material_inputs or 'none'}")
    for result in report.material:
        print(f"      {result.explain()}")

    # -- 6. Recommendation ---------------------------------------------------
    banner("6. Recommend an alternative (FR-027, FR-028)")
    recommendation = DecisionRecommendation(
        recommendation_id="rec-001",
        case_id=case.case_id,
        recommended_alternative_ref=discount_10.alternative_id,
        supporting_evaluation_refs=[eval_10.evaluation_id, eval_05.evaluation_id],
        expected_outcome_summary={
            "expected_net": str(
                evaluation.expected_net_value(
                    {"incremental_revenue": eval_10.financial_effects["incremental_revenue"]},
                    {"discount_cost": eval_10.financial_effects["discount_cost"]},
                )
            )
        },
        confidence_summary={"confidence": "medium"},
        material_tradeoffs=["10% discount trades margin for volume"],
        rejected_alternative_refs=[discount_05.alternative_id],
        rejection_reasons={discount_05.alternative_id: "lower expected net value"},
        recommendation_reason="Highest expected net value above the mandatory margin floor",
        expiration_time=THEN,
        created_at=NOW,
    )
    case.recommendation_ref = recommendation.recommendation_id
    case.alternatives_ref = [discount_10.alternative_id, discount_05.alternative_id]
    advance(entity, "recommended", PROPOSER, "DecisionRecommendationCreated", case=case)
    print(f"\n    recommended        {recommendation.recommended_alternative_ref}")
    print(f"    reason             {recommendation.recommendation_reason}")

    # -- 7. Approval ---------------------------------------------------------
    banner("7. Request approval from the canonical authority (FR-030, FR-031)")
    advance(entity, "approval_pending", PROPOSER, "DecisionApprovalRequested", case=case)
    approval = governance.request_approval(
        PROPOSER,
        case,
        recommendation_summary={
            "decision_scope": case.tenant_id,
            "action_types": ["apply_discount"],
            "amount_limits": {"USD": money("15000")},
            "effective_interval": (NOW, THEN),
            "expiration_time": THEN,
        },
    )
    if approval.decision_outcome != "granted":
        print(f"\n    Approval was {approval.decision_outcome}; the decision stops here.")
        return
    case.approval_ref = approval.approval_id
    governance.assert_separation_of_duties(
        case,
        approver_principal=approval.approving_principal_ref,
        executor_principal=EXECUTOR.actor_id,
        verifier_principal=REVIEWER.actor_id,
    )
    advance(entity, "approved", APPROVER, "DecisionApprovalGranted", case=case)
    print(f"\n    approval_id        {approval.approval_id}")
    print(f"    outcome            {approval.decision_outcome}")
    print(f"    amount limit       {approval.amount_limits.get(CURRENCY)}")
    revalidated = governance.revalidate_authorization(
        case, now=NOW, action_type="apply_discount", amount=money("7200")
    )
    print(f"    revalidated        {revalidated.decision_outcome} for apply_discount $7,200")

    # -- 8. Execution --------------------------------------------------------
    banner("8. Create and submit the execution intent (FR-033..037)")
    intent = execution.create_intent(
        EXECUTOR,
        case,
        action_type="apply_discount",
        action_payload={"line": "SKU-42", "percent": 10},
        now=NOW,
        amount=money("7200"),
    )
    advance(entity, "execution_pending", EXECUTOR, "ExecutionIntentCreated", case=case)
    outcome = execution.submit(EXECUTOR, case)
    advance(entity, "execution_requested", EXECUTOR, "ExecutionIntentSubmitted", case=case)
    if outcome.outcome_status == "succeeded":
        advance(entity, "execution_succeeded", EXECUTOR, "ExecutionOutcomeConfirmed", case=case)
    print(f"\n    intent_id          {intent.intent_id}")
    print(f"    idempotency key    {intent.idempotency_key}")
    print(f"    outcome            {outcome.outcome_status}")
    correlated = execution.correlate(case)
    print(f"    correlated         intent {correlated.intent_ref} -> outcome {correlated.outcome_id}")
    # Submitting again must not repeat the action (INV-007).
    duplicate = execution.submit(EXECUTOR, case)
    print(f"    duplicate submit   {duplicate.outcome_status} (same outcome, action not repeated)")

    # -- 9. Value realization ------------------------------------------------
    banner("9. Measure the realized value (FR-038..042)")
    advance(entity, "outcome_measuring", PROPOSER, "DecisionOutcomeMeasured", case=case)
    advance(entity, "outcome_measured", PROPOSER, "DecisionOutcomeMeasured", case=case)
    realization = ValueRealizationRecord(
        realization_id="real-001",
        case_id=case.case_id,
        value_hypothesis_ref="hyp-001",
        baseline_snapshot_ref="snap-001",
        observed_value=money("9100"),
        observed_outcome_refs=[outcome.outcome_id],
        attribution_method_ref="method:matched_comparison",
        attribution_category="correlated",
        confidence_level="medium",
        measurement_window=(NOW, THEN),
        status="recorded",
    )
    case.value_realization_ref = realization.realization_id
    advance(entity, "value_attribution_pending", PROPOSER, "ValueAttributionEstimated", case=case)
    advance(entity, "value_attributed", PROPOSER, "ValueRealizationRecorded", case=case)
    expected_net = evaluation.expected_net_value(
        {"incremental_revenue": eval_10.financial_effects["incremental_revenue"]},
        {"discount_cost": eval_10.financial_effects["discount_cost"]},
    )
    print(f"\n    expected net value {expected_net}")
    print(f"    observed value     {realization.observed_value}")

    # -- 10. Review and corrective action (the former FR-045 gap) -----------
    banner("10. Review the outcome and route corrective action (FR-043..045)")
    advance(entity, "under_review", REVIEWER, "DecisionReviewRequested", case=case)
    review_record = review.compare(
        REVIEWER,
        case_id=case.case_id,
        review_type="outcome",
        review_trigger="post-execution",
        evaluation=eval_10,
        realization=realization,
        reviewer_ref=REVIEWER.actor_id,
    )
    print(f"\n    assessment         {review_record.outcome_assessment}")
    print(f"    findings           {review_record.findings or 'none'}")
    for finding_id in review_record.findings:
        finding = review.findings[finding_id]
        print(f"      {finding.code}: {finding.description}")

    if review_record.findings:
        proposal = review.propose_corrective_action(
            REVIEWER,
            review_id=review_record.review_id,
            case_id=case.case_id,
            target_kind="model",
            target_ref=model.model_version_id,
            target_owner_ref=MODEL_OWNER.actor_id,
            requested_change="Re-estimate the elasticity parameter and revalidate",
            rationale="Observed value fell materially short of the expected value",
            severity="high",
            finding_refs=list(review_record.findings),
        )
        accepted = review.accept(MODEL_OWNER, proposal.proposal_id, note="accepted for retraining")
        implemented = review.mark_implemented(
            MODEL_OWNER, proposal.proposal_id, implemented_at=U12.now()
        )
        print(f"\n    proposal           {proposal.proposal_id} ({proposal.target_kind})")
        print(f"    routed to          {proposal.target_owner_ref}")
        print(f"    lifecycle          {accepted.status} -> {implemented.status}")
    completed = review.complete(REVIEWER, review_record.review_id)
    advance(entity, "completed", REVIEWER, "DecisionReviewCompleted", case=case)
    print(f"\n    review status      {completed.review_status}")
    print(f"    case lifecycle     {entity.state} (terminal)")

    # -- 11. Observability (the former FR-049 gap) ---------------------------
    banner("11. Operational indicators (FR-049)")
    for stage, terminal in (
        ("context_validated", False),
        ("constraints_validated", False),
        ("evaluated", False),
        ("approved", False),
        ("execution_succeeded", True),
    ):
        observability.record_lifecycle(
            PROPOSER,
            correlation_id=case.correlation_id,
            stage=stage,
            status=stage,
            terminal=terminal,
        )
    for operation, result in (
        ("evaluate_alternative", "succeeded"),
        ("evaluate_alternative", "succeeded"),
        ("submit_execution", "succeeded"),
        ("resolve_unknown_outcome", "succeeded"),
        ("model:margin_forecast", "succeeded"),
        ("model:margin_forecast", "succeeded"),
        ("model:margin_forecast", "failed"),
    ):
        observability.record_operation(
            PROPOSER, operation=operation, outcome=result, correlation_id=case.correlation_id
        )
    observability.record_latency(
        PROPOSER,
        correlation_id=case.correlation_id,
        stage="evaluate_alternative",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01.2+00:00",
    )
    health = observability.assess_health(
        REVIEWER,
        subject_ref=model.model_version_id,
        subject_kind="model",
        operation="model:margin_forecast",
    )
    timeline = observability.incident_timeline(REVIEWER, case.correlation_id)
    snapshot = observability.snapshot(REVIEWER)
    print(f"\n    lifecycle stages   {len(snapshot['lifecycle'])} recorded")
    print(f"    reliability ops    {len(snapshot['reliability'])}")
    print(f"    failure rate       {snapshot['reliability']['submit_execution']['failure_rate']}")
    print(f"    model health       {health.status} ({health.detail})")
    print(f"    incident timeline  {len(timeline)} correlated events")
    print(f"    open alerts        {snapshot['open_alerts']}")

    banner("Demo complete")
    note(
        "The decision travelled draft -> completed through every governed stage, "
        "with approval and execution performed by distinct principals, the "
        "duplicate submission de-duplicated, the shortfall between expected and "
        "observed value surfaced as a finding, and the whole path correlated "
        "under one correlation id. No network and no credentials were used."
    )


# ---------------------------------------------------------------------------
# Delegated authority callbacks.
#
# These are the documented integration seams to the canonical approval (U14)
# and execution (U09) capabilities. A real deployment binds these to the real
# authorities; the demo supplies deterministic local stand-ins so the whole
# lifecycle can run in one process.
# ---------------------------------------------------------------------------

DISCOUNT_LEDGER: dict[str, str] = {}


def resolve_approval(case: DecisionCase, summary: dict):
    """The canonical approval authority's resolution.

    Returns (granted, authority_ref, principal_ref, conditions). An
    unreachable authority must raise, which the caller treats as *not
    approved* (fail-safe, LAW-ARCH-07).
    """
    amount = summary.get("amount_limits", {}).get(CURRENCY)
    if amount is not None and amount > money("20000"):
        return False, "authority:finance", APPROVER.actor_id, ["over the auto-approve ceiling"]
    return True, "authority:finance", APPROVER.actor_id, ["notify finance team"]


def submit_discount(intent) -> ExecutionOutcome:
    """Apply the discount through the canonical execution authority."""
    DISCOUNT_LEDGER[intent.idempotency_key] = "applied"
    return ExecutionOutcome(
        outcome_id="outcome-" + intent.idempotency_key,
        intent_ref=intent.intent_id,
        execution_authority_ref="authority:pricing",
        external_execution_ref="pricing-run-001",
        outcome_status="succeeded",
        authoritative_evidence_refs=["evidence:pricing-run-001"],
        occurred_at=U12.now(),
        recorded_at=U12.now(),
        reconciliation_status="reconciled",
    )


def query_discount(intent_ref: str) -> ExecutionOutcome:
    if intent_ref not in DISCOUNT_LEDGER:
        return ExecutionOutcome(
            outcome_id="status-" + intent_ref,
            intent_ref=intent_ref,
            execution_authority_ref="authority:pricing",
            outcome_status="unknown",
            failure_reason="the pricing authority cannot determine the status yet",
            recorded_at=U12.now(),
        )
    return ExecutionOutcome(
        outcome_id="outcome-" + intent_ref,
        intent_ref=intent_ref,
        execution_authority_ref="authority:pricing",
        external_execution_ref="pricing-run-001",
        outcome_status="succeeded",
        authoritative_evidence_refs=["evidence:pricing-run-001"],
        recorded_at=U12.now(),
        reconciliation_status="reconciled",
    )


def _margin_model():
    from marketplace.universes.u12_intelligence.entities import DecisionModelVersion

    return DecisionModelVersion(
        model_version_id="model-margin-elasticity-v3",
        model_type="forecaster",
        semantic_purpose="margin_forecasting",
        implementation_ref="repo:elasticity-model@v3",
        version="3",
        validation_status="validated",
        applicable_context="tenant-A:pricing",
        limitations=["elasticity estimated from 90-day window"],
        approval_ref="approval-001",
        effective_interval=(NOW, THEN),
        retirement_status="active",
    )


if __name__ == "__main__":
    main()
