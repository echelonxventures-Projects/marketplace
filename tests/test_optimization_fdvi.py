"""U12 FDVI optimization orchestration (Spec 030 Module 08; FR-024..026).

Covers the optimizer that had previously been imported by nothing. The central
guarantee under test (LAW-UMFDVI-036, AC-019) is that an optimization result
never claims a stronger optimality guarantee than the solver actually supports:
``optimality_claim`` is derived from the solver's declared capability and the
run's termination, never assigned by the caller.
"""

from decimal import Decimal

import pytest

from marketplace.kernel.errors import (
    AuthorizationError,
    DependencyUnavailableError,
    InvariantViolationError,
    NotFoundError,
    TimeoutError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.constraints import (
    ConstraintRegistry,
    ConstraintSet,
)
from marketplace.universes.u12_intelligence.entities import DecisionAlternative
from marketplace.universes.u12_intelligence.optimization import (
    Candidate,
    OptimizationProblem,
    OptimizationService,
    ResourceBudget,
    SolverRegistration,
    SolverRegistry,
    exact_enumeration_solver,
    greedy_heuristic_solver,
)

pytestmark = pytest.mark.unit

NOW = "2026-01-01T00:00:00+00:00"
THEN = "2027-01-01T00:00:00+00:00"


def alternative(aid):
    return DecisionAlternative(
        alternative_id=aid,
        case_id="case-1",
        alternative_type="approve_refund",
        action_intent="refund the order",
    )


@pytest.fixture
def constraints():
    registry = ConstraintRegistry()
    # An evaluator that cannot find its required input cannot claim the
    # constraint is satisfied, so a context without a cost fact fails.
    registry.register_evaluator(
        "budget.cap",
        lambda _constraint, ctx: "cost" in ctx
        and Decimal(str(ctx["cost"])) <= Decimal("100"),
    )
    return registry


@pytest.fixture
def constraint_set(constraints, authz):
    constraint = constraints.define(
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
    return ConstraintSet(set_id="cs-1", case_id="case-1", constraints=[constraint])


def candidate(aid, cost, margin):
    return Candidate(
        alternative=alternative(aid),
        context={"cost": cost},
        objective_values={"margin": Decimal(str(margin))},
    )


@pytest.fixture
def solvers():
    registry = SolverRegistry()
    return registry


@pytest.fixture
def exact(solvers, authz):
    return solvers.register(
        authz,
        SolverRegistration(
            solver_id="exact.enumeration",
            semantic_purpose="select_alternative",
            implementation_ref="optimization.exact_enumeration_solver",
            version="1.0",
            proves_optimality=True,
        ),
        exact_enumeration_solver,
    )


@pytest.fixture
def service(solvers, constraints):
    return OptimizationService(solvers, constraints)


class TestSolverGovernance:
    """FR-024: only approved optimization methods may run."""

    def test_register_and_select_exact_solver(self, solvers, exact):
        selected = solvers.select("select_alternative")
        assert selected.solver_id == "exact.enumeration"
        assert selected.proves_optimality

    def test_select_prefers_an_exact_solver_over_a_heuristic(self, solvers, authz, exact):
        solvers.register(
            authz,
            SolverRegistration(
                solver_id="greedy.first",
                semantic_purpose="select_alternative",
                implementation_ref="optimization.greedy_heuristic_solver",
                version="1.0",
                proves_optimality=False,
                is_heuristic=True,
            ),
            greedy_heuristic_solver,
        )
        assert solvers.select("select_alternative").solver_id == "exact.enumeration"

    def test_select_unknown_purpose_raises(self, solvers):
        with pytest.raises(NotFoundError):
            solvers.select("no.such.purpose")

    def test_requires_solver_governance_role(self, solvers, other_tenant_authz):
        with pytest.raises(AuthorizationError):
            solvers.register(
                other_tenant_authz,
                SolverRegistration(
                    solver_id="x",
                    semantic_purpose="p",
                    implementation_ref="x",
                    version="1",
                    proves_optimality=False,
                ),
                greedy_heuristic_solver,
            )

    def test_an_unapproved_fallback_solver_is_rejected(self, solvers, authz):
        with pytest.raises(ValidationError):
            solvers.register(
                authz,
                SolverRegistration(
                    solver_id="greedy.fallback",
                    semantic_purpose="select_alternative",
                    implementation_ref="optimization.greedy_heuristic_solver",
                    version="1.0",
                    proves_optimality=False,
                    is_heuristic=True,
                    fallback_of="exact.enumeration",
                ),
                greedy_heuristic_solver,
            )

    def test_an_approved_fallback_registers_against_its_parent(
        self, solvers, authz, exact
    ):
        fallback = solvers.register(
            authz,
            SolverRegistration(
                solver_id="greedy.fallback",
                semantic_purpose="select_alternative",
                implementation_ref="optimization.greedy_heuristic_solver",
                version="1.0",
                proves_optimality=False,
                is_heuristic=True,
                fallback_of="exact.enumeration",
                approved_fallback=True,
            ),
            greedy_heuristic_solver,
        )
        assert solvers.registration("greedy.fallback") is fallback
        assert fallback.fallback_of == "exact.enumeration"

    def test_a_primary_solver_has_no_fallback(self, solvers, exact):
        assert solvers.fallback_of("exact.enumeration") is None

    def test_a_solver_without_a_bound_function_is_reported(
        self, service, constraints, constraint_set, authz, exact
    ):
        service.solvers._solve.pop("exact.enumeration")
        problem = OptimizationProblem(
            case_id="case-1",
            objective_set_ref="os-1",
            constraint_set=constraint_set,
            candidates=[candidate("a1", 50, 0.3)],
        )
        with pytest.raises(DependencyUnavailableError):
            service.submit(authz, problem, exact)


class TestResourceBudget:
    """FR-024: every run executes within explicitly declared resource limits."""

    def test_a_run_within_budget_is_accepted(self, service, constraint_set, authz, exact):
        run = service.submit(
            authz,
            OptimizationProblem(
                case_id="case-1",
                objective_set_ref="os-1",
                constraint_set=constraint_set,
                candidates=[candidate("a1", 50, 0.3)],
            ),
            exact,
        )
        assert run.termination_reason == "completed"
        assert run.resource_budget["max_candidates"] == ResourceBudget().max_candidates

    def test_exceeding_the_candidate_limit_is_rejected(
        self, service, constraint_set, authz, exact
    ):
        problem = OptimizationProblem(
            case_id="case-1",
            objective_set_ref="os-1",
            constraint_set=constraint_set,
            candidates=[candidate(f"a{i}", 10, 0.1) for i in range(3)],
            budget=ResourceBudget(max_candidates=1),
        )
        with pytest.raises(InvariantViolationError):
            service.submit(authz, problem, exact)

    def test_budget_assert_within_enforces_every_limit(self):
        budget = ResourceBudget(max_candidates=10, max_iterations=10, wall_clock_seconds=10.0)
        budget.assert_within(1, 1, 0.1)
        with pytest.raises(InvariantViolationError):
            budget.assert_within(11, 1, 0.1)
        with pytest.raises(InvariantViolationError):
            budget.assert_within(1, 11, 0.1)
        with pytest.raises(TimeoutError):
            budget.assert_within(1, 1, 11.0)


class TestOptimalityHonesty:
    """FR-026: proven optimality is distinguished from approximate or heuristic."""

    def test_an_exact_solver_proves_optimality(self, service, constraint_set, authz, exact):
        run = service.submit(
            authz,
            OptimizationProblem(
                case_id="case-1",
                objective_set_ref="os-1",
                constraint_set=constraint_set,
                candidates=[candidate("a1", 50, 0.2), candidate("a2", 80, 0.5)],
            ),
            exact,
        )
        assert run.solution_status == "optimal"
        assert run.optimality_claim == "proven_optimal"
        assert run.selected_alternative_ref == "a2"
        service.assert_claim_honest(run)

    def test_a_solver_that_cannot_prove_optimality_is_downgraded(
        self, solvers, service, constraint_set, authz
    ):
        approximate = solvers.register(
            authz,
            SolverRegistration(
                solver_id="approx.solver",
                semantic_purpose="select_alternative",
                implementation_ref="optimization.exact_enumeration_solver",
                version="1.0",
                proves_optimality=False,
            ),
            exact_enumeration_solver,
        )
        run = service.submit(
            authz,
            OptimizationProblem(
                case_id="case-1",
                objective_set_ref="os-1",
                constraint_set=constraint_set,
                candidates=[candidate("a1", 50, 0.5)],
            ),
            approximate,
        )
        assert run.solution_status == "optimal"
        assert run.optimality_claim == "approximately_optimal"

    def test_a_solver_whose_winner_violates_a_mandatory_constraint_is_not_proven(
        self, solvers, service, constraint_set, constraints, authz
    ):
        from marketplace.universes.u12_intelligence.optimization import SolverResult

        def lying_solver(_problem, _registration):
            return SolverResult(
                selected=candidate("a1", 500, 0.9),  # cost 500 breaches the 100 cap
                solution_status="optimal",
                termination_reason="completed",
                candidates_evaluated=1,
                feasible_candidates=1,
                iterations=1,
            )

        liar = solvers.register(
            authz,
            SolverRegistration(
                solver_id="lying.solver",
                semantic_purpose="select_alternative",
                implementation_ref="test",
                version="1.0",
                proves_optimality=True,
            ),
            lying_solver,
        )
        run = service.submit(
            authz,
            OptimizationProblem(
                case_id="case-1",
                objective_set_ref="os-1",
                constraint_set=constraint_set,
                candidates=[candidate("a1", 500, 0.9)],
            ),
            liar,
        )
        assert run.optimality_claim == "approximately_optimal"
        service.assert_claim_honest(run)

    def test_a_heuristic_solver_never_claims_optimality(
        self, solvers, service, constraint_set, authz
    ):
        heuristic = solvers.register(
            authz,
            SolverRegistration(
                solver_id="greedy.first",
                semantic_purpose="select_alternative",
                implementation_ref="optimization.greedy_heuristic_solver",
                version="1.0",
                proves_optimality=False,
                is_heuristic=True,
            ),
            greedy_heuristic_solver,
        )
        run = service.submit(
            authz,
            OptimizationProblem(
                case_id="case-1",
                objective_set_ref="os-1",
                constraint_set=constraint_set,
                candidates=[candidate("a1", 50, 0.5)],
            ),
            heuristic,
        )
        assert run.solution_status == "heuristic"
        assert run.optimality_claim == "heuristic"
        assert run.limitation_summary
        service.assert_claim_honest(run)

    def test_no_feasible_candidate_is_reported_as_infeasible(
        self, service, constraint_set, authz, exact
    ):
        problem = OptimizationProblem(
            case_id="case-1",
            objective_set_ref="os-1",
            constraint_set=constraint_set,
            candidates=[candidate("a1", 500, 0.9)],  # breaches the mandatory cap
        )
        run = service.submit(authz, problem, exact)
        assert run.optimality_claim == "infeasible"
        assert run.solution_status == "infeasible"
        blocking = service.explain_infeasibility(run, problem)
        assert blocking and "budget.cap" in blocking[0]

    def test_explain_infeasibility_is_silent_for_feasible_runs(
        self, service, constraint_set, authz, exact
    ):
        problem = OptimizationProblem(
            case_id="case-1",
            objective_set_ref="os-1",
            constraint_set=constraint_set,
            candidates=[candidate("a1", 50, 0.5)],
        )
        run = service.submit(authz, problem, exact)
        assert service.explain_infeasibility(run, problem) == []

    def test_a_run_that_hits_its_resource_budget_is_incomplete(
        self, solvers, service, constraint_set, authz
    ):
        from marketplace.universes.u12_intelligence.optimization import SolverResult

        def bounded_solver(_problem, _registration):
            return SolverResult(
                selected=candidate("a1", 50, 0.5),
                solution_status="optimal",
                termination_reason="resource_limit",
                candidates_evaluated=1,
                feasible_candidates=1,
                iterations=1,
                limitations=["stopped at the approved limit"],
            )

        bounded = solvers.register(
            authz,
            SolverRegistration(
                solver_id="bounded.solver",
                semantic_purpose="select_alternative",
                implementation_ref="test",
                version="1.0",
                proves_optimality=True,
            ),
            bounded_solver,
        )
        run = service.submit(
            authz,
            OptimizationProblem(
                case_id="case-1",
                objective_set_ref="os-1",
                constraint_set=constraint_set,
                candidates=[candidate("a1", 50, 0.5)],
            ),
            bounded,
        )
        assert run.optimality_claim == "incomplete"
        assert "stopped at the approved limit" in run.limitation_summary


class TestSolverResultValidation:
    """FR-025: solver results are validated before being accepted."""

    def test_a_honest_run_passes_validation(self, service, constraint_set, authz, exact):
        run = service.submit(
            authz,
            OptimizationProblem(
                case_id="case-1",
                objective_set_ref="os-1",
                constraint_set=constraint_set,
                candidates=[candidate("a1", 50, 0.5)],
            ),
            exact,
        )
        service.assert_claim_honest(run)

    def test_a_proven_optimal_claim_with_a_non_optimal_status_is_rejected(self, service):
        from marketplace.universes.u12_intelligence.entities import OptimizationRun

        run = OptimizationRun(
            run_id="r1",
            case_id="case-1",
            objective_set_ref="os-1",
            constraint_set_ref="cs-1",
            search_method_ref="exact.enumeration",
            algorithm_version="1.0",
            solution_status="heuristic",
            optimality_claim="proven_optimal",
        )
        with pytest.raises(InvariantViolationError):
            service.assert_claim_honest(run)

    def test_an_unsupported_claim_is_rejected(self, service):
        from marketplace.universes.u12_intelligence.entities import OptimizationRun

        run = OptimizationRun(
            run_id="r1",
            case_id="case-1",
            objective_set_ref="os-1",
            constraint_set_ref="cs-1",
            search_method_ref="exact.enumeration",
            algorithm_version="1.0",
            optimality_claim="best_possible",
        )
        with pytest.raises(InvariantViolationError):
            service.assert_claim_honest(run)

    def test_a_failing_solver_records_its_own_failure_and_reraises(
        self, solvers, service, constraint_set, authz
    ):
        def exploding(_problem, _registration):
            raise RuntimeError("solver blew up")

        boom = solvers.register(
            authz,
            SolverRegistration(
                solver_id="boom.solver",
                semantic_purpose="select_alternative",
                implementation_ref="test",
                version="1.0",
                proves_optimality=True,
            ),
            exploding,
        )
        with pytest.raises(RuntimeError):
            service.submit(
                authz,
                OptimizationProblem(
                    case_id="case-1",
                    objective_set_ref="os-1",
                    constraint_set=constraint_set,
                    candidates=[candidate("a1", 50, 0.5)],
                ),
                boom,
            )
        (run,) = service.runs.values()
        assert run.termination_reason == "failed"
        assert run.optimality_claim == "unknown"
        assert "solver raised RuntimeError" in run.limitation_summary[0]
