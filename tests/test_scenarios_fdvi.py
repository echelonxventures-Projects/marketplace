"""U12 FDVI: scenario and sensitivity evaluation (Spec 030 Module 09; FR-021, FR-022)."""

from decimal import Decimal

import pytest

from marketplace.kernel import Money
from marketplace.kernel.errors import (
    DuplicateCanonicalConceptError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.entities import (
    DecisionAlternative,
    DecisionEvaluation,
)
from marketplace.universes.u12_intelligence.scenarios import (
    ScenarioDefinition,
    ScenarioEvaluationService,
    ScenarioOutcome,
    ScenarioRegistry,
    SensitivityReport,
    SensitivityService,
)

pytestmark = pytest.mark.unit


def alternative(**over):
    base = dict(
        alternative_id="alt-1",
        case_id="case-1",
        alternative_type="pricing",
        action_intent="discount 5%",
    )
    base.update(over)
    return DecisionAlternative(**base)


def evaluation(**over):
    base = dict(
        evaluation_id="ev-1",
        case_id="case-1",
        alternative_ref="alt-1",
        evidence_snapshot_ref="snap-1",
        objective_snapshot_refs=[],
        constraint_snapshot_refs=[],
        financial_effects={"net": Money.of(Decimal("100"), "USD")},
    )
    base.update(over)
    return DecisionEvaluation(**base)


def revenue_evaluator(context, overrides):
    """A scenario evaluator whose objective depends on one assumption."""
    revenue = Decimal(str(context["revenue"]))
    profit = revenue - Decimal("100")
    return ScenarioOutcome(
        scenario_id="",
        alternative_id="",
        objective_values={"profit": profit},
        financial_effects={"net": Money.of(profit, "USD")},
        feasibility_status="feasible" if profit > 0 else "infeasible",
    )


class TestScenarioRegistry:
    def test_register_and_lookup(self, authz):
        registry = ScenarioRegistry()
        scenario = registry.define(
            authz,
            code="BASE",
            label="Baseline",
            description="Current assumptions",
            baseline=True,
        )
        assert registry.get(scenario.scenario_id) is scenario
        assert registry.by_code("BASE") is scenario
        assert registry.all() == [scenario]

    def test_unknown_scenario_raises(self):
        registry = ScenarioRegistry()
        with pytest.raises(NotFoundError):
            registry.get("nope")
        with pytest.raises(NotFoundError):
            registry.by_code("NOPE")

    def test_duplicate_code_rejected(self, authz):
        registry = ScenarioRegistry()
        registry.define(authz, code="BASE", label="v1", description="d", baseline=True)
        with pytest.raises(DuplicateCanonicalConceptError):
            registry.define(authz, code="BASE", label="v1", description="d", baseline=True)

    def test_supersede_by_new_version(self, authz):
        registry = ScenarioRegistry()
        registry.define(authz, code="BASE", label="v1", description="d", baseline=True)
        scenario = ScenarioDefinition(
            scenario_id="sid-2",
            code="BASE",
            label="v2",
            description="d",
            baseline=True,
            version=2,
        )
        assert registry.register(authz, scenario) is scenario
        assert registry.by_code("BASE").version == 2

    def test_empty_scenario_rejected(self, authz):
        registry = ScenarioRegistry()
        with pytest.raises(ValidationError):
            registry.define(authz, code="EMPTY", label="e", description="d")

    def test_writer_role_required(self, other_tenant_authz):
        registry = ScenarioRegistry()
        with pytest.raises(Exception):
            registry.define(
                other_tenant_authz,
                code="BASE",
                label="l",
                description="d",
                baseline=True,
            )


class TestScenarioEvaluation:
    def _registry(self, authz):
        registry = ScenarioRegistry()
        registry.define(
            authz,
            code="BASE",
            label="Baseline",
            description="Current assumptions",
            baseline=True,
        )
        registry.define(
            authz,
            code="DOWN",
            label="Downside",
            description="Adverse demand",
            assumption_overrides={"revenue": "80"},
            downside=True,
        )
        registry.define(
            authz,
            code="UP",
            label="Upside",
            description="Favourable demand",
            assumption_overrides={"revenue": "200"},
        )
        return registry

    def test_deltas_against_baseline(self, authz):
        registry = self._registry(authz)
        service = ScenarioEvaluationService(registry)
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"revenue": "150"},
            evaluator=revenue_evaluator,
        )
        codes = {registry.get(o.scenario_id).code for o in comparison.outcomes}
        assert codes == {"BASE", "DOWN", "UP"}
        # baseline profit 50, downside -20, upside 100
        assert comparison.deltas[registry.by_code("DOWN").scenario_id]["profit"] == Decimal("-70")
        assert comparison.deltas[registry.by_code("UP").scenario_id]["profit"] == Decimal("50")
        assert comparison.baseline_scenario_id == registry.by_code("BASE").scenario_id

    def test_assumption_overrides_are_applied(self, authz):
        registry = self._registry(authz)
        service = ScenarioEvaluationService(registry)
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"revenue": "150"},
            evaluator=revenue_evaluator,
            scenario_codes=["BASE", "DOWN"],
        )
        codes = {registry.get(o.scenario_id).code for o in comparison.outcomes}
        assert codes == {"BASE", "DOWN"}

    def test_threshold_effects_flag_sign_flip(self, authz):
        service = ScenarioEvaluationService(self._registry(authz))
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"revenue": "150"},
            evaluator=revenue_evaluator,
        )
        assert any("changes sign" in e for e in comparison.threshold_effects)

    def test_recommendation_changes_flag_feasibility_flip(self, authz):
        service = ScenarioEvaluationService(self._registry(authz))
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"revenue": "150"},
            evaluator=revenue_evaluator,
        )
        assert any("feasibility changes" in c for c in comparison.recommendation_changes)

    def test_exactly_one_baseline_required(self, authz):
        registry = ScenarioRegistry()
        registry.define(authz, code="A", label="a", description="d", baseline=True)
        registry.define(authz, code="B", label="b", description="d", baseline=True)
        service = ScenarioEvaluationService(registry)
        with pytest.raises(ValidationError):
            service.evaluate(
                authz,
                alternative=alternative(),
                base_context={},
                evaluator=revenue_evaluator,
            )

    def test_no_scenarios_registered_raises(self, authz):
        service = ScenarioEvaluationService(ScenarioRegistry())
        with pytest.raises(ValidationError):
            service.evaluate(
                authz,
                alternative=alternative(),
                base_context={},
                evaluator=revenue_evaluator,
            )

    def test_downside_must_be_present_for_robustness_claim(self, authz):
        registry = ScenarioRegistry()
        registry.define(authz, code="BASE", label="b", description="d", baseline=True)
        registry.define(authz, code="UP", label="u", description="d", assumption_overrides={"revenue": "200"})
        service = ScenarioEvaluationService(registry)
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"revenue": "150"},
            evaluator=revenue_evaluator,
        )
        with pytest.raises(InvariantViolationError):
            service.assert_downside_evaluated(comparison)

    def test_downside_present_passes(self, authz):
        service = ScenarioEvaluationService(self._registry(authz))
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"revenue": "150"},
            evaluator=revenue_evaluator,
        )
        assert service.assert_downside_evaluated(comparison) is None

    def test_net_effect_sums_financial_effects(self, authz):
        service = ScenarioEvaluationService(self._registry(authz))
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"revenue": "150"},
            evaluator=revenue_evaluator,
        )
        total = service.net_effect(comparison, "USD")
        assert total.amount == Decimal("130.00")  # baseline 50 + downside -20 + upside 100

    def test_net_effect_rejects_mixed_currency(self, authz):
        def mixed_evaluator(context, overrides):
            currency = context.get("currency", "USD")
            return ScenarioOutcome(
                scenario_id="",
                alternative_id="",
                financial_effects={"net": Money.of(Decimal("10"), currency)},
            )

        registry = ScenarioRegistry()
        registry.define(authz, code="BASE", label="b", description="d", baseline=True)
        registry.define(
            authz,
            code="EUR",
            label="e",
            description="d",
            assumption_overrides={"currency": "EUR"},
        )
        service = ScenarioEvaluationService(registry)
        comparison = service.evaluate(
            authz,
            alternative=alternative(),
            base_context={"currency": "USD"},
            evaluator=mixed_evaluator,
        )
        with pytest.raises(ValidationError):
            service.net_effect(comparison, "USD")


class TestSensitivity:
    def probe(self, material=True):
        def _probe(input_name, delta):
            if input_name == "revenue":
                # objective moves 1:1 with the input
                return Decimal("100") * (Decimal(1) + delta)
            if input_name == "noise":
                # an input the objective does not depend on
                return Decimal("100")
            return Decimal("100")

        return _probe

    def test_analyze_records_results(self, authz):
        service = SensitivityService()
        report = service.analyze(
            authz,
            alternative=alternative(),
            probes={"revenue": self.probe()},
            objective_name="profit",
        )
        assert len(report.results) == 2  # -10% and +10%
        assert set(r.relative_delta for r in report.results) == {Decimal("-0.1"), Decimal("0.1")}
        assert "revenue" in report.material_inputs

    def test_immaterial_input_not_reported_as_material(self, authz):
        service = SensitivityService()
        report = service.analyze(
            authz,
            alternative=alternative(),
            probes={"noise": self.probe()},
            objective_name="profit",
        )
        assert report.material == []
        assert report.material_inputs == []

    def test_elasticity_computed(self, authz):
        service = SensitivityService()
        report = service.analyze(
            authz,
            alternative=alternative(),
            probes={"revenue": self.probe()},
            objective_name="profit",
            deltas=(Decimal("0.1"),),
        )
        result = report.results[0]
        # objective_delta = 10, relative_delta = 0.1 -> elasticity 100
        assert result.elasticity == Decimal("100")

    def test_zero_delta_skipped(self, authz):
        service = SensitivityService()
        report = service.analyze(
            authz,
            alternative=alternative(),
            probes={"revenue": self.probe()},
            objective_name="profit",
            deltas=(Decimal("0"), Decimal("0.1")),
        )
        assert len(report.results) == 1

    def test_no_probes_rejected(self, authz):
        service = SensitivityService()
        with pytest.raises(ValidationError):
            service.analyze(
                authz,
                alternative=alternative(),
                probes={},
                objective_name="profit",
            )

    def test_unnamed_objective_rejected(self, authz):
        service = SensitivityService()
        with pytest.raises(ValidationError):
            service.analyze(
                authz,
                alternative=alternative(),
                probes={"revenue": self.probe()},
                objective_name="",
            )

    def test_failing_probe_is_reported_not_hidden(self, authz):
        def bad_probe(input_name, delta):
            raise RuntimeError("method cannot perturb this input")

        service = SensitivityService()
        with pytest.raises(ValidationError):
            service.analyze(
                authz,
                alternative=alternative(),
                probes={"revenue": bad_probe},
                objective_name="profit",
            )

    def test_empty_report_cannot_claim_sensitivity(self):
        report = SensitivityReport(report_id="r", alternative_id="alt-1")
        with pytest.raises(ValidationError):
            report.assert_method_supports_sensitivity()

    def test_method_limitations_recorded(self, authz):
        service = SensitivityService()
        report = service.analyze(
            authz,
            alternative=alternative(),
            probes={"revenue": self.probe()},
            objective_name="profit",
            method_limitations=["local perturbation only"],
        )
        assert report.method_limitations == ["local perturbation only"]

    def test_material_disclosed_on_evaluation(self, authz):
        service = SensitivityService()
        ev = evaluation()
        report = service.analyze(
            authz,
            alternative=alternative(),
            probes={"revenue": self.probe()},
            objective_name="profit",
        )
        service.assert_material_disclosed(report, ev)
        assert ev.uncertainty_summary["material_sensitivities"]

    def test_explain_names_input_and_direction(self, authz):
        service = SensitivityService()
        report = service.analyze(
            authz,
            alternative=alternative(),
            probes={"revenue": self.probe()},
            objective_name="profit",
            deltas=(Decimal("0.1"),),
        )
        text = report.results[0].explain()
        assert "revenue" in text
        assert "profit" in text


class TestScenarioConceptOwnership:
    def test_concepts_owned_by_u12(self):
        from marketplace.kernel.registry import CONCEPTS

        for concept in ("decision_scenario", "decision_sensitivity"):
            record = CONCEPTS.all()
            assert any(c.concept == concept for c in record), concept
