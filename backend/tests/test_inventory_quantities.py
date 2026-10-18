"""Boundary cases that would otherwise round, overflow or invert stock effects."""

from decimal import Decimal, localcontext

import pytest
from pydantic import TypeAdapter, ValidationError

from app.modules.inventory.quantities import MovementQuantity, StockCount

count = TypeAdapter(StockCount)
movement = TypeAdapter(MovementQuantity)


@pytest.mark.parametrize(
    "value", ["0.001", "12.345", 1, Decimal("0.125"), "999999999999999.999"]
)
def test_exact_positive_quantity_survives_validation_and_json(value):
    result = movement.validate_python(value)
    assert result == Decimal(value)
    assert isinstance(result, Decimal)
    assert movement.validate_json(movement.dump_json(result)) == result


@pytest.mark.parametrize("value", [0, "0", "0.000", Decimal("0")])
def test_zero_is_a_valid_count_but_not_a_movement(value):
    assert count.validate_python(value) == 0
    with pytest.raises(ValidationError):
        movement.validate_python(value)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        1.0,
        0.1,
        None,
        [],
        {},
        "",
        "invalid",
        "NaN",
        "Infinity",
        "-Infinity",
        "-0.001",
        "1.0000",
        "0.0001",
        "1000000000000000",
        "1e100000",
        "1e-100000",
        "1" * 65,
    ],
)
def test_invalid_quantity_is_rejected_without_rounding(value):
    with pytest.raises(ValidationError):
        count.validate_python(value)


def test_json_float_is_rejected_instead_of_converted_to_decimal():
    with pytest.raises(ValidationError):
        movement.validate_json("0.1")
    assert movement.validate_json('"0.1"') == Decimal("0.1")


def test_validation_does_not_depend_on_process_decimal_precision():
    with localcontext() as context:
        context.prec = 2
        assert count.validate_python("999999999999999.999") == Decimal(
            "999999999999999.999"
        )
