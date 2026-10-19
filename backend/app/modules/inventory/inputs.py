"""Desired physical count; prior stock, tenant and valuation remain server-owned."""

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.inventory.costs import UnitCost
from app.modules.inventory.quantities import StockCount


class PhysicalCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    product_id: UUID
    counted_quantity: StockCount
    reason: str = Field(min_length=1, max_length=500, strict=True)
    declared_unit_cost: UnitCost | None = None

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


class CountPageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    limit: int = Field(default=25, ge=1, le=100, strict=True)
    after: UUID | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def canonical_limit(cls, value: object) -> object:
        if isinstance(value, str):
            if re.fullmatch(r"[1-9][0-9]{0,2}", value, flags=re.ASCII) is None:
                raise ValueError("Invalid page limit")
            return int(value)
        return value
