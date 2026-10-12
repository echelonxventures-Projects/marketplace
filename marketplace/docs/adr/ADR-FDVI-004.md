# ADR-FDVI-004 — Enforcement Hooks Must Resolve to Real Code

| Field | Value |
|---|---|
| Status | ACCEPTED |
| Date | 2026-10-11 |
| Owner | U12 — Intelligence & AI |
| Source | UCOS-MKT-MASTER-SPECIFICATION-030 §6; GOV-06 Zero orphan artifacts; LAW-GOV-11 |
| Supersedes | The enforcement paths as originally registered in `constitution.py` |

## Context

Each `LAW-UMFDVI-*` record declares an `enforcement` string naming the code that
enforces the law. On audit, 21 of the 27 registered hooks did not resolve:

* `decision`, `models`, `value`, `review`, `recommendation` and `dependencies`
  were named as sub-modules of `u12_intelligence`, but no such modules exist.
  The behaviour they describe lives in `entities.py`, `evaluation.py` and
  `governance.py`.
* Several hooks named attributes that are dataclass *fields* rather than
  callable or class-level objects, so they could not be resolved by attribute
  lookup even where the module was correct.

The effect was that the law registry asserted *where* each law is enforced
without any of those assertions being checkable. Nothing failed, because nothing
ever looked.

## Options considered

1. **Create the six named modules** so the hooks resolve as written. Rejected:
   they would re-export classes already defined elsewhere, duplicating the
   canonical decision ontology (GOV-01, GOV-05) and creating a second authority
   for the same concepts.
2. **Delete the `enforcement` field.** Rejected: the field carries real
   traceability value when it is accurate, and GOV-06 requires every artifact to
   have traceable owners.
3. **Repoint each hook at the code that actually enforces the law, and make the
   resolution machine-checkable.** Accepted.

## Decision

1. Each `LAW-UMFDVI-*` `enforcement` string now names a module, class or member
   that genuinely exists under `marketplace/`.
2. `constitution.resolve_enforcement` resolves a dotted hook against the package
   and returns `None` when it dangles; `dangling_enforcements()` reports the
   offenders.
3. The test suite asserts zero dangling hooks, so a hook that later rots fails
   a test rather than silently persisting (LAW-GOV-11 evidence-based status).

## Consequences

- The law registry's enforcement claims are now falsifiable and continuously
  verified.
- Hooks point at real enforcers, so an auditor can navigate from a law to the
  code that upholds it.
- Renaming or moving any enforcer requires updating its law record, which the
  test enforces.

## Evidence

- `marketplace/universes/u12_intelligence/constitution.py` — repointed hooks,
  `resolve_enforcement`, `dangling_enforcements`.
- Test: `tests/test_registry.py::TestEnforcementHooksResolve::test_no_dangling_enforcement_hooks`.
