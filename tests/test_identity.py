"""U01 Identity & Access: party/identity separation, auth, consent, isolation."""

import pytest

from marketplace.kernel import AuthorizationContext
from marketplace.kernel.errors import (
    AuthenticationError,
    AuthorizationError,
    ConsentRequiredError,
    MarketplaceError,
    NotFoundError,
)
from marketplace.universes.u01_identity.service import IdentityService, TokenAuthorizationResolver

pytestmark = pytest.mark.security


@pytest.fixture
def identity():
    return IdentityService()


class TestPartyManagement:
    def test_register_party(self, identity, authz):
        party = identity.register_party(authz, "person", "Ada Lovelace", "tenant-A")
        assert party.party_type == "person"
        assert party.tenant_id == "tenant-A"
        assert party.status == "active"

    def test_invalid_party_type(self, identity, authz):
        with pytest.raises(MarketplaceError):
            identity.register_party(authz, "robot", "X", "tenant-A")

    def test_cross_tenant_write_denied(self, identity, other_tenant_authz):
        with pytest.raises(Exception):
            identity.register_party(other_tenant_authz, "person", "X", "tenant-A")

    def test_get_party_enforces_tenant(self, identity, authz, other_tenant_authz):
        party = identity.register_party(authz, "person", "Ada", "tenant-A")
        assert identity.get_party(authz, party.party_id).party_id == party.party_id
        with pytest.raises(Exception):
            identity.get_party(other_tenant_authz, party.party_id)

    def test_missing_party_not_found(self, identity, authz):
        with pytest.raises(NotFoundError):
            identity.get_party(authz, "nope")

    def test_merge_parties_reassigns_identities(self, identity, authz):
        source = identity.register_party(authz, "person", "Dup One", "tenant-A")
        target = identity.register_party(authz, "person", "Keep Me", "tenant-A")
        ident = identity.register_identity(authz, source.party_id, "subject-1")
        merged = identity.merge_parties(authz, source.party_id, target.party_id, "dedupe")
        assert merged.party_id == target.party_id
        assert identity.identities[ident]["party_id"] == target.party_id
        assert source.status == "merged"
        assert source.merged_into == target.party_id
        assert merged.version == 1

    def test_party_and_identity_are_distinct(self, identity, authz):
        """REQ-IAM-001: a party is not an identity and vice versa."""
        party = identity.register_party(authz, "person", "Ada", "tenant-A")
        ident = identity.register_identity(authz, party.party_id, "subject-1")
        assert ident != party.party_id
        assert identity.identities[ident]["party_id"] == party.party_id
        assert identity.identities[ident]["subject"] == "subject-1"


class TestAuthentication:
    def make_credential(self, identity, authz):
        party = identity.register_party(authz, "person", "Ada", "tenant-A")
        ident = identity.register_identity(authz, party.party_id, "subject-1")
        identity.enroll_credential(ident, "s3cret")
        return ident

    def test_authenticate_success(self, identity, authz):
        ident = self.make_credential(identity, authz)
        session = identity.authenticate("subject-1", "s3cret", tenant_id="tenant-A")
        assert session.identity_id == ident
        assert session.tenant_id == "tenant-A"

    def test_authenticate_wrong_password(self, identity, authz):
        self.make_credential(identity, authz)
        with pytest.raises(AuthenticationError):
            identity.authenticate("subject-1", "wrong", tenant_id="tenant-A")

    def test_authenticate_unknown_subject(self, identity):
        with pytest.raises(AuthenticationError):
            identity.authenticate("nobody", "x", tenant_id="tenant-A")

    def test_mfa_required_when_enrolled(self, identity, authz):
        party = identity.register_party(authz, "person", "Ada", "tenant-A")
        ident = identity.register_identity(authz, party.party_id, "subject-1", mfa_enrolled=True)
        identity.enroll_credential(ident, "s3cret")
        with pytest.raises(AuthenticationError):
            identity.authenticate("subject-1", "s3cret", tenant_id="tenant-A")
        session = identity.authenticate("subject-1", "s3cret", tenant_id="tenant-A", mfa_code="123")
        assert session.mfa_verified

    def test_plaintext_secret_is_never_stored(self, identity, authz):
        ident = self.make_credential(identity, authz)
        cred = next(iter(identity.credentials.values()))
        assert cred.verifier != "s3cret"
        assert cred.identity_id == ident


class TestConsent:
    def test_grant_consent_by_principal(self, identity, authz):
        consent = identity.grant_consent(authz, authz.actor_id, "marketing", ["email"])
        assert consent.scopes == frozenset({"email"})
        assert consent.active_at(consent.granted_at)

    def test_consent_by_third_party_denied(self, identity, authz):
        """Only the principal (or a consent admin) may grant their own consent."""
        scoped = AuthorizationContext(
            actor_id="actor-1", actor_type="person", roles=frozenset({"party.writer"}),
            tenant_id="tenant-A",
        )
        with pytest.raises(ConsentRequiredError):
            identity.grant_consent(scoped, "someone-else", "marketing", ["email"])

    def test_consent_admin_can_grant_for_other(self, identity, authz):
        consent = identity.grant_consent(authz, "user-x", "marketing", ["email"])
        assert consent.principal_id == "user-x"

    def test_revoke_consent(self, identity, authz):
        consent = identity.grant_consent(authz, authz.actor_id, "marketing", ["email"])
        revoked = identity.revoke_consent(authz, consent.consent_id)
        assert revoked.revoked_at is not None
        assert not revoked.active_at(revoked.revoked_at)

    def test_expired_consent_inactive(self, identity, authz):
        consent = identity.grant_consent(
            authz, authz.actor_id, "marketing", ["email"], expires_at="2026-01-01T00:00:00+00:00"
        )
        assert not consent.active_at("2026-06-01T00:00:00+00:00")


class TestDelegation:
    def test_delegate_and_validate(self, identity, authz):
        d = identity.delegate(authz, "assistant", ["approve.refund"], "2026-12-31T00:00:00+00:00")
        assert identity.validate_delegation(d.delegation_id, "approve.refund") == d

    def test_delegation_wrong_scope(self, identity, authz):
        d = identity.delegate(authz, "assistant", ["approve.refund"], "2026-12-31T00:00:00+00:00")
        with pytest.raises(AuthorizationError):
            identity.validate_delegation(d.delegation_id, "approve.payout")

    def test_delegation_unknown(self, identity):
        with pytest.raises(AuthorizationError):
            identity.validate_delegation("nope", "x")

    def test_delegation_limit(self, identity, authz):
        d = identity.delegate(
            authz, "assistant", ["approve.refund"], "2026-12-31T00:00:00+00:00",
            transaction_limits={"amount": 100},
        )
        assert identity.validate_delegation(d.delegation_id, "approve.refund", dimension="amount", amount=50)
        with pytest.raises(AuthorizationError):
            identity.validate_delegation(d.delegation_id, "approve.refund", dimension="amount", amount=500)


class TestTokenResolver:
    def test_resolve_valid_token(self, identity, authz):
        party = identity.register_party(authz, "person", "Ada", "tenant-A")
        ident = identity.register_identity(authz, party.party_id, "subject-1")
        resolver = TokenAuthorizationResolver(identity)
        ctx = resolver.resolve(f"{ident}:tenant-A:party.writer")
        assert ctx.actor_id == ident
        assert ctx.tenant_id == "tenant-A"
        assert "party.writer" in ctx.roles

    def test_malformed_token_denied(self, identity):
        resolver = TokenAuthorizationResolver(identity)
        with pytest.raises(AuthenticationError):
            resolver.resolve("garbage")

    def test_inactive_identity_token_denied(self, identity, authz):
        party = identity.register_party(authz, "person", "Ada", "tenant-A")
        ident = identity.register_identity(authz, party.party_id, "subject-1")
        identity.identities[ident]["status"] = "suspended"
        resolver = TokenAuthorizationResolver(identity)
        with pytest.raises(AuthenticationError):
            resolver.resolve(f"{ident}:tenant-A:party.writer")
