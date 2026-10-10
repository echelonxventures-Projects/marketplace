"""Canonical identifier generation and mapping.

Enforces the contract requirement: "Define canonical identifier generation and
mapping; forbid accidental identity through mutable display fields."

Identifiers are:
  * deterministic in format and unique within their scope;
  * typed by prefix so a Product ID can never be confused with an Order ID
    (LAW-ARCH-02);
  * mapped from external identifiers (GTIN, seller SKU, provider IDs) through
    a governed mapping store, so external IDs are evidence, not identity.
"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

# Each canonical concept owns a stable identifier prefix. Adding a prefix is a
# governed registry change; prefixes are never reused (GOV-12).
IDENTIFIER_PREFIXES: dict[str, str] = {
    "party": "PRT",
    "identity": "IDT",
    "organization": "ORG",
    "product": "PRD",
    "variant": "VAR",
    "sku": "SKU",
    "listing": "LST",
    "offer": "OFR",
    "pricebook": "PBK",
    "promotion": "PRM",
    "coupon": "CPN",
    "inventory": "INV",
    "reservation": "RSV",
    "seller": "SLR",
    "cart": "CRT",
    "checkout": "CHK",
    "order": "ORD",
    "order_line": "ORL",
    "payment": "PAY",
    "ledger": "LDG",
    "settlement": "STL",
    "fulfillment": "FUL",
    "shipment": "SHP",
    "return": "RTN",
    "claim": "CLM",
    "dispute": "DSP",
    "policy": "POL",
    "workflow": "WFL",
    "event": "EVT",
    "evidence": "EVD",
    "model": "MDL",
    "agent": "AGT",
    "risk": "RSK",
    "tax": "TAX",
    "refund": "RFD",
    "payout": "POU",
    "case": "CAS",
}

_ID_PATTERN = re.compile(r"^[A-Z]{3}-[A-Z0-9]{1,4}-[0-9A-HJKMNP-TV-Z]{26}$")


def _ulidish() -> str:
    """Crockford-base32 time-sortable identifier (uuid6-ish, stdlib only)."""
    u = uuid.uuid6().int
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    chars = []
    for _ in range(26):
        chars.append(alphabet[u & 31])
        u >>= 5
    return "".join(reversed(chars))


class IdentifierError(ValueError):
    pass


def generate(concept: str, scope_code: Optional[str] = None) -> str:
    """Generate a new canonical identifier for ``concept``."""
    prefix = IDENTIFIER_PREFIXES.get(concept)
    if prefix is None:
        raise IdentifierError(
            f"No canonical identifier prefix for concept {concept!r}; "
            "register one in IDENTIFIER_PREFIXES"
        )
    scope = (scope_code or "0").upper()[:4]
    return f"{prefix}-{scope}-{_ulidish()}"


def validate(value: str, concept: Optional[str] = None) -> str:
    """Validate an identifier's shape and (optionally) its concept prefix."""
    if not isinstance(value, str) or not _ID_PATTERN.match(value):
        raise IdentifierError(f"Malformed canonical identifier: {value!r}")
    if concept is not None:
        expected = IDENTIFIER_PREFIXES[concept]
        if not value.startswith(expected + "-"):
            raise IdentifierError(
                f"Identifier {value!r} does not belong to concept {concept!r} "
                f"(expected prefix {expected})"
            )
    return value


def concept_of(value: str) -> str:
    """Reverse-map an identifier's prefix to its canonical concept."""
    prefix = value.split("-", 1)[0]
    for concept, pfx in IDENTIFIER_PREFIXES.items():
        if pfx == prefix:
            return concept
    raise IdentifierError(f"Unknown identifier prefix {prefix!r} in {value!r}")


# ---------------------------------------------------------------------------
# External identifier mapping (REQ-CAT-002, source-of-truth rules)
# ---------------------------------------------------------------------------


@dataclass
class ExternalIdentifierMapping:
    mapping_id: str
    external_system: str
    external_id: str
    canonical_concept: str
    canonical_id: str
    scope: str  # ownership scope of the external id, e.g. seller id for SKUs
    provenance: str
    created_at: str


class IdentifierMappingStore:
    """Governed mapping from external identifiers to canonical identifiers.

    External identifiers (GTIN, seller SKU, provider payment id) are evidence
    about a canonical concept, never its identity. Two different external
    systems may reuse the same external id; the (system, id, scope) triple is
    the key.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], ExternalIdentifierMapping] = {}
        self._by_canonical: dict[str, list[ExternalIdentifierMapping]] = {}
        self._lock = threading.RLock()

    def map(
        self,
        external_system: str,
        external_id: str,
        concept: str,
        canonical_id: str,
        scope: str,
        provenance: str,
        now: str,
    ) -> ExternalIdentifierMapping:
        key = (external_system.upper(), external_id, scope)
        with self._lock:
            existing = self._by_key.get(key)
            if existing is not None:
                if existing.canonical_id != canonical_id:
                    raise IdentifierError(
                        f"External id {external_id!r} from {external_system!r} scope "
                        f"{scope!r} is already mapped to {existing.canonical_id!r}; "
                        "conflicting mappings require adjudication, not overwrite"
                    )
                return existing
            mapping = ExternalIdentifierMapping(
                mapping_id=generate("evidence"),
                external_system=external_system.upper(),
                external_id=external_id,
                canonical_concept=concept,
                canonical_id=validate(canonical_id, concept),
                scope=scope,
                provenance=provenance,
                created_at=now,
            )
            self._by_key[key] = mapping
            self._by_canonical.setdefault(canonical_id, []).append(mapping)
            return mapping

    def resolve(self, external_system: str, external_id: str, scope: str) -> Optional[str]:
        mapping = self._by_key.get((external_system.upper(), external_id, scope))
        return mapping.canonical_id if mapping else None

    def mappings_for(self, canonical_id: str) -> list[ExternalIdentifierMapping]:
        return list(self._by_canonical.get(canonical_id, []))


def content_hash(payload: bytes | str | dict) -> str:
    """A stable SHA-256 digest used for evidence integrity and dedup keys."""
    import json

    if isinstance(payload, dict):
        payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class VersionedRef:
    """A reference to a canonical artifact at an explicit version."""

    id: str
    version: int = 1

    def to_dict(self) -> dict:
        return {"id": self.id, "version": self.version}
