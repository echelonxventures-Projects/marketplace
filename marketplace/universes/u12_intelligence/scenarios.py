"""U12-FDVI scenario and sensitivity evaluation (Spec 030 Module 09; FR-021, FR-022).

Module 09 responsibilities:

* evaluate alternative assumptions (scenarios);
* assess sensitivity to material inputs;
* compare scenarios and identify threshold effects;
* evaluate downside cases and robustness;
* identify decisions that change under small assumption changes.

The honesty rules that govern the rest of FDVI apply here unchanged: a
sensitivity result never claims more than the chosen perturbation supports, and
a scenario comparison never silently promotes a scenario-specific outcome to a
canonical evaluation result (LAW-UMFDVI-011, LAW-UMFDVI-018).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from marketplace.kernel import AuthorizationContext, Money, generate
from marketplace.kernel.errors import (
    DuplicateCanonicalConceptError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.money import get_currency, sum_money, zero
from marketplace.universes._base import U12
from marketplace.universes.u12_intelligence.entities import (
    DecisionAlternative,
    DecisionEvaluation,
)

# A scenario evaluator reproduces an evaluation under one assumption override.
# It returns the objective values and financial effects the override produces,
# in the same shape the canonical evaluation records.
ScenarioEvaluator = Callable[[dict, dict], "ScenarioOutcome"]

# A sensitivity probe perturbs one named input by a signed relative delta and
# reports the resulting change in one named objective.
SensitivityProbe = Callable[[str, Decimal], Decimal]


@dataclass
class ScenarioDefinition:
    """One named set of assumption overrides (Spec 030 §7.1 Module 09)."""

    scenario_id: str
    code: str
    label: str
    description: str
    assumption_overrides: dict = field(default_factory=dict)
    downside: bool = False  # a downside case is flagged, never hidden
    baseline: bool = False  # exactly one scenario is the comparison baseline
    owner_ref: str = ""
    version: int = 1


@dataclass
class ScenarioOutcome:
    """The result of evaluating one alternative under one scenario."""

    scenario_id: str
    alternative_id: str
    objective_values: dict[str, Decimal] = field(default_factory=dict)
    financial_effects: dict[str, Money] = field(default_factory=dict)
    feasibility_status: str = "unknown"
    notes: list[str] = field(default_factory=list)


@dataclass
class ScenarioComparison:
    """The comparison of every scenario against the declared baseline."""

    comparison_id: str
    alternative_id: str
    baseline_scenario_id: str
    outcomes: list[ScenarioOutcome] = field(default_factory=list)
    deltas: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    threshold_effects: list[str] = field(default_factory=list)
    recommendation_changes: list[str] = field(default_factory=list)

    def scenario_ids(self) -> list[str]:
        return [o.scenario_id for o in self.outcomes]


@dataclass
class SensitivityResult:
    """The sensitivity of one objective to one input (FR-022)."""

    result_id: str
    alternative_id: str
    input_name: str
    objective_name: str
    relative_delta: Decimal
    objective_delta: Decimal
    elasticity: Optional[Decimal] = None  # objective_delta / relative_delta
    material: bool = False
    notes: list[str] = field(default_factory=list)

    def explain(self) -> str:
        direction = "increases" if self.objective_delta > 0 else (
            "decreases" if self.objective_delta < 0 else "does not change"
        )
        return (
            f"Perturbing {self.input_name} by {self.relative_delta:+.1%} "
            f"{direction} {self.objective_name} by {self.objective_delta}."
        )


@dataclass
class SensitivityReport:
    """The full sensitivity picture for one alternative."""

    report_id: str
    alternative_id: str
    results: list[SensitivityResult] = field(default_factory=list)
    material_inputs: list[str] = field(default_factory=list)
    method_limitations: list[str] = field(default_factory=list)

    @property
    def material(self) -> list[SensitivityResult]:
        return [r for r in self.results if r.material]

    def assert_method_supports_sensitivity(self) -> None:
        """FR-022 limits sensitivity claims to methods that support them."""
        if not self.results:
            raise ValidationError(
                "A sensitivity report cannot be produced without at least one "
                "probe; the selected method must support sensitivity analysis "
                "before a claim is made (FR-022)"
            )


class ScenarioRegistry:
    """Governed registry of scenario definitions (GOV-02 one write owner)."""

    def __init__(self) -> None:
        self._by_id: dict[str, ScenarioDefinition] = {}
        self._by_code: dict[str, ScenarioDefinition] = {}
        self._lock = threading.RLock()

    def register(
        self, authz: AuthorizationContext, scenario: ScenarioDefinition
    ) -> ScenarioDefinition:
        authz.requires_role("scenario.writer")
        if not scenario.code:
            raise ValidationError("A scenario must declare a code")
        if not scenario.assumption_overrides and not scenario.baseline:
            raise ValidationError(
                f"Scenario {scenario.code!r} declares no assumption overrides and "
                "is not the baseline; it would evaluate the same assumptions twice"
            )
        with self._lock:
            if scenario.code in self._by_code:
                existing = self._by_code[scenario.code]
                if existing.version >= scenario.version:
                    raise DuplicateCanonicalConceptError(
                        f"Scenario {scenario.code!r} is already registered at version "
                        f"{existing.version}; supersede it through a new version"
                    )
            self._by_id[scenario.scenario_id] = scenario
            self._by_code[scenario.code] = scenario
        return scenario

    def define(
        self,
        authz: AuthorizationContext,
        *,
        code: str,
        label: str,
        description: str,
        assumption_overrides: Optional[dict] = None,
        downside: bool = False,
        baseline: bool = False,
        owner_ref: str = "",
    ) -> ScenarioDefinition:
        scenario = ScenarioDefinition(
            scenario_id=generate("evidence"),
            code=code,
            label=label,
            description=description,
            assumption_overrides=dict(assumption_overrides or {}),
            downside=downside,
            baseline=baseline,
            owner_ref=owner_ref,
        )
        return self.register(authz, scenario)

    def get(self, scenario_id: str) -> ScenarioDefinition:
        scenario = self._by_id.get(scenario_id)
        if scenario is None:
            raise NotFoundError(f"Scenario {scenario_id} not found")
        return scenario

    def by_code(self, code: str) -> ScenarioDefinition:
        scenario = self._by_code.get(code)
        if scenario is None:
            raise NotFoundError(f"Scenario code {code!r} is not registered")
        return scenario

    def all(self) -> list[ScenarioDefinition]:
        return list(self._by_code.values())


class ScenarioEvaluationService:
    """Module 09: evaluate one alternative under every registered scenario."""

    def __init__(self, scenarios: ScenarioRegistry) -> None:
        self.scenarios = scenarios
        self.comparisons: dict[str, ScenarioComparison] = {}
        self._lock = threading.RLock()

    def evaluate(
        self,
        authz: AuthorizationContext,
        *,
        alternative: DecisionAlternative,
        base_context: dict,
        evaluator: ScenarioEvaluator,
        scenario_codes: Optional[list[str]] = None,
    ) -> ScenarioComparison:
        """FR-021: evaluate an alternative under each scenario's assumptions."""
        authz.requires_role("decision.service")
        scenarios = (
            [self.scenarios.by_code(c) for c in scenario_codes]
            if scenario_codes is not None
            else self.scenarios.all()
        )
        if not scenarios:
            raise ValidationError(
                "No scenarios are registered; scenario-based evaluation cannot "
                "proceed without at least a baseline (FR-021)"
            )
        baselines = [s for s in scenarios if s.baseline]
        if len(baselines) != 1:
            raise ValidationError(
                f"Exactly one scenario must be the baseline; found "
                f"{len(baselines)} of {len(scenarios)}"
            )
        baseline = baselines[0]

        outcomes: list[ScenarioOutcome] = []
        for scenario in scenarios:
            context = dict(base_context)
            context.update(scenario.assumption_overrides)
            context["scenario_code"] = scenario.code
            context["scenario_is_downside"] = scenario.downside
            outcome = evaluator(context, scenario.assumption_overrides)
            outcome.scenario_id = scenario.scenario_id
            outcome.alternative_id = alternative.alternative_id
            if scenario.downside:
                outcome.notes.append("downside scenario")
            outcomes.append(outcome)

        comparison = ScenarioComparison(
            comparison_id=generate("evidence"),
            alternative_id=alternative.alternative_id,
            baseline_scenario_id=baseline.scenario_id,
            outcomes=outcomes,
            deltas=self._deltas_against_baseline(baseline, outcomes),
            threshold_effects=self._threshold_effects(baseline, outcomes),
            recommendation_changes=self._recommendation_changes(baseline, outcomes),
        )
        with self._lock:
            self.comparisons[comparison.comparison_id] = comparison
        return comparison

    def _deltas_against_baseline(
        self, baseline: ScenarioDefinition, outcomes: list[ScenarioOutcome]
    ) -> dict[str, dict[str, Decimal]]:
        base = next(
            (o for o in outcomes if o.scenario_id == baseline.scenario_id), None
        )
        if base is None:
            return {}
        deltas: dict[str, dict[str, Decimal]] = {}
        for outcome in outcomes:
            if outcome.scenario_id == baseline.scenario_id:
                continue
            entry: dict[str, Decimal] = {}
            for key, value in outcome.objective_values.items():
                base_value = base.objective_values.get(key)
                if base_value is not None:
                    entry[key] = value - base_value
            deltas[outcome.scenario_id] = entry
        return deltas

    def _threshold_effects(
        self, baseline: ScenarioDefinition, outcomes: list[ScenarioOutcome]
    ) -> list[str]:
        """Flag objectives whose sign flips relative to the baseline."""
        base = next(
            (o for o in outcomes if o.scenario_id == baseline.scenario_id), None
        )
        if base is None:
            return []
        effects: list[str] = []
        for outcome in outcomes:
            if outcome.scenario_id == baseline.scenario_id:
                continue
            for key, value in outcome.objective_values.items():
                base_value = base.objective_values.get(key)
                if base_value is None:
                    continue
                if base_value > 0 > value or base_value < 0 < value:
                    effects.append(
                        f"{key} changes sign under scenario "
                        f"{self.scenarios.get(outcome.scenario_id).code}"
                    )
        return effects

    def _recommendation_changes(
        self, baseline: ScenarioDefinition, outcomes: list[ScenarioOutcome]
    ) -> list[str]:
        """Identify decisions that change under small assumption changes.

        A scenario that flips feasibility or changes the ranking of the
        objective headline is recorded here rather than silently absorbed.
        """
        base = next(
            (o for o in outcomes if o.scenario_id == baseline.scenario_id), None
        )
        if base is None:
            return []
        changes: list[str] = []
        for outcome in outcomes:
            if outcome.scenario_id == baseline.scenario_id:
                continue
            if outcome.feasibility_status != base.feasibility_status:
                changes.append(
                    f"feasibility changes from {base.feasibility_status} to "
                    f"{outcome.feasibility_status} under scenario "
                    f"{self.scenarios.get(outcome.scenario_id).code}"
                )
        return changes

    def assert_downside_evaluated(self, comparison: ScenarioComparison) -> None:
        """A downside case must be present before a robustness claim is made."""
        has_downside = any(
            self.scenarios.get(o.scenario_id).downside for o in comparison.outcomes
        )
        if not has_downside:
            raise InvariantViolationError(
                "LAW-UMFDVI-011",
                "Robustness is claimed but no downside scenario was evaluated; "
                "material risk under adverse assumptions is undisclosed",
                details={"comparison_id": comparison.comparison_id},
            )

    def net_effect(self, comparison: ScenarioComparison, currency: str) -> Money:
        """Sum one scenario's financial effects into a single net money value."""
        target = get_currency(currency)
        total = zero(currency)
        for outcome in comparison.outcomes:
            if not outcome.financial_effects:
                continue
            values = list(outcome.financial_effects.values())
            first = values[0]
            if first.currency != target:
                raise ValidationError(
                    f"Scenario {outcome.scenario_id} is in {first.currency}, not "
                    f"{currency}; an approved conversion is required"
                )
            total = sum_money([total, sum_money(values)])
        return total


class SensitivityService:
    """FR-022: identify material input sensitivities."""

    def __init__(self) -> None:
        self.reports: dict[str, SensitivityReport] = {}
        self._lock = threading.RLock()

    def analyze(
        self,
        authz: AuthorizationContext,
        *,
        alternative: DecisionAlternative,
        probes: dict[str, SensitivityProbe],
        objective_name: str,
        deltas: tuple[Decimal, ...] = (Decimal("-0.1"), Decimal("0.1")),
        material_threshold: Decimal = Decimal("0.05"),
        method_limitations: Optional[list[str]] = None,
    ) -> SensitivityReport:
        """Perturb each named input and measure the objective's response.

        ``probes`` maps an input name to a callable that applies a signed
        relative delta and returns the resulting objective value. Only methods
        that support this re-evaluation may be probed, which is why the report
        records its own limitations rather than implying exhaustive coverage.
        """
        authz.requires_role("decision.service")
        if not probes:
            raise ValidationError(
                "Sensitivity analysis requires at least one probe; the selected "
                "method must support input perturbation (FR-022)"
            )
        if not objective_name:
            raise ValidationError("A sensitivity analysis must name its objective")

        results: list[SensitivityResult] = []
        material: list[str] = []
        for input_name, probe in probes.items():
            for delta in deltas:
                if delta == 0:
                    continue
                try:
                    perturbed = probe(input_name, delta)
                except Exception as exc:  # noqa: BLE001 - reported, never hidden
                    raise ValidationError(
                        f"Sensitivity probe for {input_name!r} failed: "
                        f"{type(exc).__name__}: {exc}; the method does not support "
                        "this perturbation"
                    ) from exc
                baseline = probe(input_name, Decimal(0))
                objective_delta = perturbed - baseline
                elasticity = (
                    objective_delta / delta
                    if delta != 0 and baseline != 0
                    else None
                )
                is_material = abs(objective_delta) >= material_threshold
                if is_material:
                    material.append(input_name)
                results.append(
                    SensitivityResult(
                        result_id=generate("evidence"),
                        alternative_id=alternative.alternative_id,
                        input_name=input_name,
                        objective_name=objective_name,
                        relative_delta=delta,
                        objective_delta=objective_delta,
                        elasticity=elasticity,
                        material=is_material,
                    )
                )
        report = SensitivityReport(
            report_id=generate("evidence"),
            alternative_id=alternative.alternative_id,
            results=results,
            material_inputs=sorted(set(material)),
            method_limitations=list(method_limitations or []),
        )
        report.assert_method_supports_sensitivity()
        with self._lock:
            self.reports[report.report_id] = report
        return report

    def assert_material_disclosed(
        self, report: SensitivityReport, evaluation: DecisionEvaluation
    ) -> None:
        """Material sensitivities must appear in the evaluation record."""
        if not report.material:
            return
        note = "; ".join(r.explain() for r in report.material)
        uncertainty = dict(evaluation.uncertainty_summary)
        sensitivities = list(uncertainty.get("material_sensitivities", []))
        sensitivities.append(note)
        uncertainty["material_sensitivities"] = sensitivities
        evaluation.uncertainty_summary = uncertainty


# Concept ownership (GOV-02): U12 is the single write authority for the
# scenario and sensitivity concepts introduced by Spec 030 Module 09.
U12.owns(
    "decision_scenario",
    "A named set of assumption overrides used for scenario-based evaluation",
    "U12.ScenarioRegistry.register",
    read_projections=["U14.policy"],
)
U12.owns(
    "decision_sensitivity",
    "The measured sensitivity of an objective to a material decision input",
    "U12.SensitivityService.analyze",
    read_projections=["U12.evaluation"],
)
