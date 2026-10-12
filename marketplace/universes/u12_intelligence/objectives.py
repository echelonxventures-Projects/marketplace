"""U12-FDVI objective registry (Spec 030 Module 02; FR-005, FR-006, FR-023).

Objectives are canonical definitions with explicit measurement semantics:
meaning, unit, direction, horizon, population and owner (LAW-UMFDVI-004).
An objective without measurement semantics is rejected or marked incomplete
(AC-005).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from marketplace.kernel import AuthorizationContext, generate
from marketplace.kernel.errors import (
    DuplicateCanonicalConceptError,
    NotFoundError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.entities import DecisionObjective

# Measurement semantics that every objective must declare (Spec 030 §8.2/§9.2).
REQUIRED_SEMANTICS = ("unit", "desired_direction", "time_horizon", "owner_ref", "definition_ref")
DIRECTIONS = frozenset({"maximize", "minimize", "target", "reduce", "maintain"})


@dataclass
class ObjectiveSet:
    set_id: str
    case_id: str
    objective_ids: list[str]
    precedence: list[str]  # objective codes in governed priority order
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)


class ObjectiveRegistry:
    def __init__(self) -> None:
        self._by_code: dict[str, DecisionObjective] = {}
        self._by_id: dict[str, DecisionObjective] = {}
        self._lock = threading.RLock()

    def register(
        self, authz: AuthorizationContext, objective: DecisionObjective
    ) -> DecisionObjective:
        authz.requires_role("objective.writer")
        if objective.desired_direction not in DIRECTIONS:
            raise ValidationError(
                f"Objective direction {objective.desired_direction!r} is not one of {sorted(DIRECTIONS)}"
            )
        if not objective.unit:
            raise ValidationError("An objective must declare its measurement unit")
        if objective.target_value is None and objective.target_interval is None:
            raise ValidationError(
                "An objective must declare a target value or target interval"
            )
        if objective.target_interval is not None:
            low, high = objective.target_interval
            if low > high:
                raise ValidationError("A target interval's lower bound exceeds its upper bound")
        with self._lock:
            if objective.code in self._by_code:
                existing = self._by_code[objective.code]
                if existing.version >= objective.version:
                    raise DuplicateCanonicalConceptError(
                        f"Objective {objective.code!r} is already registered at version "
                        f"{existing.version}; supersede it through a new version"
                    )
            self._by_code[objective.code] = objective
            self._by_id[objective.objective_id] = objective
        return objective

    def define(
        self,
        authz: AuthorizationContext,
        *,
        code: str,
        objective_type: str,
        owner_ref: str,
        definition_ref: str,
        desired_direction: str,
        measurement_definition_ref: str,
        target_value: Optional[Decimal],
        time_horizon: str,
        unit: str,
        priority: int = 5,
        mandatory: bool = False,
        target_interval: Optional[tuple[Decimal, Decimal]] = None,
        currency: Optional[str] = None,
        stakeholder_scope: str = "",
        constraint_refs: Optional[list[str]] = None,
    ) -> DecisionObjective:
        objective = DecisionObjective(
            objective_id=generate("evidence"),
            code=code,
            objective_type=objective_type,
            owner_ref=owner_ref,
            definition_ref=definition_ref,
            desired_direction=desired_direction,
            measurement_definition_ref=measurement_definition_ref,
            target_value=target_value,
            target_interval=target_interval,
            time_horizon=time_horizon,
            priority=priority,
            mandatory=mandatory,
            unit=unit,
            currency=currency,
            stakeholder_scope=stakeholder_scope,
            constraint_refs=list(constraint_refs or []),
        )
        return self.register(authz, objective)

    def get(self, objective_id: str) -> DecisionObjective:
        objective = self._by_id.get(objective_id)
        if objective is None:
            raise NotFoundError(f"Objective {objective_id} not found")
        return objective

    def by_code(self, code: str) -> DecisionObjective:
        objective = self._by_code.get(code)
        if objective is None:
            raise NotFoundError(f"Objective code {code!r} is not registered")
        return objective

    def all(self) -> list[DecisionObjective]:
        return list(self._by_code.values())

    def validate_semantics(self, objective: DecisionObjective) -> list[str]:
        """AC-005: report missing measurement semantics rather than accepting."""
        problems: list[str] = []
        if not objective.measurement_definition_ref:
            problems.append("measurement_definition_ref is required")
        if not objective.time_horizon:
            problems.append("time_horizon is required")
        if objective.mandatory and objective.target_value is None and objective.target_interval is None:
            problems.append("a mandatory objective needs a concrete target")
        return problems

    def resolve_set(
        self,
        authz: AuthorizationContext,
        case_id: str,
        objective_codes: list[str],
        precedence: Optional[list[str]] = None,
    ) -> ObjectiveSet:
        objectives = [self.by_code(c) for c in objective_codes]
        for objective in objectives:
            problems = self.validate_semantics(objective)
            if problems and objective.mandatory:
                raise ValidationError(
                    f"Objective {objective.code!r} has incomplete measurement semantics: {problems}"
                )
        order = precedence or [o.code for o in sorted(objectives, key=lambda o: o.priority)]
        unresolved = [c for c in order if c not in objective_codes]
        if unresolved:
            raise ValidationError(f"Precedence references unlisted objectives {unresolved}")
        conflicts = self.detect_conflicts(objectives)
        return ObjectiveSet(
            set_id=generate("evidence"),
            case_id=case_id,
            objective_ids=[o.objective_id for o in objectives],
            precedence=order,
            conflicts=conflicts,
        )

    def detect_conflicts(self, objectives: list[DecisionObjective]) -> list[tuple[str, str, str]]:
        """Surface material conflicts rather than hiding them in an aggregate (AC-017)."""
        conflicts: list[tuple[str, str, str]] = []
        for i, a in enumerate(objectives):
            for b in objectives[i + 1:]:
                if a.code == b.code:
                    continue
                if a.mandatory and b.mandatory and a.desired_direction != b.desired_direction:
                    if a.unit == b.unit and a.time_horizon == b.time_horizon:
                        conflicts.append(
                            (a.code, b.code, "opposite mandatory directions on the same unit and horizon")
                        )
        return conflicts
