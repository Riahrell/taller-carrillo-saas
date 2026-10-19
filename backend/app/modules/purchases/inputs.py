"""Desired purchase data, without client-owned totals, stock or authority."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.inventory.costs import UnitCost
from app.modules.inventory.quantities import MovementQuantity

MAX_PURCHASE_LINES = 100


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
