"""Idempotent exact compensation of the last count/manual movement only."""

import hashlib
import json
from datetime import datetime
from decimal import Context, Decimal, localcontext
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.authentication import _limit_transaction
from app.modules.access.sessions import _database_now
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import StockReversalInput
from app.modules.inventory.models import (
    ManualStockRecord,
    StockBalance,
    StockCountRecord,
    StockMovement,
    StockReversalRecord,
)
from app.modules.inventory.service import (
    InventoryConflict,
    InventoryDenied,
    InventoryInvalid,
    InventoryUnavailable,
)


class ReversalView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    product_id: UUID
    operation_key: UUID
    original_source: str
    original_id: UUID
    reason: str
    before_quantity: Decimal
    after_quantity: Decimal
    before_value: Decimal
    after_value: Decimal
    quantity_delta: Decimal
    value_delta: Decimal
    created_at: datetime
    actor_membership_id: UUID = Field(exclude=True, repr=False)

    @field_serializer(
        "before_quantity", "after_quantity", "quantity_delta", when_used="json"
    )
    def quantity_json(self, value):
        return format(value, ".3f")

    @field_serializer("before_value", "after_value", "value_delta", when_used="json")
    def value_json(self, value):
        return format(value, ".6f")


def view(record, actor):
    return ReversalView(
        **{
            key: getattr(record, key)
            for key in ReversalView.model_fields
            if key != "actor_membership_id"
        },
        actor_membership_id=actor,
    )


def reverse(
    service,
    token,
    *,
    data: StockReversalInput,
    operation_key: UUID,
    expected_actor: UUID,
):
    if type(data) is not StockReversalInput or not isinstance(operation_key, UUID):
        raise InventoryDenied("Access denied")
    data = StockReversalInput.model_validate(data.model_dump())
    fingerprint = hashlib.sha256(
        json.dumps(
            {"kind": "stock_reversal_v1", **data.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    try:
        with service._factory.begin() as session:
            _limit_transaction(session)
            actor = service._authorize(session, token, expected_actor)
            previous = session.scalar(
                select(StockReversalRecord).where(
                    StockReversalRecord.tenant_id == actor.tenant_id,
                    StockReversalRecord.operation_key == operation_key,
                )
            )
            if previous is not None:
                if previous.request_fingerprint != fingerprint:
                    raise InventoryConflict("Operation key conflict")
                service._authorize(session, token, expected_actor)
                result = view(previous, actor.membership_id)
            else:
                product = session.scalar(
                    select(Product)
                    .where(
                        Product.id == data.product_id,
                        Product.tenant_id == actor.tenant_id,
                        Product.is_active.is_(True),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if product is None:
                    raise InventoryDenied("Access denied")
                model = (
                    StockCountRecord if data.source == "COUNT" else ManualStockRecord
                )
                original = session.scalar(
                    select(model).where(
                        model.tenant_id == actor.tenant_id,
                        model.product_id == product.id,
                        model.id == data.original_id,
                    )
                )
                if original is None:
                    raise InventoryDenied("Access denied")
                source_column = (
                    StockMovement.count_id
                    if data.source == "COUNT"
                    else StockMovement.manual_record_id
                )
                movement = session.scalar(
                    select(StockMovement).where(
                        StockMovement.tenant_id == actor.tenant_id,
                        StockMovement.product_id == product.id,
                        source_column == original.id,
                    )
                )
                if movement is None:
                    raise InventoryInvalid("No stock movement to reverse")
                # Fail closed on equal timestamps too; never guess ordering.
                later = session.scalar(
                    select(StockMovement.id)
                    .where(
                        StockMovement.tenant_id == actor.tenant_id,
                        StockMovement.product_id == product.id,
                        StockMovement.id != movement.id,
                        StockMovement.created_at >= movement.created_at,
                    )
                    .limit(1)
                )
                used = session.scalar(
                    select(StockReversalRecord.id).where(
                        StockReversalRecord.original_movement_id == movement.id
                    )
                )
                balance = session.scalar(
                    select(StockBalance)
                    .where(
                        StockBalance.tenant_id == actor.tenant_id,
                        StockBalance.product_id == product.id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    later is not None
                    or used is not None
                    or balance is None
                    or balance.quantity != original.after_quantity
                    or balance.value != original.after_value
                ):
                    raise InventoryInvalid(
                        "Only unchanged last movement can be reversed"
                    )
                with localcontext(Context(prec=80)):
                    quantity_delta, value_delta = (
                        -movement.quantity_delta,
                        -movement.value_delta,
                    )
                record = StockReversalRecord(
                    tenant_id=actor.tenant_id,
                    product_id=product.id,
                    actor_membership_id=actor.membership_id,
                    operation_key=operation_key,
                    request_fingerprint=fingerprint,
                    original_movement_id=movement.id,
                    original_source=data.source,
                    original_id=data.original_id,
                    original_quantity_delta=movement.quantity_delta,
                    original_value_delta=movement.value_delta,
                    reason=data.reason,
                    before_quantity=balance.quantity,
                    before_value=balance.value,
                    after_quantity=original.before_quantity,
                    after_value=original.before_value,
                    quantity_delta=quantity_delta,
                    value_delta=value_delta,
                    created_at=_database_now(session),
                )
                session.add(record)
                session.flush()
                session.add(
                    StockMovement(
                        tenant_id=actor.tenant_id,
                        product_id=product.id,
                        reversal_id=record.id,
                        quantity_delta=quantity_delta,
                        value_delta=value_delta,
                        created_at=record.created_at,
                    )
                )
                balance.quantity, balance.value = (
                    record.after_quantity,
                    record.after_value,
                )
                session.flush()
                service._authorize(session, token, expected_actor)
                result = view(record, actor.membership_id)
        return result
    except SQLAlchemyError:
        raise InventoryUnavailable("Inventory unavailable") from None
