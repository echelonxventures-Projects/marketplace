"""U14 Governance & Compliance: policies, retention, certification and authorization."""

import pytest

from marketplace.kernel import AuthorizationContext
from marketplace.kernel.errors import NotFoundError, PolicyViolationError, ValidationError
from marketplace.universes.u11_u15.services import GovernanceService as U14GovernanceService

pytestmark = pytest.mark.unit


@pytest.fixture
def service():
    return U14GovernanceService()


@pytest.fixture
def policy_writer():
    return AuthorizationContext(
        actor_id="policy-admin",
        actor_type="person",
        roles=frozenset({"policy.writer"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def privacy_admin():
    return AuthorizationContext(
        actor_id="privacy-admin",
        actor_type="person",
        roles=frozenset({"privacy.admin"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def cert_recorder():
    return AuthorizationContext(
        actor_id="cert-recorder",
        actor_type="service",
        roles=frozenset({"certification.recorder"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def implementer():
    return AuthorizationContext(
        actor_id="release-manager",
        actor_type="person",
        roles=frozenset({"implementation.authorizer"}),
        tenant_id="tenant-A",
    )


class TestPolicies:
    def test_publish_and_evaluate_allow(self, service, policy_writer):
        service.publish_policy(policy_writer, "refund.window", "Refund within 30 days", "allow", "days_since_purchase <= 30")
        assert service.evaluate("refund.window", {"days_since_purchase": 10}) == "allow"

    def test_evaluate_no_match(self, service, policy_writer):
        service.publish_policy(policy_writer, "refund.window", "Refund within 30 days", "allow", "days_since_purchase <= 30")
        assert service.evaluate("refund.window", {"days_since_purchase": 90}) == "no_match"

    def test_deny_effect(self, service, policy_writer):
        service.publish_policy(policy_writer, "banned.seller", "Banned seller", "deny", "seller_status == 'banned'")
        assert service.evaluate("banned.seller", {"seller_status": "banned"}) == "deny"

    def test_require_approval_effect(self, service, policy_writer):
        service.publish_policy(policy_writer, "large.refund", "Large refund", "require_approval", "amount > 1000")
        assert service.evaluate("large.refund", {"amount": 5000}) == "require_approval"

    def test_versioning_supersedes_previous(self, service, policy_writer):
        first = service.publish_policy(policy_writer, "refund.window", "v1", "allow", "days_since_purchase <= 30")
        second = service.publish_policy(policy_writer, "refund.window", "v2", "allow", "days_since_purchase <= 60")
        assert first.version == 1 and first.status == "superseded"
        assert second.version == 2 and second.status == "active"
        assert service.active_policy("refund.window").version == 2

    def test_unknown_active_policy_raises(self, service):
        with pytest.raises(NotFoundError):
            service.active_policy("nope")

    def test_invalid_effect_rejected(self, service, policy_writer):
        with pytest.raises(ValidationError):
            service.publish_policy(policy_writer, "x", "d", "maybe", "True")

    def test_malformed_condition_is_reported(self, service, policy_writer):
        service.publish_policy(policy_writer, "bad", "d", "allow", "this is not python")
        with pytest.raises(ValidationError):
            service.evaluate("bad", {})

    def test_condition_has_no_builtins(self, service, policy_writer):
        service.publish_policy(policy_writer, "sneaky", "d", "allow", "open('/etc/passwd')")
        with pytest.raises(ValidationError):
            service.evaluate("sneaky", {})

    def test_writer_role_required(self, service, privacy_admin):
        with pytest.raises(Exception):
            service.publish_policy(privacy_admin, "x", "d", "allow", "True")


class TestRetention:
    def test_define_retention(self, service, privacy_admin):
        schedule = service.define_retention(privacy_admin, "order_history", "EU", 2555, "tax", "national law")
        assert schedule.retention_days == 2555
        assert schedule.jurisdiction == "EU"

    def test_negative_retention_rejected(self, service, privacy_admin):
        with pytest.raises(ValidationError):
            service.define_retention(privacy_admin, "x", "EU", -1, "p", "l")

    def test_privacy_admin_required(self, service, policy_writer):
        with pytest.raises(Exception):
            service.define_retention(policy_writer, "x", "EU", 30, "p", "l")


class TestCertification:
    def test_record_certification(self, service, cert_recorder):
        record = service.record_certification(
            cert_recorder, "payment-processing", ["REQ-1", "REQ-2"], ["EV-1"], "pass",
            valid_until="2027-01-01T00:00:00+00:00",
        )
        assert record.result == "pass"
        assert record.verifier == "cert-recorder"
        assert record.valid_until is not None

    def test_certification_requires_evidence(self, service, cert_recorder):
        with pytest.raises(ValidationError):
            service.record_certification(cert_recorder, "scope", ["REQ-1"], [], "pass")

    def test_invalid_result_rejected(self, service, cert_recorder):
        with pytest.raises(ValidationError):
            service.record_certification(cert_recorder, "scope", ["REQ-1"], ["EV-1"], "mostly")

    def test_recorder_role_required(self, service, policy_writer):
        with pytest.raises(Exception):
            service.record_certification(policy_writer, "scope", [], ["EV-1"], "pass")


class TestImplementationAuthorization:
    def test_authorize_implementation(self, service, implementer):
        authz = service.authorize_implementation(
            implementer, "TRANCHE-1", "plan-ref", security_review="passed", privacy_review="conditional"
        )
        assert authz["tranche_id"] == "TRANCHE-1"
        assert authz["authorized_by"] == "release-manager"

    def test_security_review_outcome_required(self, service, implementer):
        with pytest.raises(PolicyViolationError):
            service.authorize_implementation(
                implementer, "T", "p", security_review="skipped", privacy_review="passed"
            )

    def test_privacy_review_outcome_required(self, service, implementer):
        with pytest.raises(PolicyViolationError):
            service.authorize_implementation(
                implementer, "T", "p", security_review="passed", privacy_review="skipped"
            )

    def test_authorizer_role_required(self, service, policy_writer):
        with pytest.raises(Exception):
            service.authorize_implementation(
                policy_writer, "T", "p", security_review="passed", privacy_review="passed"
            )


class TestU14ConceptOwnership:
    def test_concepts_owned(self):
        from marketplace.kernel.registry import CONCEPTS

        owned = {c.concept for c in CONCEPTS.all() if c.owner_universe == "U14"}
        assert {"policy", "retention_schedule", "certification_record"} <= owned
