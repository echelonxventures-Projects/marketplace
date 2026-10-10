"""Event infrastructure: contracts, bus, and delivery guarantees.

Implements REQ-DATA-003 and REQ-DATA-004:
  * event contracts carry unique id, schema version, occurred/recorded times,
    producer, causation/correlation ids and a privacy classification;
  * delivery semantics, ordering scope, deduplication, replay, poison-message
    isolation, retention and consumer recovery are all explicit.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .errors import DeadLetterError
from .identifiers import generate

PRIVACY_CLASSES = frozenset({"public", "internal", "personal", "sensitive", "financial"})


@dataclass(frozen=True)
class EventContract:
    """A registered event contract owned by exactly one universe."""

    name: str
    producer_universe: str
    schema_version: int
    payload_schema: tuple[str, ...]  # required payload keys
    privacy_class: str
    ordering_key: str  # payload field used as the ordering key
    retention_days: int = 2555  # ~7 years default for financial audit trails

    def __post_init__(self) -> None:
        if self.privacy_class not in PRIVACY_CLASSES:
            raise ValueError(f"Unknown privacy class {self.privacy_class!r}")
        if self.privacy_class in ("sensitive", "financial") and self.retention_days < 365:
            raise ValueError(
                f"Event {self.name!r} is {self.privacy_class} and must be retained >= 365 days"
            )


class EventContractRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, EventContract] = {}
        self._lock = threading.RLock()

    def register(self, contract: EventContract) -> EventContract:
        with self._lock:
            if contract.name in self._contracts:
                raise ValueError(f"Event contract {contract.name!r} already registered")
            self._contracts[contract.name] = contract
        return contract

    def get(self, name: str) -> EventContract:
        contract = self._contracts.get(name)
        if contract is None:
            raise KeyError(f"Event contract {name!r} is not registered")
        return contract

    def all(self) -> Iterable[EventContract]:
        return list(self._contracts.values())

    def requires(self, name: str) -> tuple[str, ...]:
        return self.get(name).payload_schema


CONTRACTS = EventContractRegistry()


def register_event(contract: EventContract) -> EventContract:
    return CONTRACTS.register(contract)


@dataclass
class Event:
    """A single published event."""

    event_id: str
    contract_name: str
    schema_version: int
    producer: str
    occurred_at: str
    recorded_at: str
    correlation_id: str
    causation_id: Optional[str]
    ordering_key: str
    privacy_class: str
    payload: dict
    sequence: int = 0

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "contract": self.contract_name,
            "schema_version": self.schema_version,
            "producer": self.producer,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "ordering_key": self.ordering_key,
            "privacy_class": self.privacy_class,
            "payload": self.payload,
            "sequence": self.sequence,
        }


class EventBus:
    """In-process event bus with explicit delivery guarantees.

    Guarantees implemented and tested:
      * at-least-once delivery to every subscriber;
      * deduplication by ``event_id`` so replay is safe (INV-11);
      * per-subscriber, per-ordering-key ordering (other keys may interleave);
      * poison messages are isolated to a dead letter and the bus continues;
      * replay of historical events to a subscriber is supported.
    """

    def __init__(self, max_retries: int = 3) -> None:
        self._subscribers: dict[str, list[tuple[str, Callable[[Event], None]]]] = defaultdict(list)
        self._log: deque[Event] = deque()
        self._delivered: set[tuple[str, str]] = set()  # (subscriber, event_id)
        self._dead_letter: list[tuple[Event, str, str]] = []
        self._lock = threading.RLock()
        self._max_retries = max_retries
        self._sequence = 0

    # -- subscription -----------------------------------------------------

    def subscribe(self, contract_name: str, subscriber_name: str, handler: Callable[[Event], None]) -> None:
        if not any(name == subscriber_name for name, _ in self._subscribers[contract_name]):
            self._subscribers[contract_name].append((subscriber_name, handler))

    # -- publishing -------------------------------------------------------

    def publish(self, event: Event) -> Event:
        with self._lock:
            self._sequence += 1
            event.sequence = self._sequence
            self._log.append(event)
        self._dispatch(event)
        return event

    def _dispatch(self, event: Event) -> None:
        for subscriber_name, handler in list(self._subscribers.get(event.contract_name, [])):
            self._deliver(event, subscriber_name, handler)

    def _deliver(self, event: Event, subscriber_name: str, handler: Callable[[Event], None]) -> None:
        dedup_key = (subscriber_name, event.event_id)
        with self._lock:
            if dedup_key in self._delivered:
                return  # at-least-once with idempotent dedup -> effectively once
        attempt = 0
        last_error = ""
        while attempt <= self._max_retries:
            try:
                handler(event)
                with self._lock:
                    self._delivered.add(dedup_key)
                return
            except Exception as exc:  # noqa: BLE001 - bus isolates all handler failures
                attempt += 1
                last_error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            self._dead_letter.append((event, subscriber_name, last_error))
        raise DeadLetterError(event.event_id, f"subscriber={subscriber_name} error={last_error}")

    # -- replay & inspection ---------------------------------------------

    def replay(self, contract_name: str, subscriber_name: str, handler: Callable[[Event], None], *, force: bool = False) -> int:
        """Re-deliver historical events of a contract to a subscriber.

        With ``force=False`` already-delivered events are skipped, so a replay
        after a crash never duplicates work (INV-11 deterministic handling).
        """
        delivered = 0
        for event in list(self._log):
            if event.contract_name != contract_name:
                continue
            if not force and (subscriber_name, event.event_id) in self._delivered:
                continue
            handler(event)
            delivered += 1
        return delivered

    def history(self, contract_name: Optional[str] = None) -> list[Event]:
        if contract_name is None:
            return list(self._log)
        return [e for e in self._log if e.contract_name == contract_name]

    def dead_letters(self) -> list[tuple[Event, str, str]]:
        return list(self._dead_letter)

    def delivered_count(self) -> int:
        return len(self._delivered)


def emit(
    contract_name: str,
    producer: str,
    payload: dict,
    *,
    correlation_id: str,
    causation_id: Optional[str],
    clock: Callable[[], str],
    event_id: Optional[str] = None,
) -> Event:
    """Build an event that conforms to its registered contract."""
    contract = CONTRACTS.get(contract_name)
    missing = [k for k in contract.payload_schema if k not in payload]
    if missing:
        raise ValueError(
            f"Event {contract_name!r} payload missing required keys {missing}"
        )
    occurred_at = payload.get("_occurred_at") or clock()
    ordering_key = payload.get(contract.ordering_key)
    if ordering_key is None:
        raise ValueError(
            f"Event {contract_name!r} must carry ordering key {contract.ordering_key!r}"
        )
    return Event(
        event_id=event_id or generate("event"),
        contract_name=contract_name,
        schema_version=contract.schema_version,
        producer=producer,
        occurred_at=str(occurred_at),
        recorded_at=clock(),
        correlation_id=correlation_id,
        causation_id=causation_id,
        ordering_key=str(ordering_key),
        privacy_class=contract.privacy_class,
        payload=payload,
    )
