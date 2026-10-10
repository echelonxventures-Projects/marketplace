"""Marketplace kernel: canonical primitives and governance infrastructure."""

from . import authorization, errors, events, identifiers, money, persistence, registry, saga, state
from .authorization import (
    AuditChain,
    AuthorizationContext,
    AuthorizationResolver,
    Clock,
    EvidenceRecord,
    EvidenceStore,
    FailingAuthorizationResolver,
    ProvenanceRecord,
)
from .errors import MarketplaceError
from .events import CONTRACTS, EventBus, Event, EventContract, emit, register_event
from .identifiers import content_hash, generate, validate
from .money import Currency, Money, sum_money, zero
from .persistence import InMemoryStore, SqlProjectionStore
from .registry import (
    CAPABILITIES,
    CONCEPTS,
    INVARIANTS,
    REQUIREMENTS,
    CapabilityRecord,
    ConceptRecord,
    InvariantRecord,
    RequirementRecord,
)
from .saga import IdempotencyStore, Saga, SagaStep
from .state import REGISTRY, StateMachine, Stateful, register_machine

__all__ = [
    "AuditChain",
    "AuthorizationContext",
    "AuthorizationResolver",
    "CAPABILITIES",
    "CONCEPTS",
    "Clock",
    "Currency",
    "CapabilityRecord",
    "ConceptRecord",
    "Event",
    "EventBus",
    "EventContract",
    "CONTRACTS",
    "EvidenceRecord",
    "EvidenceStore",
    "FailingAuthorizationResolver",
    "INVARIANTS",
    "IdempotencyStore",
    "InMemoryStore",
    "InvariantRecord",
    "MarketplaceError",
    "Money",
    "ProvenanceRecord",
    "REQUIREMENTS",
    "RequirementRecord",
    "Saga",
    "SagaStep",
    "SqlProjectionStore",
    "StateMachine",
    "Stateful",
    "content_hash",
    "emit",
    "generate",
    "register_event",
    "register_machine",
    "REGISTRY",
    "sum_money",
    "validate",
    "zero",
]
