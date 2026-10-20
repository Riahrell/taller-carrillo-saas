"""Desired purchase data, without client-owned totals, stock or authority."""

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.inventory.costs import UnitCost
from app.modules.inventory.quantities import MovementQuantity

MAX_PURCHASE_LINES = 100


class SupplierCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=200, strict=True)

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Invalid supplier name")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid supplier name") from None
        return value


class PurchaseLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    product_id: UUID
    quantity: MovementQuantity
    unit_cost: UnitCost


class PurchaseCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    supplier_id: UUID
    lines: tuple[PurchaseLine, ...] = Field(min_length=1, max_length=MAX_PURCHASE_LINES)

    @model_validator(mode="after")
    def unique_products(self) -> "PurchaseCreation":
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("A product must appear only once per purchase")
        return self


class PurchasePageQuery(BaseModel):
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


class ReceiptConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
