"""Authorization, provenance, audit and clock primitives.

LAW-ARCH-07 (fail-safe authorization): an operation that cannot establish
authorization does not proceed, and unavailability of an authorization
dependency is *not* a reason to bypass it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from .errors import AuthorizationError, TenantIsolationError


# ---------------------------------------------------------------------------
# Clock — single canonical time source (deterministic in tests)
# ---------------------------------------------------------------------------


class Clock:
    """Canonical UTC clock. Tests inject a deterministic clock."""

    def __init__(self, start: Optional[str] = None, tick_seconds: float = 0.0) -> None:
        self._start = datetime.fromisoformat(start) if start else datetime.now(timezone.utc)
        self._tick = tick_seconds
        self._elapsed = 0.0

    def now(self) -> str:
        from datetime import timedelta

        moment = self._start + timedelta(seconds=self._elapsed)
        self._elapsed += self._tick
        return moment.isoformat()

    def __call__(self) -> str:
        return self.now()


# ---------------------------------------------------------------------------
# Authorization context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizationContext:
    """The resolved authority under which a consequential action runs."""

    actor_id: str
    actor_type: str  # person | service | agent
    roles: frozenset[str]
    tenant_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    delegation_id: Optional[str] = None  # consent/delegation chain, if delegated
    on_behalf_of: Optional[str] = None

    def requires_role(self, role: str) -> "AuthorizationContext":
        if role not in self.roles:
            raise AuthorizationError(
                f"Actor {self.actor_id!r} lacks required role {role!r}",
                details={"roles": sorted(self.roles)},
            )
        return self

    def requires_scope(self, scope: str) -> "AuthorizationContext":
        if scope not in self.scopes:
            raise AuthorizationError(
                f"Actor {self.actor_id!r} lacks required scope {scope!r}",
                details={"scopes": sorted(self.scopes)},
            )
        return self

    def same_tenant(self, resource_tenant_id: str) -> "AuthorizationContext":
        if self.tenant_id != resource_tenant_id:
            raise TenantIsolationError(
                f"Actor in tenant {self.tenant_id!r} cannot access resource in tenant "
                f"{resource_tenant_id!r}",
                details={
                    "actor_tenant": self.tenant_id,
                    "resource_tenant": resource_tenant_id,
                },
            )
        return self

    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "roles": sorted(self.roles),
            "tenant_id": self.tenant_id,
            "scopes": sorted(self.scopes),
            "delegation_id": self.delegation_id,
            "on_behalf_of": self.on_behalf_of,
        }


class AuthorizationResolver:
    """Resolves an authorization context and validates delegated authority.

    This is the fail-safe gate. When the resolver itself is unavailable the
    caller must deny rather than proceed (LAW-ARCH-07), which is modelled by
    ``FailingAuthorizationResolver`` and asserted in the security tests.
    """

    def resolve(self, token: str) -> AuthorizationContext:
        raise NotImplementedError

    def validate_delegation(
        self, delegation_id: str, action: str, limits: dict
    ) -> None:
        raise NotImplementedError


class FailingAuthorizationResolver(AuthorizationResolver):
    """Models an unavailable authorization dependency: every call fails closed."""

    def resolve(self, token: str) -> AuthorizationContext:
        raise AuthorizationError(
            "Authorization service unavailable; refusing to proceed (fail-safe). "
            "Authorization is never bypassed because a dependency is down."
        )

    def validate_delegation(self, delegation_id: str, action: str, limits: dict) -> None:
        raise AuthorizationError(
            "Authorization service unavailable; delegated authority cannot be validated."
        )


class Delegation:
    """Consent/delegation grant (REQ-IAM-004)."""

    def __init__(
        self,
        delegation_id: str,
        principal: str,
        delegate: str,
        scopes: Iterable[str],
        validity_start: str,
        validity_end: str,
        transaction_limits: Optional[dict] = None,
        revokable: bool = True,
    ) -> None:
        self.delegation_id = delegation_id
        self.principal = principal
        self.delegate = delegate
        self.scopes = frozenset(scopes)
        self.validity_start = validity_start
        self.validity_end = validity_end
        self.transaction_limits = transaction_limits or {}
        self.revoked = False
        self.revokable = revokable

    def active_at(self, moment: str) -> bool:
        if self.revoked:
            return False
        return self.validity_start <= moment <= self.validity_end

    def covers(self, scope: str) -> bool:
        return scope in self.scopes

    def within_limits(self, dimension: str, amount) -> bool:
        limit = self.transaction_limits.get(dimension)
        if limit is None:
            return True
        return amount <= limit

    def revoke(self, at: str) -> None:
        if not self.revokable:
            raise AuthorizationError(f"Delegation {self.delegation_id} is not revokable")
        self.revoked = True
        self.revoked_at = at


# ---------------------------------------------------------------------------
# Provenance & audit (GOV-08, REQ-SEC-004)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceRecord:
    """Who, under what authority, changed what, when, why, from what source."""

    record_id: str
    actor: str
    authority: str
    action: str
    target_type: str
    target_id: str
    reason: str
    source: str
    recorded_at: str
    version_before: Optional[int] = None
    version_after: Optional[int] = None
    correlation_id: Optional[str] = None
    signature: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "actor": self.actor,
            "authority": self.authority,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "reason": self.reason,
            "source": self.source,
            "recorded_at": self.recorded_at,
            "version_before": self.version_before,
            "version_after": self.version_after,
            "correlation_id": self.correlation_id,
            "signature": self.signature,
        }


class AuditChain:
    """Append-only, tamper-evident audit log (REQ-SEC-004).

    Each entry chains to the previous entry's digest, so retroactive edits are
    detectable. Entries are never deleted (LAW-ARCH-04 extends to audit).
    """

    def __init__(self, secret: bytes = b"ucos-marketplace-audit-v1") -> None:
        self._entries: list[dict] = []
        self._previous_digest = "GENESIS"
        self._secret = secret
        self._lock = threading.RLock()

    def record(
        self,
        *,
        actor: str,
        authority: str,
        action: str,
        target_type: str,
        target_id: str,
        reason: str,
        source: str,
        recorded_at: str,
        correlation_id: Optional[str] = None,
        version_before: Optional[int] = None,
        version_after: Optional[int] = None,
    ) -> dict:
        entry = {
            "actor": actor,
            "authority": authority,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "reason": reason,
            "source": source,
            "recorded_at": recorded_at,
            "correlation_id": correlation_id,
            "version_before": version_before,
            "version_after": version_after,
            "previous_digest": self._previous_digest,
        }
        entry["digest"] = self._digest(entry)
        with self._lock:
            self._entries.append(entry)
            self._previous_digest = entry["digest"]
        return entry

    def _digest(self, entry: dict) -> str:
        payload = {
            k: v for k, v in entry.items() if k not in ("digest", "previous_digest")
        }
        serialized = json.dumps(payload, sort_keys=True, default=str).encode()
        return hmac.new(self._secret, serialized, hashlib.sha256).hexdigest()

    def verify(self) -> list[int]:
        """Return indices of entries whose chain is broken (empty == intact)."""
        broken: list[int] = []
        previous = "GENESIS"
        for index, entry in enumerate(self._entries):
            recomputed = self._digest({k: v for k, v in entry.items() if k != "digest"})
            if entry["previous_digest"] != previous or entry["digest"] != recomputed:
                broken.append(index)
            previous = entry["digest"]
        return broken

    def entries(self) -> list[dict]:
        return list(self._entries)

    def for_target(self, target_id: str) -> list[dict]:
        return [e for e in self._entries if e["target_id"] == target_id]

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Evidence store (GOV-11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    requirement_id: str
    kind: str  # design | build | test | operation | decision
    location: str
    description: str
    produced_at: str
    verifier: str
    pass_criteria: str
    result: str  # pass | fail | inconclusive


class EvidenceStore:
    def __init__(self) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._by_requirement: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def record(self, evidence: EvidenceRecord) -> EvidenceRecord:
        with self._lock:
            self._records[evidence.evidence_id] = evidence
            self._by_requirement.setdefault(evidence.requirement_id, []).append(
                evidence.evidence_id
            )
        return evidence

    def for_requirement(self, requirement_id: str) -> list[EvidenceRecord]:
        return [self._records[i] for i in self._by_requirement.get(requirement_id, [])]

    def all(self) -> list[EvidenceRecord]:
        return list(self._records.values())
