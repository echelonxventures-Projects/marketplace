"""U09 Financial Commerce: append-only ledger, double-entry balance, reversal."""

import pytest

from marketplace.kernel import Money
from marketplace.kernel.errors import ValidationError
from marketplace.universes.u09_finance.ledger import LedgerService

pytestmark = pytest.mark.invariant


@pytest.fixture
def ledger():
    service = LedgerService()
    for account_id, kind in (("cash", "asset"), ("revenue", "revenue"), ("refunds", "expense")):
        service.open_account(account_id, account_id.upper(), account_id, kind, "USD")
    service.open_journal("j1", "sales", "USD")
    return service


class TestChartOfAccounts:
    def test_open_account(self, ledger):
        assert ledger.account("cash").kind == "asset"

    def test_unknown_account_kind(self, ledger):
        with pytest.raises(ValidationError):
            ledger.open_account("x", "X", "X", "not_a_kind", "USD")

    def test_missing_account(self, ledger):
        with pytest.raises(Exception):
            ledger.account("nope")

    def test_account_currency(self, ledger):
        assert ledger.account("cash").currency == "USD"


class TestDoubleEntry:
    def post(self, ledger, **over):
        base = dict(
            journal_id="j1",
            description="sale",
            debit_account_id="cash",
            credit_account_id="revenue",
            amount=Money.of(10, "USD"),
            source="test",
            correlation_id="c1",
        )
        base.update(over)
        return ledger.post(**base)

    def test_post_entry(self, ledger):
        entry = self.post(ledger)
        assert entry.debit_account_id == "cash"
        assert entry.credit_account_id == "revenue"
        assert entry.amount == Money.of(10, "USD")

    def test_balanced_pair_is_accepted(self, ledger):
        # a debit and credit of equal amount in one journal balances
        self.post(ledger)
        assert len(ledger.journals["j1"].entries) == 1

    def test_same_account_pair_rejected(self, ledger):
        with pytest.raises(ValidationError):
            self.post(ledger, debit_account_id="cash", credit_account_id="cash")

    def test_journal_currency_mismatch_rejected(self, ledger):
        with pytest.raises(ValidationError):
            self.post(ledger, amount=Money.of(10, "EUR"))

    def test_account_currency_mismatch_rejected(self, ledger):
        ledger.open_account("eur", "EUR", "eur", "asset", "EUR")
        with pytest.raises(ValidationError):
            self.post(ledger, debit_account_id="eur")

    def test_auto_opens_journal(self, ledger):
        entry = self.post(ledger, journal_id="j2")
        assert entry.journal_id == "j2"
        assert "j2" in ledger.journals

    def test_entry_sequence_increments(self, ledger):
        first = self.post(ledger)
        second = self.post(ledger, correlation_id="c2")
        assert first.sequence == 0
        assert second.sequence == 1


class TestReversal:
    """LAW-ARCH-04: corrections are compensating entries, never destructive edits."""

    def test_reverse_entry(self, ledger):
        original = ledger.post(
            journal_id="j1", description="sale", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of(10, "USD"),
            source="test", correlation_id="c1",
        )
        reversal = ledger.reverse(original.entry_id, reason="refund", correlation_id="c2")
        assert reversal.debit_account_id == original.credit_account_id
        assert reversal.credit_account_id == original.debit_account_id
        assert reversal.amount == original.amount
        assert original.entry_id in reversal.reverses_entry_id

    def test_original_entry_preserved(self, ledger):
        original = ledger.post(
            journal_id="j1", description="sale", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of(10, "USD"),
            source="test", correlation_id="c1",
        )
        ledger.reverse(original.entry_id, reason="refund", correlation_id="c2")
        # the original is untouched — the ledger is append-only
        assert ledger._find_entry(original.entry_id).description == "sale"

    def test_reversing_a_reversal_denied(self, ledger):
        original = ledger.post(
            journal_id="j1", description="sale", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of(10, "USD"),
            source="test", correlation_id="c1",
        )
        reversal = ledger.reverse(original.entry_id, reason="refund", correlation_id="c2")
        with pytest.raises(ValidationError):
            ledger.reverse(reversal.entry_id, reason="no", correlation_id="c3")

    def test_double_reversal_denied(self, ledger):
        original = ledger.post(
            journal_id="j1", description="sale", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of(10, "USD"),
            source="test", correlation_id="c1",
        )
        ledger.reverse(original.entry_id, reason="refund", correlation_id="c2")
        with pytest.raises(ValidationError):
            ledger.reverse(original.entry_id, reason="again", correlation_id="c3")


class TestLedgerBalance:
    def test_journal_balances(self, ledger):
        ledger.post(
            journal_id="j1", description="sale", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of(25, "USD"),
            source="test", correlation_id="c1",
        )
        ledger.assert_all_journals_balanced()

    def test_reversal_restores_balance(self, ledger):
        original = ledger.post(
            journal_id="j1", description="sale", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of(25, "USD"),
            source="test", correlation_id="c1",
        )
        ledger.reverse(original.entry_id, reason="refund", correlation_id="c2")
        ledger.assert_all_journals_balanced()

    def test_trial_balance_reports_all_accounts(self, ledger):
        ledger.post(
            journal_id="j1", description="sale", debit_account_id="cash",
            credit_account_id="revenue", amount=Money.of(25, "USD"),
            source="test", correlation_id="c1",
        )
        tb = ledger.trial_balance()
        assert "CASH" in tb and "REVENUE" in tb
