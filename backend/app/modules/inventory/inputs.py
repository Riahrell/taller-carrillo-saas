"""Desired physical count; prior stock, tenant and valuation remain server-owned."""

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.inventory.costs import UnitCost
from app.modules.inventory.quantities import MovementQuantity, StockCount


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


class ManualStockInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    product_id: UUID
    direction: Literal["IN", "OUT"]
    quantity: MovementQuantity
    reason: str = Field(min_length=1, max_length=500, strict=True)
    unit_cost: UnitCost | None = None

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        return PhysicalCount.meaningful_reason(value)

    @model_validator(mode="after")
    def directional_cost(self):
        if (self.direction == "IN") != (self.unit_cost is not None):
            raise ValueError("Entries require cost; withdrawals use server valuation")
        return self


class HistoryPageQuery(CountPageQuery):
    after: str | None = Field(default=None, strict=True, max_length=44)

    @field_validator("after")
    @classmethod
    def typed_cursor(cls, value: str | None) -> str | None:
        if value is not None:
            match = re.fullmatch(r"(COUNT|MANUAL|RECEIPT):([0-9a-f-]{36})", value)
            if match is None or str(UUID(match[2])) != match[2]:
                raise ValueError("Invalid history cursor")
        return value
