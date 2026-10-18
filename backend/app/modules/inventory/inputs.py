"""Desired physical count; prior stock, tenant and valuation remain server-owned."""

from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.inventory.quantities import StockCount
from app.modules.inventory.valuation import MAX_VALUE


class PhysicalCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    product_id: UUID
    counted_quantity: StockCount
    reason: str = Field(min_length=1, max_length=500, strict=True)
    declared_unit_cost: Decimal | None = None

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Invalid count reason")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid count reason") from None
        return value

    @field_validator("declared_unit_cost", mode="before")
    @classmethod
    def exact_declared_cost(cls, value: object) -> Decimal | None:
        if value is None:
            return None
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
