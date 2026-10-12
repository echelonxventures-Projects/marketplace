"""U12-FDVI constraint and policy resolution (Spec 030 Module 03; FR-007, FR-008).

Constraints are retrieved from their authoritative sources and evaluated as
data. Mandatory constraints are never silently relaxed to make a problem
feasible (LAW-UMFDVI-005). Conflicting mandatory constraints are surfaced, not
averaged away (LAW-UMFDVI-021, AC-007).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from marketplace.kernel import AuthorizationContext, Money, generate
from marketplace.kernel.errors import (
    InvariantViolationError,
    NotFoundError,
    PolicyViolationError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.entities import (
    DecisionConstraint,
    DecisionPolicySnapshot,
)


@dataclass
class ConstraintSet:
    set_id: str
    case_id: str
    constraints: list[DecisionConstraint] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    policy_snapshot: Optional[DecisionPolicySnapshot] = None
    version: int = 1

    @property
    def mandatory(self) -> list[DecisionConstraint]:
        return [c for c in self.constraints if c.mandatory]

    @property
    def soft(self) -> list[DecisionConstraint]:
        return [c for c in self.constraints if not c.mandatory]


# A constraint evaluator maps (constraint, candidate context) -> bool satisfied.
# It is registered per constraint code so evaluation stays data-driven (GOV-07).
Evaluator = Callable[[DecisionConstraint, dict], bool]


class ConstraintRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, DecisionConstraint] = {}
        self._by_code: dict[str, DecisionConstraint] = {}
        self._evaluators: dict[str, Evaluator] = {}
        self._lock = threading.RLock()

    def register(
        self, authz: AuthorizationContext, constraint: DecisionConstraint
    ) -> DecisionConstraint:
        authz.requires_role("constraint.writer")
        if not constraint.effective_interval[0] or not constraint.effective_interval[1]:
            raise ValidationError("A constraint must declare its effective interval")
        with self._lock:
            if constraint.code in self._by_code:
                existing = self._by_code[constraint.code]
                if existing.version >= constraint.version:
                    raise ValidationError(
                        f"Constraint {constraint.code!r} already registered at version "
                        f"{existing.version}; supersede through a new version"
                    )
            self._by_id[constraint.constraint_id] = constraint
            self._by_code[constraint.code] = constraint
        return constraint

    def define(
        self,
        authz: AuthorizationContext,
        *,
        code: str,
        constraint_type: str,
        semantic_definition_ref: str,
        owner_ref: str,
        mandatory: bool,
        scope: str,
        evaluation_method_ref: str,
        effective_interval: tuple[str, str],
        source_policy_ref: str,
        exception_policy_ref: Optional[str] = None,
    ) -> DecisionConstraint:
        constraint = DecisionConstraint(
            constraint_id=generate("evidence"),
            code=code,
            constraint_type=constraint_type,
            semantic_definition_ref=semantic_definition_ref,
            owner_ref=owner_ref,
            mandatory=mandatory,
            scope=scope,
            evaluation_method_ref=evaluation_method_ref,
            effective_interval=effective_interval,
            source_policy_ref=source_policy_ref,
            exception_policy_ref=exception_policy_ref,
        )
        return self.register(authz, constraint)

    def register_evaluator(self, code: str, evaluator: Evaluator) -> None:
        self._evaluators[code] = evaluator

    def evaluator_for(self, code: str) -> Evaluator:
        evaluator = self._evaluators.get(code)
        if evaluator is None:
            raise NotFoundError(
                f"No evaluator registered for constraint {code!r}; the evaluation "
                "method must be governed, not inferred"
            )
        return evaluator

    def get(self, constraint_id: str) -> DecisionConstraint:
        constraint = self._by_id.get(constraint_id)
        if constraint is None:
            raise NotFoundError(f"Constraint {constraint_id} not found")
        return constraint

    def by_code(self, code: str) -> DecisionConstraint:
        constraint = self._by_code.get(code)
        if constraint is None:
            raise NotFoundError(f"Constraint code {code!r} is not registered")
        return constraint

    def resolve_set(
        self,
        authz: AuthorizationContext,
        case_id: str,
        codes: list[str],
        *,
        now: str,
        precedence: Optional[list[str]] = None,
    ) -> ConstraintSet:
        constraints = [self.by_code(c) for c in codes]
        for constraint in constraints:
            start, end = constraint.effective_interval
            if now < start or now > end:
                raise InvariantViolationError(
                    "LAW-UMFDVI-014",
                    f"Constraint {constraint.code!r} is not effective at {now} "
                    f"(effective {start}..{end})",
                    details={"constraint": constraint.code, "now": now},
                )
        conflicts = self.detect_conflicts(constraints)
        snapshot = DecisionPolicySnapshot(
            snapshot_id=generate("evidence"),
            case_id=case_id,
            policy_refs=[c.source_policy_ref for c in constraints],
            policy_versions={c.code: c.version for c in constraints},
            authority_refs=[c.owner_ref for c in constraints],
            evaluation_time=now,
            scope=case_id,
            precedence_resolution=precedence or [],
            conflict_status="detected" if conflicts else "none",
        )
        return ConstraintSet(
            set_id=generate("evidence"),
            case_id=case_id,
            constraints=constraints,
            conflicts=conflicts,
            policy_snapshot=snapshot,
        )

    def detect_conflicts(self, constraints: list[DecisionConstraint]) -> list[dict]:
        """Detect mandatory constraints that cannot hold simultaneously."""
        conflicts: list[dict] = []
        by_scope: dict[str, list[DecisionConstraint]] = {}
        for c in constraints:
            if c.mandatory:
                by_scope.setdefault(c.scope, []).append(c)
        for scope, group in by_scope.items():
            if len(group) < 2:
                continue
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if a.semantic_definition_ref == b.semantic_definition_ref:
                        conflicts.append(
                            {
                                "constraints": [a.code, b.code],
                                "scope": scope,
                                "reason": "two mandatory constraints govern the same semantic definition",
                            }
                        )
        return conflicts

    def evaluate(self, constraint: DecisionConstraint, candidate: dict) -> tuple[bool, str]:
        evaluator = self.evaluator_for(constraint.code)
        try:
            satisfied = bool(evaluator(constraint, candidate))
        except Exception as exc:  # noqa: BLE001 - failure is reported, never hidden
            return False, f"evaluator error: {type(exc).__name__}: {exc}"
        if not satisfied and constraint.mandatory and constraint.exception_policy_ref is None:
            return False, "mandatory constraint violated; no exception policy is registered"
        return satisfied, "" if satisfied else f"constraint {constraint.code!r} not satisfied"

    def assert_mandatory_satisfied(self, constraint_set: ConstraintSet, candidate: dict) -> None:
        """LAW-UMFDVI-005: mandatory violations cannot be converted to feasible."""
        for constraint in constraint_set.mandatory:
            satisfied, reason = self.evaluate(constraint, candidate)
            if not satisfied:
                raise InvariantViolationError(
                    "LAW-UMFDVI-005",
                    f"Mandatory constraint {constraint.code!r} is violated: {reason}. "
                    "Mandatory constraints are not relaxed to reach feasibility.",
                    details={
                        "constraint": constraint.code,
                        "candidate": {k: v for k, v in candidate.items() if k != "payload"},
                    },
                )


# ---------------------------------------------------------------------------
# Canonical risk and fairness constraint shapes (Spec 030 §9.3, §P-025)
# ---------------------------------------------------------------------------


def risk_limit_constraint(
    authz: AuthorizationContext,
    registry: ConstraintRegistry,
    *,
    code: str,
    owner_ref: str,
    effective_interval: tuple[str, str],
    source_policy_ref: str,
    limit_amount: Optional[Money] = None,
    metric: Optional[str] = None,
) -> DecisionConstraint:
    """A risk boundary owned by the canonical risk authority (LAW-UMFDVI-013).

    The decision engine may *evaluate* against the limit; it may never accept
    risk on the authority's behalf.
    """
    constraint = DecisionConstraint(
        constraint_id=generate("evidence"),
        code=code,
        constraint_type="risk",
        semantic_definition_ref=source_policy_ref,
        owner_ref=owner_ref,
        mandatory=True,
        scope="risk",
        evaluation_method_ref="risk.limit.evaluate",
        effective_interval=effective_interval,
        source_policy_ref=source_policy_ref,
    )
    registry.register_evaluator(
        code,
        lambda constraint, candidate: _risk_satisfied(constraint, candidate, limit_amount, metric),
    )
    registry.register(authz, constraint)
    return constraint


def _risk_satisfied(
    constraint: DecisionConstraint,
    candidate: dict,
    limit_amount: Optional[Money],
    metric: Optional[str],
) -> bool:
    exposure = candidate.get("risk_exposure")
    if exposure is None and metric is not None:
        exposure = candidate.get(metric)
    if exposure is None or limit_amount is None:
        return True  # no applicable exposure declared
    if isinstance(exposure, Money) and isinstance(limit_amount, Money):
        if exposure.currency != limit_amount.currency:
            raise ValidationError(
                f"Risk exposure {exposure.currency} and limit {limit_amount.currency} "
                "are incompatible; an approved conversion method is required"
            )
        return exposure <= limit_amount
    if isinstance(exposure, Decimal) and isinstance(limit_amount, Money):
        return exposure <= limit_amount.amount
    return True


def fairness_constraint(
    authz: AuthorizationContext,
    registry: ConstraintRegistry,
    *,
    code: str,
    owner_ref: str,
    effective_interval: tuple[str, str],
    source_policy_ref: str,
    evaluator: Evaluator,
) -> DecisionConstraint:
    constraint = DecisionConstraint(
        constraint_id=generate("evidence"),
        code=code,
        constraint_type="fairness",
        semantic_definition_ref=source_policy_ref,
        owner_ref=owner_ref,
        mandatory=True,
        scope="fairness",
        evaluation_method_ref="fairness.evaluate",
        effective_interval=effective_interval,
        source_policy_ref=source_policy_ref,
    )
    registry.register_evaluator(code, evaluator)
    registry.register(authz, constraint)
    return constraint


def raise_conflict(constraint_set: ConstraintSet) -> None:
    """Route a detected conflict to the policy owner instead of resolving it silently."""
    if constraint_set.conflicts:
        raise PolicyViolationError(
            "Mandatory constraints conflict; the conflict is escalated to the "
            "policy owner rather than relaxed (LAW-UMFDVI-021)",
            details={"conflicts": constraint_set.conflicts},
        )
