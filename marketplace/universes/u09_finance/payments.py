"""U09a — Payment orchestration.

REQ-FIN-001, REQ-FIN-002, REQ-FIN-003. Enforces INV-03 (a provider callback
cannot create duplicate capture, refund or posting) and LAW-ARCH-03
(authorization, capture, void and refund are distinct operations with distinct
states).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from marketplace.kernel import AuthorizationContext, IdempotencyStore, Money, generate
from marketplace.kernel.errors import (
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    UnknownOutcomeError,
    ValidationError,
)
from marketplace.kernel.money import zero
from marketplace.universes._base import U09
from marketplace.universes.u09_finance.entities import (
    PaymentEntity,
    PaymentIntent,
    PaymentOperation,
    Refund,
    RefundEntity,
)


@dataclass
class ProviderResult:
    """The outcome of one provider operation, as reported by an adapter."""

    operation_id: str
    provider_operation_id: str
    status: str  # succeeded | failed | declined | unknown
    amount: Money


class PaymentAdapter:
    """REQ-FIN-001: a versioned, normalized provider contract.

    Adapters never mutate domain state; they return a ``ProviderResult`` that
    the payment service applies through the state machine.
    """

    code = "base"

    def authorize(self, operation: PaymentOperation) -> ProviderResult:
        raise NotImplementedError

    def capture(self, operation: PaymentOperation) -> ProviderResult:
        raise NotImplementedError

    def refund(self, operation: PaymentOperation) -> ProviderResult:
        raise NotImplementedError


class DeterministicPaymentAdapter(PaymentAdapter):
    """A reference adapter whose behaviour is fully controlled by the caller.

    It is used by the integration tests with explicitly seeded outcomes, so
    nothing in the payment path is ever faked or hardcoded to succeed.
    """

    code = "deterministic"

    def __init__(self) -> None:
        self.calls: list[PaymentOperation] = []
        self.outcomes: dict[tuple[str, str], str] = {}
        self.default_status = "succeeded"

    def will_return(self, kind: str, key: str, status: str) -> None:
        self.outcomes[(kind, key)] = status

    def _outcome(self, kind: str, operation: PaymentOperation) -> ProviderResult:
        self.calls.append(operation)
        status = self.outcomes.get((kind, operation.idempotency_key), self.default_status)
        return ProviderResult(
            operation_id=operation.operation_id,
            provider_operation_id=f"PROV-{operation.operation_id[-8:]}",
            status=status,
            amount=operation.amount,
        )

    def authorize(self, operation: PaymentOperation) -> ProviderResult:
        return self._outcome("authorize", operation)

    def capture(self, operation: PaymentOperation) -> ProviderResult:
        return self._outcome("capture", operation)

    def refund(self, operation: PaymentOperation) -> ProviderResult:
        return self._outcome("refund", operation)


def _map_operation_status(kind: str, status: str) -> Optional[str]:
    if kind == "authorize":
        return {"succeeded": "authorized", "failed": "failed", "declined": "declined"}.get(status)
    if kind == "capture":
        return {"succeeded": "captured", "failed": "failed"}.get(status)
    if kind == "refund":
        return {"succeeded": "refunded", "failed": "failed"}.get(status)
    return None


class PaymentService:
    def __init__(self) -> None:
        self.intents: dict[str, PaymentIntent] = {}
        self.operations: list[PaymentOperation] = []
        self.refunds: dict[str, Refund] = {}
        self.providers: dict[str, PaymentAdapter] = {}
        self._idempotency = IdempotencyStore()
        self._lock = threading.RLock()

    def register_provider(self, code: str, adapter: PaymentAdapter) -> None:
        self.providers[code] = adapter

    # -- authorize ---------------------------------------------------------

    def authorize(
        self,
        *,
        order_id: str,
        buyer_id: str,
        amount: Money,
        method: str,
        provider_code: str,
        correlation_id: str,
        idempotency_key: str,
        intent_id: Optional[str] = None,
    ) -> PaymentIntent:
        provider = self.providers.get(provider_code)
        if provider is None:
            raise NotFoundError(
                f"Payment provider {provider_code!r} has no registered adapter; "
                "execution is refused rather than silently simulated"
            )

        def run() -> PaymentIntent:
            with self._lock:
                intent = PaymentIntent(
                    intent_id=intent_id or generate("payment"),
                    order_id=order_id,
                    buyer_id=buyer_id,
                    amount=amount,
                    method=method,
                    provider=provider_code,
                    correlation_id=correlation_id,
                    created_at=U09.now(),
                )
                self.intents[intent.intent_id] = intent
            operation = PaymentOperation(
                operation_id=generate("payment"),
                intent_id=intent.intent_id,
                kind="authorize",
                amount=amount,
                provider=provider_code,
                provider_operation_id=None,
                requested_at=U09.now(),
                idempotency_key=idempotency_key,
            )
            self.operations.append(operation)
            entity = PaymentEntity(intent.intent_id)
            entity.state = intent.status
            entity.transition_to(
                "authorizing", actor="payment.service",
                actor_roles=frozenset({"payment.service"}),
                reason=f"authorize {amount}", recorded_at=U09.now(),
            )
            intent.status = entity.state
            result = provider.authorize(operation)
            self._apply(intent, operation, result)
            return intent

        return self._idempotency.execute(idempotency_key, run)

    # -- capture -----------------------------------------------------------

    def capture(self, intent_id: str, *, amount: Optional[Money] = None, idempotency_key: str) -> PaymentIntent:
        def run() -> PaymentIntent:
            intent = self._require_intent(intent_id)
            if intent.status not in {"authorized", "partially_captured"}:
                raise IllegalTransitionError(
                    intent_id, intent.status, "captured",
                    PaymentEntity.machine.legal_targets(intent.status),
                )
            provider = self._require_provider(intent.provider)
            capture_amount = amount if amount is not None else intent.amount
            if capture_amount > intent.amount:
                raise ValidationError("Capture cannot exceed the authorized amount")
            operation = PaymentOperation(
                operation_id=generate("payment"),
                intent_id=intent_id,
                kind="capture",
                amount=capture_amount,
                provider=intent.provider,
                provider_operation_id=None,
                requested_at=U09.now(),
                idempotency_key=idempotency_key,
            )
            self.operations.append(operation)
            result = provider.capture(operation)
            self._apply(intent, operation, result)
            intent.captured_amount = capture_amount
            return intent

        return self._idempotency.execute(idempotency_key, run)

    def void(self, intent_id: str, *, idempotency_key: str) -> PaymentIntent:
        def run() -> PaymentIntent:
            intent = self._require_intent(intent_id)
            if intent.status != "authorized":
                raise IllegalTransitionError(
                    intent_id, intent.status, "voided",
                    PaymentEntity.machine.legal_targets(intent.status),
                )
            entity = PaymentEntity(intent_id)
            entity.state = intent.status
            entity.transition_to(
                "voided", actor="payment.service",
                actor_roles=frozenset({"payment.service"}),
                reason="void before capture", recorded_at=U09.now(),
            )
            intent.status = entity.state
            return intent

        return self._idempotency.execute(idempotency_key, run)

    # -- refund (approval and execution are separate, LAW-ARCH-03) ---------

    def request_refund(
        self, authz: AuthorizationContext, *, intent_id: str, order_id: str, amount: Money, reason: str
    ) -> Refund:
        authz.requires_role("refund.requester")
        intent = self._require_intent(intent_id)
        if intent.status not in {"captured", "partially_refunded", "partially_captured"}:
            raise IllegalTransitionError(
                intent_id, intent.status, "refunded",
                PaymentEntity.machine.legal_targets(intent.status),
            )
        already = intent.refunded_amount or zero(intent.amount.currency.code)
        capturable = intent.captured_amount or intent.amount
        if already + amount > capturable:
            raise InvariantViolationError(
                "INV-03",
                f"Refund of {amount} exceeds captured amount; already refunded {already}, "
                f"capturable {capturable}",
                details={"intent_id": intent_id},
            )
        refund = Refund(
            refund_id=generate("refund"),
            intent_id=intent_id,
            order_id=order_id,
            amount=amount,
            reason=reason,
            requested_at=U09.now(),
        )
        self.refunds[refund.refund_id] = refund
        return refund

    def approve_refund(self, authz: AuthorizationContext, refund_id: str) -> Refund:
        authz.requires_role("finance.approver")
        refund = self._require_refund(refund_id)
        entity = RefundEntity(refund_id)
        entity.state = refund.status
        entity.transition_to(
            "approved", actor=authz.actor_id, actor_roles=authz.roles,
            reason="refund approved for execution", recorded_at=U09.now(),
        )
        refund.status = entity.state
        refund.approver = authz.actor_id
        return refund

    def execute_refund(self, refund_id: str, *, idempotency_key: str) -> Refund:
        def run() -> Refund:
            refund = self._require_refund(refund_id)
            if refund.status != "approved":
                raise IllegalTransitionError(
                    refund_id, refund.status, "succeeded",
                    RefundEntity.machine.legal_targets(refund.status),
                )
            intent = self._require_intent(refund.intent_id)
            provider = self._require_provider(intent.provider)
            operation = PaymentOperation(
                operation_id=generate("payment"),
                intent_id=intent.intent_id,
                kind="refund",
                amount=refund.amount,
                provider=intent.provider,
                provider_operation_id=None,
                requested_at=U09.now(),
                idempotency_key=idempotency_key,
            )
            self.operations.append(operation)
            entity = RefundEntity(refund_id)
            entity.state = refund.status
            entity.transition_to(
                "submitted", actor="payment.service",
                actor_roles=frozenset({"payment.service"}),
                reason="refund submitted to provider", recorded_at=U09.now(),
            )
            refund.status = entity.state
            refund.operation_id = operation.operation_id
            result = provider.refund(operation)
            if result.status not in {"succeeded", "failed"}:
                raise UnknownOutcomeError(
                    operation.operation_id,
                    f"Provider returned {result.status!r} for refund {refund_id}; resolve "
                    "the authoritative status before retrying (execution-recovery rule)",
                )
            entity = RefundEntity(refund_id)
            entity.state = refund.status
            entity.transition_to(
                "succeeded" if result.status == "succeeded" else "failed",
                actor="payment.provider", actor_roles=frozenset({"payment.provider"}),
                reason=f"provider result {result.status}", recorded_at=U09.now(),
            )
            refund.status = entity.state
            if refund.status == "succeeded":
                currency = intent.amount.currency.code
                intent.refunded_amount = (intent.refunded_amount or zero(currency)) + refund.amount
                if intent.refunded_amount >= (intent.captured_amount or intent.amount):
                    payment_entity = PaymentEntity(intent.intent_id)
                    payment_entity.state = intent.status
                    payment_entity.transition_to(
                        "refunded", actor="payment.service",
                        actor_roles=frozenset({"payment.service"}),
                        reason="captured amount fully refunded", recorded_at=U09.now(),
                    )
                    intent.status = payment_entity.state
            return refund

        return self._idempotency.execute(idempotency_key, run)

    # -- provider callbacks (INV-03) ---------------------------------------

    def ingest_provider_callback(self, callback: ProviderResult, *, idempotency_key: str) -> PaymentOperation:
        """Idempotent callback ingestion: a replay updates, never duplicates."""
        with self._lock:
            operation = next(
                (
                    o for o in self.operations
                    if o.idempotency_key == idempotency_key and o.kind == callback.kind
                ),
                None,
            )
            if operation is None:
                operation = PaymentOperation(
                    operation_id=generate("payment"),
                    intent_id="",
                    kind=callback.kind,
                    amount=callback.amount,
                    provider="external",
                    provider_operation_id=callback.provider_operation_id,
                    requested_at=U09.now(),
                    idempotency_key=idempotency_key,
                )
                self.operations.append(operation)
            operation.status = callback.status
            operation.provider_operation_id = callback.provider_operation_id
            operation.completed_at = U09.now()
            return operation

    # -- internals ---------------------------------------------------------

    def _apply(self, intent: PaymentIntent, operation: PaymentOperation, result: ProviderResult) -> None:
        operation.provider_operation_id = result.provider_operation_id
        operation.completed_at = U09.now()
        if result.status == "unknown":
            operation.status = "unknown"
            raise UnknownOutcomeError(
                operation.operation_id,
                f"Provider {operation.provider} returned an unknown outcome for intent "
                f"{intent.intent_id}. Resolve the authoritative status before retrying.",
            )
        operation.status = result.status
        target = _map_operation_status(operation.kind, result.status)
        if target is None:
            raise UnknownOutcomeError(
                operation.operation_id,
                f"Provider returned {result.status!r} for a {operation.kind} operation; "
                "no legal transition applies",
            )
        entity = PaymentEntity(intent.intent_id)
        entity.state = intent.status
        entity.transition_to(
            target, actor="payment.provider", actor_roles=frozenset({"payment.provider"}),
            reason=f"provider {operation.kind} -> {result.status}", recorded_at=U09.now(),
        )
        intent.status = entity.state
        intent.provider_operation_id = result.provider_operation_id

    def _require_intent(self, intent_id: str) -> PaymentIntent:
        intent = self.intents.get(intent_id)
        if intent is None:
            raise NotFoundError(f"Payment intent {intent_id} not found")
        return intent

    def _require_refund(self, refund_id: str) -> Refund:
        refund = self.refunds.get(refund_id)
        if refund is None:
            raise NotFoundError(f"Refund {refund_id} not found")
        return refund

    def _require_provider(self, code: str) -> PaymentAdapter:
        provider = self.providers.get(code)
        if provider is None:
            raise NotFoundError(f"Provider {code!r} has no registered adapter")
        return provider
