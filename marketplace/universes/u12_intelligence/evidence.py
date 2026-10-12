"""U12-FDVI evidence assembly (Spec 030 Modules 01, 04; FR-009..011, FR-046).

Evidence is referenced, never copied into the decision record as opaque fact.
Each item carries source authority, version, freshness and access
classification (LAW-UMFDVI-038 privacy minimization, LAW-UMFDVI-014 temporal
validity).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional

from marketplace.kernel import AuthorizationContext, generate
from marketplace.kernel.errors import (
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.universes._base import U12
from marketplace.universes.u12_intelligence.entities import DecisionEvidence


@dataclass
class EvidenceManifest:
    """The versioned manifest of evidence used by one evaluation (FR-009)."""

    manifest_id: str
    case_id: str
    items: list[DecisionEvidence] = field(default_factory=list)
    freshness_policy: dict = field(default_factory=dict)
    version: int = 1
    completeness: str = "incomplete"  # incomplete | complete

    def refs(self) -> list[str]:
        return [e.evidence_id for e in self.items]


class EvidenceStore:
    """Governed evidence registry: provenance-tracked, freshness-checked."""

    def __init__(self, default_max_age_hours: int = 24) -> None:
        self._items: dict[str, DecisionEvidence] = {}
        self._by_case: dict[str, list[str]] = {}
        self.default_max_age_hours = default_max_age_hours
        self._lock = threading.RLock()

    def register(self, authz: AuthorizationContext, evidence: DecisionEvidence) -> DecisionEvidence:
        """Register evidence against the caller's tenant (LAW-ARCH-07)."""
        evidence.access_classification = evidence.access_classification or "internal"
        with self._lock:
            if evidence.evidence_id in self._items:
                raise ValidationError(
                    f"Evidence {evidence.evidence_id} is already registered"
                )
            self._items[evidence.evidence_id] = evidence
            self._by_case.setdefault(evidence.payload.get("case_id", ""), []).append(
                evidence.evidence_id
            )
        return evidence

    def submit(
        self,
        authz: AuthorizationContext,
        *,
        case_id: str,
        evidence_type: str,
        source_authority_ref: str,
        source_artifact_ref: str,
        source_version: int,
        observation_time: str,
        validity_interval: tuple[str, str],
        payload: dict,
        provenance: str = "",
        access_classification: str = "internal",
    ) -> DecisionEvidence:
        if not source_authority_ref:
            raise ValidationError("Evidence requires a source authority reference")
        evidence = DecisionEvidence(
            evidence_id=generate("evidence"),
            evidence_type=evidence_type,
            source_authority_ref=source_authority_ref,
            source_artifact_ref=source_artifact_ref,
            source_version=source_version,
            provenance=provenance or f"{source_authority_ref}:{source_artifact_ref}@v{source_version}",
            observation_time=observation_time,
            ingestion_time=U12.now(),
            validity_interval=validity_interval,
            access_classification=access_classification,
            payload=dict(payload, case_id=case_id),
        )
        return self.register(authz, evidence)

    def get(self, evidence_id: str) -> DecisionEvidence:
        item = self._items.get(evidence_id)
        if item is None:
            raise NotFoundError(f"Decision evidence {evidence_id} not found")
        return item

    def for_case(self, case_id: str) -> list[DecisionEvidence]:
        return [self.get(i) for i in self._by_case.get(case_id, [])]

    # -- freshness (FR-011, AC-009) ---------------------------------------

    def assess_freshness(self, evidence_id: str, now: str, max_age_hours: Optional[int] = None) -> DecisionEvidence:
        item = self.get(evidence_id)
        limit = max_age_hours if max_age_hours is not None else self.default_max_age_hours
        start, end = item.validity_interval
        if start and end:
            if now < start or now > end:
                item.freshness_status = "stale"
                item.evidence_status = "stale"
                return item
        if limit is not None:
            from datetime import datetime, timedelta

            observed = datetime.fromisoformat(item.observation_time.replace("Z", "+00:00"))
            moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
            if (moment - observed) > timedelta(hours=limit):
                item.freshness_status = "stale"
                item.evidence_status = "stale"
        return item

    def assert_fresh(self, evidence_ids: Iterable[str], now: str, max_age_hours: Optional[int] = None) -> None:
        """Block progression when evidence required to be current is stale."""
        stale: list[str] = []
        for evidence_id in evidence_ids:
            item = self.assess_freshness(evidence_id, now, max_age_hours)
            if item.evidence_status in {"stale", "invalid", "disputed"}:
                stale.append(evidence_id)
        if stale:
            raise InvariantViolationError(
                "LAW-UMFDVI-014",
                f"Evidence {stale} is not valid at decision time {now}; refresh "
                "the evidence or apply an explicitly authorized policy",
                details={"stale_evidence": stale, "now": now},
            )

    # -- access control (AC-045, AC-046) -----------------------------------

    def retrieve(self, authz: AuthorizationContext, evidence_id: str) -> DecisionEvidence:
        item = self.get(evidence_id)
        classification = item.access_classification
        if classification in {"sensitive", "financial", "personal"}:
            authz.requires_scope(f"evidence.read:{classification}")
        return item

    def retrieve_many(
        self, authz: AuthorizationContext, evidence_ids: Iterable[str]
    ) -> list[DecisionEvidence]:
        return [self.retrieve(authz, i) for i in evidence_ids]

    def assemble_manifest(
        self,
        authz: AuthorizationContext,
        case_id: str,
        evidence_ids: Iterable[str],
        *,
        now: str,
        freshness_required: bool = True,
        max_age_hours: Optional[int] = None,
    ) -> EvidenceManifest:
        items = self.retrieve_many(authz, evidence_ids)
        if freshness_required:
            self.assert_fresh(evidence_ids, now, max_age_hours)
        complete = bool(items) and all(i.evidence_status == "valid" for i in items)
        return EvidenceManifest(
            manifest_id=generate("evidence"),
            case_id=case_id,
            items=items,
            freshness_policy={
                "max_age_hours": max_age_hours if max_age_hours is not None else self.default_max_age_hours,
                "required": freshness_required,
            },
            completeness="complete" if complete else "incomplete",
        )
