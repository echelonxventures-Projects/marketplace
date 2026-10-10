"""UCOS Omega constitutional laws for the Universal Marketplace.

Every law in this module is machine-enforceable. The constitution is the single
canonical source for governance rules; no other module may redefine them
(GOV-01 Knowledge Once, GOV-02 Canonical source of truth).

Law identifiers are stable and append-only. Retiring a law requires a new law
record that supersedes it; the original record is never deleted or renumbered
(GOV-12 Controlled evolution).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

# ---------------------------------------------------------------------------
# Law registry (self-describing, append-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Law:
    """A constitutional law with a stable identifier and enforcement hook."""

    law_id: str
    principle: str
    normative_text: str
    enforcement: str
    source: str = "UM-ACGCS-001 / UCOS-MKT-MASTER-SPECIFICATION"

    def __post_init__(self) -> None:
        if not self.law_id.startswith("LAW-"):
            raise ValueError(f"Law identifiers must use the LAW- prefix: {self.law_id!r}")
        if not self.principle:
            raise ValueError(f"{self.law_id} must declare a principle name")


_LAWS: dict[str, Law] = {}


def register_law(law: Law) -> Law:
    """Register a law. Identifiers are unique and never recycled."""
    if law.law_id in _LAWS:
        raise ValueError(f"Duplicate law identifier {law.law_id}; laws are append-only")
    _LAWS[law.law_id] = law
    return law


def get_law(law_id: str) -> Law:
    return _LAWS[law_id]


def all_laws() -> Iterable[Law]:
    return list(_LAWS.values())


# ---------------------------------------------------------------------------
# The 15 foundational governance principles (GOV-01 .. GOV-15)
# ---------------------------------------------------------------------------

register_law(
    Law(
        law_id="LAW-GOV-01",
        principle="Knowledge Once",
        normative_text=(
            "Each approved definition is captured once under its canonical authority and "
            "referenced by stable identifier elsewhere. Copy-paste specifications and "
            "conflicting shadow definitions are prohibited."
        ),
        enforcement="registry.kernel.ConceptRegistry.assert_single_owner",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-02",
        principle="Canonical source of truth",
        normative_text=(
            "One authoritative owner and write path exists for each canonical concept. "
            "Consumer projections are explicitly derived and reconciled."
        ),
        enforcement="registry.kernel.ConceptRegistry.write_path_owner",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-03",
        principle="Discover before creating",
        normative_text=(
            "Search current registries, catalogs, schemas, services and policy stores "
            "before proposing a new artifact or capability."
        ),
        enforcement="registry.kernel.ConceptRegistry.find",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-04",
        principle="Reuse before extending",
        normative_text=(
            "Prefer a suitable existing capability. Extend through a governed compatible "
            "contract when reuse is viable; record why a new capability is necessary when not."
        ),
        enforcement="registry.kernel.CapabilityRegistry.disposition",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-05",
        principle="Zero duplication and overlap",
        normative_text=(
            "Duplicate artifacts, policies, APIs and ownership are detected and resolved "
            "through merge, delegation, retirement, or documented bounded specialization."
        ),
        enforcement="registry.kernel.ConceptRegistry.detect_duplicates",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-06",
        principle="Zero orphan artifacts",
        normative_text=(
            "Every requirement, schema, API, event, policy, workflow, dataset and control "
            "has an owner, lifecycle, dependencies and traceability links."
        ),
        enforcement="traceability.kernel.validate_no_orphans",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-07",
        principle="No hardcoded business ceilings",
        normative_text=(
            "Categories, attributes, relationship depth, locales, regions, channels and "
            "policy variants are governed extensions. Operational quotas and safety bounds "
            "are configurable, observable limits."
        ),
        enforcement="config.kernel.GovernedConfig",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-08",
        principle="Explicit authority and provenance",
        normative_text=(
            "Actor, authority, source, time, reason, version and evidence are recorded for "
            "consequential changes and decisions."
        ),
        enforcement="provenance.kernel.ProvenanceRecord",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-09",
        principle="Technology neutrality",
        normative_text=(
            "Observable behavior, contracts and outcomes are specified before prescribing "
            "implementation technology. Technology decisions are recorded separately with "
            "rationale and review triggers."
        ),
        enforcement="adr.docs/adr/ADR-001",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-10",
        principle="Secure and private by design",
        normative_text=(
            "Least privilege, data minimization, purpose limitation, retention, threat "
            "modeling, audit, and privacy/security review apply across the lifecycle."
        ),
        enforcement="authorization.kernel.RequireAuthorization",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-11",
        principle="Evidence-based status",
        normative_text=(
            "Document presence is not implementation evidence. A completion status requires "
            "evidence, scope, date/version, verifier and passed criteria."
        ),
        enforcement="evidence.kernel.EvidenceStore",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-12",
        principle="Controlled evolution",
        normative_text=(
            "Compatibility is preserved and migration is by versioned, reviewed changes. "
            "Breaking changes require impact analysis, migration and rollback plans, approval."
        ),
        enforcement="versioning.kernel.ContractVersion",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-13",
        principle="Fail safely and reconcile",
        normative_text=(
            "Distributed workflows define idempotency, retry, timeout, compensation, "
            "dead-letter handling, reconciliation and manual recovery."
        ),
        enforcement="saga.kernel.Saga.rollback / eventbus.kernel.EventBus",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-14",
        principle="Human accountability for automation",
        normative_text=(
            "Automated and AI decisions retain accountable owners, policy boundaries, "
            "explainable records, override paths, and human review where impact warrants it."
        ),
        enforcement="governance.u12_intelligence.ApprovalGate",
    )
)

register_law(
    Law(
        law_id="LAW-GOV-15",
        principle="No implementation before authorization",
        normative_text=(
            "Approval of a specification or an assessment is not implementation "
            "authorization. Each implementation tranche requires an approved plan, owners, "
            "funding, security/privacy controls and a release gate."
        ),
        enforcement="governance.u14_governance.AuthorizationOfImplementation",
    )
)


# ---------------------------------------------------------------------------
# Architectural laws specific to the commerce domain (master spec §2, §4)
# ---------------------------------------------------------------------------

register_law(
    Law(
        law_id="LAW-ARCH-01",
        principle="Canonical production chain",
        normative_text=(
            "Downstream artifacts consume validated outputs of preceding chain stages and "
            "must not silently redefine a canonical concept owned by an upstream domain."
        ),
        enforcement="registry.kernel.ConceptRegistry.assert_no_redefinition",
    )
)

register_law(
    Law(
        law_id="LAW-ARCH-02",
        principle="Distinct canonical commerce concepts",
        normative_text=(
            "Product, Offer, Inventory, Price, Order, Payment, Shipment and Settlement are "
            "distinct canonical concepts. A product is not an offer, an offer is not "
            "inventory, and an order is not a payment."
        ),
        enforcement="registry.kernel.ConceptRegistry.concept_boundary",
    )
)

register_law(
    Law(
        law_id="LAW-ARCH-03",
        principle="Decision and execution separation",
        normative_text=(
            "A recommendation is not an approval. An approval is not an execution. An "
            "execution request is not an execution result. An execution result is not "
            "automatically verified financial value."
        ),
        enforcement="workflows.finance.DecisionExecutionSeparation",
    )
)

register_law(
    Law(
        law_id="LAW-ARCH-04",
        principle="Append-only financial facts",
        normative_text=(
            "Financial ledger records are append-only; corrections are compensating entries, "
            "never destructive edits."
        ),
        enforcement="universes.u09_finance.ledger.append_only",
    )
)

register_law(
    Law(
        law_id="LAW-ARCH-05",
        principle="Snapshot at commitment",
        normative_text=(
            "Order, price, tax, seller terms and fulfillment promises are snapshotted at "
            "commitment with provenance and applicable versions."
        ),
        enforcement="universes.u08_order.snapshot",
    )
)

register_law(
    Law(
        law_id="LAW-ARCH-06",
        principle="Derived projections are not authorities",
        normative_text=(
            "Search indexes, recommendation projections and analytics stores are derived "
            "representations. They must not become the authoritative record for product "
            "ownership, available stock, order status or financial balances."
        ),
        enforcement="universes.u11_experience.projection",
    )
)

register_law(
    Law(
        law_id="LAW-ARCH-07",
        principle="Fail-safe authorization",
        normative_text=(
            "Before any consequential action the current authorization is validated against "
            "the canonical authority. If authorization cannot be established, execution does "
            "not proceed. Authorization is never bypassed because an approval service is "
            "temporarily unavailable."
        ),
        enforcement="authorization.kernel.fail_safe",
    )
)

register_law(
    Law(
        law_id="LAW-ARCH-08",
        principle="AI output is not canonical fact",
        normative_text=(
            "AI/agent actions are bounded by delegated authority, policy and revocation. "
            "AI output must never silently become canonical product, financial, identity or "
            "compliance fact."
        ),
        enforcement="universes.u12_intelligence.bounded_agent",
    )
)


def enforce(law_id: str) -> Callable:
    """Decorator marker: attach a function to the law that enforces it.

    This is a registry convenience only; the enforcement hooks themselves live
    in the kernel and universes and are exercised by the test suite.
    """

    def decorator(fn: Callable) -> Callable:
        fn.__enforces_law__ = law_id  # type: ignore[attr-defined]
        return fn

    get_law(law_id)
    return decorator
