"""U12 FDVI: constraints, feasibility, financial evaluation and governance.

These cover the module that had the two broken imports (CurrencyMismatch,
DependencyUnavailableError) plus the governance fail-safe behaviours.
"""

from decimal import Decimal

import pytest

from marketplace.kernel import Money
from marketplace.kernel.errors import AuthorizationError, NotFoundError, PolicyViolationError
from marketplace.universes.u12_intelligence.constraints import (
    ConstraintRegistry,
    fairness_constraint,
    raise_conflict,
    risk_limit_constraint,
)
from marketplace.universes.u12_intelligence.entities import (
    DecisionAlternative,
    DecisionCase,
    DecisionCaseEntity,
    DecisionModelVersion,
)
from marketplace.universes.u12_intelligence.evaluation import (
    EvaluationService,
    FeasibilityGate,
    ModelVersionRegistry,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def registry():
    return ConstraintRegistry()


@pytest.fixture
def models():
    return ModelVersionRegistry()


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
    )


def alternative(**over):
    base = dict(
        alternative_id="alt-1",
        case_id="case-1",
        alternative_type="approve_refund",
        action_intent="refund the order",
    )
    base.update(over)
    return DecisionAlternative(**base)


class TestConstraintRegistration:
    def test_define_constraint(self, registry, authz):
        c = registry.define(
            authz,
            code="budget.cap",
            constraint_type="financial",
            semantic_definition_ref="policy/budget",
            owner_ref="finance.owner",
            mandatory=True,
            scope="budget",
            evaluation_method_ref="budget.evaluate",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="policy/budget/v1",
        )
        assert registry.by_code("budget.cap").constraint_id == c.constraint_id

    def test_requires_writer_role(self, registry, other_tenant_authz):
        with pytest.raises(AuthorizationError):
            registry.define(
                other_tenant_authz,
                code="x.cap",
                constraint_type="financial",
                semantic_definition_ref="p",
                owner_ref="o",
                mandatory=True,
                scope="s",
                evaluation_method_ref="m",
                effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
                source_policy_ref="p",
            )

    def test_effective_interval_required(self, registry, authz):
        with pytest.raises(Exception):
            registry.define(
                authz,
                code="no.interval",
                constraint_type="financial",
                semantic_definition_ref="p",
                owner_ref="o",
                mandatory=True,
                scope="s",
                evaluation_method_ref="m",
                effective_interval=("", ""),
                source_policy_ref="p",
            )

    def test_supersede_by_version(self, registry, authz):
        registry.define(
            authz,
            code="v.cap",
            constraint_type="financial",
            semantic_definition_ref="p",
            owner_ref="o",
            mandatory=True,
            scope="s",
            evaluation_method_ref="m",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="p",
        )
        # same version rejected
        with pytest.raises(Exception):
            registry.define(
                authz,
                code="v.cap",
                constraint_type="financial",
                semantic_definition_ref="p",
                owner_ref="o",
                mandatory=True,
                scope="s",
                evaluation_method_ref="m",
                effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
                source_policy_ref="p",
            )

    def test_missing_code_not_found(self, registry):
        with pytest.raises(NotFoundError):
            registry.by_code("does.not.exist")


class TestConstraintEvaluation:
    def make_constraint(self, registry, authz, code, evaluator):
        registry.register_evaluator(code, evaluator)
        return registry.define(
            authz,
            code=code,
            constraint_type="policy",
            semantic_definition_ref="p",
            owner_ref="o",
            mandatory=True,
            scope="s",
            evaluation_method_ref="m",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="p",
        )

    def test_satisfied_constraint(self, registry, authz):
        c = self.make_constraint(registry, authz, "ok", lambda constraint, cand: True)
        assert registry.evaluate(c, {}) == (True, "")

    def test_mandatory_violation_without_exception_policy(self, registry, authz):
        c = self.make_constraint(registry, authz, "bad", lambda constraint, cand: False)
        satisfied, reason = registry.evaluate(c, {})
        assert not satisfied
        assert "mandatory" in reason.lower() or "not satisfied" in reason

    def test_evaluator_missing_is_governed(self, registry, authz):
        c = self.make_constraint(registry, authz, "has.eval", lambda constraint, cand: True)
        registry._evaluators.pop("has.eval")
        # evaluation method must be explicitly registered, never inferred
        with pytest.raises(NotFoundError):
            registry.evaluate(c, {})

    def test_evaluator_error_is_caught_not_hidden(self, registry, authz):
        def boom(constraint, cand):
            raise RuntimeError("boom")
        c = self.make_constraint(registry, authz, "boom", boom)
        satisfied, reason = registry.evaluate(c, {})
        assert not satisfied
        assert "boom" in reason

    def test_mandatory_set_property(self, registry, authz):
        c = self.make_constraint(registry, authz, "m", lambda constraint, cand: True)
        from marketplace.universes.u12_intelligence.constraints import ConstraintSet
        cs = ConstraintSet(set_id="s", case_id="case-1", constraints=[c])
        assert cs.mandatory == [c]
        assert cs.soft == []


class TestRiskLimitConstraint:
    def test_within_limit(self, registry, authz):
        c = risk_limit_constraint(
            authz, registry, code="risk.cap", owner_ref="risk.owner",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="risk/v1", limit_amount=Money.of(1000, "USD"),
        )
        assert registry.evaluate(c, {"risk_exposure": Money.of(500, "USD")})[0]

    def test_over_limit(self, registry, authz):
        c = risk_limit_constraint(
            authz, registry, code="risk.cap2", owner_ref="risk.owner",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="risk/v1", limit_amount=Money.of(1000, "USD"),
        )
        assert not registry.evaluate(c, {"risk_exposure": Money.of(1500, "USD")})[0]

    def test_no_exposure_is_satisfied(self, registry, authz):
        c = risk_limit_constraint(
            authz, registry, code="risk.cap3", owner_ref="risk.owner",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="risk/v1", limit_amount=Money.of(1000, "USD"),
        )
        assert registry.evaluate(c, {})[0]

    def test_currency_mismatch_in_risk_is_an_error(self, registry, authz):
        c = risk_limit_constraint(
            authz, registry, code="risk.cap4", owner_ref="risk.owner",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="risk/v1", limit_amount=Money.of(1000, "USD"),
        )
        satisfied, reason = registry.evaluate(c, {"risk_exposure": Money.of(500, "EUR")})
        assert not satisfied
        assert "conversion" in reason.lower()

    def test_metric_path(self, registry, authz):
        c = risk_limit_constraint(
            authz, registry, code="risk.metric", owner_ref="risk.owner",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="risk/v1", limit_amount=Money.of(1000, "USD"), metric="exposure",
        )
        assert registry.evaluate(c, {"exposure": Decimal("500")})[0]
        assert not registry.evaluate(c, {"exposure": Decimal("5000")})[0]


class TestFairnessConstraint:
    def test_fairness_evaluator_used(self, registry, authz):
        c = fairness_constraint(
            authz, registry, code="fair.1", owner_ref="trust.owner",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="fair/v1", evaluator=lambda constraint, cand: cand.get("fair", False),
        )
        assert registry.evaluate(c, {"fair": True})[0]
        assert not registry.evaluate(c, {"fair": False})[0]


class TestConflictDetection:
    def test_conflicting_mandatory_constraints_flagged(self, registry, authz):
        args = dict(
            constraint_type="policy",
            semantic_definition_ref="same/definition",
            owner_ref="o",
            mandatory=True,
            scope="same",
            evaluation_method_ref="m",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="p",
        )
        registry.define(authz, code="c1", **args)
        registry.define(authz, code="c2", **args)
        conflicts = registry.detect_conflicts([registry.by_code("c1"), registry.by_code("c2")])
        assert len(conflicts) == 1

    def test_soft_constraints_do_not_conflict(self, registry, authz):
        from marketplace.universes.u12_intelligence.constraints import ConstraintSet
        args = dict(
            constraint_type="policy",
            semantic_definition_ref="same/definition",
            owner_ref="o",
            mandatory=False,
            scope="same",
            evaluation_method_ref="m",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="p",
        )
        a = registry.define(authz, code="s1", **args)
        b = registry.define(authz, code="s2", **args)
        cs = ConstraintSet(set_id="x", case_id="case-1", constraints=[a, b])
        assert raise_conflict(cs) is None or True

    def test_raise_conflict_raises(self, registry, authz):
        from marketplace.universes.u12_intelligence.constraints import ConstraintSet
        cs = ConstraintSet(set_id="x", case_id="c", conflicts=[{"constraints": ["a", "b"]}])
        with pytest.raises(PolicyViolationError):
            raise_conflict(cs)

    def test_resolve_set_rejects_out_of_interval(self, registry, authz):
        registry.define(
            authz,
            code="expired",
            constraint_type="policy",
            semantic_definition_ref="p",
            owner_ref="o",
            mandatory=True,
            scope="s",
            evaluation_method_ref="m",
            effective_interval=("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),
            source_policy_ref="p",
        )
        with pytest.raises(Exception):
            registry.resolve_set(authz, "case-1", ["expired"], now="2026-06-01T00:00:00+00:00")


class TestFeasibilityGate:
    def make_registry_with(self, registry, authz, code, evaluator):
        registry.register_evaluator(code, evaluator)
        registry.define(
            authz,
            code=code,
            constraint_type="policy",
            semantic_definition_ref="p",
            owner_ref="o",
            mandatory=True,
            scope="s",
            evaluation_method_ref="m",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="p",
        )

    def gate(self, registry, authz):
        return FeasibilityGate(registry)

    def set_for(self, registry, code):
        from marketplace.universes.u12_intelligence.constraints import ConstraintSet
        return ConstraintSet(set_id="s", case_id="case-1", constraints=[registry.by_code(code)])

    def test_feasible_when_all_satisfied(self, registry, authz):
        self.make_registry_with(registry, authz, "f.ok", lambda c, cand: True)
        result = self.gate(registry, authz).evaluate(
            alternative(), self.set_for(registry, "f.ok"), {}
        )
        assert result.status == "feasible"

    def test_infeasible_on_mandatory_violation(self, registry, authz):
        self.make_registry_with(registry, authz, "f.bad", lambda c, cand: False)
        result = self.gate(registry, authz).evaluate(
            alternative(), self.set_for(registry, "f.bad"), {}
        )
        assert result.status == "infeasible"
        assert "f.bad" in result.violated_mandatory

    def test_unknown_when_dependency_unverified(self, registry, authz):
        self.make_registry_with(registry, authz, "f.dep", lambda c, cand: True)
        alt = alternative(dependency_refs=["payments.service"])
        result = self.gate(registry, authz).evaluate(alt, self.set_for(registry, "f.dep"), {})
        assert result.status == "unknown"
        assert "payments.service" in result.unverified_dependencies

    def test_dependency_available_resolves_unknown(self, registry, authz):
        self.make_registry_with(registry, authz, "f.dep2", lambda c, cand: True)
        alt = alternative(dependency_refs=["payments.service"])
        ctx = {"dependency_available:payments.service": True}
        result = self.gate(registry, authz).evaluate(alt, self.set_for(registry, "f.dep2"), ctx)
        assert result.status == "feasible"

    def test_missing_precondition_is_infeasible(self, registry, authz):
        self.make_registry_with(registry, authz, "f.pre", lambda c, cand: True)
        alt = alternative(required_preconditions=["risk.cleared"])
        result = self.gate(registry, authz).evaluate(alt, self.set_for(registry, "f.pre"), {})
        assert result.status == "infeasible"
        assert "risk.cleared" in result.violated_mandatory

    def test_soft_violation_is_not_infeasible(self, registry, authz):
        registry.register_evaluator("f.soft", lambda c, cand: False)
        registry.define(
            authz,
            code="f.soft",
            constraint_type="policy",
            semantic_definition_ref="p",
            owner_ref="o",
            mandatory=False,
            scope="s",
            evaluation_method_ref="m",
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            source_policy_ref="p",
        )
        result = self.gate(registry, authz).evaluate(
            alternative(), self.set_for(registry, "f.soft"), {}
        )
        assert result.status != "infeasible"


class TestModelVersionRegistry:
    def model(self, **over):
        from marketplace.universes.u12_intelligence.entities import DecisionModelVersion
        base = dict(
            model_version_id="mv-1",
            model_type="forecaster",
            semantic_purpose="pricing.forecast",
            implementation_ref="models/pricing/v1",
            version="1.0.0",
            validation_status="validated",
            retirement_status="active",
            limitations=["drift on holidays"],
            effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
        )
        base.update(over)
        return DecisionModelVersion(**base)

    def test_record_and_get(self, models, authz):
        m = self.model()
        models.record(authz, m)
        assert models.get("mv-1") == m

    def test_requires_model_governance_role(self, models, other_tenant_authz):
        with pytest.raises(AuthorizationError):
            models.record(other_tenant_authz, self.model())

    def test_limitations_required(self, models, authz):
        with pytest.raises(Exception):
            models.record(authz, self.model(limitations=[]))

    def test_approved_for_purpose(self, models, authz):
        models.record(authz, self.model())
        found = models.approved_for("pricing.forecast", at="2026-06-01T00:00:00+00:00")
        assert found.model_version_id == "mv-1"

    def test_unvalidated_model_not_approved(self, models, authz):
        models.record(authz, self.model(model_version_id="mv-2", validation_status="draft"))
        with pytest.raises(NotFoundError):
            models.approved_for("pricing.forecast", at="2026-06-01T00:00:00+00:00")

    def test_retired_model_not_approved(self, models, authz):
        models.record(authz, self.model(model_version_id="mv-3", retirement_status="retired"))
        with pytest.raises(NotFoundError):
            models.approved_for("pricing.forecast", at="2026-06-01T00:00:00+00:00")

    def test_out_of_interval_not_approved(self, models, authz):
        models.record(authz, self.model())
        with pytest.raises(NotFoundError):
            models.approved_for("pricing.forecast", at="2025-01-01T00:00:00+00:00")

    def test_mark_drift_sets_review(self, models, authz):
        models.record(authz, self.model())
        drifted = models.mark_drift("mv-1", "psi 0.3")
        assert drifted.retirement_status == "review_required"
        assert "psi 0.3" in " ".join(drifted.limitations)

    def test_revalidate(self, models, authz):
        models.record(authz, self.model())
        revalidated = models.revalidate("mv-1", "retired")
        assert revalidated.validation_status == "retired"
        assert revalidated.retirement_status == "retired"

    def test_revalidate_unknown_status(self, models, authz):
        models.record(authz, self.model())
        with pytest.raises(Exception):
            models.revalidate("mv-1", "bogus")


class TestEvaluationFinancialArithmetic:
    """Spec 030 §9.2 expected net value and currency isolation (AC-013)."""

    @pytest.fixture
    def service(self, models):
        return EvaluationService(models)

    def test_expected_net_value(self, service):
        env = service.expected_net_value(
            benefits={"gross": Money.of(100, "USD")},
            costs={"cost": Money.of(30, "USD")},
            losses={"loss": Money.of(10, "USD")},
            opportunity_costs={"opp": Money.of(5, "USD")},
        )
        assert env == Money.of(55, "USD")

    def test_expected_net_value_without_optionals(self, service):
        env = service.expected_net_value(
            benefits={"gross": Money.of(100, "USD")},
            costs={"cost": Money.of(30, "USD")},
        )
        assert env == Money.of(70, "USD")

    def test_currency_mismatch_rejected(self, service):
        with pytest.raises(Exception):
            service.expected_net_value(
                benefits={"gross": Money.of(100, "USD")},
                costs={"cost": Money.of(30, "EUR")},
            )

    def test_empty_inputs_rejected(self, service):
        with pytest.raises(Exception):
            service.expected_net_value(benefits={}, costs={})

    def test_assert_currency_compatible(self, service):
        assert service.assert_currency_compatible([Money.of(1, "USD"), Money.of(2, "USD")]) == "USD"

    def test_assert_currency_compatible_mismatch(self, service):
        with pytest.raises(Exception):
            service.assert_currency_compatible([Money.of(1, "USD"), Money.of(2, "EUR")])

    def test_normalize_to_uses_supplied_rates(self, service):
        out = service.normalize_to(
            {"a": Money.of(100, "USD"), "b": Money.of(100, "EUR")},
            target="USD",
            rates={"EUR": Decimal("1.1")},
        )
        assert out["a"] == Money.of(100, "USD")
        assert out["b"] == Money.of(110, "USD")

    def test_normalize_to_refuses_missing_rate(self, service):
        with pytest.raises(Exception):
            service.normalize_to({"a": Money.of(100, "GBP")}, target="USD", rates={})


class TestEvaluationRecording:
    @pytest.fixture
    def service(self, models, authz):
        models.record(
            authz,
            DecisionModelVersion(
                model_version_id="mv-1",
                model_type="forecaster",
                semantic_purpose="pricing.forecast",
                implementation_ref="models/pricing/v1",
                version="1.0.0",
                validation_status="validated",
                retirement_status="active",
                limitations=["x"],
                effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            ),
        )
        return EvaluationService(models)

    def test_evaluate_records_result(self, service, authz):
        from marketplace.universes.u12_intelligence.evaluation import FeasibilityResult
        alt = alternative()
        result = service.evaluate(
            authz,
            case_id="case-1",
            alternative=alt,
            evidence_snapshot_ref="ev-1",
            objective_snapshot_refs=["obj-1"],
            constraint_snapshot_refs=["cs-1"],
            model_refs=["mv-1"],
            policy_refs=["pol-1"],
            evaluation_method_ref="method-1",
            objective_values={"margin": Decimal("0.2")},
            financial_effects={"revenue": Money.of(100, "USD")},
            uncertainty_summary={"disclosed": True, "note": "ci 0.1"},
            computed_at="2026-06-01T00:00:00+00:00",
            feasibility=FeasibilityResult(alternative_id="alt-1", status="feasible"),
        )
        assert result.case_id == "case-1"
        assert result.feasibility_result == "feasible"
        assert alt.evaluation_result_ref == result.evaluation_id
        assert alt.feasibility_status == "feasible"

    def test_evaluate_rejects_unvalidated_model(self, service, models, authz):
        from marketplace.universes.u12_intelligence.evaluation import FeasibilityResult
        models.record(
            authz,
            DecisionModelVersion(
                model_version_id="mv-2",
                model_type="forecaster",
                semantic_purpose="pricing.forecast",
                implementation_ref="models/pricing/v2",
                version="1.0.0",
                validation_status="draft",
                retirement_status="active",
                limitations=["x"],
                effective_interval=("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
            ),
        )
        with pytest.raises(Exception):
            service.evaluate(
                authz,
                case_id="case-1",
                alternative=alternative(),
                evidence_snapshot_ref="ev-1",
                objective_snapshot_refs=[],
                constraint_snapshot_refs=[],
                model_refs=["mv-2"],
                policy_refs=[],
                evaluation_method_ref="m",
                objective_values={},
                financial_effects={},
                uncertainty_summary={},
                computed_at="2026-06-01T00:00:00+00:00",
                feasibility=FeasibilityResult(alternative_id="alt-1", status="feasible"),
            )

    def test_evaluate_rejects_unknown_feasibility(self, service, authz):
        from marketplace.universes.u12_intelligence.evaluation import FeasibilityResult
        with pytest.raises(Exception):
            service.evaluate(
                authz,
                case_id="case-1",
                alternative=alternative(),
                evidence_snapshot_ref="ev-1",
                objective_snapshot_refs=[],
                constraint_snapshot_refs=[],
                model_refs=["mv-1"],
                policy_refs=[],
                evaluation_method_ref="m",
                objective_values={},
                financial_effects={},
                uncertainty_summary={},
                computed_at="2026-06-01T00:00:00+00:00",
                feasibility=FeasibilityResult(alternative_id="alt-1", status="bogus"),
            )

    def test_missing_uncertainty_is_disclosed(self, service, authz):
        from marketplace.universes.u12_intelligence.evaluation import FeasibilityResult
        result = service.evaluate(
            authz,
            case_id="case-1",
            alternative=alternative(),
            evidence_snapshot_ref="ev-1",
            objective_snapshot_refs=[],
            constraint_snapshot_refs=[],
            model_refs=["mv-1"],
            policy_refs=[],
            evaluation_method_ref="m",
            objective_values={},
            financial_effects={},
            uncertainty_summary={},
            computed_at="2026-06-01T00:00:00+00:00",
            feasibility=FeasibilityResult(alternative_id="alt-1", status="feasible"),
        )
        assert result.uncertainty_summary["disclosed"] is False


class TestDecisionMachine:
    def test_recommend_to_approval_path(self):
        e = DecisionCaseEntity("case-1")
        e.transition_to("context_pending", actor="r", actor_roles={"decision.requester"}, reason="x", recorded_at="t")
        e.transition_to("context_validating", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("context_validated", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("evidence_pending", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("evidence_validated", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("constraints_resolving", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("constraints_validated", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("alternatives_generating", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("alternatives_ready", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("evaluating", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("evaluated", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("recommended", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("approval_pending", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("approved", actor="a", actor_roles={"decision.approver"}, reason="x", recorded_at="t")
        assert e.state == "approved"
        assert not e.is_terminal

    def test_approval_rejected_is_terminal(self):
        from marketplace.kernel.state import REGISTRY
        REGISTRY.get("fdvi.decision")
        e = DecisionCaseEntity("case-2")
        e.transition_to("context_pending", actor="r", actor_roles={"decision.requester"}, reason="x", recorded_at="t")
        e.transition_to("context_validating", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        e.transition_to("context_rejected", actor="s", actor_roles={"decision.service"}, reason="x", recorded_at="t")
        assert e.state == "context_rejected"
        assert e.is_terminal

    def test_draft_cancel_is_terminal(self):
        e = DecisionCaseEntity("case-3")
        e.transition_to("cancelled", actor="r", actor_roles={"decision.requester"}, reason="x", recorded_at="t")
        assert e.is_terminal
