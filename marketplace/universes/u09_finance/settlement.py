"""U09c — Settlement, payout and reconciliation.

REQ-FIN-005, REQ-FIN-006, REQ-FIN-007, REQ-FIN-009. Enforces the
decision/execution separation: a settlement is calculated, then approved by a
separate authority, then paid — never paid directly from calculation
(LAW-ARCH-03).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from marketplace.kernel import AuthorizationContext, Money, generate
from marketplace.kernel.errors import (
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.money import sum_money, zero
from marketplace.universes._base import U09
from marketplace.universes.u09_finance.entities import (
    Payout,
    ReconciliationItem,
    SellerPayable,
    Settlement,
    SettlementEntity,
)


class SettlementService:
    def __init__(self) -> None:
        self.settlements: dict[str, Settlement] = {}
        self.payouts: dict[str, Payout] = {}
        self.payables: dict[str, SellerPayable] = {}
        self.reconciliation_items: list[ReconciliationItem] = []
        self._lock = threading.RLock()

    # -- payables (REQ-FIN-005) -------------------------------------------

    def create_payable(
        self,
        *,
        seller_id: str,
        order_id: str,
        gross_sales: Money,
        commission: Money,
        fees: Money,
        taxes_collected: Optional[Money] = None,
        adjustments: Optional[Money] = None,
    ) -> SellerPayable:
        currency = gross_sales.currency.code
        for amount in (commission, fees, taxes_collected or zero(currency), adjustments or zero(currency)):
            if amount.currency.code != currency:
                raise InvariantViolationError(
                    "GOV-05",
                    f"Payable components must share the currency {currency}; found {amount.currency}",
                )
        taxes = taxes_collected or zero(currency)
        adj = adjustments or zero(currency)
        net = gross_sales - commission - fees + adj - taxes
        payable = SellerPayable(
            payable_id=generate("payout"),
            seller_id=seller_id,
            order_id=order_id,
            gross_sales=gross_sales,
            commission=commission,
            taxes_collected=taxes,
            fees=fees,
            adjustments=adj,
            net_payable=net,
            currency=currency,
            created_at=U09.now(),
        )
        self.payables[payable.payable_id] = payable
        return payable

    # -- settlement ---------------------------------------------------------

    def open_settlement(self, period_label: str, currency: str) -> Settlement:
        settlement = Settlement(
            settlement_id=generate("settlement"),
            period_label=period_label,
            currency=currency,
        )
        self.settlements[settlement.settlement_id] = settlement
        return settlement

    def add_payable(self, settlement_id: str, payable: SellerPayable) -> Settlement:
        settlement = self._require_settlement(settlement_id)
        if settlement.status != "scheduled":
            raise IllegalTransitionError(
                settlement_id, settlement.status, "calculated",
                SettlementEntity.machine.legal_targets(settlement.status),
            )
        if payable.currency != settlement.currency:
            raise ValidationError(
                f"Settlement is {settlement.currency}; payable is {payable.currency}"
            )
        settlement.payables.append(payable)
        payable.settlement_id = settlement_id
        return settlement

    def calculate(self, settlement_id: str) -> Settlement:
        """REQ-FIN-005: transparent totals with no hidden rounding."""
        settlement = self._require_settlement(settlement_id)
        entity = SettlementEntity(settlement_id)
        entity.state = settlement.status
        entity.transition_to(
            "calculated", actor="settlement.service",
            actor_roles=frozenset({"settlement.service"}),
            reason="payables aggregated", recorded_at=U09.now(),
        )
        settlement.status = entity.state
        settlement.total_payable = sum_money([p.net_payable for p in settlement.payables])
        settlement.total_commission = sum_money([p.commission for p in settlement.payables])
        return settlement

    def approve(self, authz: AuthorizationContext, settlement_id: str) -> Settlement:
        """Approval is a separate authority from calculation (LAW-ARCH-03)."""
        authz.requires_role("finance.approver")
        settlement = self._require_settlement(settlement_id)
        entity = SettlementEntity(settlement_id)
        entity.state = settlement.status
        entity.transition_to(
            "approved", actor=authz.actor_id, actor_roles=authz.roles,
            reason="settlement approved for payout", recorded_at=U09.now(),
        )
        settlement.status = entity.state
        settlement.approved_by = authz.actor_id
        return settlement

    def pay(self, settlement_id: str, *, destination_for, idempotency_key: str) -> list[Payout]:
        """Execute payouts. Requires prior approval; never pays unapproved sums."""
        settlement = self._require_settlement(settlement_id)
        if settlement.status != "approved":
            raise IllegalTransitionError(
                settlement_id, settlement.status, "paid",
                SettlementEntity.machine.legal_targets(settlement.status),
            )

        def run() -> list[Payout]:
            payouts: list[Payout] = []
            by_seller: dict[str, list[SellerPayable]] = {}
            for payable in settlement.payables:
                by_seller.setdefault(payable.seller_id, []).append(payable)
            for seller_id, payables in by_seller.items():
                amount = sum_money([p.net_payable for p in payables])
                if amount.is_negative():
                    raise InvariantViolationError(
                        "INV-04",
                        f"Payout to {seller_id} is negative ({amount}); a negative payout "
                        "requires an approved adjustment, not an automatic debit",
                        details={"seller_id": seller_id},
                    )
                payout = Payout(
                    payout_id=generate("payout"),
                    settlement_id=settlement_id,
                    seller_id=seller_id,
                    amount=amount,
                    destination=destination_for(seller_id),
                    status="initiated",
                    at=U09.now(),
                )
                self.payouts[payout.payout_id] = payout
                payouts.append(payout)
            entity = SettlementEntity(settlement_id)
            entity.state = settlement.status
            entity.transition_to(
                "paid", actor="payout.service", actor_roles=frozenset({"payout.service"}),
                reason=f"{len(payouts)} payouts executed", recorded_at=U09.now(),
            )
            settlement.status = entity.state
            settlement.paid_at = U09.now()
            return payouts

        return run()

    # -- reconciliation (REQ-FIN-007) ---------------------------------------

    def reconcile(
        self,
        settlement_id: str,
        *,
        provider_statement: dict[str, Money],
        bank_payouts: dict[str, Money],
    ) -> list[ReconciliationItem]:
        """Match payables against provider statements and bank payouts.

        Every unmatched item is surfaced explicitly — never silently absorbed.
        """
        settlement = self._require_settlement(settlement_id)
        items: list[ReconciliationItem] = []
        for payable in settlement.payables:
            payout = next(
                (p for p in self.payouts.values()
                 if p.settlement_id == settlement_id and p.seller_id == payable.seller_id),
                None,
            )
            expected = payout.amount if payout else payable.net_payable
            observed = bank_payouts.get(payable.seller_id)
            items.append(
                ReconciliationItem(
                    item_id=generate("evidence"),
                    kind="bank_payout",
                    external_reference=f"bank:{payable.seller_id}",
                    expected=expected,
                    observed=observed,
                    matched=observed is not None and observed == expected,
                    unmatched_reason=None
                    if observed is not None and observed == expected
                    else "no matching bank payout record"
                    if observed is None
                    else f"expected {expected}, observed {observed}",
                    at=U09.now(),
                )
            )
        for reference, observed in provider_statement.items():
            items.append(
                ReconciliationItem(
                    item_id=generate("evidence"),
                    kind="provider_statement",
                    external_reference=reference,
                    expected=None,
                    observed=observed,
                    matched=False,
                    unmatched_reason="provider statement line without a matching payable",
                    at=U09.now(),
                )
            )
        unmatched = [i for i in items if not i.matched]
        self.reconciliation_items.extend(items)
        if unmatched:
            raise InvariantViolationError(
                "REQ-FIN-007",
                f"{len(unmatched)} reconciliation item(s) unmatched in settlement "
                f"{settlement_id}; each requires disposition before close",
                details={"unmatched": [i.item_id for i in unmatched]},
            )
        return items

    def exception_aging(self, at: str) -> dict[str, int]:
        """REQ-FIN-009: exception aging across unmatched reconciliation items."""
        from datetime import datetime

        reference = datetime.fromisoformat(at.replace("Z", "+00:00"))
        aging = {"current": 0, "1_7_days": 0, "8_30_days": 0, "over_30_days": 0}
        for item in self.reconciliation_items:
            if item.matched:
                continue
            observed_at = datetime.fromisoformat(item.at.replace("Z", "+00:00"))
            age_days = (reference - observed_at).days
            bucket = (
                "current" if age_days <= 0
                else "1_7_days" if age_days <= 7
                else "8_30_days" if age_days <= 30
                else "over_30_days"
            )
            aging[bucket] += 1
        return aging

    def _require_settlement(self, settlement_id: str) -> Settlement:
        settlement = self.settlements.get(settlement_id)
        if settlement is None:
            raise NotFoundError(f"Settlement {settlement_id} not found")
        return settlement
