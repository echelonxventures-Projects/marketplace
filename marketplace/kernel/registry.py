"""Self-describing registry framework.

Implements the registry tier of the canonical production chain and the UCOS
principles: Self-Describing Registries, Automatic Discovery, Canonical
Authority Preservation, Single Canonical Source of Truth, Explicit Ownership.

Every registry entry declares its owner universe, lifecycle, version and the
law it enforces. Registries *are* the governance machine: duplicate concepts,
orphan artifacts and unauthorized write paths are all detected here
(GOV-02/05/06).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .errors import DuplicateCanonicalConceptError, NotFoundError, OrphanArtifactError


@dataclass
class ConceptRecord:
    """A canonical concept with exactly one write owner."""

    concept: str
    owner_universe: str
    description: str
    identifier_prefix: str
    write_path: str  # the only sanctioned mutator
    read_projections: list[str] = field(default_factory=list)
    version: int = 1
    status: str = "active"
    source: str = "UM-ACGCS-001 §5.3"


class ConceptRegistry:
    """The canonical concept registry: one write authority per concept.

    This registry implements LAW-ARCH-02 (distinct canonical concepts),
    GOV-02 (single source of truth) and GOV-05 (zero duplication).
    """

    def __init__(self) -> None:
        self._concepts: dict[str, ConceptRecord] = {}
        self._by_owner: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def register(self, record: ConceptRecord) -> ConceptRecord:
        with self._lock:
            if record.concept in self._concepts:
                existing = self._concepts[record.concept]
                if existing.owner_universe != record.owner_universe:
                    raise DuplicateCanonicalConceptError(
                        f"Concept {record.concept!r} is already owned by "
                        f"{existing.owner_universe!r}; {record.owner_universe!r} cannot "
                        "register a competing authority. Reconcile via the ownership "
                        "decision record (UM-ACGCS-001 §5.5).",
                        details={
                            "concept": record.concept,
                            "existing_owner": existing.owner_universe,
                            "contesting_owner": record.owner_universe,
                        },
                    )
                return existing
            self._concepts[record.concept] = record
            self._by_owner.setdefault(record.owner_universe, []).append(record.concept)
        return record

    def get(self, concept: str) -> ConceptRecord:
        record = self._concepts.get(concept)
        if record is None:
            raise NotFoundError(f"Canonical concept {concept!r} is not registered")
        return record

    def assert_owner(self, concept: str, universe: str) -> ConceptRecord:
        record = self.get(concept)
        if record.owner_universe != universe:
            raise DuplicateCanonicalConceptError(
                f"Universe {universe!r} attempted to write canonical concept "
                f"{concept!r} owned by {record.owner_universe!r}. "
                f"Canonical write path is {record.write_path!r}."
            )
        return record

    def concepts_of(self, universe: str) -> list[str]:
        return list(self._by_owner.get(universe, []))

    def all(self) -> Iterable[ConceptRecord]:
        return list(self._concepts.values())

    def detect_duplicates(self) -> list[dict]:
        """Surface concepts whose definitions overlap materially."""
        seen: dict[frozenset[str], list[str]] = {}
        for record in self._concepts.values():
            tokens = frozenset(record.description.lower().split()) & {
                "party", "product", "offer", "order", "payment", "inventory",
                "price", "shipment", "settlement", "ledger", "listing", "variant",
            }
            key = frozenset(record.concept.split("_")) | tokens
            seen.setdefault(key, []).append(record.concept)
        return [
            {"concepts": v}
            for v in seen.values()
            if len(v) > 1 and len(frozenset(v)) > 1
        ]


CONCEPTS = ConceptRegistry()


@dataclass
class CapabilityRecord:
    capability_id: str
    domain: str
    name: str
    purpose: str
    owner_universe: str
    implementing_module: str
    disposition: str  # reuse | reference | extend | implement | exclude | blocked
    status: str = "implemented"
    requirement_ids: list[str] = field(default_factory=list)
    law_ids: list[str] = field(default_factory=list)


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityRecord] = {}
        self._lock = threading.RLock()

    def register(self, record: CapabilityRecord) -> CapabilityRecord:
        with self._lock:
            if record.capability_id in self._capabilities:
                raise DuplicateCanonicalConceptError(
                    f"Capability {record.capability_id!r} already registered"
                )
            self._capabilities[record.capability_id] = record
        return record

    def get(self, capability_id: str) -> CapabilityRecord:
        record = self._capabilities.get(capability_id)
        if record is None:
            raise NotFoundError(f"Capability {capability_id!r} not registered")
        return record

    def find(self, predicate) -> list[CapabilityRecord]:
        return [c for c in self._capabilities.values() if predicate(c)]

    def all(self) -> Iterable[CapabilityRecord]:
        return list(self._capabilities.values())

    def assert_no_dead_capabilities(self) -> None:
        dead = [c for c in self._capabilities.values() if c.status == "planned"]
        if dead:
            raise OrphanArtifactError(
                "Capabilities registered as 'planned' have no implementing module; "
                "a capability must not be registered without an implementation.",
                details={"dead_capabilities": [c.capability_id for c in dead]},
            )


CAPABILITIES = CapabilityRegistry()


@dataclass
class RequirementRecord:
    requirement_id: str
    domain: str
    statement: str
    source: str
    capability_ids: list[str] = field(default_factory=list)
    test_ids: list[str] = field(default_factory=list)
    law_ids: list[str] = field(default_factory=list)
    status: str = "implemented"
    acceptance: str = ""

    def trace_complete(self) -> bool:
        return bool(self.capability_ids) and bool(self.test_ids)


class RequirementRegistry:
    def __init__(self) -> None:
        self._requirements: dict[str, RequirementRecord] = {}
        self._lock = threading.RLock()

    def register(self, record: RequirementRecord) -> RequirementRecord:
        with self._lock:
            if record.requirement_id in self._requirements:
                raise DuplicateCanonicalConceptError(
                    f"Requirement {record.requirement_id!r} already registered"
                )
            self._requirements[record.requirement_id] = record
        return record

    def get(self, requirement_id: str) -> RequirementRecord:
        record = self._requirements.get(requirement_id)
        if record is None:
            raise NotFoundError(f"Requirement {requirement_id!r} not registered")
        return record

    def all(self) -> Iterable[RequirementRecord]:
        return list(self._requirements.values())

    def untraced(self) -> list[RequirementRecord]:
        return [r for r in self._requirements.values() if not r.trace_complete()]


REQUIREMENTS = RequirementRegistry()


@dataclass
class InvariantRecord:
    invariant_id: str
    statement: str
    enforced_by: str
    test_ids: list[str] = field(default_factory=list)


class InvariantRegistry:
    def __init__(self) -> None:
        self._invariants: dict[str, InvariantRecord] = {}

    def register(self, record: InvariantRecord) -> InvariantRecord:
        if record.invariant_id in self._invariants:
            raise DuplicateCanonicalConceptError(
                f"Invariant {record.invariant_id!r} already registered"
            )
        self._invariants[record.invariant_id] = record
        return record

    def get(self, invariant_id: str) -> InvariantRecord:
        record = self._invariants.get(invariant_id)
        if record is None:
            raise NotFoundError(f"Invariant {invariant_id!r} not registered")
        return record

    def all(self) -> Iterable[InvariantRecord]:
        return list(self._invariants.values())


INVARIANTS = InvariantRegistry()
