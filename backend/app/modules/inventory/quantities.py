"""Exact NUMERIC(18,3) inputs, without implicit rounding or float conversion."""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, Field

MAX_QUANTITY = Decimal("999999999999999.999")


def _exact_quantity(value: object) -> Decimal:
    # JSON clients must send decimal strings; integers are also exact. A float
    # has already lost the original representation, even if it looks integral.
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError("Quantity must be an exact decimal")
    if isinstance(value, str) and (not value or len(value) > 64):
        raise ValueError("Quantity representation is too long or empty")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid quantity") from None
    if not parsed.is_finite():
        raise ValueError("Quantity must be finite")
    if parsed.as_tuple().exponent < -3:
        raise ValueError("Quantity allows at most three decimal places")
    if parsed < 0 or parsed > MAX_QUANTITY:
        raise ValueError("Quantity is outside NUMERIC(18,3) bounds")
    return parsed


# The movement direction belongs to its authorized operation, never its sign.
# A physical count may be zero; a stock entry/withdrawal must be positive.
StockCount = Annotated[Decimal, BeforeValidator(_exact_quantity)]
MovementQuantity = Annotated[Decimal, Field(gt=0), BeforeValidator(_exact_quantity)]
