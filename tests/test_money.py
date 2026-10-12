"""Kernel money primitives: currency isolation, Decimal-only, allocation."""

from decimal import Decimal

import pytest

from marketplace.kernel.errors import InvariantViolationError
from marketplace.kernel.money import (
    Currency,
    CurrencyMismatch,
    Money,
    get_currency,
    register_currency,
    sum_money,
    zero,
)

pytestmark = pytest.mark.unit


class TestMoneyConstruction:
    def test_of_quantizes_to_currency_exponent(self):
        m = Money.of("10.005", "USD")
        assert m.amount == Decimal("10.00")  # ROUND_HALF_EVEN: 10.005 -> 10.00

    def test_of_accepts_decimal_and_str(self):
        assert Money.of(Decimal("1.5"), "USD") == Money.of("1.50", "USD")

    def test_float_amount_rejected(self):
        with pytest.raises(TypeError):
            Money(1.5, get_currency("USD"))

    def test_currency_must_be_iso_shape(self):
        with pytest.raises(ValueError):
            Currency("us", 2)
        with pytest.raises(ValueError):
            Currency("US1", 2)

    def test_unknown_currency_rejected(self):
        with pytest.raises(ValueError):
            get_currency("XYZ")

    def test_duplicate_registration_rejected(self):
        with pytest.raises(ValueError):
            register_currency(Currency("USD", 2))

    def test_jpy_has_zero_minor_units(self):
        assert get_currency("JPY").minor_units == 0
        assert Money.of("100", "JPY").amount == Decimal("100")


class TestCurrencyIsolation:
    """NFR-025: incompatible currencies are never silently combined."""

    def test_add_rejects_mixed_currencies(self):
        with pytest.raises(CurrencyMismatch):
            Money.of(1, "USD") + Money.of(1, "EUR")

    def test_sub_rejects_mixed_currencies(self):
        with pytest.raises(CurrencyMismatch):
            Money.of(1, "USD") - Money.of(1, "EUR")

    def test_comparison_rejects_mixed_currencies(self):
        with pytest.raises(CurrencyMismatch):
            Money.of(1, "USD") < Money.of(2, "EUR")

    def test_money_times_money_rejected(self):
        with pytest.raises(TypeError):
            Money.of(1, "USD") * Money.of(2, "USD")

    def test_convert_to_needs_explicit_positive_rate(self):
        with pytest.raises(ValueError):
            Money.of(100, "USD").convert_to(Decimal("0"), "EUR")
        assert Money.of(100, "USD").convert_to(Decimal("0.9"), "EUR") == Money.of(90, "EUR")

    def test_sum_money_rejects_mixed_list(self):
        with pytest.raises(CurrencyMismatch):
            sum_money([Money.of(1, "USD"), Money.of(1, "EUR")])

    def test_sum_money_requires_nonempty(self):
        with pytest.raises(ValueError):
            sum_money([])

    def test_zero_has_explicit_currency(self):
        assert zero("USD").currency == get_currency("USD")
        assert zero("USD").is_zero()


class TestArithmetic:
    def test_add_stays_quantized(self):
        assert (Money.of("0.1", "USD") + Money.of("0.2", "USD")) == Money.of("0.30", "USD")

    def test_sub_can_be_negative(self):
        assert (Money.of(1, "USD") - Money.of(2, "USD")).is_negative()

    def test_division_by_zero_rejected(self):
        with pytest.raises(ZeroDivisionError):
            Money.of(1, "USD") / 0

    def test_money_division_is_a_rate(self):
        assert Money.of(10, "USD").ratio(Money.of(4, "USD")) == Decimal("2.5")

    def test_scalar_multiplication(self):
        assert Money.of(10, "USD") * 3 == Money.of(30, "USD")
        assert 3 * Money.of(10, "USD") == Money.of(30, "USD")

    def test_hashable(self):
        assert hash(Money.of(1, "USD")) == hash(Money.of(1, "USD"))
        assert len({Money.of(1, "USD"), Money.of(1, "USD"), Money.of(2, "USD")}) == 2


class TestAllocation:
    """Allocation must be lossless: parts sum to exactly the original."""

    def test_allocate_sums_exactly(self):
        total = Money.of("100.00", "USD")
        parts = total.allocate([Decimal("1"), Decimal("1"), Decimal("1")])
        assert sum_money(parts) == total

    def test_allocate_uneven_ratios_sum_exactly(self):
        total = Money.of("100.00", "USD")
        parts = total.allocate([Decimal("1"), Decimal("2"), Decimal("3")])
        assert sum_money(parts) == total

    def test_allocate_single_part(self):
        total = Money.of("99.99", "USD")
        assert total.allocate([Decimal("7")]) == [total]

    def test_allocate_empty_ratios(self):
        assert Money.of(10, "USD").allocate([]) == []

    def test_allocate_rejects_zero_total(self):
        with pytest.raises(ValueError):
            Money.of(10, "USD").allocate([Decimal("0")])

    def test_allocate_penny_split(self):
        total = Money.of("0.03", "USD")
        parts = total.allocate([Decimal("1"), Decimal("1"), Decimal("1")])
        assert sum_money(parts) == total
        assert all(p.amount == Decimal("0.01") for p in parts)


class TestRoundTrip:
    def test_dict_roundtrip(self):
        m = Money.of("123.45", "USD")
        assert Money.from_dict(m.to_dict()) == m

    def test_invariant_error_is_money_aware(self):
        assert issubclass(CurrencyMismatch, ValueError)
        assert issubclass(InvariantViolationError, Exception)
