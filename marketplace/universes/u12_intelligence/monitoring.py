"""U12-FDVI decision observability and control (Spec 030 §7.1 Module 17; FR-049).

Module 17 responsibilities:

* monitor lifecycle progress;
* measure decision latency;
* monitor model and solver health;
* detect failed dependencies;
* detect policy violations;
* monitor value realization;
* produce alerts and operational evidence;
* support incident investigation.

The module is a read-only projection over the decision pipeline. It records what
happened and derives indicators from those records; it never mutates a decision,
an approval, a model or a policy (Spec 030 §7.1 Module 17, NFR-011).

The honesty rules that govern the rest of FDVI apply here unchanged. An
indicator reports a rate only when it has observations to compute one from, so
an unmeasured operation is reported as unknown rather than as a reassuring zero
(LAW-UMFDVI-010, LAW-UMFDVI-011).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Optional

from marketplace.kernel import AuthorizationContext, generate
from marketplace.kernel.errors import (
    NotFoundError,
    ValidationError,
)
from marketplace.universes._base import U12

# The outcome of one observed operation, for reliability accounting.
OUTCOMES = frozenset({"succeeded", "failed", "unknown", "rejected"})

# What kind of subject a health indicator describes.
HEALTH_SUBJECTS = frozenset({"model", "solver", "dependency"})

# Health is derived from recorded observations, never asserted directly.
HEALTH_STATUSES = frozenset({"healthy", "degraded", "unhealthy", "unknown"})

ALERT_SEVERITIES = frozenset({"info", "warning", "critical"})

ALERT_STATUSES = frozenset({"open", "acknowledged", "resolved"})


@dataclass
class LifecycleIndicator:
    """How far decisions have progressed through each lifecycle stage (FR-049)."""

    indicator_id: str
    stage: str
    entered: int = 0
    terminal: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    last_observed_at: str = ""

    @property
    def in_flight(self) -> int:
        return max(0, self.entered - self.terminal)


@dataclass
class ReliabilityIndicator:
    """The reliability of one operation as actually observed (FR-049).

    ``failure_rate`` is ``None`` until at least one observation exists, because
    a zero-observation indicator cannot honestly claim a zero failure rate.
    """

    indicator_id: str
    operation: str
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    unknown: int = 0
    rejected: int = 0
    last_observed_at: str = ""

    @property
    def failure_rate(self) -> Optional[Decimal]:
        if self.total == 0:
            return None
        return Decimal(self.failed) / Decimal(self.total)

    @property
    def unknown_rate(self) -> Optional[Decimal]:
        if self.total == 0:
            return None
        return Decimal(self.unknown) / Decimal(self.total)

    def assert_rate_substantiated(self) -> None:
        """A rate may only be quoted when observations back it (FR-049)."""
        if self.total == 0:
            raise ValidationError(
                f"Reliability indicator for {self.operation!r} has no "
                "observations; a failure rate cannot be reported for an "
                "unmeasured operation (FR-049)"
            )


@dataclass
class LatencyObservation:
    """One measured duration of a single decision stage."""

    observation_id: str
    correlation_id: str
    stage: str
    started_at: str
    completed_at: str
    duration_seconds: Decimal


@dataclass
class LatencySummary:
    """Aggregate latency for one stage, computed from recorded observations."""

    summary_id: str
    stage: str
    count: int = 0
    min_seconds: Optional[Decimal] = None
    max_seconds: Optional[Decimal] = None
    mean_seconds: Optional[Decimal] = None
    median_seconds: Optional[Decimal] = None

    def assert_substantiated(self) -> None:
        if self.count == 0:
            raise ValidationError(
                f"Latency summary for stage {self.stage!r} has no observations; "
                "no latency statistic can be reported (FR-049)"
            )


@dataclass
class HealthIndicator:
    """The health of a model, solver or dependency, derived from observations."""

    indicator_id: str
    subject_ref: str
    subject_kind: str
    status: str = "unknown"
    observed_total: int = 0
    observed_failures: int = 0
    detail: str = ""
    observed_at: str = ""


@dataclass
class Alert:
    """An operational signal raised when an indicator breaches a threshold."""

    alert_id: str
    severity: str
    indicator: str
    condition: str
    observed_value: str
    threshold: str
    correlation_id: str = ""
    raised_at: str = ""
    acknowledged_by: str = ""
    acknowledged_at: str = ""
    status: str = "open"


@dataclass
class AlertPolicy:
    """Governed thresholds that turn indicators into alerts (AC-047 adjacency).

    A threshold of ``None`` means the condition is not monitored, rather than
    monitored at an implicit zero.
    """

    failure_rate_warning: Decimal = Decimal("0.05")
    failure_rate_critical: Decimal = Decimal("0.20")
    unknown_rate_warning: Decimal = Decimal("0.10")
    latency_warning_seconds: Optional[Decimal] = Decimal("60")
    unresolved_dependency_critical: int = 3
    open_review_warning: int = 10


def _parse_timestamp(value: str) -> datetime:
    if not value:
        raise ValidationError("A latency observation needs both timestamps")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"Timestamp {value!r} is not ISO-8601; latency cannot be measured"
        ) from exc


class ObservabilityService:
    """Module 17: expose lifecycle and reliability indicators (FR-049).

    The service observes the decision pipeline. Recording is open to the
    pipeline's own authority; reading is a separate authority, so an operator
    can inspect indicators without being able to fabricate them.
    """

    def __init__(self, alert_policy: Optional[AlertPolicy] = None) -> None:
        self.alert_policy = alert_policy or AlertPolicy()
        self.lifecycle: dict[str, LifecycleIndicator] = {}
        self.reliability: dict[str, ReliabilityIndicator] = {}
        self.latency: list[LatencyObservation] = []
        self.health: dict[str, HealthIndicator] = {}
        self.alerts: dict[str, Alert] = {}
        self._incidents: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    # -- lifecycle progress ------------------------------------------------

    def record_lifecycle(
        self,
        authz: AuthorizationContext,
        *,
        correlation_id: str,
        stage: str,
        status: str,
        terminal: bool = False,
        observed_at: str = "",
    ) -> LifecycleIndicator:
        """Record that one decision reached a lifecycle stage."""
        authz.requires_role("observability.writer")
        if not stage:
            raise ValidationError("A lifecycle observation must name its stage")
        with self._lock:
            indicator = self.lifecycle.get(stage)
            if indicator is None:
                indicator = LifecycleIndicator(
                    indicator_id=generate("evidence"), stage=stage
                )
                self.lifecycle[stage] = indicator
            indicator.entered += 1
            if terminal:
                indicator.terminal += 1
            indicator.by_status[status] = indicator.by_status.get(status, 0) + 1
            indicator.last_observed_at = observed_at or U12.now()
            self._correlate(correlation_id, f"lifecycle:{stage}")
        return indicator

    # -- reliability -------------------------------------------------------

    def record_operation(
        self,
        authz: AuthorizationContext,
        *,
        operation: str,
        outcome: str,
        correlation_id: str = "",
        observed_at: str = "",
    ) -> ReliabilityIndicator:
        """Record the outcome of one operation for reliability accounting."""
        authz.requires_role("observability.writer")
        if outcome not in OUTCOMES:
            raise ValidationError(
                f"Operation outcome {outcome!r} is not one of {sorted(OUTCOMES)}"
            )
        with self._lock:
            indicator = self.reliability.get(operation)
            if indicator is None:
                indicator = ReliabilityIndicator(
                    indicator_id=generate("evidence"), operation=operation
                )
                self.reliability[operation] = indicator
            indicator.total += 1
            if outcome == "succeeded":
                indicator.succeeded += 1
            elif outcome == "failed":
                indicator.failed += 1
            elif outcome == "unknown":
                indicator.unknown += 1
            else:
                indicator.rejected += 1
            indicator.last_observed_at = observed_at or U12.now()
            if correlation_id:
                self._correlate(correlation_id, f"reliability:{operation}")
        return indicator

    # -- latency -----------------------------------------------------------

    def record_latency(
        self,
        authz: AuthorizationContext,
        *,
        correlation_id: str,
        stage: str,
        started_at: str,
        completed_at: str,
    ) -> LatencyObservation:
        """Measure the duration of one decision stage (NFR-011)."""
        authz.requires_role("observability.writer")
        start = _parse_timestamp(started_at)
        end = _parse_timestamp(completed_at)
        if end < start:
            raise ValidationError(
                f"Stage {stage!r} completed before it started; the duration "
                "would be negative"
            )
        duration = Decimal(str((end - start).total_seconds()))
        observation = LatencyObservation(
            observation_id=generate("evidence"),
            correlation_id=correlation_id,
            stage=stage,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
        )
        with self._lock:
            self.latency.append(observation)
            self._correlate(correlation_id, f"latency:{stage}")
        return observation

    def latency_summary(self, stage: str) -> LatencySummary:
        """Aggregate every recorded duration for one stage."""
        durations = [o.duration_seconds for o in self.latency if o.stage == stage]
        summary = LatencySummary(
            summary_id=generate("evidence"), stage=stage, count=len(durations)
        )
        if not durations:
            return summary
        summary.min_seconds = min(durations)
        summary.max_seconds = max(durations)
        summary.mean_seconds = sum(durations) / Decimal(len(durations))
        summary.median_seconds = Decimal(str(median(durations)))
        return summary

    # -- health ------------------------------------------------------------

    def assess_health(
        self,
        authz: AuthorizationContext,
        *,
        subject_ref: str,
        subject_kind: str,
        operation: Optional[str] = None,
    ) -> HealthIndicator:
        """Derive the health of a model, solver or dependency from observations.

        Health is never asserted; it is computed from the reliability of the
        operation that subject performs, so an unused subject is reported as
        unknown rather than healthy.
        """
        authz.requires_role("observability.reader")
        if subject_kind not in HEALTH_SUBJECTS:
            raise ValidationError(
                f"Health subject kind {subject_kind!r} is not one of "
                f"{sorted(HEALTH_SUBJECTS)}"
            )
        key = f"{subject_kind}:{subject_ref}"
        operation = operation or key
        reliability = self.reliability.get(operation)
        status = "unknown"
        detail = "no observations recorded"
        if reliability is not None and reliability.total > 0:
            rate = reliability.failure_rate
            assert rate is not None  # total > 0 above
            if rate >= self.alert_policy.failure_rate_critical:
                status = "unhealthy"
            elif rate >= self.alert_policy.failure_rate_warning:
                status = "degraded"
            else:
                status = "healthy"
            detail = (
                f"{reliability.failed} of {reliability.total} "
                f"{operation} observations failed"
            )
        indicator = HealthIndicator(
            indicator_id=generate("evidence"),
            subject_ref=subject_ref,
            subject_kind=subject_kind,
            status=status,
            observed_total=reliability.total if reliability else 0,
            observed_failures=reliability.failed if reliability else 0,
            detail=detail,
            observed_at=U12.now(),
        )
        with self._lock:
            self.health[key] = indicator
        return indicator

    # -- alerts and incident investigation ---------------------------------

    def _correlate(self, correlation_id: str, path: str) -> None:
        if correlation_id:
            self._incidents.setdefault(correlation_id, []).append(path)

    def raise_alert(
        self,
        authz: AuthorizationContext,
        *,
        severity: str,
        indicator: str,
        condition: str,
        observed_value: str,
        threshold: str,
        correlation_id: str = "",
    ) -> Alert:
        """Record one alert. Raising an alert never changes decision state."""
        authz.requires_role("observability.writer")
        if severity not in ALERT_SEVERITIES:
            raise ValidationError(
                f"Alert severity {severity!r} is not one of {sorted(ALERT_SEVERITIES)}"
            )
        alert = Alert(
            alert_id=generate("evidence"),
            severity=severity,
            indicator=indicator,
            condition=condition,
            observed_value=observed_value,
            threshold=threshold,
            correlation_id=correlation_id,
            raised_at=U12.now(),
        )
        with self._lock:
            self.alerts[alert.alert_id] = alert
            if correlation_id:
                self._correlate(correlation_id, f"alert:{indicator}")
        return alert

    def evaluate_alerts(
        self, authz: AuthorizationContext
    ) -> list[Alert]:
        """Derive alerts from the recorded indicators and the alert policy.

        This is the only place thresholds are applied, so an alert always names
        the policy threshold it breached rather than an implicit judgement.
        """
        authz.requires_role("observability.reader")
        raised: list[Alert] = []
        policy = self.alert_policy
        for operation, indicator in sorted(self.reliability.items()):
            if indicator.total == 0:
                continue
            rate = indicator.failure_rate
            assert rate is not None
            if rate >= policy.failure_rate_critical:
                raised.append(
                    self._alert_from(
                        authz,
                        severity="critical",
                        indicator=f"reliability:{operation}",
                        condition="failure rate at or above the critical threshold",
                        observed_value=f"{rate:.1%}",
                        threshold=f"{policy.failure_rate_critical:.1%}",
                    )
                )
            elif rate >= policy.failure_rate_warning:
                raised.append(
                    self._alert_from(
                        authz,
                        severity="warning",
                        indicator=f"reliability:{operation}",
                        condition="failure rate at or above the warning threshold",
                        observed_value=f"{rate:.1%}",
                        threshold=f"{policy.failure_rate_warning:.1%}",
                    )
                )
            unknown = indicator.unknown_rate
            assert unknown is not None
            if unknown >= policy.unknown_rate_warning:
                raised.append(
                    self._alert_from(
                        authz,
                        severity="warning",
                        indicator=f"reliability:{operation}",
                        condition="unknown-outcome rate at or above the warning threshold",
                        observed_value=f"{unknown:.1%}",
                        threshold=f"{policy.unknown_rate_warning:.1%}",
                    )
                )
        if policy.latency_warning_seconds is not None:
            for stage in sorted({o.stage for o in self.latency}):
                summary = self.latency_summary(stage)
                if summary.median_seconds is None:
                    continue
                if summary.median_seconds >= policy.latency_warning_seconds:
                    raised.append(
                        self._alert_from(
                            authz,
                            severity="warning",
                            indicator=f"latency:{stage}",
                            condition="median latency at or above the warning threshold",
                            observed_value=f"{summary.median_seconds}s",
                            threshold=f"{policy.latency_warning_seconds}s",
                        )
                    )
        return raised

    def _alert_from(
        self,
        authz: AuthorizationContext,
        *,
        severity: str,
        indicator: str,
        condition: str,
        observed_value: str,
        threshold: str,
    ) -> Alert:
        alert = Alert(
            alert_id=generate("evidence"),
            severity=severity,
            indicator=indicator,
            condition=condition,
            observed_value=observed_value,
            threshold=threshold,
            raised_at=U12.now(),
        )
        self.alerts[alert.alert_id] = alert
        return alert

    def acknowledge_alert(
        self,
        authz: AuthorizationContext,
        alert_id: str,
    ) -> Alert:
        """Acknowledge an open alert, recording who saw it (incident support)."""
        authz.requires_role("observability.reader")
        alert = self.alerts.get(alert_id)
        if alert is None:
            raise NotFoundError(f"Alert {alert_id} not found")
        if alert.status != "open":
            raise ValidationError(
                f"Alert {alert_id} is {alert.status!r}, not 'open'"
            )
        alert.status = "acknowledged"
        alert.acknowledged_by = authz.actor_id
        alert.acknowledged_at = U12.now()
        return alert

    def resolve_alert(self, authz: AuthorizationContext, alert_id: str) -> Alert:
        """Close an acknowledged alert once the condition is addressed."""
        authz.requires_role("observability.reader")
        alert = self.alerts.get(alert_id)
        if alert is None:
            raise NotFoundError(f"Alert {alert_id} not found")
        if alert.status != "acknowledged":
            raise ValidationError(
                f"Alert {alert_id} is {alert.status!r}; an alert must be "
                "acknowledged before it is resolved"
            )
        alert.status = "resolved"
        return alert

    def incident_timeline(
        self, authz: AuthorizationContext, correlation_id: str
    ) -> list[str]:
        """Every recorded observation for one correlation id (AC-043 support)."""
        authz.requires_role("observability.reader")
        return list(self._incidents.get(correlation_id, []))

    def _latency_projection(self, stage: str) -> dict:
        summary = self.latency_summary(stage)
        return {
            "count": summary.count,
            "median_seconds": (
                None if summary.median_seconds is None
                else str(summary.median_seconds)
            ),
        }

    def snapshot(self, authz: AuthorizationContext) -> dict:
        """A read-only view of every indicator (FR-049 exposure contract)."""
        authz.requires_role("observability.reader")
        return {
            "lifecycle": {
                stage: {
                    "entered": i.entered,
                    "terminal": i.terminal,
                    "in_flight": i.in_flight,
                    "by_status": dict(i.by_status),
                }
                for stage, i in sorted(self.lifecycle.items())
            },
            "reliability": {
                op: {
                    "total": i.total,
                    "succeeded": i.succeeded,
                    "failed": i.failed,
                    "unknown": i.unknown,
                    "rejected": i.rejected,
                    "failure_rate": (
                        None if i.failure_rate is None else str(i.failure_rate)
                    ),
                }
                for op, i in sorted(self.reliability.items())
            },
            "latency": {
                stage: self._latency_projection(stage)
                for stage in sorted({o.stage for o in self.latency})
            },
            "health": {
                key: {"status": h.status, "detail": h.detail}
                for key, h in sorted(self.health.items())
            },
            "open_alerts": sum(
                1 for a in self.alerts.values() if a.status == "open"
            ),
        }


# Concept ownership (GOV-02): U12 is the single write authority for the
# observability concepts introduced by Spec 030 Module 17.
U12.owns(
    "decision_observability",
    "Lifecycle, latency, reliability and health indicators for the decision "
    "pipeline, exposed as a read-only projection",
    "U12.ObservabilityService.snapshot",
    read_projections=["U14.policy", "U12.models", "U12.evaluation"],
)
