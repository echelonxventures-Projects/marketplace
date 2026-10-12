"""U09b — The append-only accounting ledger.

Enforces:
  * LAW-ARCH-04: ledger records are append-only; corrections are compensating
    entries that reverse the original, never destructive edits;
  * INV-04: journals balance by configured accounting dimensions, and every
    journal's debits equal its credits;
  * REQ-FIN-004: balanced, immutable journal entries with a defined chart of
    accounts and defined correction procedure.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from marketplace.kernel import Money, generate
from marketplace.kernel.errors import (
    LedgerImbalanceError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.money import get_currency
from marketplace.universes._base import U09
from marketplace.universes.u09_finance.entities import JournalEntry, LedgerAccount


def account_currency(code: str):
    return get_currency(code)


@dataclass
class Journal:
    """A set of entries that must balance within its accounting dimensions."""

    journal_id: str
    label: str
    currency: str
    balancing_dimensions: tuple[str, ...] = ("order_id",)
    closed: bool = False
    entries: list[JournalEntry] = field(default_factory=list)


class LedgerService:
    def __init__(self) -> None:
        self.accounts: dict[str, LedgerAccount] = {}
        self.journals: dict[str, Journal] = {}
        self._lock = threading.RLock()

    # -- chart of accounts -------------------------------------------------

    def open_account(
        self,
        account_id: str,
        code: str,
        label: str,
        kind: str,
        currency: str,
        *,
        party_id: Optional[str] = None,
        dimensions: Optional[dict] = None,
    ) -> LedgerAccount:
        if kind not in {"asset", "liability", "revenue", "expense", "equity"}:
            raise ValidationError(f"Unknown account kind {kind!r}")
        account = LedgerAccount(
            account_id=account_id,
            code=code,
            label=label,
            kind=kind,
            currency=currency,
            party_id=party_id,
            dimensions=dict(dimensions or {}),
        )
        self.accounts[account_id] = account
        return account

    def account(self, account_id: str) -> LedgerAccount:
        account = self.accounts.get(account_id)
        if account is None:
            raise NotFoundError(f"Ledger account {account_id} not found")
        return account

    # -- journals ----------------------------------------------------------

    def open_journal(self, journal_id: str, label: str, currency: str, *, balancing_dimensions=("order_id",)) -> Journal:
        journal = Journal(
            journal_id=journal_id, label=label, currency=currency,
            balancing_dimensions=tuple(balancing_dimensions),
        )
        self.journals[journal_id] = journal
        return journal

    # -- posting -----------------------------------------------------------

    def post(
        self,
        *,
        journal_id: str,
        description: str,
        debit_account_id: str,
        credit_account_id: str,
        amount: Money,
        source: str,
        correlation_id: str,
        dimensions: Optional[dict] = None,
    ) -> JournalEntry:
        """Post one double-entry record. Raises if the pair is unbalanced."""
        if debit_account_id == credit_account_id:
            raise ValidationError("Debit and credit accounts must differ")
        with self._lock:
            journal = self.journals.get(journal_id)
            if journal is None:
                journal = self.open_journal(journal_id, f"auto:{journal_id}", amount.currency.code)
            if journal.currency != amount.currency.code:
                raise ValidationError(
                    f"Journal {journal_id} is {journal.currency}; cannot post {amount.currency}"
                )
            for account_id in (debit_account_id, credit_account_id):
                account = self.account(account_id)
                if account.currency != amount.currency.code:
                    raise ValidationError(
                        f"Account {account_id} is {account.currency}; cannot post {amount.currency}"
                    )
            entry = JournalEntry(
                entry_id=generate("ledger"),
                journal_id=journal_id,
                posted_at=U09.now(),
                description=description,
                currency=amount.currency.code,
                debit_account_id=debit_account_id,
                credit_account_id=credit_account_id,
                amount=amount,
                dimensions=dict(dimensions or {}),
                source=source,
                correlation_id=correlation_id,
                sequence=len(journal.entries),
            )
            journal.entries.append(entry)
            self._assert_balanced(journal)
            return entry

    def reverse(
        self, entry_id: str, *, reason: str, correlation_id: str
    ) -> JournalEntry:
        """LAW-ARCH-04: the only permitted correction is a reversing entry.

        The original entry is preserved untouched; the reversal debits the
        credit account and credits the debit account, and links back.
        """
        with self._lock:
            original = self._find_entry(entry_id)
            if original.reverses_entry_id is not None:
                raise ValidationError(
                    f"Entry {entry_id} is itself a reversal; reversing a reversal is "
                    "prohibited (append-only ledger, LAW-ARCH-04)"
                )
            if any(e.reverses_entry_id == entry_id for e in self._all_entries()):
                raise ValidationError(
                    f"Entry {entry_id} is already reversed by a compensating entry"
                )
            reversal = JournalEntry(
                entry_id=generate("ledger"),
                journal_id=original.journal_id,
                posted_at=U09.now(),
                description=f"REVERSAL of {entry_id}: {reason}",
                currency=original.currency,
                debit_account_id=original.credit_account_id,
                credit_account_id=original.debit_account_id,
                amount=original.amount,
                dimensions=dict(original.dimensions),
                source=original.source,
                correlation_id=correlation_id,
                reverses_entry_id=entry_id,
                sequence=len(self.journals[original.journal_id].entries),
            )
            self.journals[original.journal_id].entries.append(reversal)
            self._assert_balanced(self.journals[original.journal_id])
            return reversal

    # -- balances ----------------------------------------------------------

    def balance(self, account_id: str) -> Money:
        """Signed balance: debits positive, credits negative.

        Reversals cancel their originals.
        """
        account = self.account(account_id)
        total = Decimal(0)
        for entry in self._all_entries():
            if entry.debit_account_id == account_id:
                total += entry.amount.amount
            elif entry.credit_account_id == account_id:
                total -= entry.amount.amount
        return Money(total, account_currency(account.currency))

    def trial_balance(self) -> dict[str, str]:
        return {
            account.code: str(self.balance(account.account_id))
            for account in self.accounts.values()
        }

    # -- invariants --------------------------------------------------------

    def assert_all_journals_balanced(self) -> None:
        for journal in self.journals.values():
            self._assert_balanced(journal)

    def _assert_balanced(self, journal: Journal) -> None:
        """INV-04: within every accounting dimension bucket, debits equal credits.

        Each posting contributes ``amount`` to the debit side of its debit
        account and ``amount`` to the credit side of its credit account, both
        attributed to the bucket derived from ``balancing_dimensions``. A
        journal is balanced iff every bucket's debit total equals its credit
        total. A reversing entry is itself a balanced posting, and because it
        lands in the same bucket as its original it cancels the net position.
        """
        debit_side: dict[tuple, Decimal] = defaultdict(lambda: Decimal(0))
        credit_side: dict[tuple, Decimal] = defaultdict(lambda: Decimal(0))
        for entry in journal.entries:
            if not entry.dimensions:
                key: tuple = ()
            else:
                key = tuple(entry.dimensions.get(d) for d in journal.balancing_dimensions)
            debit_side[key] += entry.amount.amount
            credit_side[key] += entry.amount.amount
        buckets = set(debit_side) | set(credit_side)
        unbalanced = {
            k: {"debit": str(debit_side[k]), "credit": str(credit_side[k])}
            for k in buckets
            if debit_side[k] != credit_side[k]
        }
        if unbalanced:
            raise LedgerImbalanceError(
                f"Journal {journal.journal_id} is unbalanced by accounting dimensions "
                f"{journal.balancing_dimensions}: {unbalanced}",
                details={"journal_id": journal.journal_id, "unbalanced": unbalanced},
            )

    def net_position(self, journal_id: str, bucket: tuple) -> Decimal:
        """Net debit-minus-credit position of one accounting-dimension bucket."""
        journal = self.journals[journal_id]
        net = Decimal(0)
        for entry in journal.entries:
            if not entry.dimensions:
                key: tuple = ()
            else:
                key = tuple(entry.dimensions.get(d) for d in journal.balancing_dimensions)
            if key != bucket:
                continue
            net += entry.amount.amount if entry.reverses_entry_id is None else -entry.amount.amount
        return net

    def _all_entries(self) -> list[JournalEntry]:
        return [e for j in self.journals.values() for e in j.entries]

    def _find_entry(self, entry_id: str) -> JournalEntry:
        entry = next((e for e in self._all_entries() if e.entry_id == entry_id), None)
        if entry is None:
            raise NotFoundError(f"Ledger entry {entry_id} not found")
        return entry


# Concept ownership declarations (GOV-02): U09 is the single write authority for
# the financial concepts. The payment-intent/refund/settlement concepts are
# owned here so that the payment service's own declarations are the canonical
# registration point for the accounting domain.
U09.owns("ledger_account", "Chart-of-accounts account with currency, kind and dimensions", "U09.LedgerService.open_account")
U09.owns("journal_entry", "Append-only double-entry record; corrections are reversals", "U09.LedgerService.post", read_projections=["U08.order", "U14.audit"])
U09.owns("payment_intent", "Authorized payment commitment for an order", "U09.PaymentService.authorize", read_projections=["U08.order"])
U09.owns("payment_operation", "Provider operation: authorize, capture, void or refund", "U09.PaymentService.authorize", read_projections=["U09.payment_intent"])
U09.owns("refund", "Return of captured funds with reason and authorization", "U09.PaymentService.request_refund", read_projections=["U08.order"])
U09.owns("seller_payable", "Amount owed to a seller, computed under settlement policy", "U09.SettlementService.create_payable", read_projections=["U07.commission_terms"])
U09.owns("settlement", "Settlement period, statement and payout reconciliation", "U09.SettlementService.create_settlement", read_projections=["U07.seller_profile"])
