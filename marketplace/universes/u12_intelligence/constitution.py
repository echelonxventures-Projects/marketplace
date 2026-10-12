"""U12-FDVI constitutional extension for Specification 030.

Spec 030 §6 proposes laws UMFDVI-001..040. The canonical law registry requires
the ``LAW-`` prefix (``constitution/laws.py::Law.__post_init__``), so net-new
laws are registered as ``LAW-UMFDVI-<nnn>`` preserving the spec numbering 1:1.

Laws that materially duplicate existing canonical laws are NOT re-registered
(GOV-01 Knowledge Once, GOV-05 Zero duplication). Their normative effect is
inherited, and ``UMFDVI_TO_CANONICAL`` records the mapping so every proposed
identifier resolves to exactly one enforceable canonical law (ADR-FDVI-002).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from marketplace.constitution.laws import Law, all_laws, get_law, register_law

# ---------------------------------------------------------------------------
# Mapping of proposed UMFDVI identifiers to the existing canonical laws that
# already govern the same normative behaviour. Every UMFDVI-<nnn> must resolve
# to exactly one enforceable law (verified by the registry tests).
# ---------------------------------------------------------------------------

UMFDVI_TO_CANONICAL: dict[str, str] = {
    "UMFDVI-001": "LAW-GOV-02",  # canonical source of truth / authority
    "UMFDVI-002": "LAW-GOV-08",  # explicit authority and provenance
    "UMFDVI-007": "LAW-ARCH-03",  # decision and execution separation
    "UMFDVI-008": "LAW-ARCH-07",  # fail-safe authorization
    "UMFDVI-015": "LAW-GOV-13",  # fail safely and reconcile (idempotency)
    "UMFDVI-017": "LAW-GOV-12",  # controlled evolution / historical integrity
    "UMFDVI-020": "LAW-GOV-08",  # approval scope recorded with authority
    "UMFDVI-022": "LAW-ARCH-07",  # fail-safe
    "UMFDVI-028": "LAW-ARCH-04",  # no unauthorized mutation of financial facts
    "UMFDVI-029": "LAW-GOV-08",  # auditability
    "UMFDVI-030": "LAW-GOV-02",  # registry integrity / single owner
    "UMFDVI-039": "LAW-GOV-13",  # recovery integrity
    "UMFDVI-040": "LAW-GOV-11",  # evidence-based status / certification
}


def _law(number: int, principle: str, text: str, enforcement: str) -> None:
    register_law(
        Law(
            law_id=f"LAW-UMFDVI-{number:03d}",
            principle=principle,
            normative_text=text,
            enforcement=enforcement,
            source="UCOS-MKT-MASTER-SPECIFICATION-030 §6",
        )
    )


# UMFDVI-003 — Context Completeness Law
_law(
    3,
    "Context completeness",
    "A decision must not proceed beyond its permitted stage when mandatory "
    "context is missing.",
    "universes.u12_intelligence.entities.DECISION_MACHINE",
)

# UMFDVI-004 — Objective Definition Law
_law(
    4,
    "Objective definition",
    "Every optimization task must declare its objectives and measurement "
    "semantics: meaning, unit, direction, horizon, population and owner.",
    "universes.u12_intelligence.objectives.ObjectiveRegistry.register",
)

# UMFDVI-005 — Constraint Preservation Law
_law(
    5,
    "Constraint preservation",
    "Mandatory constraints must not be violated or silently weakened to "
    "improve an objective score.",
    "universes.u12_intelligence.constraints.ConstraintRegistry.assert_mandatory_satisfied",
)

# UMFDVI-006 — Feasibility Law
_law(
    6,
    "Feasibility classification",
    "The system must distinguish feasible, infeasible, unknown and unverified "
    "alternatives. An infeasible alternative must not be recommended.",
    "universes.u12_intelligence.evaluation.FeasibilityGate.evaluate",
)

# UMFDVI-009 — Bounded Autonomy Law
_law(
    9,
    "Bounded autonomy",
    "Autonomous decision execution must remain within explicit, authorized "
    "action class, policy, scope, limits and recovery behaviour.",
    "universes.u12_intelligence.governance.ApprovalScope.authorizes",
)

# UMFDVI-010 — Model Transparency Law
_law(
    10,
    "Model transparency",
    "Every material model-dependent result must identify its model version, "
    "purpose, limitations and validation status.",
    "universes.u12_intelligence.evaluation.ModelVersionRegistry.record",
)

# UMFDVI-011 — Uncertainty Disclosure Law
_law(
    11,
    "Uncertainty disclosure",
    "Material uncertainty must be represented in the decision record, never "
    "silently omitted.",
    "universes.u12_intelligence.evaluation.EvaluationService.evaluate",
)

# UMFDVI-012 — Multi-Objective Integrity Law
_law(
    12,
    "Multi-objective integrity",
    "Conflicting objectives must be represented and evaluated transparently; a "
    "trade-off must not be concealed inside an undocumented aggregate score.",
    "universes.u12_intelligence.optimization.OptimizationService.assert_claim_honest",
)

# UMFDVI-013 — Risk Boundary Law
_law(
    13,
    "Risk boundary",
    "An optimization result must not override applicable canonical risk limits "
    "or the risk acceptance authority.",
    "universes.u12_intelligence.constraints.risk_limit_constraint",
)

# UMFDVI-014 — Temporal Validity Law
_law(
    14,
    "Temporal validity",
    "Evidence, policies, approvals and recommendations must be checked for "
    "validity at the required decision stage.",
    "universes.u12_intelligence.evidence.EvidenceStore.assert_fresh",
)

# UMFDVI-016 — Decision Versioning Law
_law(
    16,
    "Decision versioning",
    "Every material decision revision must be versioned and traceable to its "
    "predecessor.",
    "universes.u12_intelligence.entities.DecisionCase.version",
)

# UMFDVI-018 — Value Distinction Law
_law(
    18,
    "Value distinction",
    "Expected value, observed value, estimated incremental value and verified "
    "realized value must remain distinguishable.",
    "universes.u12_intelligence.entities.ValueRealizationRecord",
)

# UMFDVI-019 — Attribution Integrity Law
_law(
    19,
    "Attribution integrity",
    "A measured change must not automatically be attributed to a decision "
    "without an approved attribution method.",
    "universes.u12_intelligence.entities.ValueRealizationRecord.attribution_method_ref",
)

# UMFDVI-021 — Conflict Detection Law
_law(
    21,
    "Conflict detection",
    "Material conflicts among objectives, constraints, policies or authorities "
    "must be surfaced rather than silently resolved.",
    "universes.u12_intelligence.constraints.ConstraintRegistry.detect_conflicts",
)

# UMFDVI-023 — No Silent Fallback Law
_law(
    23,
    "No silent fallback",
    "Fallback algorithms, policies, models and data sources must be explicitly "
    "approved and observable.",
    "universes.u12_intelligence.optimization.SolverRegistry.fallback_of",
)

# UMFDVI-024 — Controlled Learning Law
_law(
    24,
    "Controlled learning",
    "Observed outcomes may inform future models or policies only through "
    "governed learning and validation processes.",
    "universes.u12_intelligence.entities.DecisionReview",
)

# UMFDVI-025 — Fairness and Impact Law
_law(
    25,
    "Fairness and impact",
    "Applicable stakeholder impact, fairness and discrimination constraints "
    "must be evaluated before consequential decisions.",
    "universes.u12_intelligence.constraints.fairness_constraint",
)

# UMFDVI-026 — Explainability Law
_law(
    26,
    "Explainability",
    "Consequential recommendations must provide explanations appropriate to "
    "their impact and audience.",
    "universes.u12_intelligence.entities.DecisionRecommendation.is_expired",
)

# UMFDVI-027 — Resource Bound Law
_law(
    27,
    "Resource bound",
    "Optimization workloads must operate within approved resource and "
    "execution limits.",
    "universes.u12_intelligence.optimization.ResourceBudget",
)

# UMFDVI-031 — Reproducibility Law
_law(
    31,
    "Reproducibility",
    "Material decision evaluations must retain sufficient information for "
    "reproduction, or document why exact reproduction is unavailable.",
    "universes.u12_intelligence.entities.DECISION_MACHINE.transition",
)

# UMFDVI-032 — Change Impact Law
_law(
    32,
    "Change impact",
    "Material changes to decision models, constraints or policies must trigger "
    "impact analysis and appropriate revalidation.",
    "universes.u12_intelligence.evaluation.ModelVersionRegistry.revalidate",
)

# UMFDVI-033 — Separation of Duties Law
_law(
    33,
    "Separation of duties",
    "Where required by canonical governance, proposal, approval, execution and "
    "verification must not be controlled by the same unauthorized principal.",
    "universes.u12_intelligence.governance.GovernanceService.assert_separation_of_duties",
)

# UMFDVI-034 — Outcome Evidence Law
_law(
    34,
    "Outcome evidence",
    "A decision must not be classified as successfully realized solely because "
    "an execution request was accepted.",
    "universes.u12_intelligence.entities.ValueRealizationRecord.attribution_category",
)

# UMFDVI-035 — Decision Expiration Law
_law(
    35,
    "Decision expiration",
    "Recommendations and approvals must expire, be revalidated, or remain valid "
    "only under explicit lifecycle rules.",
    "universes.u12_intelligence.entities.DecisionRecommendation.is_expired",
)

# UMFDVI-036 — Optimization Integrity Law
_law(
    36,
    "Optimization integrity",
    "An optimization result must accurately describe whether the solution is "
    "optimal, approximately optimal, feasible, heuristic, incomplete or "
    "unknown.",
    "universes.u12_intelligence.optimization.OptimizationRun.optimality_claim",
)

# UMFDVI-037 — Dependency Contract Law
_law(
    37,
    "Dependency contract",
    "All material dependencies must be governed by explicit contracts and "
    "failure semantics.",
    "universes.u12_intelligence.governance.DependencyUnavailable",
)

# UMFDVI-038 — Privacy Minimization Law
_law(
    38,
    "Privacy minimization",
    "Decision evaluation must use only the data necessary and authorized for "
    "the declared purpose.",
    "universes.u12_intelligence.evidence.EvidenceManifest",
)


def umfdvi_law(proposed_id: str) -> Law:
    """Resolve a proposed UMFDVI identifier to its enforceable canonical law."""
    if proposed_id in UMFDVI_TO_CANONICAL:
        return get_law(UMFDVI_TO_CANONICAL[proposed_id])
    canonical_id = proposed_id.replace("UMFDVI-", "LAW-UMFDVI-")
    return get_law(canonical_id)


def resolve_enforcement(path: str) -> object:
    """Resolve a dotted ``module.Class.member`` enforcement hook to an object.

    Returns ``None`` when the hook is dangling, so the registry tests can fail
    loudly instead of silently trusting a path that may never have existed.
    """
    parts = path.split(".")
    module = None
    for index in range(len(parts), 0, -1):
        try:
            module = importlib.import_module("marketplace." + ".".join(parts[:index]))
            rest = parts[index:]
            break
        except ModuleNotFoundError:
            continue
    if module is None:
        return None
    obj: object = module
    for part in rest:
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


@dataclass(frozen=True)
class EnforcementCheck:
    """The verified state of one law's enforcement hook."""

    law_id: str
    enforcement: str
    resolved: bool

    @property
    def dangling(self) -> bool:
        return not self.resolved


def enforcement_checks() -> list[EnforcementCheck]:
    """Verify every registered FDVI law's enforcement hook points at real code."""
    return [
        EnforcementCheck(
            law_id=law.law_id,
            enforcement=law.enforcement,
            resolved=resolve_enforcement(law.enforcement) is not None,
        )
        for law in all_laws()
        if law.law_id.startswith("LAW-UMFDVI-")
    ]


def dangling_enforcements() -> list[EnforcementCheck]:
    return [check for check in enforcement_checks() if check.dangling]


def umfdvi_coverage() -> dict[str, str]:
    """Every proposed UMFDVI-001..040 -> the canonical law that governs it."""
    out: dict[str, str] = {}
    registered = {law.law_id for law in all_laws()}
    for number in range(1, 41):
        proposed = f"UMFDVI-{number:03d}"
        if proposed in UMFDVI_TO_CANONICAL:
            out[proposed] = UMFDVI_TO_CANONICAL[proposed]
        else:
            canonical = f"LAW-UMFDVI-{number:03d}"
            if canonical not in registered:
                raise ValueError(
                    f"{proposed} is neither registered as {canonical} nor mapped "
                    "to an existing canonical law; coverage is incomplete"
                )
            out[proposed] = canonical
    return out
