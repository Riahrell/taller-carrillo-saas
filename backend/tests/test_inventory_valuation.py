from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from app.modules.inventory.valuation import MAX_VALUE, StockValue

D = Decimal


def test_receipts_use_weighted_value_and_outputs_keep_historical_cost():
    first = StockValue(D(0), D(0)).receive(D(2), D(3))
    mixed = first.receive(D(1), D(6))
    remaining = mixed.withdraw(D(1))
    assert remaining == StockValue(D(2), D(8))
    assert mixed.value - remaining.value == D(4)
    assert remaining.receive(D(1), D(20)).value == D(28)
    assert mixed.value - remaining.value == D(4)


def test_fractional_outputs_preserve_value_and_full_output_removes_dust():
    original = StockValue(D(3), D(1))
    second = original.withdraw(D(1))
    third = second.withdraw(D(1))
    empty = third.withdraw(D(1))
    assert second.value == D("0.666667")
    assert third.value == D("0.333333")
    assert empty == StockValue(D(0), D(0))
    assert (
        sum(
            (
                original.value - second.value,
                second.value - third.value,
                third.value - empty.value,
            )
        )
        == original.value
    )


def test_half_up_and_ambient_decimal_context_are_independent():
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        received = StockValue(D(0), D(0)).receive(D("0.001"), D("0.000500"))
        remaining = StockValue(D(2), D("0.000001")).withdraw(D(1))
    assert received.value == D("0.000001")
    assert remaining.value == 0


@pytest.mark.parametrize("quantity", [D(0), D(-1), D("1.0001"), 1.5, True])
def test_invalid_movement_quantities(quantity):
    balance = StockValue(D(1), D(1))
    with pytest.raises(ValueError):
        balance.withdraw(quantity)
    with pytest.raises(ValueError):
        balance.receive(quantity, D(1))


@pytest.mark.parametrize(
    "value", [D(-1), D("NaN"), D("Infinity"), D("0.0000001"), 1.0, True]
)
def test_invalid_value_or_unit_cost(value):
    with pytest.raises(ValueError):
        StockValue(D(1), value)
    with pytest.raises(ValueError):
        StockValue(D(1), D(1)).receive(D(1), value)


def test_negative_stock_and_capacity_overflow_rejected_without_mutation():
    balance = StockValue(D(1), MAX_VALUE)
    with pytest.raises(ValueError):
        balance.withdraw(D(2))
    with pytest.raises(ValueError):
        balance.receive(D(1), D(1))
    with pytest.raises(ValueError):
        StockValue(D("999999999999999.999"), D(0)).receive(D("0.001"), D(0))
    assert balance == StockValue(D(1), MAX_VALUE)


def test_zero_value_requires_explicit_cost_and_empty_stock_has_no_value():
    assert StockValue(D(0), D(0)).receive(D(1), D(0)) == StockValue(D(1), D(0))
    with pytest.raises(ValueError):
        StockValue(D(0), D("0.000001"))


def test_maximum_value_partial_withdrawal_preserves_six_decimal_precision():
    balance = StockValue(D(2), MAX_VALUE)
    remaining = balance.withdraw(D(1))
    assert remaining.value == D("4999999999999999999999.999999")
    assert remaining.withdraw(D(1)) == StockValue(D(0), D(0))


def test_receipt_product_overflow_is_rejected_before_returning_balance():
    with pytest.raises(ValueError):
        StockValue(D(0), D(0)).receive(D(2), MAX_VALUE)
