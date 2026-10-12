# ADR-FDVI-001 — Canonical Capability Placement for Specification 030

| Field | Value |
|---|---|
| Status | ACCEPTED |
| Date | 2026-10-11 |
| Owner | U12 — Intelligence & AI |
| Source | UCOS-MKT-MASTER-SPECIFICATION-030 §0.4, §22 ADR-001, §23 GAP-001/004 |
| Supersedes | Proposal `UCOS-MKT-FDVI-030000` as a new Universe |

## Context

Specification 030 proposes a capability "Financial Decision Governance,
Optimization & Value Realization Intelligence" with a proposed **new** universe
id `UCOS-MKT-FDVI-030000`. The specification itself marks this as
"PROPOSED — PENDING CANONICAL RECONCILIATION" and its own ADR-001 requires a
placement decision before implementation.

The repository's canonical master index is the 15-universe model declared in
`marketplace/universes/_base.py` and cross-confirmed by the Architectural
Completeness and Gap Closure Specification §5.2. There is no U16, and the
canonical model states the 15 universes are the logical boundaries.

## Options considered

1. **New universe U16 / `UCOS-MKT-FDVI-030000`.** Rejected: it contradicts the
   canonical 15-universe index, and Spec 030 §0.4 explicitly forbids the
   capability from owning canonical financial facts — so it is not a
   full peer of U09, it is a *consumer* of them.
2. **Sub-capability of U09 (Financial Commerce).** Rejected: U09 owns payment,
   ledger, settlement and reconciliation *execution* authorities. Spec 030 §0.4
   expressly excludes FDVI from those. Placing it there would blur the
   decision/execution separation that LAW-ARCH-03 mandates.
3. **Sub-capability of U12 (Intelligence & AI).** Accepted.

## Decision

FDVI is implemented as a **governed sub-capability of U12 — Intelligence & AI**.

U12 already canonically owns `decision_record` ("Decision provenance: inputs,
output, model/version, rationale"), `ai_task`, recommendation decisions and
model governance. Spec 030's ontology (DecisionCase, DecisionEvaluation,
OptimizationRun, DecisionRecommendation, DecisionModelVersion) is the governed
financial-decision extension of exactly those concepts. The genuinely missing
concepts are registered as U12-owned extensions through the existing
`Universe.owns` contract.

## Consequences

- The 15-universe canonical index is preserved; no unapproved governance change.
- Decision science reuses U12's model registration, policy authorization and
  approval gates instead of duplicating them (GOV-01, GOV-04, GOV-05).
- FDVI never becomes a write authority for ledger, payments, settlement,
  pricing, tax, orders, inventory, budget or risk acceptance (Spec 030 §0.4);
  it consumes those contracts through explicit adapters.
- Law identifiers use the canonical `LAW-` prefix: `LAW-UMFDVI-*`
  (see ADR-FDVI-002).

## Evidence

- `marketplace/universes/_base.py` — 15 universes, no U16.
- `marketplace/universes/u11_u15/services.py` — U12 owns `decision_record`.
- Test: `tests/test_registry.py::TestConceptRegistry::test_decision_case_owned_by_u12`.
