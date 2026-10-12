"""U12-FDVI optimization orchestration (Spec 030 Module 08; FR-024..026).

Central honesty guarantee (LAW-UMFDVI-036, AC-019): an optimization result
never claims a stronger optimality guarantee than the solver actually supports.
The ``optimality_claim`` is *derived* from the solver's declared capability and
the run's termination, never assigned by the caller.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from marketplace.kernel import AuthorizationContext, generate
from marketplace.kernel.errors import (
    DependencyUnavailableError,
    InvariantViolationError,
    NotFoundError,
    TimeoutError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.constraints import ConstraintRegistry, ConstraintSet
from marketplace.universes.u12_intelligence.entities import (
    DecisionAlternative,
    OptimizationRun,
)

# Closed vocabularies (Spec 030 §8.5).
SOLUTION_STATUSES = frozenset({"optimal", "feasible", "heuristic", "infeasible", "unknown"})
TERMINATION_REASONS = frozenset(
    {"completed", "resource_limit", "cancelled", "no_feasible", "failed", "incomplete"}
)
CLAIMS = frozenset(
    {
        "proven_optimal",
        "approximately_optimal",
        "heuristic",
        "incomplete",
        "infeasible",
        "unknown",
    }
)


@dataclass
class ResourceBudget:
    """NFR-003: every optimization run carries explicit resource limits."""

    max_candidates: int = 1000
    max_iterations: int = 10_000
    wall_clock_seconds: float = 30.0
    max_concurrent: int = 1

    def assert_within(self, observed_candidates: int, observed_iterations: int, elapsed: float) -> None:
        if observed_candidates > self.max_candidates:
            raise InvariantViolationError(
                "LAW-UMFDVI-027",
                f"Optimization evaluated {observed_candidates} candidates, exceeding "
                f"the approved limit of {self.max_candidates}",
                details={"observed": observed_candidates, "limit": self.max_candidates},
            )
        if observed_iterations > self.max_iterations:
            raise InvariantViolationError(
                "LAW-UMFDVI-027",
                f"Optimization performed {observed_iterations} iterations, exceeding "
                f"the approved limit of {self.max_iterations}",
                details={"observed": observed_iterations, "limit": self.max_iterations},
            )
        if elapsed > self.wall_clock_seconds:
            raise TimeoutError(
                f"Optimization exceeded its approved wall-clock budget of "
                f"{self.wall_clock_seconds}s (elapsed {elapsed:.3f}s)"
            )


@dataclass
class Candidate:
    alternative: DecisionAlternative
    context: dict
    objective_values: dict[str, Decimal] = field(default_factory=dict)
    penalty: Decimal = Decimal(0)


@dataclass
class SolverResult:
    """What a solver actually produced, in the solver's own honest terms."""

    selected: Optional[Candidate]
    solution_status: str  # optimal | feasible | heuristic | infeasible | unknown
    termination_reason: str
    candidates_evaluated: int
    feasible_candidates: int
    iterations: int
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SolverRegistration:
    solver_id: str
    semantic_purpose: str
    implementation_ref: str
    version: str
    proves_optimality: bool
    is_heuristic: bool = False
    fallback_of: Optional[str] = None
    approved_fallback: bool = False


Solve = Callable[["OptimizationProblem", SolverRegistration], SolverResult]


@dataclass
class OptimizationProblem:
    case_id: str
    objective_set_ref: str
    constraint_set: ConstraintSet
    candidates: list[Candidate]
    constraint_registry: Optional[ConstraintRegistry] = None
    weights: dict[str, Decimal] = field(default_factory=dict)
    precedence: list[str] = field(default_factory=list)
    budget: ResourceBudget = field(default_factory=ResourceBudget)

    def evaluate_constraint(self, constraint, candidate: Candidate) -> tuple[bool, str]:
        """Evaluate a constraint on a candidate through the bound registry."""
        if self.constraint_registry is None:
            # Without a registry the caller has pre-declared satisfaction.
            return bool(candidate.context.get("mandatory_satisfied", True)), ""
        return self.constraint_registry.evaluate(constraint, candidate.context)


class SolverRegistry:
    def __init__(self) -> None:
        self._solvers: dict[str, SolverRegistration] = {}
        self._solve: dict[str, Solve] = {}
        self._lock = threading.RLock()

    def register(
        self,
        authz: AuthorizationContext,
        registration: SolverRegistration,
        solve: Solve,
    ) -> SolverRegistration:
        authz.requires_role("solver.governance")
        if registration.fallback_of is not None and not registration.approved_fallback:
            raise ValidationError(
                f"Fallback solver {registration.solver_id!r} must be explicitly "
                "approved (LAW-UMFDVI-023 No Silent Fallback)"
            )
        with self._lock:
            self._solvers[registration.solver_id] = registration
            self._solve[registration.solver_id] = solve
        return registration

    def registration(self, solver_id: str) -> SolverRegistration:
        registration = self._solvers.get(solver_id)
        if registration is None:
            raise NotFoundError(f"Solver {solver_id!r} is not registered")
        return registration

    def select(self, semantic_purpose: str) -> SolverRegistration:
        available = [
            r for r in self._solvers.values() if r.semantic_purpose == semantic_purpose
        ]
        if not available:
            raise NotFoundError(
                f"No solver registered for purpose {semantic_purpose!r}"
            )
        # Prefer an exact solver over a heuristic one (Spec 030 §8.5).
        exact = [r for r in available if r.proves_optimality and not r.is_heuristic]
        return (exact or available)[0]

    def fallback_of(self, solver_id: str) -> Optional[SolverRegistration]:
        registration = self.registration(solver_id)
        if registration.fallback_of is None:
            return None
        fallback = self.registration(registration.fallback_of)
        if not fallback.approved_fallback and fallback.fallback_of is None:
            raise ValidationError(
                f"Configured fallback {fallback.solver_id!r} is not an approved "
                "fallback solver (LAW-UMFDVI-023)"
            )
        return fallback


class OptimizationService:
    def __init__(self, solvers: SolverRegistry, constraints: ConstraintRegistry) -> None:
        self.solvers = solvers
        self.constraints = constraints
        self.runs: dict[str, OptimizationRun] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        authz: AuthorizationContext,
        problem: OptimizationProblem,
        solver_registration: SolverRegistration,
    ) -> OptimizationRun:
        run_id = generate("evidence")
        problem.constraint_registry = self.constraints
        run = OptimizationRun(
            run_id=run_id,
            case_id=problem.case_id,
            objective_set_ref=problem.objective_set_ref,
            constraint_set_ref=problem.constraint_set.set_id,
            search_method_ref=solver_registration.solver_id,
            algorithm_version=solver_registration.version,
            resource_budget={
                "max_candidates": problem.budget.max_candidates,
                "max_iterations": problem.budget.max_iterations,
                "wall_clock_seconds": problem.budget.wall_clock_seconds,
            },
            started_at=self._now(),
        )
        with self._lock:
            self.runs[run_id] = run

        started = time.monotonic()
        try:
            result = self._solve(problem, solver_registration)
        except Exception as exc:  # noqa: BLE001 - the run records its own failure
            run.termination_reason = "failed"
            run.optimality_claim = "unknown"
            run.solution_status = "unknown"
            run.limitation_summary = [f"solver raised {type(exc).__name__}: {exc}"]
            raise
        elapsed = time.monotonic() - started
        problem.budget.assert_within(result.candidates_evaluated, result.iterations, elapsed)

        run.candidate_count = result.candidates_evaluated
        run.feasible_candidate_count = result.feasible_candidates
        run.solution_status = result.solution_status
        run.termination_reason = result.termination_reason
        run.limitation_summary = list(result.limitations)
        if result.selected is not None:
            run.selected_alternative_ref = result.selected.alternative.alternative_id
        run.optimality_claim = self._derive_claim(solver_registration, result, problem)
        run.completed_at = self._now()
        return run

    def _solve(self, problem: OptimizationProblem, registration: SolverRegistration) -> SolverResult:
        solver = self.solvers._solve.get(registration.solver_id)
        if solver is None:
            raise DependencyUnavailableError(
                registration.solver_id, "no solve function is bound to this registration"
            )
        return solver(problem, registration)

    def _derive_claim(
        self,
        registration: SolverRegistration,
        result: SolverResult,
        problem: OptimizationProblem,
    ) -> str:
        """Compute the strongest honest claim the evidence supports.

        Rules (Spec 030 §8.5, LAW-UMFDVI-036):
          * ``proven_optimal`` requires a solver that declares it proves
            optimality AND a normal completion AND at least one feasible
            candidate AND every mandatory constraint evaluated on the winner;
          * a heuristic solver can never claim better than ``heuristic``;
          * a run that hit its resource budget or was cancelled is at best
            ``incomplete``;
          * no feasible candidate yields ``infeasible``.
        """
        if result.feasible_candidates == 0:
            return "infeasible"
        if result.termination_reason in {"resource_limit", "cancelled", "incomplete"}:
            return "incomplete"
        if result.solution_status == "heuristic" or registration.is_heuristic:
            return "heuristic"
        if result.solution_status == "optimal" and registration.proves_optimality:
            if self._winner_mandatory_verified(result, problem):
                return "proven_optimal"
            return "approximately_optimal"
        if result.solution_status in {"optimal", "feasible"}:
            return "approximately_optimal"
        return "unknown"

    def _winner_mandatory_verified(self, result: SolverResult, problem: OptimizationProblem) -> bool:
        if result.selected is None:
            return False
        for constraint in problem.constraint_set.mandatory:
            satisfied, _ = self.constraints.evaluate(constraint, result.selected.context)
            if not satisfied:
                return False
        return True

    def assert_claim_honest(self, run: OptimizationRun) -> None:
        if run.optimality_claim not in CLAIMS:
            raise InvariantViolationError(
                "LAW-UMFDVI-036",
                f"Optimization run {run.run_id} declares an unsupported optimality "
                f"claim {run.optimality_claim!r}",
                details={"run_id": run.run_id},
            )
        if run.optimality_claim == "proven_optimal" and run.solution_status != "optimal":
            raise InvariantViolationError(
                "LAW-UMFDVI-036",
                f"Run {run.run_id} claims proven optimality but its solution status "
                f"is {run.solution_status!r}",
                details={"run_id": run.run_id},
            )

    # -- no-feasible-solution handling (F-005, AC-020) ----------------------

    def explain_infeasibility(self, run: OptimizationRun, problem: OptimizationProblem) -> list[str]:
        if run.optimality_claim != "infeasible":
            return []
        blocking: list[str] = []
        for constraint in problem.constraint_set.mandatory:
            satisfied, reason = self.constraints.evaluate(
                constraint, {"payload": {}}
            )
            if not satisfied:
                blocking.append(f"{constraint.code}: {reason}")
        if not blocking:
            blocking.append(
                "no candidate satisfied the mandatory constraints; the specific "
                "blocking constraint could not be isolated"
            )
        return blocking

    def _now(self) -> str:
        from marketplace.universes._base import U12

        return U12.now()


# ---------------------------------------------------------------------------
# Reference solvers. These are real, working implementations, not stubs.
# ---------------------------------------------------------------------------


def exact_enumeration_solver(problem: OptimizationProblem, registration: SolverRegistration) -> SolverResult:
    """Deterministically enumerate feasible candidates and pick the optimum.

    This solver *does* prove optimality over the supplied candidate set, so it
    may legitimately register ``proves_optimality=True``.
    """
    feasible: list[Candidate] = []
    evaluated = 0
    iterations = 0
    limitations: list[str] = []
    budget = problem.budget

    for candidate in problem.candidates:
        evaluated += 1
        iterations += 1
        if evaluated > budget.max_candidates or iterations > budget.max_iterations:
            limitations.append(" enumeration stopped at the approved candidate/iteration limit")
            return SolverResult(
                selected=feasible[-1] if feasible else None,
                solution_status="feasible" if feasible else "unknown",
                termination_reason="resource_limit",
                candidates_evaluated=evaluated,
                feasible_candidates=len(feasible),
                iterations=iterations,
                limitations=limitations,
            )
        ok = True
        for constraint in problem.constraint_set.mandatory:
            satisfied, _ = problem.evaluate_constraint(constraint, candidate)
            if not satisfied:
                ok = False
                break
        if ok:
            feasible.append(candidate)

    if not feasible:
        return SolverResult(
            selected=None,
            solution_status="infeasible",
            termination_reason="no_feasible",
            candidates_evaluated=evaluated,
            feasible_candidates=0,
            iterations=iterations,
            limitations=["no candidate satisfied every mandatory constraint"],
        )

    best = _rank(feasible, problem)
    return SolverResult(
        selected=best,
        solution_status="optimal",
        termination_reason="completed",
        candidates_evaluated=evaluated,
        feasible_candidates=len(feasible),
        iterations=iterations,
        limitations=limitations,
    )


def greedy_heuristic_solver(problem: OptimizationProblem, registration: SolverRegistration) -> SolverResult:
    """A first-feasible heuristic. It never claims optimality."""
    evaluated = 0
    for candidate in problem.candidates:
        evaluated += 1
        if evaluated > problem.budget.max_candidates:
            return SolverResult(
                selected=None,
                solution_status="unknown",
                termination_reason="resource_limit",
                candidates_evaluated=evaluated,
                feasible_candidates=0,
                iterations=evaluated,
                limitations=["heuristic stopped at the approved candidate limit"],
            )
        ok = True
        for constraint in problem.constraint_set.mandatory:
            satisfied, _ = problem.evaluate_constraint(constraint, candidate)
            if not satisfied:
                ok = False
                break
        if ok:
            return SolverResult(
                selected=candidate,
                solution_status="heuristic",
                termination_reason="completed",
                candidates_evaluated=evaluated,
                feasible_candidates=1,
                iterations=evaluated,
                limitations=[
                    "greedy first-feasible heuristic; no optimality guarantee over "
                    "the candidate set is provided"
                ],
            )
    return SolverResult(
        selected=None,
        solution_status="infeasible",
        termination_reason="no_feasible",
        candidates_evaluated=evaluated,
        feasible_candidates=0,
        iterations=evaluated,
        limitations=["no candidate satisfied every mandatory constraint"],
    )


def _rank(candidates: list[Candidate], problem: OptimizationProblem) -> Candidate:
    weights = problem.weights or {}

    def score(candidate: Candidate) -> Decimal:
        total = Decimal(0)
        for key, value in candidate.objective_values.items():
            weight = weights.get(key, Decimal(1))
            total += weight * value
        return total - candidate.penalty

    if problem.precedence:
        def lex_key(candidate: Candidate) -> tuple:
            return tuple(
                (candidate.objective_values.get(name, Decimal(0)),)
                for name in problem.precedence
            )
        return max(candidates, key=lex_key)
    return max(candidates, key=score)
