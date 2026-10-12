"""U12 FDVI: decision observability and control (Spec 030 Module 17; FR-049)."""

from decimal import Decimal

import pytest

from marketplace.kernel import AuthorizationContext
from marketplace.kernel.errors import NotFoundError, ValidationError
from marketplace.universes.u12_intelligence.monitoring import (
    AlertPolicy,
    ObservabilityService,
    ReliabilityIndicator,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def writer():
    return AuthorizationContext(
        actor_id="pipeline",
        actor_type="service",
        roles=frozenset({"observability.writer"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def reader():
    return AuthorizationContext(
        actor_id="operator",
        actor_type="person",
        roles=frozenset({"observability.reader"}),
        tenant_id="tenant-A",
    )


class TestLifecycleIndicators:
    def test_progress_and_in_flight(self, writer, reader):
        service = ObservabilityService()
        service.record_lifecycle(writer, correlation_id="c1", stage="evaluated", status="evaluated")
        service.record_lifecycle(writer, correlation_id="c2", stage="evaluated", status="evaluated")
        service.record_lifecycle(
            writer, correlation_id="c1", stage="approved", status="approved", terminal=True
        )
        indicator = service.lifecycle["evaluated"]
        assert indicator.entered == 2
        assert indicator.terminal == 0
        assert indicator.in_flight == 2
        assert service.lifecycle["approved"].terminal == 1

    def test_stage_must_be_named(self, writer):
        service = ObservabilityService()
        with pytest.raises(ValidationError):
            service.record_lifecycle(writer, correlation_id="c1", stage="", status="s")

    def test_writer_role_required(self, reader):
        service = ObservabilityService()
        with pytest.raises(Exception):
            service.record_lifecycle(reader, correlation_id="c1", stage="s", status="s")


class TestReliabilityIndicators:
    def test_failure_rate_needs_observations(self):
        indicator = ReliabilityIndicator(indicator_id="i", operation="evaluate")
        assert indicator.failure_rate is None
        assert indicator.unknown_rate is None

    def test_unmeasured_rate_cannot_be_claimed(self):
        indicator = ReliabilityIndicator(indicator_id="i", operation="evaluate")
        with pytest.raises(ValidationError):
            indicator.assert_rate_substantiated()

    def test_rate_computed_from_observations(self, writer):
        service = ObservabilityService()
        for outcome in ("succeeded", "succeeded", "failed", "failed"):
            service.record_operation(writer, operation="evaluate", outcome=outcome)
        indicator = service.reliability["evaluate"]
        assert indicator.total == 4
        assert indicator.failure_rate == Decimal("0.5")
        assert indicator.assert_rate_substantiated() is None

    def test_invalid_outcome_rejected(self, writer):
        service = ObservabilityService()
        with pytest.raises(ValidationError):
            service.record_operation(writer, operation="evaluate", outcome="mostly-fine")

    def test_writer_role_required(self, reader):
        service = ObservabilityService()
        with pytest.raises(Exception):
            service.record_operation(reader, operation="evaluate", outcome="succeeded")


class TestLatency:
    def test_duration_measured(self, writer):
        service = ObservabilityService()
        observation = service.record_latency(
            writer,
            correlation_id="c1",
            stage="evaluate",
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:30+00:00",
        )
        assert observation.duration_seconds == Decimal("30")

    def test_aggregate_stats(self, writer):
        service = ObservabilityService()
        for start, end in (
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:10+00:00"),
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:20+00:00"),
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:30+00:00"),
        ):
            service.record_latency(
                writer, correlation_id="c1", stage="evaluate",
                started_at=start, completed_at=end,
            )
        summary = service.latency_summary("evaluate")
        assert summary.count == 3
        assert summary.min_seconds == Decimal("10")
        assert summary.max_seconds == Decimal("30")
        assert summary.mean_seconds == Decimal("20")
        assert summary.median_seconds == Decimal("20")

    def test_empty_summary_cannot_be_claimed(self, writer):
        service = ObservabilityService()
        summary = service.latency_summary("evaluate")
        assert summary.count == 0
        with pytest.raises(ValidationError):
            summary.assert_substantiated()

    def test_negative_duration_rejected(self, writer):
        service = ObservabilityService()
        with pytest.raises(ValidationError):
            service.record_latency(
                writer,
                correlation_id="c1",
                stage="evaluate",
                started_at="2026-01-01T00:00:30+00:00",
                completed_at="2026-01-01T00:00:00+00:00",
            )

    def test_malformed_timestamp_rejected(self, writer):
        service = ObservabilityService()
        with pytest.raises(ValidationError):
            service.record_latency(
                writer,
                correlation_id="c1",
                stage="evaluate",
                started_at="not-a-timestamp",
                completed_at="2026-01-01T00:00:00+00:00",
            )


class TestHealth:
    def test_unknown_when_unobserved(self, reader):
        service = ObservabilityService()
        health = service.assess_health(reader, subject_ref="model-1", subject_kind="model")
        assert health.status == "unknown"
        assert health.observed_total == 0

    def test_healthy_below_warning_threshold(self, writer, reader):
        service = ObservabilityService()
        service.record_operation(writer, operation="model:model-1", outcome="succeeded")
        health = service.assess_health(
            reader, subject_ref="model-1", subject_kind="model", operation="model:model-1"
        )
        assert health.status == "healthy"

    def test_degraded_between_thresholds(self, writer, reader):
        service = ObservabilityService(
            AlertPolicy(failure_rate_warning=Decimal("0.1"), failure_rate_critical=Decimal("0.5"))
        )
        for _ in range(8):
            service.record_operation(writer, operation="model:model-1", outcome="succeeded")
        for _ in range(2):
            service.record_operation(writer, operation="model:model-1", outcome="failed")
        health = service.assess_health(
            reader, subject_ref="model-1", subject_kind="model", operation="model:model-1"
        )
        assert health.status == "degraded"
        assert health.observed_failures == 2

    def test_unhealthy_at_critical_threshold(self, writer, reader):
        service = ObservabilityService(AlertPolicy(failure_rate_critical=Decimal("0.2")))
        for _ in range(3):
            service.record_operation(writer, operation="model:model-1", outcome="failed")
        health = service.assess_health(
            reader, subject_ref="model-1", subject_kind="model", operation="model:model-1"
        )
        assert health.status == "unhealthy"

    def test_invalid_subject_kind_rejected(self, reader):
        service = ObservabilityService()
        with pytest.raises(ValidationError):
            service.assess_health(reader, subject_ref="x", subject_kind="unicorn")

    def test_reader_role_required(self, writer):
        service = ObservabilityService()
        with pytest.raises(Exception):
            service.assess_health(writer, subject_ref="model-1", subject_kind="model")


class TestAlerts:
    def test_failure_rate_alerts(self, writer, reader):
        service = ObservabilityService()
        for _ in range(4):
            service.record_operation(writer, operation="evaluate", outcome="failed")
        alerts = service.evaluate_alerts(reader)
        severities = {a.severity for a in alerts}
        assert "critical" in severities
        assert all(a.threshold for a in alerts)

    def test_no_alerts_when_healthy(self, writer, reader):
        service = ObservabilityService()
        service.record_operation(writer, operation="evaluate", outcome="succeeded")
        assert service.evaluate_alerts(reader) == []

    def test_unknown_outcome_alerts(self, writer, reader):
        service = ObservabilityService(AlertPolicy(unknown_rate_warning=Decimal("0.05")))
        for _ in range(5):
            service.record_operation(writer, operation="evaluate", outcome="unknown")
        alerts = service.evaluate_alerts(reader)
        assert any("unknown-outcome" in a.condition for a in alerts)

    def test_latency_alert_uses_median(self, writer, reader):
        service = ObservabilityService(AlertPolicy(latency_warning_seconds=Decimal("5")))
        service.record_latency(
            writer, correlation_id="c1", stage="evaluate",
            started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:10+00:00",
        )
        alerts = service.evaluate_alerts(reader)
        assert any(a.indicator == "latency:evaluate" for a in alerts)

    def test_disabled_latency_threshold_no_alert(self, writer, reader):
        service = ObservabilityService(AlertPolicy(latency_warning_seconds=None))
        service.record_latency(
            writer, correlation_id="c1", stage="evaluate",
            started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:10+00:00",
        )
        assert service.evaluate_alerts(reader) == []

    def test_manual_alert_requires_writer(self, reader):
        service = ObservabilityService()
        with pytest.raises(Exception):
            service.raise_alert(
                reader, severity="warning", indicator="x", condition="c",
                observed_value="1", threshold="0",
            )

    def test_invalid_severity_rejected(self, writer):
        service = ObservabilityService()
        with pytest.raises(ValidationError):
            service.raise_alert(
                writer, severity="panic", indicator="x", condition="c",
                observed_value="1", threshold="0",
            )

    def test_acknowledge_then_resolve(self, writer, reader):
        service = ObservabilityService()
        alert = service.raise_alert(
            writer, severity="warning", indicator="x", condition="c",
            observed_value="1", threshold="0",
        )
        assert service.alerts[alert.alert_id].status == "open"
        acknowledged = service.acknowledge_alert(reader, alert.alert_id)
        assert acknowledged.status == "acknowledged"
        assert acknowledged.acknowledged_by == "operator"
        resolved = service.resolve_alert(reader, alert.alert_id)
        assert resolved.status == "resolved"

    def test_cannot_resolve_without_acknowledgement(self, writer, reader):
        service = ObservabilityService()
        alert = service.raise_alert(
            writer, severity="warning", indicator="x", condition="c",
            observed_value="1", threshold="0",
        )
        with pytest.raises(ValidationError):
            service.resolve_alert(reader, alert.alert_id)

    def test_cannot_acknowledge_twice(self, writer, reader):
        service = ObservabilityService()
        alert = service.raise_alert(
            writer, severity="warning", indicator="x", condition="c",
            observed_value="1", threshold="0",
        )
        service.acknowledge_alert(reader, alert.alert_id)
        with pytest.raises(ValidationError):
            service.acknowledge_alert(reader, alert.alert_id)

    def test_unknown_alert_raises(self, reader):
        service = ObservabilityService()
        with pytest.raises(NotFoundError):
            service.acknowledge_alert(reader, "nope")


class TestIncidentInvestigation:
    def test_timeline_groups_by_correlation(self, writer, reader):
        service = ObservabilityService()
        service.record_operation(writer, operation="evaluate", outcome="succeeded", correlation_id="c1")
        service.record_lifecycle(writer, correlation_id="c1", stage="evaluated", status="evaluated")
        service.record_lifecycle(writer, correlation_id="c2", stage="evaluated", status="evaluated")
        timeline = service.incident_timeline(reader, "c1")
        assert len(timeline) == 2
        assert service.incident_timeline(reader, "unknown") == []

    def test_snapshot_exposes_every_indicator(self, writer, reader):
        service = ObservabilityService()
        service.record_lifecycle(writer, correlation_id="c1", stage="evaluated", status="evaluated")
        service.record_operation(writer, operation="evaluate", outcome="failed")
        service.record_latency(
            writer, correlation_id="c1", stage="evaluate",
            started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:10+00:00",
        )
        service.raise_alert(
            writer, severity="warning", indicator="x", condition="c",
            observed_value="1", threshold="0",
        )
        snap = service.snapshot(reader)
        assert set(snap) == {"lifecycle", "reliability", "latency", "health", "open_alerts"}
        assert snap["lifecycle"]["evaluated"]["entered"] == 1
        assert snap["reliability"]["evaluate"]["failure_rate"] == "1"
        assert snap["latency"]["evaluate"]["count"] == 1
        assert snap["open_alerts"] == 1

    def test_snapshot_reports_null_rate_when_unmeasured(self, reader):
        service = ObservabilityService()
        snap = service.snapshot(reader)
        assert snap["reliability"] == {}

    def test_reader_role_required(self, writer):
        service = ObservabilityService()
        with pytest.raises(Exception):
            service.snapshot(writer)


class TestObservabilityConceptOwnership:
    def test_concept_owned_by_u12(self):
        from marketplace.kernel.registry import CONCEPTS

        assert any(c.concept == "decision_observability" for c in CONCEPTS.all())
