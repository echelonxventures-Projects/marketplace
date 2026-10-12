# ADR-FDVI-002 — Law Identifier Allocation for Specification 030

| Field | Value |
|---|---|
| Status | ACCEPTED |
| Date | 2026-10-11 |
| Owner | U12 — Intelligence & AI |
| Source | UCOS-MKT-MASTER-SPECIFICATION-030 §6 (UMFDVI-001..040), §23 GAP-010/031 |
| Supersedes | Proposed bare `UMFDVI-001..040` law identifiers |

## Context

Spec 030 proposes laws `UMFDVI-001..UMFDVI-040`. The canonical law registry
`marketplace/constitution/laws.py` requires every `Law.law_id` to start with
`LAW-` (enforced in `Law.__post_init__`) and to be unique and append-only
(`register_law` raises on duplicates). The proposed bare identifiers are not
registerable as written.

Several proposed laws materially duplicate laws already registered in the
canonical constitution:

| Proposed | Existing canonical law |
|---|---|
| UMFDVI-001 Canonical Authority | LAW-GOV-02, LAW-ARCH-01 |
| UMFDVI-002 Evidence Provenance | LAW-GOV-08 |
| UMFDVI-007 Decision Separation | LAW-ARCH-03 |
| UMFDVI-008 Authority Validation | LAW-ARCH-07 |
| UMFDVI-015 Idempotent Execution | LAW-GOV-13 |
| UMFDVI-017 Historical Integrity | LAW-GOV-12 |
| UMFDVI-020 Approval Scope | LAW-GOV-08, LAW-ARCH-03 |
| UMFDVI-022 Fail-Safe | LAW-ARCH-07 |
| UMFDVI-028 No Unauthorized Mutation | LAW-ARCH-04, LAW-ARCH-08 |
| UMFDVI-029 Auditability | LAW-GOV-08 |
| UMFDVI-030 Registry Integrity | LAW-GOV-02, LAW-GOV-06 |
| UMFDVI-039 Recovery Integrity | LAW-GOV-13 |
| UMFDVI-040 Certification | LAW-GOV-11 |

## Decision

1. Net-new decision-science laws are registered with the canonical `LAW-` prefix
   as `LAW-UMFDVI-<nnn>`, preserving the spec's numbering 1:1 so traceability
   from `UMFDVI-006` → `LAW-UMFDVI-006` is mechanical.
2. Laws that duplicate existing canonical laws are **not re-registered**. Their
   normative effect is inherited from the existing law, and the FDVI
   constitution module records the mapping so traceability is preserved
   (GOV-01 Knowledge Once, GOV-05 Zero duplication).

This satisfies Spec 030 §35.3 (collision check REQUIRED) without creating
competing canonical law definitions.

## Consequences

- 27 net-new laws registered; 13 mapped to existing canonical laws.
- `constitution/laws.py` remains the single canonical law registry.
- The mapping table is testable: every `UMFDVI-<nnn>` resolves to exactly one
  enforceable canonical law.

## Evidence

- `marketplace/constitution/laws.py` — `Law.__post_init__` LAW- prefix rule.
- `marketplace/universes/u12_intelligence/constitution.py` — registration + map.
- Test: `tests/test_registry.py::TestUmfdviConstitution::test_coverage_is_complete`
  and `::test_coverage_resolves_registered_laws`.
