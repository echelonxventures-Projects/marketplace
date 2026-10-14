"""U12-FDVI requirement, capability and invariant registration (Phase G/K).

Spec 030 §20 defines FR-001..050, §21 defines AC-001..050, §27.2 defines
INV-001..020 and §34 defines architectural invariants 1..40. ADR-FDVI-001
places FDVI inside U12, so this module registers the traceability graph for
that sub-capability in the canonical kernel registries:

* ``CAPABILITIES``  — one record per implemented FDVI module, naming its
  implementing module and the requirements it satisfies;
* ``REQUIREMENTS``  — one record per functional requirement, traced to the
  capability ids and test ids that implement and verify it;
* ``INVARIANTS``    — one record per invariant, traced to the tests that
  prove it.

Every test id names a test that actually exists in ``tests/``; the registry
suite asserts this, so a requirement cannot claim coverage it does not have
(GOV-06, LAW-GOV-11).
"""

from __future__ import annotations

from marketplace.kernel.registry import (
    CAPABILITIES,
    INVARIANTS,
    REQUIREMENTS,
    CapabilityRecord,
    InvariantRecord,
    RequirementRecord,
)

DOMAIN = "FDVI"
SOURCE = "UCOS-MKT-MASTER-SPECIFICATION-030"

# ---------------------------------------------------------------------------
# Capabilities — one per implemented FDVI module (ADR-FDVI-001: inside U12)
# ---------------------------------------------------------------------------

_CAPS: tuple[CapabilityRecord, ...] = (
    CapabilityRecord(
        capability_id="CAP-FDVI-CONSTRAINTS",
        domain=DOMAIN,
        name="Constraint resolution",
        purpose="Resolve, version and evaluate mandatory and soft constraints; "
                "detect and escalate conflicts rather than relaxing them.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.constraints",
        disposition="implement",
        requirement_ids=["FR-007", "FR-008", "FR-019"],
        law_ids=["LAW-UMFDVI-005", "LAW-UMFDVI-013", "LAW-UMFDVI-021", "LAW-UMFDVI-025"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-OBJECTIVES",
        domain=DOMAIN,
        name="Objective resolution",
        purpose="Resolve objective definitions from canonical owners and validate "
                "their measurement semantics.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.objectives",
        disposition="implement",
        requirement_ids=["FR-005", "FR-006", "FR-023"],
        law_ids=["LAW-UMFDVI-004", "LAW-UMFDVI-012"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-EVIDENCE",
        domain=DOMAIN,
        name="Evidence assembly",
        purpose="Assemble versioned, provenance-tracked evidence manifests and "
                "enforce freshness and access policy.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.evidence",
        disposition="implement",
        requirement_ids=["FR-009", "FR-010", "FR-011"],
        law_ids=["LAW-UMFDVI-014", "LAW-UMFDVI-038"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-EVALUATION",
        domain=DOMAIN,
        name="Feasibility and financial evaluation",
        purpose="Classify feasibility distinctly from uncertainty and evaluate "
                "financial effects with disclosed uncertainty.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.evaluation",
        disposition="implement",
        requirement_ids=["FR-014", "FR-015", "FR-016", "FR-017", "FR-018", "FR-020", "FR-046", "FR-047"],
        law_ids=["LAW-UMFDVI-006", "LAW-UMFDVI-010", "LAW-UMFDVI-011", "LAW-UMFDVI-032"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-OPTIMIZATION",
        domain=DOMAIN,
        name="Optimization orchestration",
        purpose="Run approved solvers within resource budgets and derive honest "
                "optimality claims.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.optimization",
        disposition="implement",
        requirement_ids=["FR-024", "FR-025", "FR-026"],
        law_ids=["LAW-UMFDVI-027", "LAW-UMFDVI-036"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-GOVERNANCE",
        domain=DOMAIN,
        name="Governance and execution coordination",
        purpose="Route decisions through the canonical approval authority and "
                "coordinate idempotent execution with unknown-outcome recovery.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.governance",
        disposition="implement",
        requirement_ids=["FR-030", "FR-031", "FR-033", "FR-034", "FR-035", "FR-036", "FR-037"],
        law_ids=["LAW-UMFDVI-009", "LAW-UMFDVI-020", "LAW-UMFDVI-033", "LAW-UMFDVI-037"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-ONTOLOGY",
        domain=DOMAIN,
        name="Decision ontology",
        purpose="The typed decision entities and the governed lifecycle state "
                "machine that connects them.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.entities",
        disposition="implement",
        requirement_ids=["FR-001", "FR-002", "FR-003", "FR-012", "FR-027", "FR-029", "FR-032", "FR-038", "FR-039", "FR-040", "FR-041", "FR-042", "FR-048"],
        law_ids=["LAW-UMFDVI-003", "LAW-UMFDVI-016", "LAW-UMFDVI-018", "LAW-UMFDVI-019", "LAW-UMFDVI-024", "LAW-UMFDVI-026", "LAW-UMFDVI-034", "LAW-UMFDVI-035"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-SCENARIOS",
        domain=DOMAIN,
        name="Scenario and sensitivity evaluation",
        purpose="Evaluate alternatives under named assumption sets (scenarios), "
                "compare them against a declared baseline and measure sensitivity "
                "to material inputs, without promoting a scenario-specific "
                "outcome to a canonical result.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.scenarios",
        disposition="implement",
        requirement_ids=["FR-021", "FR-022"],
        law_ids=["LAW-UMFDVI-011", "LAW-UMFDVI-018"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-REVIEW",
        domain=DOMAIN,
        name="Review and corrective action",
        purpose="Compare expected and observed outcomes, identify decision "
                "errors, drift and failed assumptions, and route governed "
                "corrective action proposals to the owner authorized to apply "
                "them. Review never mutates production policy itself.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.review",
        disposition="implement",
        requirement_ids=["FR-043", "FR-044", "FR-045"],
        law_ids=["LAW-UMFDVI-024", "LAW-UMFDVI-033", "LAW-UMFDVI-034"],
    ),
    CapabilityRecord(
        capability_id="CAP-FDVI-OBSERVABILITY",
        domain=DOMAIN,
        name="Decision observability and control",
        purpose="Expose lifecycle, latency, reliability, health and alert "
                "indicators for the decision pipeline as a read-only "
                "projection, and support incident investigation.",
        owner_universe="U12",
        implementing_module="marketplace.universes.u12_intelligence.monitoring",
        disposition="implement",
        requirement_ids=["FR-049"],
        law_ids=["LAW-UMFDVI-010", "LAW-UMFDVI-011"],
    ),
)

# ---------------------------------------------------------------------------
# Requirements — FR-001..050 traced to capabilities and tests
# ---------------------------------------------------------------------------

# Maps each functional requirement to the capability that implements it and the
# tests that verify it. Requirements whose behaviour is not yet exercised by a
# real test are marked with status "partial" and an empty test list, so the
# traceability suite reports them honestly instead of hiding them.
_REQS: tuple[RequirementRecord, ...] = (
    RequirementRecord("FR-001", DOMAIN, "Register each decision case with a unique canonical identifier.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    RequirementRecord("FR-002", DOMAIN, "Assemble a versioned decision context.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    RequirementRecord("FR-003", DOMAIN, "Validate mandatory context requirements before evaluation.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestDecisionMachine::test_approval_rejected_is_terminal"]),
    RequirementRecord("FR-004", DOMAIN, "Discover registered dependencies through governed registry interfaces.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_unknown_when_dependency_unverified", "tests/test_fdvi.py::TestFeasibilityGate::test_dependency_available_resolves_unknown"]),
    RequirementRecord("FR-005", DOMAIN, "Resolve objective definitions from their canonical owners.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OBJECTIVES"], test_ids=["tests/test_objectives_fdvi.py::TestObjectiveRegistration::test_define_and_resolve_by_code", "tests/test_objectives_fdvi.py::TestObjectiveRegistration::test_requires_objective_writer_role"]),
    RequirementRecord("FR-006", DOMAIN, "Validate objective units, direction, scope, and time horizon.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OBJECTIVES"], test_ids=["tests/test_objectives_fdvi.py::TestObjectiveSemantics::test_direction_must_be_a_known_value", "tests/test_objectives_fdvi.py::TestObjectiveSemantics::test_unit_is_mandatory", "tests/test_objectives_fdvi.py::TestObjectiveSemantics::test_a_target_is_required"]),
    RequirementRecord("FR-007", DOMAIN, "Retrieve applicable constraints from their authorized sources.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-CONSTRAINTS"], test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_define_constraint", "tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    RequirementRecord("FR-008", DOMAIN, "Identify material conflicts among applicable constraints.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-CONSTRAINTS"], test_ids=["tests/test_fdvi.py::TestConflictDetection::test_conflicting_mandatory_constraints_flagged", "tests/test_fdvi.py::TestConflictDetection::test_raise_conflict_raises"]),
    RequirementRecord("FR-009", DOMAIN, "Construct an evidence manifest for consequential evaluations.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVIDENCE"], test_ids=["tests/test_evidence_fdvi.py::TestEvidenceAssembly::test_manifest_assembles_referenced_evidence", "tests/test_evidence_fdvi.py::TestEvidenceAssembly::test_submit_registers_and_returns_evidence", "tests/test_evidence_fdvi.py::TestEvidenceAssembly::test_empty_manifest_is_incomplete"]),
    RequirementRecord("FR-010", DOMAIN, "Preserve evidence source references and provenance.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVIDENCE"], test_ids=["tests/test_evidence_fdvi.py::TestEvidenceProvenance::test_submit_records_source_references", "tests/test_evidence_fdvi.py::TestEvidenceProvenance::test_provenance_defaults_to_authority_artifact_version", "tests/test_evidence_fdvi.py::TestEvidenceProvenance::test_missing_source_authority_rejected"]),
    RequirementRecord("FR-011", DOMAIN, "Evaluate evidence freshness according to decision policy.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVIDENCE"], test_ids=["tests/test_evidence_fdvi.py::TestEvidenceFreshness::test_current_evidence_stays_current", "tests/test_evidence_fdvi.py::TestEvidenceFreshness::test_evidence_older_than_max_age_is_stale", "tests/test_evidence_fdvi.py::TestEvidenceFreshness::test_assert_fresh_blocks_stale_evidence", "tests/test_evidence_fdvi.py::TestEvidenceFreshness::test_max_age_can_be_overridden_per_call"]),
    RequirementRecord("FR-012", DOMAIN, "Support registered methods for generating candidate alternatives.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_feasible_when_all_satisfied"]),
    RequirementRecord("FR-013", DOMAIN, "Validate the structural and semantic integrity of each candidate.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_missing_precondition_is_infeasible"]),
    RequirementRecord("FR-014", DOMAIN, "Evaluate mandatory constraints before recommending an alternative.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation", "tests/test_fdvi.py::TestFeasibilityGate::test_feasible_when_all_satisfied"]),
    RequirementRecord("FR-015", DOMAIN, "Provide reasons for rejecting alternatives where determinable.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation"]),
    RequirementRecord("FR-016", DOMAIN, "Evaluate applicable financial effects using governed metric definitions.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_expected_net_value", "tests/test_fdvi.py::TestEvaluationRecording::test_evaluate_records_result"]),
    RequirementRecord("FR-017", DOMAIN, "Identify relevant cost categories without creating competing accounting records.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_expected_net_value_without_optionals"]),
    RequirementRecord("FR-018", DOMAIN, "Distinguish estimated benefits from realized benefits.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-019", DOMAIN, "Incorporate authorized risk measures and constraints.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-CONSTRAINTS"], test_ids=["tests/test_fdvi.py::TestRiskLimitConstraint::test_within_limit", "tests/test_fdvi.py::TestRiskLimitConstraint::test_over_limit", "tests/test_fdvi.py::TestRiskLimitConstraint::test_currency_mismatch_in_risk_is_an_error"]),
    RequirementRecord("FR-020", DOMAIN, "Represent material uncertainty.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestEvaluationRecording::test_missing_uncertainty_is_disclosed"]),
    RequirementRecord("FR-021", DOMAIN, "Support scenario-based evaluation where required.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-SCENARIOS"], test_ids=["tests/test_scenarios_fdvi.py::TestScenarioEvaluation::test_deltas_against_baseline", "tests/test_scenarios_fdvi.py::TestScenarioEvaluation::test_threshold_effects_flag_sign_flip", "tests/test_scenarios_fdvi.py::TestScenarioEvaluation::test_recommendation_changes_flag_feasibility_flip", "tests/test_scenarios_fdvi.py::TestScenarioEvaluation::test_downside_must_be_present_for_robustness_claim"]),
    RequirementRecord("FR-022", DOMAIN, "Identify material input sensitivities where the selected method supports them.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-SCENARIOS"], test_ids=["tests/test_scenarios_fdvi.py::TestSensitivity::test_analyze_records_results", "tests/test_scenarios_fdvi.py::TestSensitivity::test_immaterial_input_not_reported_as_material", "tests/test_scenarios_fdvi.py::TestSensitivity::test_empty_report_cannot_claim_sensitivity", "tests/test_scenarios_fdvi.py::TestSensitivity::test_failing_probe_is_reported_not_hidden"]),
    RequirementRecord("FR-023", DOMAIN, "Support multiple objectives when the decision class requires them.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OBJECTIVES"], test_ids=["tests/test_objectives_fdvi.py::TestObjectiveSets::test_resolve_set_orders_by_priority", "tests/test_objectives_fdvi.py::TestObjectiveSets::test_opposite_mandatory_directions_are_reported_as_conflicts"]),
    RequirementRecord("FR-024", DOMAIN, "Execute approved optimization methods within defined resource limits.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OPTIMIZATION"], test_ids=["tests/test_optimization_fdvi.py::TestSolverGovernance::test_register_and_select_exact_solver", "tests/test_optimization_fdvi.py::TestResourceBudget::test_a_run_within_budget_is_accepted", "tests/test_optimization_fdvi.py::TestResourceBudget::test_exceeding_the_candidate_limit_is_rejected"]),
    RequirementRecord("FR-025", DOMAIN, "Validate solver results before accepting them.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OPTIMIZATION"], test_ids=["tests/test_optimization_fdvi.py::TestSolverResultValidation::test_a_honest_run_passes_validation", "tests/test_optimization_fdvi.py::TestSolverResultValidation::test_a_proven_optimal_claim_with_a_non_optimal_status_is_rejected"]),
    RequirementRecord("FR-026", DOMAIN, "Distinguish proven optimality from approximate or heuristic results.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OPTIMIZATION"], test_ids=["tests/test_optimization_fdvi.py::TestOptimalityHonesty::test_an_exact_solver_proves_optimality", "tests/test_optimization_fdvi.py::TestOptimalityHonesty::test_a_solver_that_cannot_prove_optimality_is_downgraded", "tests/test_optimization_fdvi.py::TestOptimalityHonesty::test_a_heuristic_solver_never_claims_optimality"]),
    RequirementRecord("FR-027", DOMAIN, "Produce recommendations only from eligible and evaluated alternatives.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    RequirementRecord("FR-028", DOMAIN, "Explain material trade-offs, assumptions, and limitations.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-029", DOMAIN, "Support recommendation expiration and revalidation.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_governance.py::TestGovernanceApproval::test_expired_approval_blocks_execution"]),
    RequirementRecord("FR-030", DOMAIN, "Submit consequential decisions to the canonical approval authority.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestGovernanceApproval::test_request_approval_granted", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_rejected"]),
    RequirementRecord("FR-031", DOMAIN, "Validate approval status, scope, authority, and validity before execution.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestGovernanceApproval::test_revoked_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_expired_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_out_of_effective_window_blocks", "tests/test_governance.py::TestGovernanceApproval::test_amount_over_limit_blocks", "tests/test_governance.py::TestGovernanceApproval::test_approval_cannot_be_ported_between_cases"]),
    RequirementRecord("FR-032", DOMAIN, "Support governed supersession of prior decisions.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    RequirementRecord("FR-033", DOMAIN, "Create typed execution intents from appropriately authorized decisions.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestExecutionCoordination::test_create_intent", "tests/test_governance.py::TestExecutionCoordination::test_create_intent_requires_approval", "tests/test_governance.py::TestExecutionCoordination::test_create_intent_requires_approved_state"]),
    RequirementRecord("FR-034", DOMAIN, "Route execution intents to the responsible canonical authority.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestExecutionCoordination::test_submit_succeeds", "tests/test_governance.py::TestExecutionCoordination::test_submit_without_authority_fails_closed"]),
    RequirementRecord("FR-035", DOMAIN, "Prevent unintended duplicate execution requests.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestExecutionCoordination::test_submit_succeeds", "tests/test_e2e_checkout.py::TestPayAndSettle::test_authorize_is_idempotent", "tests/test_saga.py::TestIdempotencyStore::test_duplicate_key_returns_recorded_outcome"]),
    RequirementRecord("FR-036", DOMAIN, "Correlate execution results with their originating intents.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestExecutionCoordination::test_correlate_outcome", "tests/test_governance.py::TestExecutionCoordination::test_correlate_mismatch_detected"]),
    RequirementRecord("FR-037", DOMAIN, "Explicitly represent execution outcomes that cannot yet be determined.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestExecutionCoordination::test_unknown_outcome_refuses_resubmission", "tests/test_governance.py::TestExecutionCoordination::test_resolve_unknown_outcome", "tests/test_governance.py::TestExecutionCoordination::test_still_unknown_outcome_escalates"]),
    RequirementRecord("FR-038", DOMAIN, "Register the baseline used for a value realization exercise.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-039", DOMAIN, "Measure outcomes using an approved measurement definition.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-040", DOMAIN, "Identify the method used to attribute an outcome to a decision.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-041", DOMAIN, "Account for applicable incremental costs when calculating net value.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_expected_net_value"]),
    RequirementRecord("FR-042", DOMAIN, "Support independent verification where required.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-GOVERNANCE"], test_ids=["tests/test_governance.py::TestSeparationOfDuties::test_verifier_must_be_independent", "tests/test_governance.py::TestSeparationOfDuties::test_independent_principals_allowed"]),
    RequirementRecord("FR-043", DOMAIN, "Support post-decision review.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-REVIEW"], test_ids=["tests/test_review_fdvi.py::TestReviewComparison::test_material_gap_produces_finding", "tests/test_review_fdvi.py::TestReviewComparison::test_no_financial_effects_produces_no_finding", "tests/test_review_fdvi.py::TestFindings::test_add_finding_links_to_review"]),
    RequirementRecord("FR-044", DOMAIN, "Compare expected and observed outcomes.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-REVIEW"], test_ids=["tests/test_review_fdvi.py::TestReviewComparison::test_small_gap_produces_no_finding", "tests/test_review_fdvi.py::TestReviewComparison::test_currency_mismatch_flagged_not_converted", "tests/test_review_fdvi.py::TestReviewComparison::test_custom_materiality_threshold"]),
    RequirementRecord("FR-045", DOMAIN, "Support governed corrective action proposals.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-REVIEW"], test_ids=["tests/test_review_fdvi.py::TestCorrectiveAction::test_proposal_created_and_linked", "tests/test_review_fdvi.py::TestCorrectiveAction::test_complete_blocked_until_material_finding_routed", "tests/test_review_fdvi.py::TestCorrectiveAction::test_only_owner_may_accept", "tests/test_review_fdvi.py::TestCorrectiveAction::test_accept_then_implement_lifecycle", "tests/test_review_fdvi.py::TestNoSelfAppliedProposal::test_missing_owner_application_is_a_violation"]),
    RequirementRecord("FR-046", DOMAIN, "Associate model-dependent evaluations with versioned model references.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestModelVersionRegistry::test_record_and_get", "tests/test_fdvi.py::TestEvaluationRecording::test_evaluate_rejects_unvalidated_model"]),
    RequirementRecord("FR-047", DOMAIN, "Support monitoring for defined forms of model drift.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestModelVersionRegistry::test_mark_drift_sets_review", "tests/test_fdvi.py::TestModelVersionRegistry::test_revalidate"]),
    RequirementRecord("FR-048", DOMAIN, "Provide traceable lineage for consequential decisions.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_security.py::TestAuditChain::test_entries_chain"]),
    RequirementRecord("FR-049", DOMAIN, "Expose lifecycle and reliability indicators.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OBSERVABILITY"], test_ids=["tests/test_monitoring_fdvi.py::TestLifecycleIndicators::test_progress_and_in_flight", "tests/test_monitoring_fdvi.py::TestReliabilityIndicators::test_rate_computed_from_observations", "tests/test_monitoring_fdvi.py::TestReliabilityIndicators::test_unmeasured_rate_cannot_be_claimed", "tests/test_monitoring_fdvi.py::TestHealth::test_unknown_when_unobserved", "tests/test_monitoring_fdvi.py::TestAlerts::test_failure_rate_alerts", "tests/test_monitoring_fdvi.py::TestIncidentInvestigation::test_snapshot_exposes_every_indicator"]),
    RequirementRecord("FR-050", DOMAIN, "Produce evidence for applicable certification gates.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestUmfdviConstitution::test_coverage_is_complete"]),
)

# ---------------------------------------------------------------------------
# Invariants — §27.2 INV-001..020, each traced to the test that proves it
# ---------------------------------------------------------------------------

_INVARIANTS: tuple[InvariantRecord, ...] = (
    InvariantRecord("INV-001", "An unauthorized principal cannot approve a decision.", "universes.u12_intelligence.governance.GovernanceService", test_ids=["tests/test_invariants.py::TestInvGovernance::test_inv001_unauthorized_principal_cannot_approve", "tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_rejected"]),
    InvariantRecord("INV-002", "An expired approval cannot authorize a new execution request.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_invariants.py::TestInvGovernance::test_inv002_expired_approval_cannot_authorize_execution", "tests/test_governance.py::TestGovernanceApproval::test_expired_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_out_of_effective_window_blocks"]),
    InvariantRecord("INV-003", "A superseded decision cannot be executed using superseded authorization.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_invariants.py::TestInvGovernance::test_inv003_superseded_authorization_cannot_execute_another_case", "tests/test_governance.py::TestGovernanceApproval::test_approval_cannot_be_ported_between_cases"]),
    InvariantRecord("INV-004", "A mandatory constraint violation cannot be converted into a feasible result without a valid, authorized policy change.", "universes.u12_intelligence.constraints.ConstraintRegistry.assert_mandatory_satisfied", test_ids=["tests/test_invariants.py::TestInvConstraints::test_inv004_a_mandatory_violation_cannot_become_feasible", "tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation", "tests/test_fdvi.py::TestConstraintEvaluation::test_mandatory_violation_without_exception_policy"]),
    InvariantRecord("INV-005", "A recommendation cannot be treated as execution confirmation.", "universes.u12_intelligence.governance.ExecutionCoordinator.create_intent", test_ids=["tests/test_invariants.py::TestInvGovernance::test_inv005_a_recommendation_is_not_execution_confirmation", "tests/test_governance.py::TestGovernanceApproval::test_rejected_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution"]),
    InvariantRecord("INV-006", "Execution acceptance cannot be treated as verified realized value.", "universes.u12_intelligence.entities.ExecutionOutcome", test_ids=["tests/test_invariants.py::TestInvExecution::test_inv006_execution_acceptance_is_not_verified_value", "tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution"]),
    InvariantRecord("INV-007", "Repeated delivery of the same idempotent execution command does not intentionally duplicate the external action.", "kernel.saga.IdempotencyStore", test_ids=["tests/test_invariants.py::TestInvExecution::test_inv007_repeated_idempotent_delivery_does_not_duplicate", "tests/test_saga.py::TestIdempotencyStore::test_duplicate_key_returns_recorded_outcome", "tests/test_e2e_checkout.py::TestPayAndSettle::test_authorize_is_idempotent", "tests/test_governance.py::TestExecutionCoordination::test_unknown_outcome_refuses_resubmission"]),
    InvariantRecord("INV-008", "A model's recommendation cannot override a canonical authority decision.", "universes.u12_intelligence.governance.GovernanceService.request_approval", test_ids=["tests/test_invariants.py::TestInvGovernance::test_inv008_a_model_recommendation_cannot_override_the_authority", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_granted", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_rejected"]),
    InvariantRecord("INV-009", "An incomplete value attribution cannot be represented as verified attribution.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_invariants.py::TestInvValueAndModels::test_inv009_incomplete_attribution_is_not_verified", "tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("INV-010", "A material financial calculation cannot silently mix incompatible currencies or units.", "kernel.money.CurrencyMismatch", test_ids=["tests/test_invariants.py::TestInvFinancialArithmetic::test_inv010_material_calculation_cannot_mix_currencies", "tests/test_money.py::TestCurrencyIsolation::test_add_rejects_mixed_currencies", "tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_currency_mismatch_rejected", "tests/test_fdvi.py::TestRiskLimitConstraint::test_currency_mismatch_in_risk_is_an_error", "tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_normalize_to_refuses_missing_rate"]),
    InvariantRecord("INV-011", "A historical decision cannot be silently overwritten by a new version.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_invariants.py::TestInvConstraints::test_inv011_a_historical_decision_is_not_silently_overwritten", "tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version", "tests/test_governance.py::TestGovernanceApproval::test_approval_cannot_be_ported_between_cases"]),
    InvariantRecord("INV-012", "A replayed event cannot unintentionally trigger a duplicate external financial action.", "kernel.saga.IdempotencyStore", test_ids=["tests/test_invariants.py::TestInvExecution::test_inv012_a_replayed_event_does_not_duplicate_the_action", "tests/test_saga.py::TestIdempotencyStore::test_duplicate_key_returns_recorded_outcome", "tests/test_saga.py::TestIdempotencyStore::test_concurrent_duplicate_waits_for_outcome"]),
    InvariantRecord("INV-013", "A missing mandatory dependency cannot silently be replaced by an unapproved fallback.", "universes.u12_intelligence.governance.DependencyUnavailable", test_ids=["tests/test_invariants.py::TestInvGovernance::test_inv013_a_missing_dependency_blocks_rather_than_falls_back", "tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed", "tests/test_governance.py::TestExecutionCoordination::test_submit_without_authority_fails_closed"]),
    InvariantRecord("INV-014", "A revoked authorization is not treated as valid merely because a prior local cache contains an approval.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_invariants.py::TestInvGovernance::test_inv014_a_revoked_approval_is_not_current_authorization", "tests/test_governance.py::TestGovernanceApproval::test_revoked_approval_blocks_execution"]),
    InvariantRecord("INV-015", "A material policy change triggers the required decision impact assessment.", "universes.u12_intelligence.constraints.ConstraintRegistry.resolve_set", test_ids=["tests/test_invariants.py::TestInvConstraints::test_inv015_a_policy_change_outside_its_effective_window_is_rejected", "tests/test_fdvi.py::TestConflictDetection::test_resolve_set_rejects_out_of_interval"]),
    InvariantRecord("INV-016", "A derived financial estimate cannot mutate canonical financial records through the evaluation pathway.", "universes.u12_intelligence.evaluation.EvaluationService.evaluate", test_ids=["tests/test_invariants.py::TestInvFinancialArithmetic::test_inv016_a_derived_estimate_cannot_mutate_the_ledger", "tests/test_ledger.py::TestLedgerBalance::test_journal_balances", "tests/test_e2e_checkout.py::TestPayAndSettle::test_ledger_balances_after_posting"]),
    InvariantRecord("INV-017", "A model-dependent result identifies the model version and evaluation context.", "universes.u12_intelligence.evaluation.ModelVersionRegistry", test_ids=["tests/test_invariants.py::TestInvValueAndModels::test_inv017_a_model_result_identifies_its_version_and_context", "tests/test_fdvi.py::TestModelVersionRegistry::test_record_and_get", "tests/test_fdvi.py::TestModelVersionRegistry::test_approved_for_purpose", "tests/test_fdvi.py::TestEvaluationRecording::test_evaluate_rejects_unvalidated_model"]),
    InvariantRecord("INV-018", "An optimization result does not claim a stronger optimality guarantee than the solver supports.", "universes.u12_intelligence.optimization.OptimizationService.assert_claim_honest", test_ids=["tests/test_invariants.py::TestInvFinancialArithmetic::test_inv018_an_optimization_claim_cannot_exceed_the_solver", "tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("INV-019", "A measured outcome does not automatically establish causation.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_invariants.py::TestInvValueAndModels::test_inv019_a_measured_outcome_does_not_establish_causation", "tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("INV-020", "A certification result cannot pass a mandatory gate without the required evidence.", "constitution.laws.LAW-GOV-11", test_ids=["tests/test_invariants.py::TestInvCertification::test_inv020_certification_cannot_pass_without_evidence", "tests/test_registry.py::TestUmfdviConstitution::test_coverage_is_complete", "tests/test_registry.py::TestUmfdviConstitution::test_coverage_resolves_registered_laws"]),
)

# §34 architectural invariants 1..40 (AI-01..AI-40), traced where the platform
# already proves them. These are platform-wide, so several are proven by the
# kernel and cross-universe suites rather than by FDVI alone.
_ARCH: tuple[InvariantRecord, ...] = (
    InvariantRecord("AI-01", "One canonical owner per business concept.", "kernel.registry.ConceptRegistry.assert_owner", test_ids=["tests/test_invariants.py::TestAiCanonicalOwnership::test_ai01_one_canonical_owner_per_business_concept", "tests/test_registry.py::TestConceptRegistry::test_party_owned_by_u01", "tests/test_registry.py::TestConceptRegistry::test_order_owned_by_u08", "tests/test_registry.py::TestConceptRegistry::test_competing_owner_rejected", "tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-02", "One canonical authority per authoritative financial fact.", "universes.u09_finance.ledger", test_ids=["tests/test_invariants.py::TestAiCanonicalOwnership::test_ai02_one_canonical_authority_per_financial_fact", "tests/test_ledger.py::TestLedgerBalance::test_journal_balances"]),
    InvariantRecord("AI-03", "No unauthorized financial mutation.", "universes.u09_finance.ledger", test_ids=["tests/test_invariants.py::TestAiCanonicalOwnership::test_ai03_no_unauthorized_financial_mutation", "tests/test_ledger.py::TestLedgerBalance::test_journal_balances", "tests/test_e2e_checkout.py::TestPayAndSettle::test_ledger_balances_after_posting"]),
    InvariantRecord("AI-04", "No execution without valid authorization.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_invariants.py::TestAiAuthorization::test_ai04_no_execution_without_valid_authorization", "tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_rejected_approval_blocks_execution"]),
    InvariantRecord("AI-05", "No silent relaxation of mandatory constraints.", "universes.u12_intelligence.constraints.ConstraintRegistry.assert_mandatory_satisfied", test_ids=["tests/test_invariants.py::TestAiDecisionHonesty::test_ai05_no_silent_relaxation_of_mandatory_constraints", "tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation"]),
    InvariantRecord("AI-06", "No hidden change to objective semantics.", "universes.u12_intelligence.objectives.ObjectiveRegistry.register", test_ids=["tests/test_invariants.py::TestAiOptimizationAndObjectives::test_ai06_no_hidden_change_to_objective_semantics", "tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    InvariantRecord("AI-07", "No unqualified optimality claims.", "universes.u12_intelligence.optimization.OptimizationService.assert_claim_honest", test_ids=["tests/test_invariants.py::TestAiDecisionHonesty::test_ai07_no_unqualified_optimality_claim", "tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-08", "No unbounded optimization workloads.", "universes.u12_intelligence.optimization.ResourceBudget.assert_within", test_ids=["tests/test_invariants.py::TestAiOptimizationAndObjectives::test_ai08_no_unbounded_optimization_workload", "tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-09", "No recommendation without traceable evaluation.", "universes.u12_intelligence.entities.DecisionRecommendation", test_ids=["tests/test_invariants.py::TestAiDecisionHonesty::test_ai09_no_recommendation_without_traceable_evaluation", "tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    InvariantRecord("AI-10", "No consequential decision without required context.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_invariants.py::TestAiDecisionHonesty::test_ai10_no_consequential_decision_without_required_context", "tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    InvariantRecord("AI-11", "No material evidence without provenance.", "universes.u12_intelligence.evidence.EvidenceStore.register", test_ids=["tests/test_invariants.py::TestAiEvidenceAndTrust::test_ai11_no_material_evidence_without_provenance", "tests/test_evidence_fdvi.py::TestEvidenceProvenance::test_provenance_defaults_to_authority_artifact_version", "tests/test_evidence_fdvi.py::TestEvidenceProvenance::test_missing_source_authority_rejected"]),
    InvariantRecord("AI-12", "No stale authorization treated as current.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_invariants.py::TestAiAuthorization::test_ai12_no_stale_authorization_treated_as_current", "tests/test_governance.py::TestGovernanceApproval::test_expired_approval_blocks_execution"]),
    InvariantRecord("AI-13", "No duplicate external action caused by an uncontrolled retry.", "kernel.saga.IdempotencyStore", test_ids=["tests/test_invariants.py::TestAiExecutionAndOutcomes::test_ai13_no_duplicate_external_action_from_retry", "tests/test_saga.py::TestIdempotencyStore::test_duplicate_key_returns_recorded_outcome", "tests/test_e2e_checkout.py::TestReserveAndOrder::test_reservation_is_idempotent_across_retries"]),
    InvariantRecord("AI-14", "No execution acceptance treated as verified financial value.", "universes.u12_intelligence.entities.ExecutionOutcome", test_ids=["tests/test_invariants.py::TestAiExecutionAndOutcomes::test_ai14_execution_acceptance_is_not_verified_financial_value", "tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution"]),
    InvariantRecord("AI-15", "No expected benefit treated as realized benefit.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_invariants.py::TestAiExecutionAndOutcomes::test_ai15_no_expected_benefit_treated_as_realized", "tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-16", "No observed change automatically treated as causal attribution.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_invariants.py::TestAiExecutionAndOutcomes::test_ai16_no_observed_change_treated_as_causal", "tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-17", "No model output treated as canonical authority.", "universes.u12_intelligence.governance.GovernanceService.request_approval", test_ids=["tests/test_invariants.py::TestAiDecisionHonesty::test_ai17_no_model_output_treated_as_canonical_authority", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_granted"]),
    InvariantRecord("AI-18", "No AI-generated policy change applied without authorization.", "universes.u12_intelligence.constraints.ConstraintRegistry.register", test_ids=["tests/test_invariants.py::TestAiDecisionHonesty::test_ai18_no_unauthorized_policy_change", "tests/test_fdvi.py::TestConstraintRegistration::test_requires_writer_role"]),
    InvariantRecord("AI-19", "No hidden fallback that changes decision semantics.", "universes.u12_intelligence.optimization.SolverRegistry.register", test_ids=["tests/test_invariants.py::TestAiDecisionHonesty::test_ai19_no_hidden_fallback_changing_decision_semantics", "tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-20", "No historical decision silently overwritten.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_invariants.py::TestAiArchitectureAndConstitution::test_ai20_no_historical_decision_silently_overwritten", "tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    InvariantRecord("AI-21", "No ungoverned cross-tenant data access.", "kernel.authorization.AuthorizationContext.tenant_id", test_ids=["tests/test_invariants.py::TestAiAuthorization::test_ai21_no_ungoverned_cross_tenant_access", "tests/test_identity.py::TestAuthentication::test_authenticate_unknown_subject", "tests/test_fdvi.py::TestConstraintRegistration::test_requires_writer_role"]),
    InvariantRecord("AI-22", "No unversioned material policy change.", "universes.u12_intelligence.constraints.ConstraintRegistry.register", test_ids=["tests/test_invariants.py::TestAiOptimizationAndObjectives::test_ai22_no_unversioned_material_policy_change", "tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    InvariantRecord("AI-23", "No untracked model change affecting consequential decisions.", "universes.u12_intelligence.evaluation.ModelVersionRegistry.record", test_ids=["tests/test_invariants.py::TestAiOptimizationAndObjectives::test_ai23_no_untracked_model_change", "tests/test_fdvi.py::TestModelVersionRegistry::test_record_and_get"]),
    InvariantRecord("AI-24", "No certification claim without evidence.", "constitution.laws.LAW-GOV-11", test_ids=["tests/test_invariants.py::TestAiArchitectureAndConstitution::test_ai24_no_certification_claim_without_evidence", "tests/test_registry.py::TestUmfdviConstitution::test_coverage_resolves_registered_laws"]),
    InvariantRecord("AI-25", "No production authorization without the required release approval.", "universes.u12_intelligence.governance.GovernanceService.request_approval", test_ids=["tests/test_invariants.py::TestAiAuthorization::test_ai25_no_production_authorization_without_release_approval", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_granted"]),
    InvariantRecord("AI-26", "No hardcoded jurisdictional assumption in universal business logic.", "kernel.money.CurrencyMismatch", test_ids=["tests/test_invariants.py::TestAiArchitectureAndConstitution::test_ai26_no_hardcoded_jurisdictional_assumption", "tests/test_money.py::TestCurrencyIsolation::test_add_rejects_mixed_currencies"]),
    InvariantRecord("AI-27", "No mandatory dependency without a defined failure policy.", "universes.u12_intelligence.governance.DependencyUnavailable", test_ids=["tests/test_invariants.py::TestAiAuthorization::test_ai27_a_missing_dependency_has_a_defined_failure_policy", "tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-28", "No unresolved critical contract incompatibility.", "kernel.events.EventBus", test_ids=["tests/test_invariants.py::TestAiArchitectureAndConstitution::test_ai28_no_unresolved_contract_incompatibility", "tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    InvariantRecord("AI-29", "No duplicated metric definition without canonical reconciliation.", "kernel.registry.ConceptRegistry.detect_duplicates", test_ids=["tests/test_invariants.py::TestAiCanonicalOwnership::test_ai29_no_duplicated_canonical_definitions", "tests/test_registry.py::TestConceptRegistry::test_no_duplicate_concepts_detected"]),
    InvariantRecord("AI-30", "No material architectural change without impact analysis.", "marketplace.docs.adr", test_ids=["tests/test_invariants.py::TestAiArchitectureAndConstitution::test_ai30_no_material_change_without_impact_analysis", "tests/test_registry.py::TestConstitutionalLaws::test_law_ids_are_unique"]),
    InvariantRecord("AI-31", "No untraceable decision-to-execution relationship.", "universes.u12_intelligence.governance.ExecutionCoordinator.correlate", test_ids=["tests/test_invariants.py::TestAiExecutionAndOutcomes::test_ai31_no_untraceable_decision_to_execution_relationship", "tests/test_governance.py::TestExecutionCoordination::test_correlate_outcome", "tests/test_governance.py::TestExecutionCoordination::test_correlate_mismatch_detected"]),
    InvariantRecord("AI-32", "No untraceable decision-to-outcome relationship.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_invariants.py::TestAiExecutionAndOutcomes::test_ai32_no_untraceable_decision_to_outcome_relationship", "tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-33", "No unauthorized modification of protected evidence.", "kernel.authorization.AuthorizationContext.requires_scope", test_ids=["tests/test_invariants.py::TestAiEvidenceAndTrust::test_ai33_no_unauthorized_modification_of_protected_evidence", "tests/test_security.py::TestAuditChain::test_entries_chain"]),
    InvariantRecord("AI-34", "No false claim of verification or independent assurance.", "universes.u12_intelligence.governance.GovernanceService.assert_separation_of_duties", test_ids=["tests/test_invariants.py::TestAiEvidenceAndTrust::test_ai34_no_false_claim_of_independent_assurance", "tests/test_governance.py::TestSeparationOfDuties::test_verifier_must_be_independent", "tests/test_governance.py::TestSeparationOfDuties::test_approver_cannot_be_proposer"]),
    InvariantRecord("AI-35", "No orphaned canonical artifacts.", "kernel.registry.CapabilityRegistry.assert_no_dead_capabilities", test_ids=["tests/test_invariants.py::TestAiCanonicalOwnership::test_ai35_no_orphaned_canonical_artifacts", "tests/test_registry.py::TestCapabilityRegistry::test_no_dead_capabilities"]),
    InvariantRecord("AI-36", "No circular dependency introduced through capability contracts.", "kernel.registry.ConceptRegistry", test_ids=["tests/test_invariants.py::TestAiCanonicalOwnership::test_ai36_no_circular_dependency_in_capability_contracts", "tests/test_registry.py::TestConceptRegistry::test_no_duplicate_concepts_detected"]),
    InvariantRecord("AI-37", "No vendor-specific implementation detail allowed to redefine canonical business semantics.", "marketplace.constitution.laws", test_ids=["tests/test_invariants.py::TestAiArchitectureAndConstitution::test_ai37_no_vendor_specific_redefinition_of_business_semantics", "tests/test_registry.py::TestConstitutionalLaws::test_law_ids_use_canonical_prefix"]),
    InvariantRecord("AI-38", "No unapproved reuse of sensitive evidence for unrelated purposes.", "universes.u12_intelligence.evidence.EvidenceStore.retrieve", test_ids=["tests/test_invariants.py::TestAiEvidenceAndTrust::test_ai38_no_unapproved_reuse_of_sensitive_evidence", "tests/test_evidence_fdvi.py::TestEvidenceAccessControl::test_sensitive_evidence_requires_scope", "tests/test_evidence_fdvi.py::TestEvidenceAccessControl::test_scope_grants_access", "tests/test_evidence_fdvi.py::TestEvidenceAccessControl::test_manifest_assembly_enforces_read_scopes"]),
    InvariantRecord("AI-39", "No silent conversion of a failed decision into a successful decision.", "universes.u12_intelligence.governance.ExecutionCoordinator.submit", test_ids=["tests/test_invariants.py::TestAiExecutionAndOutcomes::test_ai39_no_silent_conversion_of_failure_into_success", "tests/test_governance.py::TestGovernanceApproval::test_rejected_approval_blocks_execution"]),
    InvariantRecord("AI-40", "No release before mandatory certification requirements are satisfied.", "constitution.laws.LAW-GOV-15", test_ids=["tests/test_invariants.py::TestAiArchitectureAndConstitution::test_ai40_no_release_before_certification_is_satisfied", "tests/test_registry.py::TestConstitutionalLaws::test_law_ids_are_unique"]),
)


def register() -> tuple[int, int, int]:
    """Populate the canonical registries. Idempotent under re-import.

    Returns the counts registered on this call (capabilities, requirements,
    invariants). Duplicate ids are rejected by the registries themselves, so a
    second import raises rather than silently double-registering.
    """
    for capability in _CAPS:
        CAPABILITIES.register(capability)
    for requirement in _REQS:
        REQUIREMENTS.register(requirement)
    for invariant in (*_INVARIANTS, *_ARCH):
        INVARIANTS.register(invariant)
    return len(_CAPS), len(_REQS), len(_INVARIANTS) + len(_ARCH)


register()
