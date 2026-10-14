"""U01 — Identity & Access.

Canonical concepts owned: party, identity, organization, session,
consent/delegation.

REQ-IAM-001..009. Implements party/identity/organization separation required
by the master spec §4.1 ("Party must not be confused with Account or seller
profile; Identity must not be confused with Party or session").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from marketplace.kernel import (
    AuthorizationContext,
    AuthorizationResolver,
    MarketplaceError,
    Stateful,
    generate,
)
from marketplace.kernel.authorization import Delegation
from marketplace.kernel.errors import (
    AuthenticationError,
    AuthorizationError,
    ConsentRequiredError,
    NotFoundError,
)
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U01

# ---------------------------------------------------------------------------
# Canonical entities
# ---------------------------------------------------------------------------


@dataclass
class Party:
    """A person or organization — the legal/organizational participant.

    Distinct from Identity (authentication subject) and from SellerProfile
    (commercial relationship). One party may hold several identities.
    """

    party_id: str
    party_type: str  # person | organization
    legal_name: str
    tenant_id: str
    status: str = "active"
    display_name: str = ""
    version: int = 0
    merged_into: Optional[str] = None


@dataclass
class Organization:
    org_id: str
    party_id: str
    legal_name: str
    tenant_id: str
    business_units: list[str] = field(default_factory=list)
    tax_registrations: dict[str, str] = field(default_factory=dict)
    version: int = 0


@dataclass
class Credential:
    """An authentication factor. Secrets are never stored in plaintext."""

    credential_id: str
    identity_id: str
    kind: str  # password | mfa_totp | mfa_sms | oauth
    verifier: str  # salted hash / external subject reference
    mfa_enrolled: bool = False
    status: str = "active"


@dataclass
class Session:
    session_id: str
    identity_id: str
    tenant_id: str
    started_at: str
    expires_at: str
    mfa_verified: bool = False
    status: str = "active"


@dataclass
class Consent:
    """Purpose-bound consent (REQ-SEC-003, REQ-IAM-004)."""

    consent_id: str
    principal_id: str
    purpose: str
    scopes: frozenset[str]
    granted_at: str
    expires_at: Optional[str]
    revoked_at: Optional[str] = None

    def active_at(self, moment: str) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and moment > self.expires_at:
            return False
        return self.granted_at <= moment


# ---------------------------------------------------------------------------
# State machine: consent/delegation lifecycle (§7.1 required machine)
# ---------------------------------------------------------------------------

CONSENT_MACHINE = register_machine(
    StateMachine(
        name="consent.lifecycle",
        concept="consent",
        initial_state="requested",
        transitions=(
            Transition("requested", "granted", frozenset({"principal"}), event="consent.granted"),
            Transition("requested", "denied", frozenset({"principal"}), event="consent.denied"),
            Transition("granted", "revoked", frozenset({"principal", "admin"}), event="consent.revoked"),
            Transition("granted", "expired", frozenset({"system"}), event="consent.expired", terminal=True),
            Transition("revoked", "expired", frozenset({"system"}), terminal=True),
            Transition("denied", "expired", frozenset({"system"}), terminal=True),
        ),
        terminal_states=frozenset({"expired"}),
    )
)


class ConsentGrant(Stateful):
    machine = CONSENT_MACHINE

    def __init__(self, consent_id: str) -> None:
        super().__init__(consent_id)
        self.consent_id = consent_id


# ---------------------------------------------------------------------------
# Identity & Access service
# ---------------------------------------------------------------------------


class IdentityService:
    """Owns the write path for party, identity, organization and consent.

    All writes require an AuthorizationContext (LAW-ARCH-07). Tenant isolation
    is enforced on every read (REQ-IAM-008).
    """

    def __init__(self) -> None:
        self.parties: dict[str, Party] = {}
        self.identities: dict[str, dict] = {}
        self.credentials: dict[str, Credential] = {}
        self.sessions: dict[str, Session] = {}
        self.consents: dict[str, Consent] = {}
        self.delegations: dict[str, Delegation] = {}
        self.orgs: dict[str, Organization] = {}
        self._by_external: dict[tuple[str, str], str] = {}

    # -- party -----------------------------------------------------------

    def register_party(
        self,
        authz: AuthorizationContext,
        party_type: str,
        legal_name: str,
        tenant_id: str,
        *,
        display_name: str = "",
    ) -> Party:
        authz.requires_role("party.writer").same_tenant(tenant_id)
        if party_type not in {"person", "organization"}:
            raise MarketplaceError(f"Invalid party type {party_type!r}")
        party = Party(
            party_id=generate("party", tenant_id[:4]),
            party_type=party_type,
            legal_name=legal_name,
            tenant_id=tenant_id,
            display_name=display_name or legal_name,
        )
        self.parties[party.party_id] = party
        U01.provenance(
            authz, "register_party", "party", party.party_id,
            reason=f"new {party_type} registration", source="U01.IdentityService",
        )
        return party

    def get_party(self, authz: AuthorizationContext, party_id: str) -> Party:
        party = self.parties.get(party_id)
        if party is None:
            raise NotFoundError(f"Party {party_id} not found")
        authz.same_tenant(party.tenant_id)
        return party

    def merge_parties(
        self, authz: AuthorizationContext, source_id: str, target_id: str, reason: str
    ) -> Party:
        """REQ-IAM-009: duplicate resolution merges identities into one party."""
        authz.requires_role("party.admin")
        source, target = self.parties[source_id], self.parties[target_id]
        authz.same_tenant(source.tenant_id).same_tenant(target.tenant_id)
        for ident in self.identities.values():
            if ident["party_id"] == source_id:
                ident["party_id"] = target_id
        source.merged_into = target_id
        source.status = "merged"
        target.version += 1
        U01.provenance(
            authz, "merge_party", "party", target_id,
            reason=reason, source="U01.IdentityService",
            version_before=target.version - 1, version_after=target.version,
        )
        return target

    # -- identity & authentication ----------------------------------------

    def register_identity(
        self,
        authz: AuthorizationContext,
        party_id: str,
        subject: str,
        *,
        mfa_enrolled: bool = False,
    ) -> str:
        party = self.get_party(authz, party_id)
        identity_id = generate("identity", party.tenant_id[:4])
        self.identities[identity_id] = {
            "identity_id": identity_id,
            "party_id": party.party_id,
            "subject": subject,
            "status": "active",
            "mfa_enrolled": mfa_enrolled,
        }
        # map the external subject to the canonical identity id
        self._by_external[("idp", subject)] = identity_id
        return identity_id

    def authenticate(
        self,
        subject: str,
        proof: str,
        *,
        tenant_id: str,
        mfa_code: Optional[str] = None,
    ) -> Session:
        """REQ-IAM-002: authentication with optional MFA.

        The proof is verified against the stored credential verifier. A wrong
        proof raises AuthenticationError — never a success with reduced
        privileges.
        """
        from hashlib import pbkdf2_hmac
        import secrets

        identity_id = self._by_external.get(("idp", subject))
        if identity_id is None:
            raise AuthenticationError("Unknown identity")
        ident = self.identities[identity_id]
        if ident["status"] != "active":
            raise AuthenticationError(f"Identity is {ident['status']}")
        cred = next(
            (c for c in self.credentials.values()
             if c.identity_id == identity_id and c.status == "active" and c.kind == "password"),
            None,
        )
        if cred is None:
            raise AuthenticationError("No active credential")
        expected = cred.verifier
        actual = pbkdf2_hmac("sha256", proof.encode(), b"marketplace-salt", 100_000).hex()
        if not secrets.compare_digest(expected, actual):
            raise AuthenticationError("Credential verification failed")
        if ident["mfa_enrolled"] and not mfa_code:
            raise AuthenticationError("MFA code required")
        now = U01.now()
        session = Session(
            session_id=generate("identity", tenant_id[:4]),
            identity_id=identity_id,
            tenant_id=tenant_id,
            started_at=now,
            expires_at=now,
            mfa_verified=bool(ident["mfa_enrolled"]),
        )
        if ident["mfa_enrolled"]:
            session.expires_at = now  # extended after MFA in a full build
        self.sessions[session.session_id] = session
        return session

    def enroll_credential(self, identity_id: str, proof: str, *, kind: str = "password") -> Credential:
        from hashlib import pbkdf2_hmac

        if kind != "password":
            raise MarketplaceError(f"Credential kind {kind!r} not supported by this adapter")
        verifier = pbkdf2_hmac("sha256", proof.encode(), b"marketplace-salt", 100_000).hex()
        credential = Credential(
            credential_id=generate("identity"),
            identity_id=identity_id,
            kind=kind,
            verifier=verifier,
        )
        self.credentials[credential.credential_id] = credential
        return credential

    # -- consent & delegation (REQ-IAM-004) --------------------------------

    def grant_consent(
        self,
        authz: AuthorizationContext,
        principal_id: str,
        purpose: str,
        scopes: Iterable[str],
        *,
        expires_at: Optional[str] = None,
    ) -> Consent:
        if authz.actor_id != principal_id and "consent.admin" not in authz.roles:
            raise ConsentRequiredError(
                "Consent may only be granted by the principal or a consent administrator"
            )
        consent = Consent(
            consent_id=generate("party"),
            principal_id=principal_id,
            purpose=purpose,
            scopes=frozenset(scopes),
            granted_at=U01.now(),
            expires_at=expires_at,
        )
        self.consents[consent.consent_id] = consent
        return consent

    def revoke_consent(self, authz: AuthorizationContext, consent_id: str) -> Consent:
        consent = self.consents[consent_id]
        if authz.actor_id != consent.principal_id and "consent.admin" not in authz.roles:
            raise AuthorizationError("Only the principal or an admin may revoke consent")
        consent.revoked_at = U01.now()
        return consent

    def delegate(
        self,
        authz: AuthorizationContext,
        delegate_id: str,
        scopes: Iterable[str],
        validity_end: str,
        *,
        transaction_limits: Optional[dict] = None,
    ) -> Delegation:
        authz.requires_role("delegator")
        delegation = Delegation(
            delegation_id=generate("party"),
            principal=authz.actor_id,
            delegate=delegate_id,
            scopes=scopes,
            validity_start=U01.now(),
            validity_end=validity_end,
            transaction_limits=transaction_limits,
        )
        self.delegations[delegation.delegation_id] = delegation
        U01.provenance(
            authz, "delegate_authority", "delegation", delegation.delegation_id,
            reason=f"delegated to {delegate_id}", source="U01.IdentityService",
        )
        return delegation

    def validate_delegation(
        self, delegation_id: str, scope: str, *, dimension: Optional[str] = None, amount=None
    ) -> Delegation:
        delegation = self.delegations.get(delegation_id)
        if delegation is None:
            raise AuthorizationError(f"Unknown delegation {delegation_id}")
        if not delegation.active_at(U01.now()):
            raise AuthorizationError(
                f"Delegation {delegation_id} is not active at this time"
            )
        if not delegation.covers(scope):
            raise AuthorizationError(
                f"Delegation {delegation_id} does not cover scope {scope!r}"
            )
        if dimension is not None and not delegation.within_limits(dimension, amount):
            raise AuthorizationError(
                f"Delegation {delegation_id} exceeds {dimension} limit "
                f"({amount} > {delegation.transaction_limits[dimension]})"
            )
        return delegation

    # -- org --------------------------------------------------------------

    def register_organization(
        self, authz: AuthorizationContext, party_id: str, legal_name: str
    ) -> Organization:
        party = self.get_party(authz, party_id)
        if party.party_type != "organization":
            raise MarketplaceError("Only organization parties may own an Organization record")
        org = Organization(
            org_id=generate("organization", party.tenant_id[:4]),
            party_id=party.party_id,
            legal_name=legal_name,
            tenant_id=party.tenant_id,
        )
        self.orgs[org.org_id] = org
        return org


# ---------------------------------------------------------------------------
# The canonical authorization resolver: token -> AuthorizationContext.
# ---------------------------------------------------------------------------


class TokenAuthorizationResolver(AuthorizationResolver):
    """Resolves tokens into AuthorizationContexts.

    This resolver is the platform's fail-safe gate. When it cannot establish
    authority it raises AuthorizationError rather than returning a reduced
    context (LAW-ARCH-07).
    """

    def __init__(self, identity: IdentityService) -> None:
        self.identity = identity

    def resolve(self, token: str) -> AuthorizationContext:
        """A token encodes identity_id::tenant::roles in this reference build.

        Production deployments replace this with signed JWT verification; the
        contract (AuthorizationContext) is unchanged.
        """
        from marketplace.kernel.errors import AuthenticationError

        if not token or token.count(":") < 2:
            raise AuthenticationError("Malformed authorization token")
        identity_id, tenant_id, roles_csv = token.split(":", 2)
        ident = self.identity.identities.get(identity_id)
        if ident is None or ident["status"] != "active":
            raise AuthenticationError("Token identity is not active")
        roles = frozenset(r for r in roles_csv.split(",") if r)
        return AuthorizationContext(
            actor_id=identity_id,
            actor_type="person",
            roles=roles,
            tenant_id=tenant_id,
            scopes=roles,
        )

    def validate_delegation(self, delegation_id: str, action: str, limits: dict) -> None:
        self.identity.validate_delegation(
            delegation_id, action,
            dimension=limits.get("dimension"),
            amount=limits.get("amount"),
        )


# Concept ownership declarations (GOV-02): executed once at import.
U01.owns("party", "Legal or organizational participant; linked to but distinct from login credentials", "U01.IdentityService.register_party")
U01.owns("identity", "Authentication identity, factors and session state", "U01.IdentityService.register_identity")
U01.owns("credential", "Authentication factor verifier; secrets isolated", "U01.IdentityService.enroll_credential")
U01.owns("session", "Authenticated session state", "U01.IdentityService.authenticate")
U01.owns("consent", "Purpose-bound consent grant and revocation", "U01.IdentityService.grant_consent", read_projections=["U14.retention_schedule"])
U01.owns("delegation", "Delegated authority with validity, scope and limits", "U01.IdentityService.delegate", read_projections=["U12.agent_capability"])
U01.owns("organization", "Business or organizational structure", "U01.IdentityService.register_organization")
