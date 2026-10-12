# ADR-FDVI-003 — Financial Precision, Optimizer Truthfulness and Execution Recovery

| Field | Value |
|---|---|
| Status | ACCEPTED |
| Date | 2026-10-11 |
| Owner | U12 — Intelligence & AI |
| Source | UCOS-MKT-MASTER-SPECIFICATION-030 §8.5, §9, §10.3, §26 F-005/F-009; NFR-024/025 |

## Context

Spec 030 places strict honesty requirements on the decision engine that are
easy to violate silently:

- §8.5 / INV-018 / AC-019: an optimization result must not claim a stronger
  optimality guarantee than the solver supports.
- §9.2 / INV-010 / AC-013: financial inputs with incompatible currencies or
  units must be rejected or normalized by an *approved* method — never silently
  combined. NFR-024/025 forbid uncontrolled binary floating point.
- §26 F-009 / AC-030: when an execution outcome is unknown, the engine must
  resolve the authoritative status rather than blindly re-submitting.
- §12.7 / AC-014: expected value and realized value must never be conflated.

## Decision

1. **Financial precision.** All monetary evaluation flows through the existing
   canonical `kernel.Money` (immutable, `decimal.Decimal`, currency-isolated,
   banker's rounding). FDVI introduces no second money type and no float
   arithmetic anywhere in the evaluation path. Currency mismatch raises
   `CurrencyMismatch` — there is no implicit conversion and no invented
   exchange rate.
2. **Optimizer truthfulness.** `OptimizationRun` carries a
   `solution_status` from a closed vocabulary and a derived `optimality_claim`
   that is *computed from declared solver capabilities*, never set by the
   caller. A solver declares `proves_optimality: bool`. The claim
   `proven_optimal` is reachable only when the solver declares that capability
   **and** the run completed normally; otherwise the strongest claim available
   is `approximate`, `heuristic` or `incomplete`. The claim is asserted in
   tests.
3. **Execution recovery.** Execution intents are submitted under
   `kernel.IdempotencyStore`. A dependency returning an indeterminate status
   raises `UnknownOutcomeError` (retryable=False); recovery must call
   `resolve_unknown_outcome`, which queries the execution authority's
   authoritative status before any re-submission is permitted. Re-submission
   after an unknown outcome is a *separate, guarded* operation, never a retry.
4. **Value separation.** `expected_outcome_summary` on a recommendation and
   `observed`/`attributable` fields on a value realization record are distinct
   typed fields. No code path writes an expected value into a realized field.

## Consequences

- The optimizer cannot lie about optimality even if asked to.
- Financial calculations are deterministic and reproducible (NFR-022).
- Unknown execution outcomes can never cause a duplicate financial action
  (INV-007/INV-012, AC-028).
- Expected and realized value are distinguishable by construction (INV-018).

## Evidence

- `marketplace/universes/u12_intelligence/optimization.py` — `OptimizationService.assert_claim_honest` and `OptimizationRun.optimality_claim`.
- `marketplace/universes/u12_intelligence/governance.py` — `submit_execution_intent`, `resolve_unknown_outcome`.
- Tests: `tests/test_fdvi.py` (financial arithmetic, model gates),
  `tests/test_governance.py` (approval scope, execution recovery),
  `tests/test_registry.py` (constitutional coverage).
