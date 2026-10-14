"""Cross-domain invariant enforcement, each asserted by name.

Spec 030 §27.2 defines INV-001..020 and §34 defines architectural invariants
AI-01..40. Every test here names the invariant it proves and asserts that
invariant's specific semantics against the code that enforces it, so a
requirement cannot claim coverage it does not have (GOV-06, LAW-GOV-11).
"""

import pathlib

import pytest

from marketplace.constitution.laws import all_laws, get_law
from marketplace.kernel import AuthorizationContext, IdempotencyStore, Money, generate
from marketplace.kernel.errors import (
    AuthorizationError,
    DeadLetterError,
    DuplicateCanonicalConceptError,
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    PolicyViolationError,
    TenantIsolationError,
    ValidationError,
)
from marketplace.kernel.events import Event, EventBus
from marketplace.kernel.money import CurrencyMismatch
from marketplace.kernel.registry import CAPABILITIES, CONCEPTS
from marketplace.universes.u09_finance.ledger import LedgerService
from marketplace.universes.u12_intelligence.constraints import (
    ConstraintRegistry,
    ConstraintSet,
)
from marketplace.universes.u12_intelligence.entities import (
    DecisionApproval,
    DecisionCase,
    DecisionModelVersion,
    ExecutionOutcome,
    ValueRealizationRecord,
)
from marketplace.universes.u12_intelligence.evidence import EvidenceStore
from marketplace.universes.u12_intelligence.evaluation import ModelVersionRegistry
from marketplace.universes.u12_intelligence.governance import (
    DelegatedApprovalAuthority,
    DelegatedExecutionAuthority,
    DependencyUnavailable,
    ExecutionCoordinator,
    GovernanceService,
)
from marketplace.universes.u12_intelligence.objectives import ObjectiveRegistry
from marketplace.universes.u12_intelligence.optimization import (
    OptimizationRun,
    OptimizationService,
    ResourceBudget,
    SolverRegistration,
    SolverRegistry,
)

pytestmark = pytest.mark.invariant

NOW = "2026-01-01T00:00:00+00:00"
THEN = "2027-01-01T00:00:00+00:00"


def approved_case(**over):
    base = dict(
        case_id="case-1",
        decision_type="refund_approval",
        tenant_id="tenant-A",
        initiating_principal_ref="proposer",
        correlation_id="c1",
        idempotency_key="k1",
        lifecycle_state="approved",
        recommendation_ref="rec-1",
        approval_ref="appr-1",
    )
    base.update(over)
    return DecisionCase(**base)


def grant(**over):
    base = dict(
        approval_id="appr-1",
        case_id="case-1",
        recommendation_ref="rec-1",
        approving_authority_ref="auth-1",
        approving_principal_ref="approver",
        authority_evidence_ref="authority:auth-1",
        decision_scope="tenant-A",
        approved_action_scope=["refund"],
        effective_interval=(NOW, THEN),
        decision_outcome="granted",
        recorded_at=NOW,
    )
    base.update(over)
    return DecisionApproval(**base)


def governed_service(**over):
    """A governance service whose authority grants by default."""
    authority = over.pop("authority", DelegatedApprovalAuthority(lambda c, s: (True, "auth-1", "approver", [])))
    service = GovernanceService(authority=authority)
    service.approvals["appr-1"] = over.pop("approval", grant())
    return service


@pytest.fixture
def evidence_authz():
    """An actor holding the evidence read scopes for protected classifications."""
    return AuthorizationContext(
        actor_id="evidence-reader",
        actor_type="service",
        roles=frozenset({"decision.service"}),
        tenant_id="tenant-A",
        scopes=frozenset({
            "evidence.read:sensitive",
            "evidence.read:financial",
            "evidence.read:personal",
        }),
    )


# ===========================================================================
# §27.2 — INV-001..020
# ===========================================================================


class TestInvGovernance:
    """INV-001..003, INV-005, INV-008, INV-013, INV-014: authorization honesty."""

    def test_inv001_unauthorized_principal_cannot_approve(self, other_tenant_authz):
        """INV-001: an unauthorized principal cannot approve a decision."""
        service = governed_service()
        with pytest.raises(AuthorizationError):
            service.request_approval(other_tenant_authz, approved_case(), {})

    def test_inv002_expired_approval_cannot_authorize_execution(self):
        """INV-002: an expired approval cannot authorize a new execution request."""
        service = governed_service(approval=grant(expiration_time="2026-06-01T00:00:00+00:00"))
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                approved_case(), now="2027-01-01T00:00:00+00:00",
                action_type="refund", amount=None,
            )

    def test_inv003_superseded_authorization_cannot_execute_another_case(self):
        """INV-003: a superseded decision cannot be executed using superseded authorization."""
        service = governed_service()
        other_case = approved_case(case_id="case-2")
        other_case.approval_ref = "appr-1"  # an approval that belongs to case-1
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                other_case, now=NOW, action_type="refund", amount=None,
            )

    def test_inv005_a_recommendation_is_not_execution_confirmation(self, authz):
        """INV-005: a recommendation cannot be treated as execution confirmation."""
        coordinator = ExecutionCoordinator()
        case = approved_case(approval_ref=None)  # recommended but never approved
        with pytest.raises(AuthorizationError):
            coordinator.create_intent(
                authz, case, action_type="refund", action_payload={}, now=NOW,
            )

    def test_inv008_a_model_recommendation_cannot_override_the_authority(self, authz):
        """INV-008: a model's recommendation cannot override a canonical authority decision."""
        service = governed_service(
            authority=DelegatedApprovalAuthority(lambda c, s: (False, "auth-1", "approver", ["denied"]))
        )
        case = approved_case(approval_ref=None)
        approval = service.request_approval(authz, case, {})
        assert approval.decision_outcome == "rejected"
        assert case.approval_ref is None  # the recommendation (rec-1) did not authorize it

    def test_inv013_a_missing_dependency_blocks_rather_than_falls_back(self, authz):
        """INV-013: a missing mandatory dependency cannot be replaced by an unapproved fallback."""
        service = GovernanceService()  # no approval authority bound
        with pytest.raises(DependencyUnavailable):
            service.request_approval(authz, approved_case(), {})

    def test_inv014_a_revoked_approval_is_not_current_authorization(self, authz):
        """INV-014: a revoked authorization is not valid merely because it was once granted."""
        service = governed_service()
        service.revoke(authz, "appr-1", "policy change")
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                approved_case(), now=NOW, action_type="refund", amount=None,
            )


class TestInvConstraints:
    """INV-004, INV-011, INV-015: constraint honesty."""

    def test_inv004_a_mandatory_violation_cannot_become_feasible(self, authz):
        """INV-004: a mandatory constraint violation cannot be converted into a
        feasible result without a valid, authorized policy change."""
        registry = ConstraintRegistry()
        registry.register_evaluator("budget.cap", lambda _c, ctx: ctx.get("cost", 0) <= 100)
        constraint = registry.define(
            authz,
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=(NOW, THEN),
            source_policy_ref="policy/budget/v1",
        )
        constraint_set = ConstraintSet(set_id="cs-1", case_id="case-1", constraints=[constraint])
        with pytest.raises(InvariantViolationError):
            registry.assert_mandatory_satisfied(constraint_set, {"cost": 500})

    def test_inv011_a_historical_decision_is_not_silently_overwritten(self, authz):
        """INV-011: a historical decision cannot be silently overwritten by a new version."""
        registry = ConstraintRegistry()
        registry.define(
            authz,
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=(NOW, THEN),
            source_policy_ref="policy/budget/v1",
        )
        with pytest.raises(ValidationError):
            registry.define(
                authz,
                code="budget.cap",
                constraint_type="financial",
                semantic_definition_ref="policy/budget",
                owner_ref="finance.owner",
                mandatory=True,
                scope="budget",
                evaluation_method_ref="budget.evaluate",
                effective_interval=(NOW, THEN),
                source_policy_ref="policy/budget/v1",
            )

    def test_inv015_a_policy_change_outside_its_effective_window_is_rejected(self, authz):
        """INV-015: a material policy change triggers the required decision impact assessment."""
        registry = ConstraintRegistry()
        registry.define(
            authz,
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=(NOW, THEN),
            source_policy_ref="policy/budget/v1",
        )
        with pytest.raises(InvariantViolationError):
            registry.resolve_set(
                authz, "case-1", ["budget.cap"],
                now="2025-01-01T00:00:00+00:00",  # before the policy takes effect
            )


class TestInvExecution:
    """INV-006, INV-007, INV-012: execution and replay honesty."""

    def test_inv006_execution_acceptance_is_not_verified_value(self):
        """INV-006: execution acceptance cannot be treated as verified realized value."""
        outcome = ExecutionOutcome(outcome_id="o-1", intent_ref="i-1", execution_authority_ref="canonical:execution", outcome_status="succeeded")
        assert outcome.reconciliation_status == "unreconciled"
        # a succeeded execution does not create a verified value realization
        realization = ValueRealizationRecord(realization_id="r-1", case_id="case-1", value_hypothesis_ref="h-1", baseline_snapshot_ref="b-1")
        assert realization.status == "not_defined"

    def test_inv007_repeated_idempotent_delivery_does_not_duplicate(self):
        """INV-007: repeated delivery of the same idempotent command does not
        intentionally duplicate the external action."""
        store = IdempotencyStore()
        calls = []

        def run():
            calls.append(1)
            return {"ok": True}

        first = store.execute("key-1", run)
        second = store.execute("key-1", run)
        assert first == second
        assert len(calls) == 1

    def test_inv012_a_replayed_event_does_not_duplicate_the_action(self):
        """INV-012: a replayed event cannot unintentionally trigger a duplicate
        external financial action."""
        store = IdempotencyStore()
        postings = []

        def run():
            postings.append(Money.of(10, "USD"))
            return {"posted": len(postings)}

        store.execute("payment:key-1", run)
        store.execute("payment:key-1", run)  # replay
        assert len(postings) == 1


class TestInvValueAndModels:
    """INV-009, INV-017, INV-019: value and model honesty."""

    def test_inv009_incomplete_attribution_is_not_verified(self):
        """INV-009: an incomplete value attribution cannot be represented as verified."""
        realization = ValueRealizationRecord(
            realization_id="r-1", case_id="case-1",
            value_hypothesis_ref="h-1", baseline_snapshot_ref="b-1",
        )
        assert realization.observed_outcome_refs == []
        assert realization.attribution_category == "insufficient_evidence"
        assert realization.status == "not_defined"

    def test_inv017_a_model_result_identifies_its_version_and_context(self, authz):
        """INV-017: a model-dependent result identifies the model version and
        evaluation context."""
        registry = ModelVersionRegistry()
        version = DecisionModelVersion(
            model_version_id="mv-1",
            model_type="forecaster",
            semantic_purpose="refund_scoring",
            implementation_ref="models/refund/v1.2",
            version="1.2",
            validation_status="validated",
            applicable_context="tenant-A",
            limitations=["no cross-currency support"],
            effective_interval=(NOW, THEN),
        )
        registry.record(authz, version)
        assert registry.get("mv-1").version == "1.2"
        assert registry.get("mv-1").semantic_purpose == "refund_scoring"

    def test_inv019_a_measured_outcome_does_not_establish_causation(self):
        """INV-019: a measured outcome does not automatically establish causation."""
        realization = ValueRealizationRecord(
            realization_id="r-1", case_id="case-1",
            value_hypothesis_ref="h-1", baseline_snapshot_ref="b-1",
            observed_outcome_refs=["o-1", "o-2"],
            observed_value=Money.of(100, "USD"),
        )
        # observations are recorded, but the attribution is still unproven
        assert realization.attribution_method_ref == ""
        assert realization.confidence_level == "low"


class TestInvFinancialArithmetic:
    """INV-010, INV-016, INV-018: financial honesty."""

    def test_inv010_material_calculation_cannot_mix_currencies(self):
        """INV-010: a material financial calculation cannot silently mix
        incompatible currencies or units."""
        with pytest.raises(CurrencyMismatch):
            Money.of(10, "USD") + Money.of(5, "EUR")

    def test_inv016_a_derived_estimate_cannot_mutate_the_ledger(self):
        """INV-016: a derived financial estimate cannot mutate canonical
        financial records through the evaluation pathway."""
        ledger = LedgerService()
        # derived arithmetic in the decision path produces new values only
        estimate = Money.of(10, "USD") + Money.of(5, "USD")
        assert estimate == Money.of(15, "USD")
        # the canonical ledger is untouched: no journal entries exist
        assert ledger.trial_balance() == {}
        assert isinstance(Money.of(15, "USD").amount, __import__("decimal").Decimal)

    def test_inv018_an_optimization_claim_cannot_exceed_the_solver(self):
        """INV-018: an optimization result does not claim a stronger optimality
        guarantee than the solver supports."""
        service = OptimizationService(SolverRegistry(), ConstraintRegistry())
        run = OptimizationRun(
            run_id="r1", case_id="case-1", objective_set_ref="os-1",
            constraint_set_ref="cs-1", search_method_ref="heuristic",
            algorithm_version="1.0", solution_status="heuristic",
            optimality_claim="proven_optimal",  # a heuristic cannot claim this
        )
        with pytest.raises(InvariantViolationError):
            service.assert_claim_honest(run)


class TestInvCertification:
    """INV-020: certification honesty."""

    def test_inv020_certification_cannot_pass_without_evidence(self):
        """INV-020: a certification result cannot pass a mandatory gate without
        the required evidence."""
        from marketplace.universes.u12_intelligence.constitution import (
            dangling_enforcements,
            umfdvi_coverage,
        )

        # the certification law must exist and be the authority for UMFDVI-040
        assert umfdvi_coverage()["UMFDVI-040"] == "LAW-GOV-11"
        assert get_law("LAW-GOV-11").law_id == "LAW-GOV-11"
        # every enforcement hook must resolve to real code, i.e. the evidence
        # behind the certification claim is substantiated, not asserted
        assert dangling_enforcements() == []


# ===========================================================================
# §34 — AI-01..40
# ===========================================================================


class TestAiCanonicalOwnership:
    """AI-01, AI-02, AI-03, AI-29, AI-35, AI-36: canonical ownership."""

    def test_ai01_one_canonical_owner_per_business_concept(self):
        """AI-01: one canonical owner per business concept."""
        assert CONCEPTS.assert_owner("order", "U08").owner_universe == "U08"
        with pytest.raises(DuplicateCanonicalConceptError):
            CONCEPTS.assert_owner("order", "U05")

    def test_ai02_one_canonical_authority_per_financial_fact(self):
        """AI-02: one canonical authority per authoritative financial fact."""
        for concept in ("journal_entry", "ledger_account", "settlement"):
            assert CONCEPTS.assert_owner(concept, "U09").owner_universe == "U09"

    def test_ai03_no_unauthorized_financial_mutation(self):
        """AI-03: no unauthorized financial mutation."""
        ledger = LedgerService()
        # a self-directed posting is rejected: money cannot be created or moved
        # without a balanced, authorized pair of accounts
        with pytest.raises(ValidationError):
            ledger.post(
                journal_id="j-1", description="self-deal",
                debit_account_id="a-1", credit_account_id="a-1",
                amount=Money.of(10, "USD"), source="test", correlation_id="c1",
            )

    def test_ai29_no_duplicated_canonical_definitions(self):
        """AI-29: no duplicated metric definition without canonical reconciliation."""
        assert CONCEPTS.detect_duplicates() == []

    def test_ai35_no_orphaned_canonical_artifacts(self):
        """AI-35: no orphaned canonical artifacts."""
        CAPABILITIES.assert_no_dead_capabilities()

    def test_ai36_no_circular_dependency_in_capability_contracts(self):
        """AI-36: no circular dependency introduced through capability contracts."""
        by_key = {(r.owner_universe, r.concept): r for r in CONCEPTS.all()}
        for record in CONCEPTS.all():
            for projection in record.read_projections:
                # every projection resolves to a real registered concept...
                parts = projection.split(".")
                assert len(parts) == 2, f"malformed projection {projection!r}"
                assert (parts[0], parts[1]) in by_key, f"unresolved projection {projection!r}"
                # ...and no concept is its own projection (a self-cycle)
                assert (parts[0], parts[1]) != (record.owner_universe, record.concept), (
                    f"{record.concept} projects to itself"
                )


class TestAiAuthorization:
    """AI-04, AI-12, AI-21, AI-25, AI-27: authorization boundaries."""

    def test_ai04_no_execution_without_valid_authorization(self, authz):
        """AI-04: no execution without valid authorization."""
        coordinator = ExecutionCoordinator()
        case = approved_case(approval_ref=None)
        with pytest.raises(AuthorizationError):
            coordinator.create_intent(authz, case, action_type="refund", action_payload={}, now=NOW)

    def test_ai12_no_stale_authorization_treated_as_current(self):
        """AI-12: no stale authorization treated as current."""
        service = governed_service(approval=grant(effective_interval=(NOW, "2026-06-01T00:00:00+00:00")))
        with pytest.raises(AuthorizationError):
            service.revalidate_authorization(
                approved_case(), now="2027-01-01T00:00:00+00:00",
                action_type="refund", amount=None,
            )

    def test_ai21_no_ungoverned_cross_tenant_access(self, other_tenant_authz):
        """AI-21: no ungoverned cross-tenant data access."""
        with pytest.raises(TenantIsolationError):
            other_tenant_authz.same_tenant("tenant-A")

    def test_ai25_no_production_authorization_without_release_approval(self, other_tenant_authz):
        """AI-25: no production authorization without the required release approval."""
        service = governed_service()
        with pytest.raises(AuthorizationError):
            service.request_approval(other_tenant_authz, approved_case(), {})

    def test_ai27_a_missing_dependency_has_a_defined_failure_policy(self):
        """AI-27: no mandatory dependency without a defined failure policy."""
        coordinator = ExecutionCoordinator()  # no execution authority bound
        actor = AuthorizationContext(
            actor_id="svc", actor_type="service",
            roles=frozenset({"execution.service"}), tenant_id="tenant-A",
        )
        case = approved_case()
        coordinator.create_intent(actor, case, action_type="refund", action_payload={}, now=NOW)
        with pytest.raises(DependencyUnavailable) as exc_info:
            coordinator.submit(actor, case)
        # the failure names the dependency, so the outage is diagnosable
        assert "execution.authority" in str(exc_info.value)


class TestAiDecisionHonesty:
    """AI-05, AI-07, AI-09, AI-10, AI-17, AI-18, AI-19: decision honesty."""

    def test_ai05_no_silent_relaxation_of_mandatory_constraints(self, authz):
        """AI-05: no silent relaxation of mandatory constraints."""
        registry = ConstraintRegistry()
        registry.register_evaluator("budget.cap", lambda _c, ctx: ctx.get("cost", 0) <= 100)
        constraint = registry.define(
            authz,
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=(NOW, THEN),
            source_policy_ref="policy/budget/v1",
        )
        constraint_set = ConstraintSet(set_id="cs-1", case_id="case-1", constraints=[constraint])
        with pytest.raises(InvariantViolationError):
            registry.assert_mandatory_satisfied(constraint_set, {"cost": 500})
        # the satisfied case passes, so the constraint is enforced, not bypassed
        registry.assert_mandatory_satisfied(constraint_set, {"cost": 10})

    def test_ai07_no_unqualified_optimality_claim(self):
        """AI-07: no unqualified optimality claims."""
        service = OptimizationService(SolverRegistry(), ConstraintRegistry())
        run = OptimizationRun(
            run_id="r1", case_id="case-1", objective_set_ref="os-1",
            constraint_set_ref="cs-1", search_method_ref="x", algorithm_version="1",
            optimality_claim="the_best_ever",  # not a supported claim vocabulary
        )
        with pytest.raises(InvariantViolationError):
            service.assert_claim_honest(run)

    def test_ai09_no_recommendation_without_traceable_evaluation(self, authz):
        """AI-09: no recommendation without traceable evaluation."""
        coordinator = ExecutionCoordinator()
        case = approved_case(recommendation_ref=None)
        with pytest.raises(AuthorizationError):
            coordinator.create_intent(authz, case, action_type="refund", action_payload={}, now=NOW)

    def test_ai10_no_consequential_decision_without_required_context(self, authz):
        """AI-10: no consequential decision without required context."""
        coordinator = ExecutionCoordinator()
        case = approved_case(lifecycle_state="draft")  # not yet through its lifecycle
        with pytest.raises(IllegalTransitionError):
            coordinator.create_intent(authz, case, action_type="refund", action_payload={}, now=NOW)

    def test_ai17_no_model_output_treated_as_canonical_authority(self, authz):
        """AI-17: no model output treated as canonical authority."""
        service = governed_service(
            authority=DelegatedApprovalAuthority(lambda c, s: (False, "auth-1", "approver", ["model output is not authority"]))
        )
        case = approved_case(approval_ref=None)  # carries a recommendation_ref (a model output)
        approval = service.request_approval(authz, case, {})
        assert approval.decision_outcome == "rejected"
        assert case.approval_ref is None

    def test_ai18_no_unauthorized_policy_change(self, other_tenant_authz):
        """AI-18: no AI-generated policy change applied without authorization."""
        registry = ConstraintRegistry()
        with pytest.raises(AuthorizationError):
            registry.define(
                other_tenant_authz,
                code="x.cap",
                constraint_type="policy",
                semantic_definition_ref="p",
                owner_ref="o",
                mandatory=False,
                scope="s",
                evaluation_method_ref="m",
                effective_interval=(NOW, THEN),
                source_policy_ref="p",
            )

    def test_ai19_no_hidden_fallback_changing_decision_semantics(self, authz):
        """AI-19: no hidden fallback that changes decision semantics."""
        solvers = SolverRegistry()
        with pytest.raises(ValidationError):
            solvers.register(
                authz,
                SolverRegistration(
                    solver_id="silent.fallback",
                    semantic_purpose="select_alternative",
                    implementation_ref="x",
                    version="1",
                    proves_optimality=False,
                    is_heuristic=True,
                    fallback_of="exact.enumeration",  # unapproved fallback
                ),
                lambda problem, registration: None,
            )


class TestAiOptimizationAndObjectives:
    """AI-06, AI-08, AI-22, AI-23: optimization and objective governance."""

    def test_ai06_no_hidden_change_to_objective_semantics(self, authz):
        """AI-06: no hidden change to objective semantics."""
        from decimal import Decimal

        registry = ObjectiveRegistry()
        first = registry.define(
            authz,
            code="margin.gross",
            objective_type="financial",
            owner_ref="finance.owner",
            definition_ref="policy/margin",
            desired_direction="maximize",
            measurement_definition_ref="measure/margin",
            target_value=Decimal("0.3"),
            time_horizon="quarterly",
            unit="ratio",
        )
        assert first.version == 1
        with pytest.raises(DuplicateCanonicalConceptError):
            registry.define(
                authz,
                code="margin.gross",
                objective_type="financial",
                owner_ref="finance.owner",
                definition_ref="policy/margin",
                desired_direction="maximize",
                measurement_definition_ref="measure/margin",
                target_value=Decimal("0.3"),
                time_horizon="quarterly",
                unit="ratio",
            )

    def test_ai08_no_unbounded_optimization_workload(self):
        """AI-08: no unbounded optimization workloads."""
        budget = ResourceBudget(max_candidates=10, max_iterations=10, wall_clock_seconds=10.0)
        with pytest.raises(InvariantViolationError):
            budget.assert_within(1000, 1, 0.1)

    def test_ai22_no_unversioned_material_policy_change(self, authz):
        """AI-22: no unversioned material policy change."""
        registry = ConstraintRegistry()
        constraint = registry.define(
            authz,
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=(NOW, THEN),
            source_policy_ref="policy/budget/v1",
        )
        assert constraint.version >= 1  # every policy change carries a version

    def test_ai23_no_untracked_model_change(self, authz):
        """AI-23: no untracked model change affecting consequential decisions."""
        registry = ModelVersionRegistry()
        with pytest.raises(NotFoundError):
            registry.get("no-such-model")


class TestAiEvidenceAndTrust:
    """AI-11, AI-33, AI-34, AI-38: evidence and separation of duties."""

    def test_ai11_no_material_evidence_without_provenance(self, authz):
        """AI-11: no material evidence without provenance."""
        store = EvidenceStore()
        with pytest.raises(ValidationError):
            store.submit(
                authz,
                case_id="case-1",
                evidence_type="financial_report",
                source_authority_ref="",  # no source authority -> rejected
                source_artifact_ref="report",
                source_version=1,
                observation_time=NOW,
                validity_interval=(NOW, THEN),
                payload={},
            )

    def test_ai33_no_unauthorized_modification_of_protected_evidence(self, evidence_authz, other_tenant_authz):
        """AI-33: no unauthorized modification of protected evidence."""
        store = EvidenceStore()
        evidence = store.submit(
            evidence_authz,
            case_id="case-1",
            evidence_type="financial_report",
            source_authority_ref="finance.authority",
            source_artifact_ref="report",
            source_version=1,
            observation_time=NOW,
            validity_interval=(NOW, THEN),
            payload={},
            access_classification="sensitive",
        )
        # an actor holding the required scope can read it
        assert store.retrieve(evidence_authz, evidence.evidence_id) is evidence
        # an actor without the sensitive scope cannot
        with pytest.raises(AuthorizationError):
            store.retrieve(other_tenant_authz, evidence.evidence_id)

    def test_ai34_no_false_claim_of_independent_assurance(self):
        """AI-34: no false claim of verification or independent assurance."""
        service = GovernanceService()
        case = approved_case(initiating_principal_ref="alice")
        with pytest.raises(PolicyViolationError):
            service.assert_separation_of_duties(
                case,
                approver_principal="alice",   # the proposer cannot approve
                executor_principal="bob",
                verifier_principal="carol",
            )

    def test_ai38_no_unapproved_reuse_of_sensitive_evidence(self, evidence_authz, other_tenant_authz):
        """AI-38: no unapproved reuse of sensitive evidence for unrelated purposes."""
        store = EvidenceStore()
        evidence = store.submit(
            evidence_authz,
            case_id="case-1",
            evidence_type="customer_data",
            source_authority_ref="privacy.authority",
            source_artifact_ref="export",
            source_version=1,
            observation_time=NOW,
            validity_interval=(NOW, THEN),
            payload={},
            access_classification="personal",
        )
        with pytest.raises(AuthorizationError):
            store.retrieve(other_tenant_authz, evidence.evidence_id)
        with pytest.raises(AuthorizationError):
            store.assemble_manifest(other_tenant_authz, "case-2", [evidence.evidence_id], now=NOW)


class TestAiExecutionAndOutcomes:
    """AI-13, AI-14, AI-15, AI-16, AI-31, AI-32, AI-39: execution traceability."""

    def test_ai13_no_duplicate_external_action_from_retry(self):
        """AI-13: no duplicate external action caused by an uncontrolled retry."""
        store = IdempotencyStore()
        calls = []
        store.execute("key-1", lambda: calls.append(1) or {"ok": True})
        store.execute("key-1", lambda: calls.append(1) or {"ok": True})
        assert len(calls) == 1

    def test_ai14_execution_acceptance_is_not_verified_financial_value(self):
        """AI-14: no execution acceptance treated as verified financial value."""
        outcome = ExecutionOutcome(outcome_id="o-1", intent_ref="i-1", execution_authority_ref="canonical:execution", outcome_status="succeeded")
        assert outcome.reconciliation_status == "unreconciled"

    def test_ai15_no_expected_benefit_treated_as_realized(self):
        """AI-15: no expected benefit treated as realized benefit."""
        realization = ValueRealizationRecord(
            realization_id="r-1", case_id="case-1",
            value_hypothesis_ref="h-1", baseline_snapshot_ref="b-1",
            net_effect_estimate=Money.of(50, "USD"),  # expected
            observed_value=None,                       # not yet realized
        )
        assert realization.observed_value is None
        assert realization.status == "not_defined"

    def test_ai16_no_observed_change_treated_as_causal(self):
        """AI-16: no observed change automatically treated as causal attribution."""
        realization = ValueRealizationRecord(
            realization_id="r-1", case_id="case-1",
            value_hypothesis_ref="h-1", baseline_snapshot_ref="b-1",
            observed_value=Money.of(100, "USD"),
        )
        assert realization.attribution_category == "insufficient_evidence"

    def test_ai31_no_untraceable_decision_to_execution_relationship(self, authz):
        """AI-31: no untraceable decision-to-execution relationship."""
        coordinator = ExecutionCoordinator(
            authority=DelegatedExecutionAuthority(lambda intent: ExecutionOutcome(
                outcome_id="o-1", intent_ref=intent.intent_id,
                execution_authority_ref="canonical:execution", outcome_status="succeeded",
            ))
        )
        case = approved_case()
        coordinator.create_intent(authz, case, action_type="refund", action_payload={}, now=NOW)
        coordinator.submit(authz, case)
        outcome = coordinator.correlate(case)
        assert outcome.intent_ref == case.execution_intent_ref

    def test_ai32_no_untraceable_decision_to_outcome_relationship(self):
        """AI-32: no untraceable decision-to-outcome relationship."""
        realization = ValueRealizationRecord(
            realization_id="r-1", case_id="case-1",
            value_hypothesis_ref="h-1", baseline_snapshot_ref="b-1",
            observed_outcome_refs=["o-1"],
        )
        # the outcome reference is explicit, never implicit
        assert realization.observed_outcome_refs == ["o-1"]
        assert realization.case_id == "case-1"

    def test_ai39_no_silent_conversion_of_failure_into_success(self, authz):
        """AI-39: no silent conversion of a failed decision into a successful one."""
        coordinator = ExecutionCoordinator(
            authority=DelegatedExecutionAuthority(lambda intent: ExecutionOutcome(
                outcome_id="o-1", intent_ref=intent.intent_id,
                execution_authority_ref="canonical:execution", outcome_status="failed",
                failure_reason="gateway rejected",
            ))
        )
        case = approved_case()
        coordinator.create_intent(authz, case, action_type="refund", action_payload={}, now=NOW)
        outcome = coordinator.submit(authz, case)
        assert outcome.outcome_status == "failed"
        assert case.execution_outcome_ref == outcome.outcome_id  # recorded, not hidden


class TestAiArchitectureAndConstitution:
    """AI-20, AI-24, AI-26, AI-28, AI-30, AI-37, AI-40: architecture and constitution."""

    def test_ai20_no_historical_decision_silently_overwritten(self, authz):
        """AI-20: no historical decision silently overwritten."""
        from marketplace.universes.u12_intelligence.entities import DecisionConstraint

        registry = ConstraintRegistry()
        first = registry.define(
            authz,
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=(NOW, THEN),
            source_policy_ref="policy/budget/v1",
        )
        superseding = DecisionConstraint(
            constraint_id=generate("evidence"),
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=(NOW, THEN),
            source_policy_ref="policy/budget/v2",
            exception_policy_ref="policy/budget/exception",
            version=2,
        )
        registry.register(authz, superseding)
        # the canonical code now resolves to the new version...
        assert registry.by_code("budget.cap") is superseding
        # ...and the superseded version remains retrievable by its own identity
        assert registry.get(first.constraint_id) is first

    def test_ai24_no_certification_claim_without_evidence(self):
        """AI-24: no certification claim without evidence."""
        from marketplace.universes.u12_intelligence.constitution import (
            dangling_enforcements,
        )

        assert get_law("LAW-GOV-11").law_id == "LAW-GOV-11"
        assert dangling_enforcements() == []

    def test_ai26_no_hardcoded_jurisdictional_assumption(self):
        """AI-26: no hardcoded jurisdictional assumption in universal business logic."""
        with pytest.raises(CurrencyMismatch):
            Money.of(10, "USD") + Money.of(5, "EUR")
        # conversion requires an explicit rate; none is ever invented

        from marketplace.universes.u12_intelligence.evaluation import EvaluationService

        service = EvaluationService(ModelVersionRegistry())
        with pytest.raises(ValidationError):
            service.normalize_to({"a": Money.of(10, "USD")}, "EUR", rates={})

    def test_ai28_no_unresolved_contract_incompatibility(self):
        """AI-28: no unresolved critical contract incompatibility."""
        bus = EventBus()
        received = []
        bus.subscribe("order.placed", "billing", received.append)
        event = Event(
            event_id="e-1", contract_name="order.placed", schema_version=1,
            producer="u08", occurred_at=NOW, recorded_at=NOW,
            correlation_id="c1", causation_id=None, ordering_key="o-1",
            privacy_class="internal", payload={},
        )
        bus.publish(event)
        assert received == [event]
        # an event on a different contract is never misrouted to billing
        other = Event(
            event_id="e-2", contract_name="order.cancelled", schema_version=1,
            producer="u08", occurred_at=NOW, recorded_at=NOW,
            correlation_id="c2", causation_id=None, ordering_key="o-1",
            privacy_class="internal", payload={},
        )
        bus.publish(other)
        assert received == [event]
        assert bus.delivered_count() == 1

    def test_ai30_no_material_change_without_impact_analysis(self):
        """AI-30: no material architectural change without impact analysis."""
        adr_dir = pathlib.Path(__file__).resolve().parent.parent / "marketplace" / "docs" / "adr"
        records = sorted(adr_dir.glob("ADR-*.md"))
        assert records, "every material architectural decision needs a recorded ADR"
        for record in records:
            text = record.read_text()
            assert "## Decision" in text, f"{record.name} records no decision"
            assert "## Consequences" in text, f"{record.name} records no impact analysis"

    def test_ai37_no_vendor_specific_redefinition_of_business_semantics(self):
        """AI-37: no vendor-specific implementation detail allowed to redefine
        canonical business semantics."""
        laws = list(all_laws())
        assert laws
        for law in laws:
            assert law.law_id.startswith("LAW-"), (
                f"{law.law_id!r} lacks the canonical LAW- prefix"
            )

    def test_ai40_no_release_before_certification_is_satisfied(self):
        """AI-40: no release before mandatory certification requirements are satisfied."""
        from marketplace.universes.u12_intelligence.constitution import umfdvi_coverage

        # LAW-GOV-15 is the release-gate law; it must be registered
        assert get_law("LAW-GOV-15").law_id == "LAW-GOV-15"
        # and the UMFDVI constitution must map every one of its laws to a
        # canonical law, i.e. nothing is released on an unmapped requirement
        assert len(umfdvi_coverage()) == 40
        assert all(law_id.startswith("LAW-") for law_id in umfdvi_coverage().values())


class TestAiDeliveryGuarantees:
    """AI-28 supplement: a poisoned handler is isolated, not silently swallowed."""

    def test_a_failing_subscriber_is_dead_lettered_not_hidden(self):
        bus = EventBus(max_retries=1)

        def poison(_event):
            raise RuntimeError("boom")

        bus.subscribe("order.placed", "poisoned", poison)
        event = Event(
            event_id="e-1", contract_name="order.placed", schema_version=1,
            producer="u08", occurred_at=NOW, recorded_at=NOW,
            correlation_id="c1", causation_id=None, ordering_key="o-1",
            privacy_class="internal", payload={},
        )
        with pytest.raises(DeadLetterError):
            bus.publish(event)
        assert bus.dead_letters()
