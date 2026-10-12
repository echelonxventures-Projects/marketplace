"""U12-FDVI feasibility and financial evaluation (Spec 030 Modules 05-07).

Enforces:
  * LAW-UMFDVI-006 — feasible / infeasible / unknown / unverified are distinct;
  * LAW-UMFDVI-010 — model-dependent results identify the model version;
  * LAW-UMFDVI-011 — material uncertainty is disclosed;
  * NFR-024/025 — Decimal money only, currency isolation (ADR-FDVI-003).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from marketplace.kernel import AuthorizationContext, Money, generate
from marketplace.kernel.errors import (
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.money import CurrencyMismatch, get_currency, sum_money, zero
from marketplace.universes.u12_intelligence.constraints import ConstraintRegistry, ConstraintSet
from marketplace.universes.u12_intelligence.entities import (
    DecisionAlternative,
    DecisionEvaluation,
    DecisionModelVersion,
)

FEASIBILITY_STATES = frozenset({"feasible", "infeasible", "unknown", "unverified"})


@dataclass
class FeasibilityResult:
    alternative_id: str
    status: str  # feasible | infeasible | unknown | unverified
    reasons: list[str] = field(default_factory=list)
    violated_mandatory: list[str] = field(default_factory=list)
    unverified_dependencies: list[str] = field(default_factory=list)


class FeasibilityGate:
    """Module 06: classifies alternatives against mandatory constraints (FR-014, FR-015)."""

    def __init__(self, constraints: ConstraintRegistry) -> None:
        self.constraints = constraints

    def evaluate(
        self,
        alternative: DecisionAlternative,
        constraint_set: ConstraintSet,
        candidate_context: dict,
    ) -> FeasibilityResult:
        reasons: list[str] = []
        violated_mandatory: list[str] = []
        unverified: list[str] = []

        for precondition in alternative.required_preconditions:
            if not candidate_context.get(precondition):
                reasons.append(f"precondition {precondition!r} is not established")
                violated_mandatory.append(precondition)

        for constraint in constraint_set.constraints:
            satisfied, reason = self.constraints.evaluate(constraint, candidate_context)
            if satisfied:
                continue
            if constraint.mandatory:
                violated_mandatory.append(constraint.code)
                reasons.append(f"mandatory constraint {constraint.code}: {reason}")
            else:
                reasons.append(f"soft constraint {constraint.code}: {reason}")

        for dependency in alternative.dependency_refs:
            if not candidate_context.get(f"dependency_available:{dependency}"):
                unverified.append(dependency)
                reasons.append(f"dependency {dependency!r} availability is unverified")

        if violated_mandatory:
            status = "infeasible"
        elif unverified or reasons:
            status = "unknown"
        else:
            status = "feasible"

        return FeasibilityResult(
            alternative_id=alternative.alternative_id,
            status=status,
            reasons=reasons,
            violated_mandatory=violated_mandatory,
            unverified_dependencies=unverified,
        )


class ModelVersionRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, DecisionModelVersion] = {}
        self._by_purpose: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def record(
        self, authz: AuthorizationContext, model: DecisionModelVersion
    ) -> DecisionModelVersion:
        """LAW-UMFDVI-010: model registration with validation status and limits."""
        authz.requires_role("model.governance")
        if not model.limitations:
            raise ValidationError(
                "A model registration must declare its known limitations "
                "(LAW-UMFDVI-010)"
            )
        with self._lock:
            self._by_id[model.model_version_id] = model
            self._by_purpose.setdefault(model.semantic_purpose, []).append(
                model.model_version_id
            )
        return model

    def get(self, model_version_id: str) -> DecisionModelVersion:
        model = self._by_id.get(model_version_id)
        if model is None:
            raise NotFoundError(f"Decision model version {model_version_id} not found")
        return model

    def approved_for(self, semantic_purpose: str, at: str) -> DecisionModelVersion:
        models = [self.get(i) for i in self._by_purpose.get(semantic_purpose, [])]
        usable = [
            m for m in models
            if m.validation_status == "validated"
            and m.retirement_status == "active"
            and (not m.effective_interval[0] or m.effective_interval[0] <= at)
            and (not m.effective_interval[1] or at <= m.effective_interval[1])
        ]
        if not usable:
            raise NotFoundError(
                f"No validated, active model is available for purpose {semantic_purpose!r} at {at}"
            )
        return usable[-1]

    def mark_drift(self, model_version_id: str, signal: str) -> DecisionModelVersion:
        model = self.get(model_version_id)
        model.retirement_status = "review_required"
        model.limitations = list(model.limitations) + [f"drift signal: {signal}"]
        return model

    def revalidate(self, model_version_id: str, status: str) -> DecisionModelVersion:
        if status not in {"validated", "failed", "retired"}:
            raise ValidationError(f"Unknown revalidation status {status!r}")
        model = self.get(model_version_id)
        model.validation_status = status
        if status == "retired":
            model.retirement_status = "retired"
        return model


class EvaluationService:
    """Module 07: financial and objective evaluation with honest uncertainty."""

    def __init__(self, models: ModelVersionRegistry) -> None:
        self.models = models
        self.evaluations: dict[str, DecisionEvaluation] = {}
        self._lock = threading.RLock()

    def evaluate(
        self,
        authz: AuthorizationContext,
        *,
        case_id: str,
        alternative: DecisionAlternative,
        evidence_snapshot_ref: str,
        objective_snapshot_refs: list[str],
        constraint_snapshot_refs: list[str],
        model_refs: list[str],
        policy_refs: list[str],
        evaluation_method_ref: str,
        objective_values: dict[str, Decimal],
        financial_effects: dict[str, Money],
        uncertainty_summary: dict,
        computed_at: str,
        feasibility: FeasibilityResult,
    ) -> DecisionEvaluation:
        if feasibility.status not in FEASIBILITY_STATES:
            raise ValidationError(
                f"Feasibility status {feasibility.status!r} is not one of {sorted(FEASIBILITY_STATES)}"
            )
        if not uncertainty_summary:
            uncertainty_summary = {
                "disclosed": False,
                "note": "no uncertainty summary was supplied for this evaluation",
            }
        for model_ref in model_refs:
            model = self.models.get(model_ref)
            if model.validation_status != "validated":
                raise ValidationError(
                    f"Evaluation references model {model_ref} whose validation status "
                    f"is {model.validation_status!r}; a material model-dependent "
                    "result must use a validated model (LAW-UMFDVI-010)"
                )
        evaluation = DecisionEvaluation(
            evaluation_id=generate("evidence"),
            case_id=case_id,
            alternative_ref=alternative.alternative_id,
            evidence_snapshot_ref=evidence_snapshot_ref,
            objective_snapshot_refs=list(objective_snapshot_refs),
            constraint_snapshot_refs=list(constraint_snapshot_refs),
            model_refs=list(model_refs),
            policy_refs=list(policy_refs),
            evaluation_method_ref=evaluation_method_ref,
            feasibility_result=feasibility.status,
            feasibility_reasons=list(feasibility.reasons),
            objective_values=dict(objective_values),
            financial_effects=dict(financial_effects),
            uncertainty_summary=dict(uncertainty_summary),
            computed_at=computed_at,
        )
        with self._lock:
            self.evaluations[evaluation.evaluation_id] = evaluation
        alternative.evaluation_result_ref = evaluation.evaluation_id
        alternative.feasibility_status = feasibility.status
        alternative.feasibility_reasons = list(feasibility.reasons)
        return evaluation

    # -- financial arithmetic (AC-013, NFR-024/025) -------------------------

    def expected_net_value(
        self,
        benefits: dict[str, Money],
        costs: dict[str, Money],
        losses: dict[str, Money] | None = None,
        opportunity_costs: dict[str, Money] | None = None,
    ) -> Money:
        """Spec 030 §9.2: Expected_Net_Value = benefits - costs - losses - opportunity costs.

        Every input must share one currency; there is no implicit conversion and
        no invented rate (ADR-FDVI-003).
        """
        buckets = [benefits, costs] + ([losses] if losses else []) + (
            [opportunity_costs] if opportunity_costs else []
        )
        currency = self._uniform_currency(buckets)
        gross = self._sum_or_zero(benefits, currency)
        total_cost = self._sum_or_zero(costs, currency)
        total_loss = self._sum_or_zero(losses or {}, currency)
        total_opportunity = self._sum_or_zero(opportunity_costs or {}, currency)
        return gross - total_cost - total_loss - total_opportunity

    def _uniform_currency(self, buckets: Iterable[dict[str, Money]]) -> str:
        codes: set[str] = set()
        for bucket in buckets:
            for value in bucket.values():
                codes.add(value.currency.code)
        if len(codes) > 1:
            raise CurrencyMismatch(
                f"Financial evaluation mixes currencies {sorted(codes)}; an approved "
                "conversion method is required — the engine never converts implicitly"
            )
        if not codes:
            raise ValidationError(
                "Expected-value calculation requires at least one monetary input so "
                "the currency is unambiguous"
            )
        return codes.pop()

    def _sum_or_zero(self, bucket: dict[str, Money], currency: str) -> Money:
        values = list(bucket.values())
        if not values:
            return zero(currency)
        return sum_money(values)

    def assert_currency_compatible(self, amounts: Iterable[Money]) -> str:
        codes = {a.currency.code for a in amounts}
        if len(codes) > 1:
            raise CurrencyMismatch(
                f"Incompatible currencies {sorted(codes)}; an approved normalization "
                "method is required or the calculation is rejected"
            )
        if not codes:
            raise ValidationError("No currency supplied")
        return codes.pop()

    def normalize_to(self, amounts: dict[str, Money], target: str, rates: dict[str, Decimal]) -> dict[str, Money]:
        """Convert a heterogeneous money map at explicitly supplied rates.

        Rates are caller-provided and provenance-tracked by the caller; the
        engine never invents a rate (AC-013).
        """
        target_currency = get_currency(target)
        out: dict[str, Money] = {}
        for key, amount in amounts.items():
            if amount.currency.code == target:
                out[key] = amount
                continue
            rate = rates.get(amount.currency.code)
            if rate is None:
                raise ValidationError(
                    f"No exchange rate supplied for {amount.currency.code} -> {target}; "
                    "conversion is refused rather than assumed"
                )
            out[key] = amount.convert_to(rate, target_currency)
        return out
