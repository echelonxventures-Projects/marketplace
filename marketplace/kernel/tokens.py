"""HMAC-signed bearer tokens (REQ-IAM-002, REQ-SEC-002).

The reference build previously carried ``identity_id:tenant:roles`` as a bare,
unsigned bearer token. That format is forgeable by anyone who learns an
identity id: the roles claim was trusted outright. This module signs the token
payload with HMAC-SHA256 and embeds an expiry, so:

* a token cannot be minted without the signing secret;
* the roles claim cannot be tampered with (the signature covers it);
* an expired token is rejected before its claims are trusted.

The wire format deliberately resembles a compact JWT so operators recognise it,
but it depends on nothing outside the standard library, preserving the
platform's zero-runtime-dependency property.

Token layout:  ``base64url(payload).base64url(signature)``  where ``payload``
is a JSON object with ``sub`` (identity id), ``tenant``, ``roles``, ``scopes``,
``iat`` (issued-at unix seconds) and ``exp`` (expiry unix seconds).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from .errors import AuthenticationError

# A token is valid for one hour by default. Short-lived tokens limit the blast
# radius of a stolen bearer token (REQ-SEC-002).
DEFAULT_TTL_SECONDS = 3600

_HEADER = {"alg": "HS256", "typ": "ucos-marketplace"}


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True)
class TokenClaims:
    """The verified assertions carried by a signed token."""

    subject: str
    tenant_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    issued_at: int
    expires_at: int

    def expired(self, now: int) -> bool:
        return now >= self.expires_at


class TokenSigner:
    """Issues and verifies HMAC-SHA256 signed bearer tokens.

    The secret is held in memory only. A process restart therefore invalidates
    every outstanding token, which is the safe failure mode: a leaked token
    stops working rather than working forever. Deployments that need tokens to
    survive restarts (or be valid across several processes) supply the same
    secret to every process.
    """

    def __init__(
        self,
        secret: Optional[bytes] = None,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Optional[Callable[[], int]] = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("token ttl_seconds must be positive")
        self._secret = secret if secret is not None else secrets.token_bytes(32)
        if isinstance(self._secret, str):
            self._secret = self._secret.encode("utf-8")
        self._ttl = ttl_seconds
        self._now = now or _unix_now
        self._lock = threading.RLock()

    # -- issuance ----------------------------------------------------------

    def issue(
        self,
        subject: str,
        tenant_id: str,
        roles: Iterable[str],
        *,
        scopes: Optional[Iterable[str]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """Sign a token for ``subject`` carrying exactly these roles.

        Roles are chosen by the issuer (the platform), never by the caller, so
        a client cannot self-assign authority (LAW-ARCH-07).
        """

        roles_sorted = sorted(set(roles))
        scopes_sorted = sorted(set(scopes)) if scopes is not None else roles_sorted
        issued_at = self._now()
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        payload = {
            "sub": subject,
            "tenant": tenant_id,
            "roles": roles_sorted,
            "scopes": scopes_sorted,
            "iat": issued_at,
            "exp": issued_at + ttl,
        }
        return self._encode(payload)

    def _encode(self, payload: dict[str, Any]) -> str:
        body = _b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = self._sign(body)
        return f"{body}.{signature}"

    def _sign(self, body: str) -> str:
        with self._lock:
            digest = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
        return _b64encode(digest)

    # -- verification -------------------------------------------------------

    def verify(self, token: str) -> TokenClaims:
        """Verify a token's signature and expiry.

        Raises ``AuthenticationError`` for any malformed, tampered or expired
        token. This is the fail-safe gate: nothing about an unverifiable token
        is trusted (LAW-ARCH-07).
        """

        if not token or not isinstance(token, str):
            raise AuthenticationError("Missing bearer token")
        parts = token.split(".")
        if len(parts) != 2:
            raise AuthenticationError("Malformed authorization token")
        body, signature = parts
        expected = self._sign(body)
        # Constant-time comparison so the signature check is not a timing
        # oracle (REQ-SEC-002).
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("Token signature verification failed")
        try:
            payload = json.loads(_b64decode(body))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthenticationError("Token payload is not decodable") from exc
        return self._claims_from(payload)

    def _claims_from(self, payload: dict[str, Any]) -> TokenClaims:
        try:
            issued_at = int(payload["iat"])
            expires_at = int(payload["exp"])
            subject = str(payload["sub"])
            tenant_id = str(payload["tenant"])
            roles = frozenset(str(r) for r in payload.get("roles", []))
            scopes = frozenset(str(s) for s in payload.get("scopes", roles))
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("Token payload is missing required claims") from exc
        if not subject or not tenant_id:
            raise AuthenticationError("Token payload is missing required claims")
        now = self._now()
        if now < issued_at:
            raise AuthenticationError("Token was issued in the future")
        if now >= expires_at:
            raise AuthenticationError("Token has expired")
        return TokenClaims(
            subject=subject,
            tenant_id=tenant_id,
            roles=roles,
            scopes=scopes,
            issued_at=issued_at,
            expires_at=expires_at,
        )


def _unix_now() -> int:
    import time

    return int(time.time())
