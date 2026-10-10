"""Canonical money arithmetic.

Implements LAW-ARCH financial precision requirements and REQ-PRC-001:
  * currency, unit, rounding, precision, aggregation and temporal semantics are
    explicit;
  * all arithmetic uses ``decimal.Decimal`` — never uncontrolled binary
    floating point;
  * incompatible currencies are never silently combined.

Rounding defaults to ROUND_HALF_EVEN (banker's rounding), the conventional
accounting default. Money objects are immutable and hashable so they may be
used as snapshot values inside order/ledger records.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass

# The canonical exponent for stored monetary units: 2 decimal places for
# currency units. Sub-cent precision is retained only inside rate math and is
# never persisted as a monetary amount.
MONEY_EXPONENT = decimal.Decimal("0.01")
ROUNDING = decimal.ROUND_HALF_EVEN


class CurrencyMismatch(ValueError):
    """Raised when an operation combines two different currencies (INV/financial)."""


@dataclass(frozen=True)
class Currency:
    """An ISO-4217-style currency code with the minor-unit exponent it uses."""

    code: str
    minor_units: int = 2

    def __post_init__(self) -> None:
        if not (self.code.isalpha() and len(self.code) == 3 and self.code.isupper()):
            raise ValueError(f"Currency codes are 3 uppercase letters, got {self.code!r}")
        if not 0 <= self.minor_units <= 4:
            raise ValueError(f"minor_units out of range: {self.minor_units}")

    @property
    def quantum(self) -> decimal.Decimal:
        return decimal.Decimal(1).scaleb(-self.minor_units)

    def __str__(self) -> str:
        return self.code


# A small governed currency registry. Additional currencies are registered
# through ``register_currency`` rather than hardcoding (GOV-07).
_CURRENCIES: dict[str, Currency] = {}


def register_currency(currency: Currency) -> Currency:
    if currency.code in _CURRENCIES:
        raise ValueError(f"Currency already registered: {currency.code}")
    _CURRENCIES[currency.code] = currency
    return currency


for _code, _minor in (
    ("USD", 2),
    ("EUR", 2),
    ("GBP", 2),
    ("INR", 2),
    ("JPY", 0),
    ("AED", 2),
    ("SGD", 2),
    ("AUD", 2),
    ("CAD", 2),
):
    register_currency(Currency(_code, _minor))


def get_currency(code: str) -> Currency:
    if code not in _CURRENCIES:
        raise ValueError(f"Unknown currency {code!r}; register it before use")
    return _CURRENCIES[code]


@dataclass(frozen=True)
class Money:
    """An amount of money in exactly one currency."""

    amount: decimal.Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, decimal.Decimal):
            raise TypeError("Money.amount must be decimal.Decimal; float is forbidden")
        if not isinstance(self.currency, Currency):
            raise TypeError("Money.currency must be a Currency instance")

    # -- construction ----------------------------------------------------

    @classmethod
    def of(cls, amount, currency: str | Currency) -> "Money":
        currency = get_currency(currency) if isinstance(currency, str) else currency
        quantized = decimal.Decimal(str(amount)).quantize(currency.quantum, rounding=ROUNDING)
        return cls(quantized, currency)

    def _quantized(self, value: decimal.Decimal) -> decimal.Decimal:
        return value.quantize(self.currency.quantum, rounding=ROUNDING)

    # -- arithmetic ------------------------------------------------------

    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} and {other.currency}; "
                "convert explicitly via convert_to"
            )

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self._quantized(self.amount + other.amount), self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self._quantized(self.amount - other.amount), self.currency)

    def __mul__(self, factor) -> "Money":
        if isinstance(factor, Money):
            raise TypeError("Money * Money is not a monetary operation")
        return Money(self._quantized(self.amount * decimal.Decimal(str(factor))), self.currency)

    __rmul__ = __mul__

    def __truediv__(self, divisor) -> "Money":
        if isinstance(divisor, Money):
            raise TypeError("Money / Money is a rate, not Money; use ratio()")
        if decimal.Decimal(str(divisor)) == 0:
            raise ZeroDivisionError("cannot divide money by zero")
        return Money(self._quantized(self.amount / decimal.Decimal(str(divisor))), self.currency)

    def ratio(self, other: "Money") -> decimal.Decimal:
        """Money / Money -> dimensionless rate, unrounded."""
        self._check(other)
        if other.amount == 0:
            raise ZeroDivisionError("ratio with zero denominator")
        return self.amount / other.amount

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __abs__(self) -> "Money":
        return Money(abs(self.amount), self.currency)

    # -- comparison ------------------------------------------------------

    def _cmp(self, other: object) -> int:
        if not isinstance(other, Money):
            return NotImplemented  # type: ignore[return-value]
        self._check(other)
        return (self.amount > other.amount) - (self.amount < other.amount)

    def __lt__(self, other: object) -> bool:
        r = self._cmp(other)
        return r is not NotImplemented and r < 0  # type: ignore[operator]

    def __le__(self, other: object) -> bool:
        r = self._cmp(other)
        return r is not NotImplemented and r <= 0  # type: ignore[operator]

    def __gt__(self, other: object) -> bool:
        r = self._cmp(other)
        return r is not NotImplemented and r > 0  # type: ignore[operator]

    def __ge__(self, other: object) -> bool:
        r = self._cmp(other)
        return r is not NotImplemented and r >= 0  # type: ignore[operator]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.currency == other.currency and self.amount == other.amount

    def __hash__(self) -> int:
        return hash((self.amount, self.currency))

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_negative(self) -> bool:
        return self.amount < 0

    # -- allocation & rounding -------------------------------------------

    def allocate(self, ratios: list[decimal.Decimal]) -> list["Money"]:
        """Split this money across ``ratios`` with exact remainder distribution.

        The largest-remainder method guarantees the parts sum to exactly the
        original amount, which is mandatory for payout splits and tax
        allocation (no rounding leakage).
        """
        if not ratios:
            return []
        total = sum(ratios, decimal.Decimal(0))
        if total <= 0:
            raise ValueError("allocation ratios must sum to a positive value")
        unit = self.currency.quantum
        total_units = int((self.amount / unit).to_integral_value(rounding=ROUNDING))
        ratio_units = [int(total_units * (r / total)) for r in ratios]
        remainder = total_units - sum(ratio_units)
        if remainder:
            # distribute the lost units to the largest fractional remainders
            fracs = sorted(
                range(len(ratios)),
                key=lambda i: (total_units * (ratios[i] / total) - ratio_units[i], ratios[i]),
                reverse=True,
            )
            for i in fracs[:remainder]:
                ratio_units[i] += 1
        return [Money(decimal.Decimal(u) * unit, self.currency) for u in ratio_units]

    def round_to_currency(self) -> "Money":
        return Money(self._quantized(self.amount), self.currency)

    def convert_to(self, rate: decimal.Decimal, target: str | Currency) -> "Money":
        """Convert to another currency at an explicitly supplied exchange rate.

        The rate is caller-supplied and provenance-tracked by the caller; this
        function never invents a rate.
        """
        target = get_currency(target) if isinstance(target, str) else target
        if rate <= 0:
            raise ValueError("exchange rates must be positive")
        converted = self.amount * rate
        return Money(converted.quantize(target.quantum, rounding=ROUNDING), target)

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {"amount": str(self.amount), "currency": self.currency.code}

    @classmethod
    def from_dict(cls, data: dict) -> "Money":
        return cls.of(decimal.Decimal(data["amount"]), data["currency"])

    def __str__(self) -> str:
        return f"{self.currency} {self.amount}"

    def __repr__(self) -> str:
        return f"Money({self.amount!r}, {self.currency.code!r})"


def sum_money(values: list[Money]) -> Money:
    """Aggregate a homogeneous list of money; empty sums to zero of that currency.

    Requires at least one value so the currency is unambiguous; mixing
    currencies raises ``CurrencyMismatch``.
    """
    if not values:
        raise ValueError("sum_money requires at least one Money value")
    total = values[0]
    for v in values[1:]:
        total = total + v
    return total


def zero(currency: str | Currency) -> Money:
    return Money.of(0, currency)
