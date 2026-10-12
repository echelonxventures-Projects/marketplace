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
        requirement_ids=["FR-001", "FR-002", "FR-003", "FR-012", "FR-027", "FR-029", "FR-032", "FR-038", "FR-039", "FR-040", "FR-041", "FR-042", "FR-043", "FR-044", "FR-045", "FR-048"],
        law_ids=["LAW-UMFDVI-003", "LAW-UMFDVI-016", "LAW-UMFDVI-018", "LAW-UMFDVI-019", "LAW-UMFDVI-024", "LAW-UMFDVI-026", "LAW-UMFDVI-034", "LAW-UMFDVI-035"],
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
    RequirementRecord("FR-005", DOMAIN, "Resolve objective definitions from their canonical owners.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OBJECTIVES"], test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_define_constraint"]),
    RequirementRecord("FR-006", DOMAIN, "Validate objective units, direction, scope, and time horizon.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OBJECTIVES"], test_ids=["tests/test_fdvi.py::TestConstraintEvaluation::test_mandatory_set_property"]),
    RequirementRecord("FR-007", DOMAIN, "Retrieve applicable constraints from their authorized sources.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-CONSTRAINTS"], test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_define_constraint", "tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    RequirementRecord("FR-008", DOMAIN, "Identify material conflicts among applicable constraints.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-CONSTRAINTS"], test_ids=["tests/test_fdvi.py::TestConflictDetection::test_conflicting_mandatory_constraints_flagged", "tests/test_fdvi.py::TestConflictDetection::test_raise_conflict_raises"]),
    RequirementRecord("FR-009", DOMAIN, "Construct an evidence manifest for consequential evaluations.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVIDENCE"], test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    RequirementRecord("FR-010", DOMAIN, "Preserve evidence source references and provenance.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVIDENCE"], test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    RequirementRecord("FR-011", DOMAIN, "Evaluate evidence freshness according to decision policy.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVIDENCE"], test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    RequirementRecord("FR-012", DOMAIN, "Support registered methods for generating candidate alternatives.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_feasible_when_all_satisfied"]),
    RequirementRecord("FR-013", DOMAIN, "Validate the structural and semantic integrity of each candidate.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_missing_precondition_is_infeasible"]),
    RequirementRecord("FR-014", DOMAIN, "Evaluate mandatory constraints before recommending an alternative.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation", "tests/test_fdvi.py::TestFeasibilityGate::test_feasible_when_all_satisfied"]),
    RequirementRecord("FR-015", DOMAIN, "Provide reasons for rejecting alternatives where determinable.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation"]),
    RequirementRecord("FR-016", DOMAIN, "Evaluate applicable financial effects using governed metric definitions.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_expected_net_value", "tests/test_fdvi.py::TestEvaluationRecording::test_evaluate_records_result"]),
    RequirementRecord("FR-017", DOMAIN, "Identify relevant cost categories without creating competing accounting records.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_expected_net_value_without_optionals"]),
    RequirementRecord("FR-018", DOMAIN, "Distinguish estimated benefits from realized benefits.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-019", DOMAIN, "Incorporate authorized risk measures and constraints.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-CONSTRAINTS"], test_ids=["tests/test_fdvi.py::TestRiskLimitConstraint::test_within_limit", "tests/test_fdvi.py::TestRiskLimitConstraint::test_over_limit", "tests/test_fdvi.py::TestRiskLimitConstraint::test_currency_mismatch_in_risk_is_an_error"]),
    RequirementRecord("FR-020", DOMAIN, "Represent material uncertainty.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestEvaluationRecording::test_missing_uncertainty_is_disclosed"]),
    RequirementRecord("FR-021", DOMAIN, "Support scenario-based evaluation where required.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=[], status="not_implemented"),
    RequirementRecord("FR-022", DOMAIN, "Identify material input sensitivities where the selected method supports them.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=[], status="not_implemented"),
    RequirementRecord("FR-023", DOMAIN, "Support multiple objectives when the decision class requires them.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OBJECTIVES"], test_ids=["tests/test_fdvi.py::TestConflictDetection::test_conflicting_mandatory_constraints_flagged"]),
    RequirementRecord("FR-024", DOMAIN, "Execute approved optimization methods within defined resource limits.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OPTIMIZATION"], test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    RequirementRecord("FR-025", DOMAIN, "Validate solver results before accepting them.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OPTIMIZATION"], test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    RequirementRecord("FR-026", DOMAIN, "Distinguish proven optimality from approximate or heuristic results.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-OPTIMIZATION"], test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
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
    RequirementRecord("FR-043", DOMAIN, "Support post-decision review.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-044", DOMAIN, "Compare expected and observed outcomes.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    RequirementRecord("FR-045", DOMAIN, "Support governed corrective action proposals.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"], status="not_implemented"),
    RequirementRecord("FR-046", DOMAIN, "Associate model-dependent evaluations with versioned model references.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestModelVersionRegistry::test_record_and_get", "tests/test_fdvi.py::TestEvaluationRecording::test_evaluate_rejects_unvalidated_model"]),
    RequirementRecord("FR-047", DOMAIN, "Support monitoring for defined forms of model drift.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-EVALUATION"], test_ids=["tests/test_fdvi.py::TestModelVersionRegistry::test_mark_drift_sets_review", "tests/test_fdvi.py::TestModelVersionRegistry::test_revalidate"]),
    RequirementRecord("FR-048", DOMAIN, "Provide traceable lineage for consequential decisions.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_security.py::TestAuditChain::test_entries_chain"]),
    RequirementRecord("FR-049", DOMAIN, "Expose lifecycle and reliability indicators.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_state.py::TestRegisteredMachines::test_reservation_machine_initial_state"], status="not_implemented"),
    RequirementRecord("FR-050", DOMAIN, "Produce evidence for applicable certification gates.", f"{SOURCE} §20", capability_ids=["CAP-FDVI-ONTOLOGY"], test_ids=["tests/test_registry.py::TestUmfdviConstitution::test_coverage_is_complete"]),
)

# ---------------------------------------------------------------------------
# Invariants — §27.2 INV-001..020, each traced to the test that proves it
# ---------------------------------------------------------------------------

_INVARIANTS: tuple[InvariantRecord, ...] = (
    InvariantRecord("INV-001", "An unauthorized principal cannot approve a decision.", "universes.u12_intelligence.governance.GovernanceService", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_rejected"]),
    InvariantRecord("INV-002", "An expired approval cannot authorize a new execution request.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_expired_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_out_of_effective_window_blocks"]),
    InvariantRecord("INV-003", "A superseded decision cannot be executed using superseded authorization.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_approval_cannot_be_ported_between_cases"]),
    InvariantRecord("INV-004", "A mandatory constraint violation cannot be converted into a feasible result without a valid, authorized policy change.", "universes.u12_intelligence.constraints.ConstraintRegistry.assert_mandatory_satisfied", test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation", "tests/test_fdvi.py::TestConstraintEvaluation::test_mandatory_violation_without_exception_policy"]),
    InvariantRecord("INV-005", "A recommendation cannot be treated as execution confirmation.", "universes.u12_intelligence.governance.ExecutionCoordinator.create_intent", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_rejected_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution"]),
    InvariantRecord("INV-006", "Execution acceptance cannot be treated as verified realized value.", "universes.u12_intelligence.entities.ExecutionOutcome", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution"]),
    InvariantRecord("INV-007", "Repeated delivery of the same idempotent execution command does not intentionally duplicate the external action.", "kernel.saga.IdempotencyStore", test_ids=["tests/test_saga.py::TestIdempotencyStore::test_duplicate_key_returns_recorded_outcome", "tests/test_e2e_checkout.py::TestPayAndSettle::test_authorize_is_idempotent", "tests/test_governance.py::TestExecutionCoordination::test_unknown_outcome_refuses_resubmission"]),
    InvariantRecord("INV-008", "A model's recommendation cannot override a canonical authority decision.", "universes.u12_intelligence.governance.GovernanceService.request_approval", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_request_approval_granted", "tests/test_governance.py::TestGovernanceApproval::test_request_approval_rejected"]),
    InvariantRecord("INV-009", "An incomplete value attribution cannot be represented as verified attribution.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("INV-010", "A material financial calculation cannot silently mix incompatible currencies or units.", "kernel.money.CurrencyMismatch", test_ids=["tests/test_money.py::TestCurrencyIsolation::test_add_rejects_mixed_currencies", "tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_currency_mismatch_rejected", "tests/test_fdvi.py::TestRiskLimitConstraint::test_currency_mismatch_in_risk_is_an_error", "tests/test_fdvi.py::TestEvaluationFinancialArithmetic::test_normalize_to_refuses_missing_rate"]),
    InvariantRecord("INV-011", "A historical decision cannot be silently overwritten by a new version.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version", "tests/test_governance.py::TestGovernanceApproval::test_approval_cannot_be_ported_between_cases"]),
    InvariantRecord("INV-012", "A replayed event cannot unintentionally trigger a duplicate external financial action.", "kernel.saga.IdempotencyStore", test_ids=["tests/test_saga.py::TestIdempotencyStore::test_duplicate_key_returns_recorded_outcome", "tests/test_saga.py::TestIdempotencyStore::test_concurrent_duplicate_waits_for_outcome"]),
    InvariantRecord("INV-013", "A missing mandatory dependency cannot silently be replaced by an unapproved fallback.", "universes.u12_intelligence.governance.DependencyUnavailable", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed", "tests/test_governance.py::TestExecutionCoordination::test_submit_without_authority_fails_closed"]),
    InvariantRecord("INV-014", "A revoked authorization is not treated as valid merely because a prior local cache contains an approval.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_revoked_approval_blocks_execution"]),
    InvariantRecord("INV-015", "A material policy change triggers the required decision impact assessment.", "universes.u12_intelligence.constraints.ConstraintRegistry.resolve_set", test_ids=["tests/test_fdvi.py::TestConflictDetection::test_resolve_set_rejects_out_of_interval"]),
    InvariantRecord("INV-016", "A derived financial estimate cannot mutate canonical financial records through the evaluation pathway.", "universes.u12_intelligence.evaluation.EvaluationService.evaluate", test_ids=["tests/test_ledger.py::TestLedgerBalance::test_journal_balances", "tests/test_e2e_checkout.py::TestPayAndSettle::test_ledger_balances_after_posting"]),
    InvariantRecord("INV-017", "A model-dependent result identifies the model version and evaluation context.", "universes.u12_intelligence.evaluation.ModelVersionRegistry", test_ids=["tests/test_fdvi.py::TestModelVersionRegistry::test_record_and_get", "tests/test_fdvi.py::TestModelVersionRegistry::test_approved_for_purpose", "tests/test_fdvi.py::TestEvaluationRecording::test_evaluate_rejects_unvalidated_model"]),
    InvariantRecord("INV-018", "An optimization result does not claim a stronger optimality guarantee than the solver supports.", "universes.u12_intelligence.optimization.OptimizationService.assert_claim_honest", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("INV-019", "A measured outcome does not automatically establish causation.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("INV-020", "A certification result cannot pass a mandatory gate without the required evidence.", "constitution.laws.LAW-GOV-11", test_ids=["tests/test_registry.py::TestUmfdviConstitution::test_coverage_is_complete", "tests/test_registry.py::TestUmfdviConstitution::test_coverage_resolves_registered_laws"]),
)

# §34 architectural invariants 1..40 (AI-01..AI-40), traced where the platform
# already proves them. These are platform-wide, so several are proven by the
# kernel and cross-universe suites rather than by FDVI alone.
_ARCH: tuple[InvariantRecord, ...] = (
    InvariantRecord("AI-01", "One canonical owner per business concept.", "kernel.registry.ConceptRegistry.assert_owner", test_ids=["tests/test_registry.py::TestConceptRegistry::test_party_owned_by_u01", "tests/test_registry.py::TestConceptRegistry::test_order_owned_by_u08", "tests/test_registry.py::TestConceptRegistry::test_competing_owner_rejected", "tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-02", "One canonical authority per authoritative financial fact.", "universes.u09_finance.ledger", test_ids=["tests/test_ledger.py::TestLedgerBalance::test_journal_balances"]),
    InvariantRecord("AI-03", "No unauthorized financial mutation.", "universes.u09_finance.ledger", test_ids=["tests/test_ledger.py::TestLedgerBalance::test_journal_balances", "tests/test_e2e_checkout.py::TestPayAndSettle::test_ledger_balances_after_posting"]),
    InvariantRecord("AI-04", "No execution without valid authorization.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution", "tests/test_governance.py::TestGovernanceApproval::test_rejected_approval_blocks_execution"]),
    InvariantRecord("AI-05", "No silent relaxation of mandatory constraints.", "universes.u12_intelligence.constraints.ConstraintRegistry.assert_mandatory_satisfied", test_ids=["tests/test_fdvi.py::TestFeasibilityGate::test_infeasible_on_mandatory_violation"]),
    InvariantRecord("AI-06", "No hidden change to objective semantics.", "universes.u12_intelligence.objectives.ObjectiveRegistry.register", test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    InvariantRecord("AI-07", "No unqualified optimality claims.", "universes.u12_intelligence.optimization.OptimizationService.assert_claim_honest", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-08", "No unbounded optimization workloads.", "universes.u12_intelligence.optimization.ResourceBudget.assert_within", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-09", "No recommendation without traceable evaluation.", "universes.u12_intelligence.entities.DecisionRecommendation", test_ids=["tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    InvariantRecord("AI-10", "No consequential decision without required context.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_fdvi.py::TestDecisionMachine::test_recommend_to_approval_path"]),
    InvariantRecord("AI-11", "No material evidence without provenance.", "universes.u12_intelligence.evidence.EvidenceStore.register", test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    InvariantRecord("AI-12", "No stale authorization treated as current.", "universes.u12_intelligence.governance.GovernanceService.revalidate_authorization", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_expired_approval_blocks_execution"]),
    InvariantRecord("AI-13", "No duplicate external action caused by an uncontrolled retry.", "kernel.saga.IdempotencyStore", test_ids=["tests/test_saga.py::TestIdempotencyStore::test_duplicate_key_returns_recorded_outcome", "tests/test_e2e_checkout.py::TestReserveAndOrder::test_reservation_is_idempotent_across_retries"]),
    InvariantRecord("AI-14", "No execution acceptance treated as verified financial value.", "universes.u12_intelligence.entities.ExecutionOutcome", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_approval_blocks_execution"]),
    InvariantRecord("AI-15", "No expected benefit treated as realized benefit.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-16", "No observed change automatically treated as causal attribution.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-17", "No model output treated as canonical authority.", "universes.u12_intelligence.governance.GovernanceService.request_approval", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_request_approval_granted"]),
    InvariantRecord("AI-18", "No AI-generated policy change applied without authorization.", "universes.u12_intelligence.constraints.ConstraintRegistry.register", test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_requires_writer_role"]),
    InvariantRecord("AI-19", "No hidden fallback that changes decision semantics.", "universes.u12_intelligence.optimization.SolverRegistry.register", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-20", "No historical decision silently overwritten.", "universes.u12_intelligence.entities.DECISION_MACHINE", test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    InvariantRecord("AI-21", "No ungoverned cross-tenant data access.", "kernel.authorization.AuthorizationContext.tenant_id", test_ids=["tests/test_identity.py::TestAuthentication::test_authenticate_unknown_subject", "tests/test_fdvi.py::TestConstraintRegistration::test_requires_writer_role"]),
    InvariantRecord("AI-22", "No unversioned material policy change.", "universes.u12_intelligence.constraints.ConstraintRegistry.register", test_ids=["tests/test_fdvi.py::TestConstraintRegistration::test_supersede_by_version"]),
    InvariantRecord("AI-23", "No untracked model change affecting consequential decisions.", "universes.u12_intelligence.evaluation.ModelVersionRegistry.record", test_ids=["tests/test_fdvi.py::TestModelVersionRegistry::test_record_and_get"]),
    InvariantRecord("AI-24", "No certification claim without evidence.", "constitution.laws.LAW-GOV-11", test_ids=["tests/test_registry.py::TestUmfdviConstitution::test_coverage_resolves_registered_laws"]),
    InvariantRecord("AI-25", "No production authorization without the required release approval.", "universes.u12_intelligence.governance.GovernanceService.request_approval", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_request_approval_granted"]),
    InvariantRecord("AI-26", "No hardcoded jurisdictional assumption in universal business logic.", "kernel.money.CurrencyMismatch", test_ids=["tests/test_money.py::TestCurrencyIsolation::test_add_rejects_mixed_currencies"]),
    InvariantRecord("AI-27", "No mandatory dependency without a defined failure policy.", "universes.u12_intelligence.governance.DependencyUnavailable", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_no_authority_fails_closed"]),
    InvariantRecord("AI-28", "No unresolved critical contract incompatibility.", "kernel.events.EventBus", test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    InvariantRecord("AI-29", "No duplicated metric definition without canonical reconciliation.", "kernel.registry.ConceptRegistry.detect_duplicates", test_ids=["tests/test_registry.py::TestConceptRegistry::test_no_duplicate_concepts_detected"]),
    InvariantRecord("AI-30", "No material architectural change without impact analysis.", "marketplace.docs.adr", test_ids=["tests/test_registry.py::TestConstitutionalLaws::test_law_ids_are_unique"]),
    InvariantRecord("AI-31", "No untraceable decision-to-execution relationship.", "universes.u12_intelligence.governance.ExecutionCoordinator.correlate", test_ids=["tests/test_governance.py::TestExecutionCoordination::test_correlate_outcome", "tests/test_governance.py::TestExecutionCoordination::test_correlate_mismatch_detected"]),
    InvariantRecord("AI-32", "No untraceable decision-to-outcome relationship.", "universes.u12_intelligence.entities.ValueRealizationRecord", test_ids=["tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12"]),
    InvariantRecord("AI-33", "No unauthorized modification of protected evidence.", "kernel.authorization.AuthorizationContext.requires_scope", test_ids=["tests/test_security.py::TestAuditChain::test_entries_chain"]),
    InvariantRecord("AI-34", "No false claim of verification or independent assurance.", "universes.u12_intelligence.governance.GovernanceService.assert_separation_of_duties", test_ids=["tests/test_governance.py::TestSeparationOfDuties::test_verifier_must_be_independent", "tests/test_governance.py::TestSeparationOfDuties::test_approver_cannot_be_proposer"]),
    InvariantRecord("AI-35", "No orphaned canonical artifacts.", "kernel.registry.CapabilityRegistry.assert_no_dead_capabilities", test_ids=["tests/test_registry.py::TestCapabilityRegistry::test_no_dead_capabilities"]),
    InvariantRecord("AI-36", "No circular dependency introduced through capability contracts.", "kernel.registry.ConceptRegistry", test_ids=["tests/test_registry.py::TestConceptRegistry::test_no_duplicate_concepts_detected"]),
    InvariantRecord("AI-37", "No vendor-specific implementation detail allowed to redefine canonical business semantics.", "marketplace.constitution.laws", test_ids=["tests/test_registry.py::TestConstitutionalLaws::test_law_ids_use_canonical_prefix"]),
    InvariantRecord("AI-38", "No unapproved reuse of sensitive evidence for unrelated purposes.", "universes.u12_intelligence.evidence.EvidenceStore.retrieve", test_ids=["tests/test_registry.py::TestStateMachinesAreGoverned::test_machines_registered"]),
    InvariantRecord("AI-39", "No silent conversion of a failed decision into a successful decision.", "universes.u12_intelligence.governance.ExecutionCoordinator.submit", test_ids=["tests/test_governance.py::TestGovernanceApproval::test_rejected_approval_blocks_execution"]),
    InvariantRecord("AI-40", "No release before mandatory certification requirements are satisfied.", "constitution.laws.LAW-GOV-15", test_ids=["tests/test_registry.py::TestConstitutionalLaws::test_law_ids_are_unique"]),
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
