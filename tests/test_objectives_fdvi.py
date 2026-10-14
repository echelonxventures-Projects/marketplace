"""U12 FDVI objective resolution (Spec 030 Module 02; FR-005, FR-006, FR-023).

Covers the objective registry that had previously been imported by nothing:
objective definitions are resolved from their canonical owner, their
measurement semantics are validated, and multiple objectives are supported
with governed precedence and conflict detection.
"""

from decimal import Decimal

import pytest

from marketplace.kernel.errors import (
    AuthorizationError,
    DuplicateCanonicalConceptError,
    NotFoundError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.entities import DecisionObjective
from marketplace.universes.u12_intelligence.objectives import ObjectiveRegistry

pytestmark = pytest.mark.unit


@pytest.fixture
def objectives():
    return ObjectiveRegistry()


def _define(objectives, authz, **over):
    base = dict(
        code="margin.gross",
        objective_type="financial",
        owner_ref="finance.owner",
        definition_ref="policy/margin",
        desired_direction="maximize",
        measurement_definition_ref="measure/gross-margin",
        target_value=Decimal("0.30"),
        time_horizon="quarterly",
        unit="ratio",
    )
    base.update(over)
    return objectives.define(authz, **base)


class TestObjectiveRegistration:
    """FR-005: resolve objective definitions from their canonical owners."""

    def test_define_and_resolve_by_code(self, objectives, authz):
        objective = _define(objectives, authz)
        assert objectives.by_code("margin.gross") is objective
        assert objectives.get(objective.objective_id) is objective
        assert objective.owner_ref == "finance.owner"
        assert objectives.all() == [objective]

    def test_requires_objective_writer_role(self, objectives, other_tenant_authz):
        with pytest.raises(AuthorizationError):
            _define(objectives, other_tenant_authz)

    def test_unknown_code_raises(self, objectives):
        with pytest.raises(NotFoundError):
            objectives.by_code("does.not.exist")

    def test_superseding_requires_a_new_version(self, objectives, authz):
        first = _define(objectives, authz)
        duplicate = DecisionObjective(
            objective_id=first.objective_id,
            code="margin.gross",
            objective_type="financial",
            owner_ref="finance.owner",
            definition_ref="policy/margin",
            desired_direction="maximize",
            measurement_definition_ref="measure/gross-margin",
            target_value=Decimal("0.30"),
            target_interval=None,
            time_horizon="quarterly",
            priority=5,
            mandatory=False,
            unit="ratio",
        )
        with pytest.raises(DuplicateCanonicalConceptError):
            objectives.register(authz, duplicate)

        duplicate.version = 2
        superseded = objectives.register(authz, duplicate)
        assert objectives.by_code("margin.gross") is superseded


class TestObjectiveSemantics:
    """FR-006: validate objective units, direction, scope and time horizon."""

    def test_direction_must_be_a_known_value(self, objectives, authz):
        with pytest.raises(ValidationError):
            _define(objectives, authz, desired_direction="as much as possible")

    def test_unit_is_mandatory(self, objectives, authz):
        with pytest.raises(ValidationError):
            _define(objectives, authz, unit="")

    def test_a_target_is_required(self, objectives, authz):
        with pytest.raises(ValidationError):
            _define(objectives, authz, target_value=None, target_interval=None)

    def test_target_interval_bounds_must_be_ordered(self, objectives, authz):
        with pytest.raises(ValidationError):
            _define(
                objectives, authz,
                target_value=None,
                target_interval=(Decimal("0.9"), Decimal("0.1")),
            )

    def test_interval_target_is_accepted(self, objectives, authz):
        objective = _define(
            objectives, authz,
            target_value=None,
            target_interval=(Decimal("0.2"), Decimal("0.4")),
        )
        assert objective.target_interval == (Decimal("0.2"), Decimal("0.4"))

    def test_validate_semantics_reports_missing_fields(self, objectives, authz):
        objective = _define(
            objectives, authz,
            measurement_definition_ref="",
            time_horizon="",
        )
        problems = objectives.validate_semantics(objective)
        assert "measurement_definition_ref is required" in problems
        assert "time_horizon is required" in problems

    def test_a_mandatory_objective_needs_a_concrete_target(self, objectives, authz):
        # register() already rejects a target-less objective, so this is defence
        # in depth: validate_semantics must still flag the missing target.
        objective = DecisionObjective(
            objective_id="obj-1",
            code="margin.gross",
            objective_type="financial",
            owner_ref="finance.owner",
            definition_ref="policy/margin",
            desired_direction="maximize",
            measurement_definition_ref="measure/gross-margin",
            target_value=None,
            target_interval=None,
            time_horizon="quarterly",
            priority=5,
            mandatory=True,
            unit="ratio",
        )
        problems = objectives.validate_semantics(objective)
        assert "a mandatory objective needs a concrete target" in problems


class TestObjectiveSets:
    """FR-023: support multiple objectives when the decision class requires it."""

    def _two(self, objectives, authz):
        a = _define(objectives, authz, code="margin.gross", priority=3)
        b = _define(
            objectives, authz,
            code="volume.units",
            owner_ref="growth.owner",
            desired_direction="maximize",
            unit="units",
            target_value=Decimal("10000"),
            priority=7,
        )
        return a, b

    def test_resolve_set_orders_by_priority(self, objectives, authz):
        _a, _b = self._two(objectives, authz)
        resolved = objectives.resolve_set(authz, "case-1", ["margin.gross", "volume.units"])
        assert resolved.objective_ids
        assert resolved.precedence == ["margin.gross", "volume.units"]

    def test_resolve_set_honours_declared_precedence(self, objectives, authz):
        _a, _b = self._two(objectives, authz)
        resolved = objectives.resolve_set(
            authz, "case-1", ["margin.gross", "volume.units"],
            precedence=["volume.units", "margin.gross"],
        )
        assert resolved.precedence == ["volume.units", "margin.gross"]

    def test_precedence_may_not_reference_unlisted_objectives(self, objectives, authz):
        _a, _b = self._two(objectives, authz)
        with pytest.raises(ValidationError):
            objectives.resolve_set(
                authz, "case-1", ["margin.gross"],
                precedence=["margin.gross", "volume.units"],
            )

    def test_a_mandatory_objective_with_incomplete_semantics_is_rejected(
        self, objectives, authz
    ):
        incomplete = DecisionObjective(
            objective_id="obj-2",
            code="margin.gross",
            objective_type="financial",
            owner_ref="finance.owner",
            definition_ref="policy/margin",
            desired_direction="maximize",
            measurement_definition_ref="",
            target_value=Decimal("0.30"),
            target_interval=None,
            time_horizon="",
            priority=5,
            mandatory=True,
            unit="ratio",
        )
        objectives.register(authz, incomplete)
        with pytest.raises(ValidationError):
            objectives.resolve_set(
                authz, "case-1", ["margin.gross"],
                precedence=["margin.gross"],
            )

    def test_opposite_mandatory_directions_are_reported_as_conflicts(
        self, objectives, authz
    ):
        _define(
            objectives, authz, code="cost.total", desired_direction="minimize",
            unit="eur", time_horizon="quarterly", mandatory=True,
        )
        _define(
            objectives, authz, code="spend.total", desired_direction="maximize",
            unit="eur", time_horizon="quarterly", mandatory=True,
        )
        conflicts = objectives.detect_conflicts(objectives.all())
        assert ("cost.total", "spend.total", "opposite mandatory directions on the same unit and horizon") in conflicts

    def test_unknown_objective_code_in_a_set_raises(self, objectives, authz):
        with pytest.raises(NotFoundError):
            objectives.resolve_set(authz, "case-1", ["margin.gross"])
