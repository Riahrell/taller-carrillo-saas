"""Pure stock valuation; authorization and atomic persistence belong to services."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from pydantic import TypeAdapter

from app.modules.inventory.quantities import MovementQuantity, StockCount

_COUNT = TypeAdapter(StockCount)
_MOVEMENT = TypeAdapter(MovementQuantity)
_SCALE = Decimal("0.000001")
MAX_VALUE = Decimal("9999999999999999999999.999999")


def _value(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Value must be a finite Decimal")
    if value.as_tuple().exponent < -6 or not 0 <= value <= MAX_VALUE:
        raise ValueError("Value is outside exact NUMERIC(28,6) bounds")
    return value


@dataclass(frozen=True)
class StockValue:
    quantity: Decimal
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _COUNT.validate_python(self.quantity))
        _value(self.value)
        if self.quantity == 0 and self.value != 0:
            raise ValueError("Empty stock cannot retain value")

    def receive(self, quantity: Decimal, unit_cost: Decimal) -> "StockValue":
        amount = _MOVEMENT.validate_python(quantity)
        cost = _value(unit_cost)
        # Do not inherit caller precision, rounding or Inexact traps. Operands
        # are bounded above before arithmetic; 80 digits cover their products.
        with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
            added = (amount * cost).quantize(_SCALE)
            return StockValue(self.quantity + amount, self.value + added)

    def withdraw(self, quantity: Decimal) -> "StockValue":
        amount = _MOVEMENT.validate_python(quantity)
        if amount > self.quantity:
            raise ValueError("Insufficient stock")
        if amount == self.quantity:
            return StockValue(Decimal(0), Decimal(0))
        with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
            # Round the applied total once, without rounding a unit average first.
            removed = (self.value * amount / self.quantity).quantize(_SCALE)
            return StockValue(self.quantity - amount, self.value - removed)

    def recount(
        self, counted: Decimal, *, declared_unit_cost: Decimal | None = None
    ) -> "StockValue":
        """Value a physical count against the server's locked current balance."""
        final_quantity = _COUNT.validate_python(counted)
        if final_quantity <= self.quantity:
            if declared_unit_cost is not None:
                raise ValueError("Declared cost does not apply to this count")
            if final_quantity == self.quantity:
                return self
            with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
                return self.withdraw(self.quantity - final_quantity)
        with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
            difference = final_quantity - self.quantity
            if self.quantity > 0 and self.value > 0:
                if declared_unit_cost is not None:
                    raise ValueError("Count must use existing historical valuation")
                added = (self.value * difference / self.quantity).quantize(_SCALE)
                return StockValue(final_quantity, self.value + added)
            if declared_unit_cost is None:
                raise ValueError("Positive count requires an explicit unit cost")
            return self.receive(difference, declared_unit_cost)
