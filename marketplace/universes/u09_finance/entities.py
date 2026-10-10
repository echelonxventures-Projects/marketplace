"""U09 — Financial Commerce: entities.

Canonical concepts owned: payment_intent, payment_operation, refund,
ledger_account, journal_entry, settlement, seller_payable, payout,
reconciliation.

Enforces the decision/execution separation (LAW-ARCH-03), append-only ledger
facts (LAW-ARCH-04) and INV-03/INV-04.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from marketplace.kernel import Money, Stateful
from marketplace.kernel.state import StateMachine, Transition, register_machine

# ---------------------------------------------------------------------------
# State machines (§7.1)
# ---------------------------------------------------------------------------

PAYMENT_MACHINE = register_machine(
    StateMachine(
        name="payment.operation",
        concept="payment_operation",
        initial_state="intent_created",
        transitions=(
            Transition("intent_created", "authorizing", frozenset({"payment.service"}), event="payment.authorizing"),
            Transition("authorizing", "authorized", frozenset({"payment.provider"}), event="payment.authorized"),
            Transition("authorizing", "declined", frozenset({"payment.provider"}), event="payment.declined", terminal=True),
            Transition("authorizing", "failed", frozenset({"payment.provider"}), event="payment.failed"),
            Transition("authorized", "captured", frozenset({"payment.provider"}), event="payment.captured", terminal=True),
            Transition("authorized", "voided", frozenset({"payment.service"}), event="payment.voided", terminal=True),
            Transition("authorized", "partially_captured", frozenset({"payment.provider"}), event="payment.partially_captured"),
            Transition("partially_captured", "captured", frozenset({"payment.provider"}), event="payment.captured", terminal=True),
            Transition("captured", "refunded", frozenset({"payment.service"}), event="payment.refunded", terminal=True),
            Transition("captured", "partially_refunded", frozenset({"payment.service"}), event="payment.partially_refunded"),
            Transition("partially_refunded", "refunded", frozenset({"payment.service"}), event="payment.refunded", terminal=True),
            Transition("partially_captured", "refunded", frozenset({"payment.service"}), event="payment.refunded", terminal=True),
            Transition("failed", "intent_created", frozenset({"payment.service"}), event="payment.retry"),
        ),
        terminal_states=frozenset({"captured", "voided", "declined", "refunded"}),
    )
)

REFUND_MACHINE = register_machine(
    StateMachine(
        name="refund.lifecycle",
        concept="refund",
        initial_state="requested",
        transitions=(
            Transition("requested", "approved", frozenset({"finance.approver"}), event="refund.approved"),
            Transition("requested", "rejected", frozenset({"finance.approver"}), event="refund.rejected", terminal=True),
            Transition("approved", "submitted", frozenset({"payment.service"}), event="refund.submitted"),
            Transition("submitted", "succeeded", frozenset({"payment.provider"}), event="refund.succeeded", terminal=True),
            Transition("submitted", "failed", frozenset({"payment.provider"}), event="refund.failed", terminal=True),
        ),
        terminal_states=frozenset({"succeeded", "failed", "rejected"}),
    )
)

SETTLEMENT_MACHINE = register_machine(
    StateMachine(
        name="settlement.lifecycle",
        concept="settlement",
        initial_state="scheduled",
        transitions=(
            Transition("scheduled", "calculated", frozenset({"settlement.service"}), event="settlement.calculated"),
            Transition("calculated", "approved", frozenset({"finance.approver"}), event="settlement.approved"),
            Transition("approved", "paid", frozenset({"payout.service"}), event="settlement.paid", terminal=True),
            Transition("calculated", "disputed", frozenset({"finance.admin"}), event="settlement.disputed"),
            Transition("disputed", "calculated", frozenset({"finance.admin"}), event="settlement.recalculated"),
        ),
        terminal_states=frozenset({"paid"}),
    )
)


class PaymentEntity(Stateful):
    machine = PAYMENT_MACHINE

    def __init__(self, payment_id: str) -> None:
        super().__init__(payment_id)
        self.payment_id = payment_id


class RefundEntity(Stateful):
    machine = REFUND_MACHINE

    def __init__(self, refund_id: str) -> None:
        super().__init__(refund_id)
        self.refund_id = refund_id


class SettlementEntity(Stateful):
    machine = SETTLEMENT_MACHINE

    def __init__(self, settlement_id: str) -> None:
        super().__init__(settlement_id)
        self.settlement_id = settlement_id


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class PaymentIntent:
    intent_id: str
    order_id: str
    buyer_id: str
    amount: Money
    method: str  # card | wallet | bank_transfer | bnpl | cash_on_delivery
    provider: str
    status: str = "intent_created"
    provider_operation_id: Optional[str] = None
    captured_amount: Optional[Money] = None
    refunded_amount: Optional[Money] = None
    correlation_id: str = ""
    created_at: str = ""


@dataclass
class PaymentOperation:
    """One provider operation: authorization, capture, void or refund (REQ-FIN-002)."""

    operation_id: str
    intent_id: str
    kind: str  # authorize | capture | void | refund
    amount: Money
    provider: str
    provider_operation_id: Optional[str]
    status: str = "pending"  # pending | succeeded | failed | unknown
    requested_at: str = ""
    completed_at: Optional[str] = None
    idempotency_key: str = ""


@dataclass
class Refund:
    refund_id: str
    intent_id: str
    order_id: str
    amount: Money
    reason: str
    status: str = "requested"
    approver: Optional[str] = None
    requested_at: str = ""
    operation_id: Optional[str] = None


@dataclass
class LedgerAccount:
    account_id: str
    code: str
    label: str
    kind: str  # asset | liability | revenue | expense | equity
    currency: str
    party_id: Optional[str] = None
    dimensions: dict = field(default_factory=dict)


@dataclass
class JournalEntry:
    """An immutable accounting record (LAW-ARCH-04).

    Corrections are compensating entries that reference the original via
    ``reverses_entry_id``; entries are never edited or deleted.
    """

    entry_id: str
    journal_id: str
    posted_at: str
    description: str
    currency: str
    debit_account_id: str
    credit_account_id: str
    amount: Money
    dimensions: dict
    source: str  # e.g. order id, settlement id
    correlation_id: str
    reverses_entry_id: Optional[str] = None
    sequence: int = 0


@dataclass
class SellerPayable:
    """REQ-FIN-005: what the platform owes a seller for a settled order."""

    payable_id: str
    seller_id: str
    order_id: str
    gross_sales: Money
    commission: Money
    taxes_collected: Optional[Money]
    fees: Money
    adjustments: Money
    net_payable: Money
    currency: str
    created_at: str
    settlement_id: Optional[str] = None


@dataclass
class Settlement:
    """REQ-FIN-005: the calculation and execution of funds due to participants."""

    settlement_id: str
    period_label: str
    currency: str
    payables: list[SellerPayable] = field(default_factory=list)
    status: str = "scheduled"
    total_payable: Optional[Money] = None
    total_commission: Optional[Money] = None
    approved_by: Optional[str] = None
    paid_at: Optional[str] = None


@dataclass
class ReconciliationItem:
    """REQ-FIN-007: one matched or unmatched external fact."""

    item_id: str
    kind: str  # provider_statement | bank_payout | ledger
    external_reference: str
    expected: Optional[Money]
    observed: Optional[Money]
    matched: bool
    unmatched_reason: Optional[str] = None
    at: str = ""


@dataclass
class Payout:
    payout_id: str
    settlement_id: str
    seller_id: str
    amount: Money
    destination: str
    status: str = "initiated"  # initiated | submitted | succeeded | failed
    provider_reference: Optional[str] = None
    at: str = ""
