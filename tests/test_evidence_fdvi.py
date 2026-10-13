"""U12 FDVI: evidence assembly, provenance and freshness (Spec 030 Modules 01,
04; FR-009, FR-010, FR-011, AC-009, AC-045, AC-046).

Evidence is *referenced* never copied into the decision record as opaque fact.
Each item carries source authority, version, freshness and an access
classification.
"""

import pytest

from marketplace.kernel import AuthorizationContext
from marketplace.kernel.errors import (
    AuthorizationError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.universes.u12_intelligence.entities import DecisionEvidence
from marketplace.universes.u12_intelligence.evidence import EvidenceStore

pytestmark = pytest.mark.unit

NOW = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def store():
    return EvidenceStore(default_max_age_hours=24)


@pytest.fixture
def authz():
    return AuthorizationContext(
        actor_id="decision-service",
        actor_type="service",
        roles=frozenset({"decision.service"}),
        tenant_id="tenant-A",
        scopes=frozenset(
            {"evidence.read:sensitive", "evidence.read:financial", "evidence.read:personal"}
        ),
    )


@pytest.fixture
def plain_authz():
    return AuthorizationContext(
        actor_id="analyst",
        actor_type="person",
        roles=frozenset({"decision.requester"}),
        tenant_id="tenant-A",
    )


def _submit(store, authz, *, case_id="case-1", observation_time=NOW, **overrides):
    params = dict(
        case_id=case_id,
        evidence_type="metric",
        source_authority_ref="U05.PricingService",
        source_artifact_ref="price-feed",
        source_version=3,
        observation_time=observation_time,
        validity_interval=("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),
        payload={"unit": "USD", "value": "100"},
    )
    params.update(overrides)
    return store.submit(authz, **params)


class TestEvidenceAssembly:
    """FR-009: construct an evidence manifest for consequential evaluations."""

    def test_submit_registers_and_returns_evidence(self, store, authz):
        evidence = _submit(store, authz)
        assert evidence.evidence_id.startswith("EVD-")
        assert evidence.evidence_status == "valid"
        assert evidence.payload["case_id"] == "case-1"

    def test_get_returns_registered_evidence(self, store, authz):
        evidence = _submit(store, authz)
        assert store.get(evidence.evidence_id) is evidence

    def test_get_unknown_evidence_raises(self, store):
        with pytest.raises(NotFoundError):
            store.get("evidence-nope")

    def test_duplicate_registration_rejected(self, store, authz):
        evidence = _submit(store, authz)
        with pytest.raises(ValidationError):
            store.register(authz, evidence)

    def test_case_index_groups_evidence(self, store, authz):
        first = _submit(store, authz, case_id="case-A")
        second = _submit(store, authz, case_id="case-A")
        _submit(store, authz, case_id="case-B")
        ids = {e.evidence_id for e in store.for_case("case-A")}
        assert ids == {first.evidence_id, second.evidence_id}

    def test_for_unknown_case_is_empty(self, store):
        assert store.for_case("case-none") == []

    def test_manifest_assembles_referenced_evidence(self, store, authz):
        a = _submit(store, authz, case_id="case-1")
        b = _submit(store, authz, case_id="case-1", evidence_type="report")
        manifest = store.assemble_manifest(authz, "case-1", [a.evidence_id, b.evidence_id], now=NOW)
        assert manifest.case_id == "case-1"
        assert set(manifest.refs()) == {a.evidence_id, b.evidence_id}
        assert manifest.completeness == "complete"

    def test_empty_manifest_is_incomplete(self, store, authz):
        manifest = store.assemble_manifest(authz, "case-1", [], now=NOW)
        assert manifest.items == []
        assert manifest.completeness == "incomplete"

    def test_manifest_reports_freshness_policy(self, store, authz):
        evidence = _submit(store, authz)
        manifest = store.assemble_manifest(authz, "case-1", [evidence.evidence_id], now=NOW)
        assert manifest.freshness_policy == {"max_age_hours": 24, "required": True}

    def test_manifest_respects_custom_max_age(self, store, authz):
        evidence = _submit(store, authz)
        manifest = store.assemble_manifest(
            authz, "case-1", [evidence.evidence_id], now=NOW, max_age_hours=1
        )
        assert manifest.freshness_policy == {"max_age_hours": 1, "required": True}

    def test_manifest_skips_freshness_when_not_required(self, store, authz):
        old = _submit(store, authz, observation_time="2025-01-01T00:00:00+00:00")
        manifest = store.assemble_manifest(
            authz, "case-1", [old.evidence_id], now=NOW, freshness_required=False
        )
        assert manifest.freshness_policy["required"] is False


class TestEvidenceProvenance:
    """FR-010: preserve source references and provenance."""

    def test_submit_records_source_references(self, store, authz):
        evidence = _submit(store, authz)
        assert evidence.source_authority_ref == "U05.PricingService"
        assert evidence.source_artifact_ref == "price-feed"
        assert evidence.source_version == 3

    def test_provenance_defaults_to_authority_artifact_version(self, store, authz):
        evidence = _submit(store, authz)
        assert evidence.provenance == "U05.PricingService:price-feed@v3"

    def test_explicit_provenance_is_preserved(self, store, authz):
        evidence = _submit(store, authz, provenance=" lineage:7 ")
        assert evidence.provenance == " lineage:7 "

    def test_missing_source_authority_rejected(self, store, authz):
        with pytest.raises(ValidationError):
            _submit(store, authz, source_authority_ref="")

    def test_access_classification_defaults_to_internal(self, store, authz):
        evidence = _submit(store, authz)
        assert evidence.access_classification == "internal"

    def test_explicit_classification_is_preserved(self, store, authz):
        evidence = _submit(store, authz, access_classification="financial")
        assert evidence.access_classification == "financial"

    def test_register_classifies_unclassified_evidence(self, store, authz):
        evidence = DecisionEvidence(
            evidence_id="ev-manual",
            evidence_type="metric",
            source_authority_ref="U05.PricingService",
            source_artifact_ref="price-feed",
            source_version=1,
        )
        registered = store.register(authz, evidence)
        assert registered.access_classification == "internal"

    def test_ingestion_time_is_recorded(self, store, authz):
        evidence = _submit(store, authz)
        assert evidence.ingestion_time != ""


class TestEvidenceFreshness:
    """FR-011 / AC-009: evaluate evidence freshness according to decision policy."""

    def test_current_evidence_stays_current(self, store, authz):
        evidence = _submit(store, authz)
        assessed = store.assess_freshness(evidence.evidence_id, NOW)
        assert assessed.freshness_status == "current"
        assert assessed.evidence_status == "valid"

    def test_evidence_older_than_max_age_is_stale(self, store, authz):
        old = _submit(store, authz, observation_time="2025-12-01T00:00:00+00:00")
        assessed = store.assess_freshness(old.evidence_id, NOW)
        assert assessed.freshness_status == "stale"
        assert assessed.evidence_status == "stale"

    def test_evidence_outside_validity_interval_is_stale(self, store, authz):
        evidence = _submit(
            store, authz,
            validity_interval=("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
            observation_time="2026-01-01T00:00:00+00:00",
        )
        assessed = store.assess_freshness(evidence.evidence_id, "2026-06-01T00:00:00+00:00")
        assert assessed.evidence_status == "stale"

    def test_evidence_before_validity_interval_is_stale(self, store, authz):
        evidence = _submit(
            store, authz,
            validity_interval=("2026-06-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"),
            observation_time="2026-06-01T00:00:00+00:00",
        )
        assessed = store.assess_freshness(evidence.evidence_id, "2026-01-01T00:00:00+00:00")
        assert assessed.evidence_status == "stale"

    def test_max_age_can_be_overridden_per_call(self, store, authz):
        # 12 h old: within the 24 h default, but stale under a 1 h policy.
        recent = _submit(store, authz, observation_time="2025-12-31T12:00:00+00:00")
        assessed = store.assess_freshness(recent.evidence_id, NOW, max_age_hours=1)
        assert assessed.evidence_status == "stale"

    def test_assess_unknown_evidence_raises(self, store):
        with pytest.raises(NotFoundError):
            store.assess_freshness("evidence-nope", NOW)

    def test_assert_fresh_passes_for_current_evidence(self, store, authz):
        evidence = _submit(store, authz)
        store.assert_fresh([evidence.evidence_id], NOW)

    def test_assert_fresh_blocks_stale_evidence(self, store, authz):
        old = _submit(store, authz, observation_time="2025-12-01T00:00:00+00:00")
        with pytest.raises(InvariantViolationError) as excinfo:
            store.assert_fresh([old.evidence_id], NOW)
        assert excinfo.value.details["stale_evidence"] == [old.evidence_id]

    def test_assert_fresh_reports_only_the_stale_items(self, store, authz):
        fresh = _submit(store, authz)
        stale = _submit(store, authz, observation_time="2025-12-01T00:00:00+00:00")
        with pytest.raises(InvariantViolationError) as excinfo:
            store.assert_fresh([fresh.evidence_id, stale.evidence_id], NOW)
        assert excinfo.value.details["stale_evidence"] == [stale.evidence_id]

    def test_assert_fresh_blocks_disputed_evidence(self, store, authz):
        disputed = _submit(store, authz)
        store.get(disputed.evidence_id).evidence_status = "disputed"
        with pytest.raises(InvariantViolationError):
            store.assert_fresh([disputed.evidence_id], NOW)

    def test_assert_fresh_accepts_empty_set(self, store):
        store.assert_fresh([], NOW)


class TestEvidenceAccessControl:
    """AC-045 / AC-046: classified evidence requires an explicit read scope."""

    def test_internal_evidence_readable_without_scope(self, store, authz, plain_authz):
        evidence = _submit(store, authz)
        assert store.retrieve(plain_authz, evidence.evidence_id) is evidence

    def test_sensitive_evidence_requires_scope(self, store, authz, plain_authz):
        evidence = _submit(store, authz, access_classification="sensitive")
        with pytest.raises(AuthorizationError):
            store.retrieve(plain_authz, evidence.evidence_id)

    def test_scope_grants_access(self, store, authz):
        evidence = _submit(store, authz, access_classification="sensitive")
        assert store.retrieve(authz, evidence.evidence_id) is evidence

    def test_financial_evidence_requires_scope(self, store, authz, plain_authz):
        evidence = _submit(store, authz, access_classification="financial")
        with pytest.raises(AuthorizationError):
            store.retrieve(plain_authz, evidence.evidence_id)

    def test_personal_evidence_requires_scope(self, store, authz, plain_authz):
        evidence = _submit(store, authz, access_classification="personal")
        with pytest.raises(AuthorizationError):
            store.retrieve(plain_authz, evidence.evidence_id)

    def test_retrieve_many_checks_every_item(self, store, authz, plain_authz):
        open_item = _submit(store, authz)
        locked = _submit(store, authz, access_classification="financial")
        with pytest.raises(AuthorizationError):
            store.retrieve_many(plain_authz, [open_item.evidence_id, locked.evidence_id])

    def test_retrieve_many_returns_all_when_authorized(self, store, authz):
        a = _submit(store, authz)
        b = _submit(store, authz, access_classification="financial")
        assert store.retrieve_many(authz, [a.evidence_id, b.evidence_id]) == [a, b]

    def test_manifest_assembly_enforces_read_scopes(self, store, authz, plain_authz):
        locked = _submit(store, authz, access_classification="financial")
        with pytest.raises(AuthorizationError):
            store.assemble_manifest(plain_authz, "case-1", [locked.evidence_id], now=NOW)
