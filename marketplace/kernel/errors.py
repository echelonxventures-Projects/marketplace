"""Canonical error semantics.

Every public operation raises errors from this module so that error contracts
are uniform, machine-checkable, and never leak internals (REQ-API-001 error
contracts, GOV-10 disclosure discipline).
"""

from __future__ import annotations

from typing import Any, Optional


class MarketplaceError(Exception):
    """Base class for all canonical domain errors."""

    code: str = "MARKETPLACE_ERROR"
    http_status: int = 500
    retryable: bool = False

    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_contract(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
            "retryable": self.retryable,
        }


# -- validation ---------------------------------------------------------


class ValidationError(MarketplaceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class SchemaValidationError(ValidationError):
    code = "SCHEMA_VALIDATION_ERROR"


# -- authorization (LAW-ARCH-07 fail-safe) ------------------------------


class AuthorizationError(MarketplaceError):
    code = "AUTHORIZATION_ERROR"
    http_status = 403


class AuthenticationError(MarketplaceError):
    code = "AUTHENTICATION_ERROR"
    http_status = 401


class TenantIsolationError(AuthorizationError):
    code = "TENANT_ISOLATION_ERROR"


class ConsentRequiredError(AuthorizationError):
    code = "CONSENT_REQUIRED"


# -- state machines -----------------------------------------------------


class IllegalTransitionError(MarketplaceError):
    code = "ILLEGAL_TRANSITION"
    http_status = 409

    def __init__(self, entity_id: str, current: str, attempted: str, legal: list[str]) -> None:
        super().__init__(
            f"Cannot transition {entity_id} from {current!r} to {attempted!r}; "
            f"legal transitions from {current!r}: {sorted(legal)}",
            details={
                "entity_id": entity_id,
                "current_state": current,
                "attempted": attempted,
                "legal": sorted(legal),
            },
        )


class InvariantViolationError(MarketplaceError):
    code = "INVARIANT_VIOLATION"
    http_status = 409

    def __init__(self, invariant_id: str, message: str, details: Optional[dict] = None) -> None:
        super().__init__(f"[{invariant_id}] {message}", details=details)
        self.invariant_id = invariant_id


# -- concurrency & idempotency ------------------------------------------


class ConcurrencyConflictError(MarketplaceError):
    code = "CONCURRENCY_CONFLICT"
    http_status = 409
    retryable = True


class OptimisticLockError(ConcurrencyConflictError):
    code = "OPTIMISTIC_LOCK"


# -- resource / not found ------------------------------------------------


class NotFoundError(MarketplaceError):
    code = "NOT_FOUND"
    http_status = 404


class ConflictError(MarketplaceError):
    code = "CONFLICT"
    http_status = 409


# -- dependency & integration (GOV-13) ----------------------------------


class DependencyUnavailableError(MarketplaceError):
    code = "DEPENDENCY_UNAVAILABLE"
    http_status = 503
    retryable = True

    def __init__(self, dependency: str, message: str) -> None:
        super().__init__(f"Dependency {dependency!r} unavailable: {message}")
        self.dependency = dependency


class TimeoutError(MarketplaceError):
    code = "TIMEOUT"
    http_status = 504
    retryable = True


class DuplicateRequestError(MarketplaceError):
    code = "DUPLICATE_REQUEST"
    http_status = 409

    def __init__(self, idempotency_key: str, message: str = "request already processed") -> None:
        super().__init__(message, details={"idempotency_key": idempotency_key})


class UnknownOutcomeError(MarketplaceError):
    """The authoritative result of a consequential action is not known.

    Recovery must resolve the authoritative status before retrying
    (execution-recovery rule; never blindly repeat a financial action).
    """

    code = "UNKNOWN_OUTCOME"
    http_status = 409
    retryable = False

    def __init__(self, operation_id: str, message: str) -> None:
        super().__init__(message, details={"operation_id": operation_id})
        self.operation_id = operation_id


class DeadLetterError(MarketplaceError):
    code = "DEAD_LETTER"
    http_status = 500

    def __init__(self, message_id: str, reason: str) -> None:
        super().__init__(f"Event {message_id} dead-lettered: {reason}")
        self.message_id = message_id


# -- financial -----------------------------------------------------------


class FinancialPrecisionError(MarketplaceError):
    code = "FINANCIAL_PRECISION"


class LedgerImbalanceError(InvariantViolationError):
    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__("INV-04", message, details)


class OversellError(InvariantViolationError):
    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__("INV-02", message, details)


# -- governance ----------------------------------------------------------


class PolicyViolationError(MarketplaceError):
    code = "POLICY_VIOLATION"
    http_status = 403


class DuplicateCanonicalConceptError(InvariantViolationError):
    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__("GOV-05", message, details)


class OrphanArtifactError(InvariantViolationError):
    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__("GOV-06", message, details)


class NotImplementedException(MarketplaceError):
    code = "NOT_IMPLEMENTED"
    http_status = 501
