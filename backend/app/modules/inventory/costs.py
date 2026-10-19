"""Exact unit costs shared by counts and purchase receipts."""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator

from app.modules.inventory.valuation import MAX_VALUE


def exact_unit_cost(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError("Cost must be an exact decimal")
    if isinstance(value, str) and (not value or len(value) > 64):
        raise ValueError("Invalid cost representation")
    try:
        cost = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid cost") from None
    if (
        not cost.is_finite()
        or cost.as_tuple().exponent < -6
        or not 0 <= cost <= MAX_VALUE
    ):
        raise ValueError("Cost outside exact NUMERIC(28,6) bounds")
    return cost


UnitCost = Annotated[Decimal, BeforeValidator(exact_unit_cost)]
