"""Cashier-only, explicitly granted withdrawals with a financial-data-free response."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.database import create_session_factory
from app.modules.access.inventory_permissions import authorize_inventory_withdrawal
from app.modules.inventory.inputs import ManualStockInput, PhysicalCount
from app.modules.inventory.manual import record_manual
from app.modules.inventory.quantities import MovementQuantity


class WithdrawalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    product_id: UUID
    quantity: MovementQuantity
    reason: str = Field(min_length=1, max_length=500, strict=True)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        return PhysicalCount.meaningful_reason(value)


class WithdrawalView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    product_id: UUID
    operation_key: UUID
    before_quantity: Decimal
    after_quantity: Decimal
    quantity_delta: Decimal
    reason: str
    created_at: datetime
    actor_membership_id: UUID = Field(exclude=True, repr=False)

    @field_serializer(
        "before_quantity", "after_quantity", "quantity_delta", when_used="json"
    )
    def quantity_json(self, value):
        return format(value, ".3f")


class InventoryWithdrawals:
    def __init__(self, engine):
        self._factory = create_session_factory(engine)

    @staticmethod
    def _authorize(session, token, expected_actor, *, recent=True):
        return authorize_inventory_withdrawal(session, token, expected_actor)

    def record(self, token, *, data, operation_key, expected_actor):
        if type(data) is not WithdrawalInput:
            from app.modules.inventory.service import InventoryDenied

            raise InventoryDenied("Access denied")
        data = WithdrawalInput.model_validate(data.model_dump())
        result = record_manual(
            self,
            token,
            data=ManualStockInput(
                product_id=data.product_id,
                direction="OUT",
                quantity=data.quantity,
                reason=data.reason,
            ),
            operation_key=operation_key,
            expected_actor=expected_actor,
            actor_bound=True,
        )
        # Never serialize the internal accounting view into the operational API.
        return WithdrawalView(
            **{key: getattr(result, key) for key in WithdrawalView.model_fields}
        )
