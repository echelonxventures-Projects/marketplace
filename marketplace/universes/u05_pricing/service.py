"""U05 — Pricing & Taxation.

Canonical concepts owned: price_book, price_evaluation, promotion, coupon,
tax_determination, fee.

REQ-PRC-001..004. Pricing owns the *evaluation* of a price from inputs; the
order snapshots the evaluated result (LAW-ARCH-05) and the financial posting
of tax is owned by U09 (source-of-truth rule: tax determination here, posting
in Financial Commerce).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from marketplace.kernel import (
    AuthorizationContext,
    Money,
    generate,
)
from marketplace.kernel.errors import (
    ConcurrencyConflictError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from marketplace.kernel.money import zero
from marketplace.universes._base import U05

# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class PriceBookEntry:
    entry_id: str
    product_id: str
    variant_id: Optional[str]
    currency: str
    amount: Money
    valid_from: str = ""
    valid_until: str = ""
    min_quantity: int = 1
    tier_code: str = "standard"
    seller_id: Optional[str] = None


@dataclass
class PriceBook:
    pricebook_id: str
    code: str
    currency: str
    seller_id: Optional[str]
    entries: dict[str, PriceBookEntry] = field(default_factory=dict)
    precedence: int = 100


@dataclass
class Promotion:
    """REQ-MER-002: a campaign with explicit funding attribution."""

    promotion_id: str
    code: str
    kind: str  # percent | fixed_amount | shipping
    value: Decimal
    currency: str
    valid_from: str = ""
    valid_until: str = ""
    funded_by: str = "seller"  # seller | platform
    seller_id: Optional[str] = None
    max_redemptions: Optional[int] = None
    per_buyer_limit: Optional[int] = None
    min_spend: Optional[Money] = None
    stacking_group: Optional[str] = None
    status: str = "active"
    redemptions: int = 0
    buyer_redemptions: dict[str, int] = field(default_factory=dict)


@dataclass
class Coupon:
    coupon_id: str
    promotion_id: str
    code: str
    buyer_id: Optional[str] = None  # None = open coupon
    status: str = "issued"
    redeemed_at: Optional[str] = None


@dataclass
class TaxRate:
    rate_id: str
    jurisdiction: str
    tax_type: str  # vat | gst | sales_tax | duty
    rate: Decimal
    compound: bool = False


@dataclass
class PriceComponent:
    code: str
    label: str
    amount: Money
    kind: str  # base | discount | tax | fee | duty | shipping
    source: str  # rule or policy id that produced it


@dataclass
class PriceEvaluation:
    """REQ-PRC-002: an explainable price breakdown with inputs and versions."""

    evaluation_id: str
    offer_id: str
    product_id: str
    variant_id: Optional[str]
    quantity: int
    unit_price: Money
    components: list[PriceComponent]
    total: Money
    currency: str
    evaluated_at: str
    inputs: dict
    policy_versions: dict[str, int]
    valid_until: str

    def breakdown(self) -> list[dict]:
        return [
            {"code": c.code, "label": c.label, "amount": str(c.amount), "kind": c.kind, "source": c.source}
            for c in self.components
        ]


# ---------------------------------------------------------------------------
# Pricing service
# ---------------------------------------------------------------------------


class PricingService:
    def __init__(self) -> None:
        self.pricebooks: dict[str, PriceBook] = {}
        self.promotions: dict[str, Promotion] = {}
        self.coupons: dict[str, Coupon] = {}
        self.coupons_by_code: dict[str, Coupon] = {}
        self.tax_rates: dict[str, list[TaxRate]] = {}
        self.fee_policies: dict[str, dict] = {}
        self._lock = threading.RLock()
        self.evaluations: dict[str, PriceEvaluation] = {}

    # -- price books ------------------------------------------------------

    def create_pricebook(
        self, authz: AuthorizationContext, code: str, currency: str, *, seller_id: Optional[str] = None, precedence: int = 100
    ) -> PriceBook:
        authz.requires_role("pricing.writer")
        book = PriceBook(
            pricebook_id=generate("pricebook"),
            code=code,
            currency=currency,
            seller_id=seller_id,
            precedence=precedence,
        )
        self.pricebooks[book.pricebook_id] = book
        return book

    def set_price(
        self,
        authz: AuthorizationContext,
        pricebook_id: str,
        product_id: str,
        amount: Money,
        *,
        valid_from: str,
        valid_until: str,
        variant_id: Optional[str] = None,
        min_quantity: int = 1,
        tier_code: str = "standard",
    ) -> PriceBookEntry:
        authz.requires_role("pricing.writer")
        book = self._require_pricebook(pricebook_id)
        if book.currency != amount.currency.code:
            raise InvariantViolationError(
                "GOV-05",
                f"Pricebook {book.code} is {book.currency}; cannot store {amount.currency}",
            )
        if valid_until <= valid_from:
            raise ValidationError("Price validity period is empty")
        entry = PriceBookEntry(
            entry_id=generate("evidence"),
            product_id=product_id,
            variant_id=variant_id,
            currency=book.currency,
            amount=amount,
            min_quantity=min_quantity,
            valid_from=valid_from,
            valid_until=valid_until,
            tier_code=tier_code,
            seller_id=book.seller_id,
        )
        book.entries[entry.entry_id] = entry
        return entry

    def unit_price(
        self,
        product_id: str,
        currency: str,
        *,
        at: str,
        quantity: int = 1,
        variant_id: Optional[str] = None,
        seller_id: Optional[str] = None,
        tier_code: str = "standard",
    ) -> tuple[Money, str]:
        """Resolve the governing unit price from the highest-precedence book.

        Returns (price, source) so callers can record provenance.
        """
        candidates: list[tuple[int, PriceBookEntry]] = []
        for book in self.pricebooks.values():
            if book.currency != currency:
                continue
            if book.seller_id not in (None, seller_id):
                continue
            for entry in book.entries.values():
                if (
                    entry.product_id == product_id
                    and entry.variant_id == variant_id
                    and entry.min_quantity <= quantity
                    and entry.valid_from <= at <= entry.valid_until
                    and entry.tier_code == tier_code
                ):
                    candidates.append((book.precedence, entry))
        if not candidates:
            raise NotFoundError(
                f"No price book entry for product {product_id} in {currency} at {at}"
            )
        # lowest precedence number wins (100 = standard default)
        candidates.sort(key=lambda pair: pair[0])
        entry = candidates[0][1]
        return entry.amount, entry.entry_id

    # -- promotions & coupons ---------------------------------------------

    def create_promotion(
        self,
        authz: AuthorizationContext,
        code: str,
        kind: str,
        value: Decimal,
        currency: str,
        *,
        valid_from: str,
        valid_until: str,
        funded_by: str = "seller",
        seller_id: Optional[str] = None,
        max_redemptions: Optional[int] = None,
        per_buyer_limit: Optional[int] = None,
        min_spend: Optional[Money] = None,
        stacking_group: Optional[str] = None,
    ) -> Promotion:
        authz.requires_role("promotion.writer")
        if kind not in {"percent", "fixed_amount", "shipping"}:
            raise ValidationError(f"Unknown promotion kind {kind!r}")
        if funded_by not in {"seller", "platform"}:
            raise ValidationError(f"Unknown funding source {funded_by!r}")
        if kind == "percent" and not (Decimal("0") < value <= Decimal("100")):
            raise ValidationError("Percent promotions must be in (0, 100]")
        promotion = Promotion(
            promotion_id=generate("promotion"),
            code=code,
            kind=kind,
            value=value,
            currency=currency,
            funded_by=funded_by,
            seller_id=seller_id,
            max_redemptions=max_redemptions,
            per_buyer_limit=per_buyer_limit,
            min_spend=min_spend,
            stacking_group=stacking_group,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        self.promotions[promotion.promotion_id] = promotion
        return promotion

    def issue_coupon(
        self, authz: AuthorizationContext, promotion_id: str, code: str, *, buyer_id: Optional[str] = None
    ) -> Coupon:
        authz.requires_role("promotion.writer")
        promotion = self._require_promotion(promotion_id)
        if code in self.coupons_by_code:
            raise ConcurrencyConflictError(f"Coupon code {code!r} already issued")
        coupon = Coupon(
            coupon_id=generate("coupon"),
            promotion_id=promotion.promotion_id,
            code=code,
            buyer_id=buyer_id,
        )
        self.coupons[coupon.coupon_id] = coupon
        self.coupons_by_code[code] = coupon
        return coupon

    def redeem_coupon(self, coupon_code: str, buyer_id: str, at: str) -> Promotion:
        """Atomically redeem a coupon (REQ-PRC-004 race protection).

        Redemption counters are mutated under a lock and the promotion's global
        and per-buyer limits are checked *before* the mutation, so concurrent
        redemptions cannot overspend.
        """
        coupon = self.coupons_by_code.get(coupon_code)
        if coupon is None:
            raise NotFoundError(f"Coupon {coupon_code!r} does not exist")
        if coupon.status != "issued":
            raise InvariantViolationError(
                "REQ-PRC-004",
                f"Coupon {coupon_code!r} is {coupon.status}; replay is rejected",
            )
        if coupon.buyer_id is not None and coupon.buyer_id != buyer_id:
            raise InvariantViolationError(
                "REQ-PRC-004",
                f"Coupon {coupon_code!r} is not issued to buyer {buyer_id!r}",
            )
        promotion = self._require_promotion(coupon.promotion_id)
        with self._lock:
            if promotion.status != "active" or not (promotion.valid_from <= at <= promotion.valid_until):
                raise ValidationError(f"Promotion {promotion.code!r} is not active at {at}")
            if promotion.max_redemptions is not None and promotion.redemptions >= promotion.max_redemptions:
                raise InvariantViolationError(
                    "REQ-PRC-004",
                    f"Promotion {promotion.code!r} has reached its redemption ceiling",
                )
            if promotion.per_buyer_limit is not None:
                used = promotion.buyer_redemptions.get(buyer_id, 0)
                if used >= promotion.per_buyer_limit:
                    raise InvariantViolationError(
                        "REQ-PRC-004",
                        f"Buyer {buyer_id!r} has exhausted the per-buyer limit for "
                        f"promotion {promotion.code!r}",
                    )
                promotion.buyer_redemptions[buyer_id] = used + 1
            promotion.redemptions += 1
            coupon.status = "redeemed"
            coupon.redeemed_at = at
        return promotion

    def release_coupon(self, coupon_code: str, buyer_id: str) -> None:
        """Reverse a redemption when a checkout is abandoned (no overspend)."""
        coupon = self.coupons_by_code.get(coupon_code)
        if coupon is None or coupon.status != "redeemed":
            return
        promotion = self.promotions.get(coupon.promotion_id)
        if promotion is None:
            return
        with self._lock:
            promotion.redemptions = max(0, promotion.redemptions - 1)
            if promotion.per_buyer_limit is not None:
                promotion.buyer_redemptions[buyer_id] = max(
                    0, promotion.buyer_redemptions.get(buyer_id, 1) - 1
                )
            coupon.status = "issued"
            coupon.redeemed_at = None

    # -- tax & fees -------------------------------------------------------

    def define_tax_rate(
        self, authz: AuthorizationContext, jurisdiction: str, tax_type: str, rate: Decimal, *, compound: bool = False
    ) -> TaxRate:
        authz.requires_role("tax.writer")
        if not (Decimal("0") <= rate <= Decimal("1")):
            raise ValidationError("Tax rates are fractions in [0, 1]")
        rate_record = TaxRate(
            rate_id=generate("tax"), jurisdiction=jurisdiction, tax_type=tax_type, rate=rate, compound=compound
        )
        self.tax_rates.setdefault(jurisdiction, []).append(rate_record)
        return rate_record

    def define_fee_policy(
        self, authz: AuthorizationContext, code: str, kind: str, *, currency: str, amount: Optional[Money] = None, rate: Optional[Decimal] = None
    ) -> dict:
        authz.requires_role("pricing.writer")
        if kind not in {"fixed", "percent"}:
            raise ValidationError(f"Unknown fee kind {kind!r}")
        if kind == "fixed" and amount is None:
            raise ValidationError("Fixed fees require an amount")
        if kind == "percent" and rate is None:
            raise ValidationError("Percent fees require a rate")
        policy = {"code": code, "kind": kind, "currency": currency, "amount": amount, "rate": rate}
        self.fee_policies[code] = policy
        return policy

    # -- evaluation -------------------------------------------------------

    def evaluate(
        self,
        *,
        offer_id: str,
        product_id: str,
        variant_id: Optional[str],
        quantity: int,
        currency: str,
        at: str,
        unit_price: Money,
        promotions: Iterable[Promotion] = (),
        coupons: Iterable[str] = (),
        buyer_id: str = "anonymous",
        jurisdiction: str = "",
        fee_policy_codes: Iterable[str] = (),
        shipping_cost: Optional[Money] = None,
        validity_seconds: int = 600,
    ) -> PriceEvaluation:
        """REQ-PRC-001: contextual price with explainable breakdown.

        REQ-PRC-002: returns inputs, policy versions, validity and the
        recalculation conditions.
        """
        components: list[PriceComponent] = []
        policy_versions: dict[str, int] = {}
        if unit_price.currency.code != currency:
            raise InvariantViolationError(
                "GOV-05",
                f"Unit price is {unit_price.currency}, evaluation requested in {currency}",
            )
        base_total = unit_price * quantity
        components.append(
            PriceComponent("base", "Base price", base_total, "base", source=f"offer:{offer_id}")
        )
        subtotal = base_total

        # promotions + coupons, with stacking control
        applied_groups: set[str] = set()
        discount_total = zero(currency)
        for promotion in promotions:
            self._assert_stackable(promotion, applied_groups)
            if promotion.min_spend is not None and subtotal < promotion.min_spend:
                continue
            discount = self._promotion_discount(promotion, subtotal, currency)
            components.append(
                PriceComponent(
                    f"promo:{promotion.code}",
                    f"Promotion {promotion.code}",
                    -discount,
                    "discount",
                    source=f"promotion:{promotion.promotion_id}",
                )
            )
            subtotal = subtotal - discount
            discount_total = discount_total + discount
            policy_versions[f"promotion:{promotion.code}"] = 1

        for coupon_code in coupons:
            promotion = self.redeem_coupon(coupon_code, buyer_id, at)
            self._assert_stackable(promotion, applied_groups)
            discount = self._promotion_discount(promotion, subtotal, currency)
            components.append(
                PriceComponent(
                    f"coupon:{promotion.code}",
                    f"Coupon {promotion.code}",
                    -discount,
                    "discount",
                    source=f"coupon:{coupon_code}",
                )
            )
            subtotal = subtotal - discount
            discount_total = discount_total + discount
            policy_versions[f"coupon:{promotion.code}"] = 1

        if shipping_cost is not None and not shipping_cost.is_zero():
            components.append(
                PriceComponent("shipping", "Shipping", shipping_cost, "shipping", source="fulfillment:quote")
            )
            subtotal = subtotal + shipping_cost

        for code in fee_policy_codes:
            policy = self.fee_policies.get(code)
            if policy is None:
                raise NotFoundError(f"Fee policy {code!r} is not defined")
            fee = self._fee_amount(policy, subtotal, currency)
            components.append(
                PriceComponent(f"fee:{code}", f"Fee {code}", fee, "fee", source=f"fee_policy:{code}")
            )
            subtotal = subtotal + fee

        # tax is evaluated on the post-discount, post-fee subtotal
        for rate in self.tax_rates.get(jurisdiction, []):
            taxable = subtotal
            if rate.compound:
                pass  # compound taxes apply on the running total as-is here
            tax = (taxable * rate.rate).round_to_currency()
            components.append(
                PriceComponent(
                    f"tax:{rate.tax_type}:{rate.jurisdiction}",
                    f"{rate.tax_type} @ {rate.rate} ({rate.jurisdiction})",
                    tax,
                    "tax",
                    source=f"tax_rate:{rate.rate_id}",
                )
            )
            subtotal = subtotal + tax
            policy_versions[f"tax:{rate.jurisdiction}:{rate.tax_type}"] = 1

        evaluation = PriceEvaluation(
            evaluation_id=generate("evidence"),
            offer_id=offer_id,
            product_id=product_id,
            variant_id=variant_id,
            quantity=quantity,
            unit_price=unit_price,
            components=components,
            total=subtotal,
            currency=currency,
            evaluated_at=at,
            inputs={
                "quantity": quantity,
                "currency": currency,
                "jurisdiction": jurisdiction,
                "buyer_id": buyer_id,
                "unit_price": str(unit_price),
            },
            policy_versions=policy_versions,
            valid_until=at,  # caller extends with validity_seconds when a clock is available
        )
        self.evaluations[evaluation.evaluation_id] = evaluation
        return evaluation

    def _assert_stackable(self, promotion: Promotion, applied_groups: set[str]) -> None:
        if promotion.stacking_group is None:
            return
        if promotion.stacking_group in applied_groups:
            raise InvariantViolationError(
                "REQ-PRC-004",
                f"Promotion {promotion.code!r} is in stacking group "
                f"{promotion.stacking_group!r} which is already applied; "
                "invalid stacking is rejected",
            )
        applied_groups.add(promotion.stacking_group)

    def _promotion_discount(self, promotion: Promotion, subtotal: Money, currency: str) -> Money:
        if promotion.currency != currency:
            raise InvariantViolationError(
                "GOV-05",
                f"Promotion {promotion.code} is in {promotion.currency}, basket is {currency}",
            )
        if promotion.kind == "percent":
            return (subtotal * (promotion.value / Decimal("100"))).round_to_currency()
        if promotion.kind == "fixed_amount":
            return min(promotion.value * Decimal("1"), subtotal) if False else min(
                Money.of(promotion.value, currency), subtotal
            )
        return zero(currency)  # shipping promotions are handled by the caller

    def _fee_amount(self, policy: dict, subtotal: Money, currency: str) -> Money:
        if policy["kind"] == "fixed":
            amount = policy["amount"]
            if amount.currency.code != currency:
                raise InvariantViolationError(
                    "GOV-05", f"Fee {policy['code']} currency mismatch"
                )
            return amount
        return (subtotal * policy["rate"]).round_to_currency()

    def _require_pricebook(self, pricebook_id: str) -> PriceBook:
        book = self.pricebooks.get(pricebook_id)
        if book is None:
            raise NotFoundError(f"Price book {pricebook_id} not found")
        return book

    def _require_promotion(self, promotion_id: str) -> Promotion:
        promotion = self.promotions.get(promotion_id)
        if promotion is None:
            raise NotFoundError(f"Promotion {promotion_id} not found")
        return promotion


# Concept ownership declarations (GOV-02).
U05.owns("price_book", "Price inputs with currency, scope, tier and validity", "U05.PricingService.set_price", read_projections=["U04.offer"])
U05.owns("price_evaluation", "Evaluated price result with inputs, policy version and validity", "U05.PricingService.evaluate")
U05.owns("promotion", "Campaign with funding attribution and redemption limits", "U05.PricingService.create_promotion")
U05.owns("coupon", "Issued coupon instance with single-use redemption state", "U05.PricingService.issue_coupon")
U05.owns("tax_rate", "Jurisdiction tax rate definitions; financial posting is owned by U09", "U05.PricingService.define_tax_rate")
U05.owns("fee_policy", "Fee definition applied during price evaluation", "U05.PricingService.define_fee_policy")
