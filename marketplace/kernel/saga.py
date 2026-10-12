"""Idempotency and saga (workflow) primitives.

GOV-13: distributed workflows define idempotency, retry, timeout, compensation,
dead-letter handling, reconciliation and manual recovery.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from .errors import (
    DuplicateRequestError,
)


class IdempotencyStore:
    """Records the outcome of an idempotent command by key.

    A repeated command with the same key returns the recorded outcome instead
    of re-executing the side effect (INV-02/INV-03 double-allocation and
    duplicate-capture prevention).
    """

    def __init__(self) -> None:
        self._outcomes: dict[str, dict] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def execute(self, key: str, operation: Callable[[], dict]) -> dict:
        if not key:
            raise ValueError("idempotency key is required for consequential commands")
        with self._lock:
            if key in self._outcomes:
                return self._outcomes[key]
            if key in self._inflight:
                event = self._inflight[key]
                owner = False
            else:
                event = threading.Event()
                self._inflight[key] = event
                owner = True
        if owner:
            # Only the thread that registered the inflight event executes; every
            # other caller waits on it (INV-02/INV-03: a duplicate command must
            # never re-run the side effect).
            try:
                result = operation()
            except Exception:
                with self._lock:
                    self._inflight.pop(key, None)
                    event.set()
                raise
            with self._lock:
                self._outcomes[key] = result
                self._inflight.pop(key, None)
            event.set()
            return result
        event.wait(timeout=30)
        with self._lock:
            if key in self._outcomes:
                return self._outcomes[key]
        raise DuplicateRequestError(key, "concurrent duplicate produced no recorded outcome")

    def has(self, key: str) -> bool:
        return key in self._outcomes

    def outcome_of(self, key: str) -> Optional[dict]:
        return self._outcomes.get(key)


@dataclass
class SagaStep:
    name: str
    execute: Callable[[dict], dict]
    compensate: Optional[Callable[[dict, dict], None]] = None
    timeout_seconds: Optional[float] = None
    retries: int = 0


@dataclass
class SagaExecution:
    saga_id: str
    name: str
    state: str = "running"  # running | completed | compensating | compensated | failed
    completed_steps: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    error: Optional[str] = None


class Saga:
    """A compensating workflow (REQ-ORD-006, REQ-FIN-003, GOV-13).

    Steps execute in order. If a step fails after retries, completed steps are
    compensated in reverse order, leaving a recoverable state. If compensation
    is unavailable, the saga records itself as ``failed`` for manual recovery
    — it never claims success.
    """

    def __init__(self, name: str, steps: list[SagaStep]) -> None:
        self.name = name
        self.steps = steps
        self.executions: dict[str, SagaExecution] = {}

    def run(self, saga_id: str, context: Optional[dict] = None) -> SagaExecution:
        execution = SagaExecution(saga_id=saga_id, name=self.name, context=dict(context or {}))
        self.executions[saga_id] = execution
        for step in self.steps:
            attempt = 0
            while True:
                try:
                    result = step.execute(execution.context)
                    if isinstance(result, dict):
                        execution.context.update(result)
                    execution.completed_steps.append(step.name)
                    break
                except Exception as exc:  # noqa: BLE001
                    attempt += 1
                    if attempt > step.retries:
                        execution.error = f"{step.name}: {type(exc).__name__}: {exc}"
                        self._compensate(execution)
                        if execution.state != "compensated":
                            execution.state = "failed"
                        return execution
        execution.state = "completed"
        return execution

    def _compensate(self, execution: SagaExecution) -> None:
        execution.state = "compensating"
        compensated: list[str] = []
        for step in reversed(self.steps):
            if step.name not in execution.completed_steps:
                continue
            if step.compensate is None:
                # The step took effect but cannot be rolled back. The saga may
                # not claim it was compensated; it fails for manual recovery.
                execution.error = (
                    f"{execution.error or ''} | step {step.name!r} completed but has no "
                    "compensation, so its effect is unrecoverable automatically"
                )
                execution.state = "failed"
                return
            try:
                step.compensate(execution.context, execution.context)
                compensated.append(step.name)
            except Exception as exc:  # noqa: BLE001
                # Compensation failed: leave the saga failed for manual recovery.
                execution.error = (
                    f"{execution.error or ''} | compensation of {step.name} failed: {exc}"
                )
                execution.state = "failed"
                return
        execution.state = "compensated"

    def execution(self, saga_id: str) -> SagaExecution:
        return self.executions[saga_id]
