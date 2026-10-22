"""Manual deltas, stock evidence and valuation in one authorized transaction."""

import hashlib
import json
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Literal
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.authentication import _limit_transaction
from app.modules.access.membership import AccessDenied
from app.modules.access.sessions import _database_now
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import CountPageQuery, ManualStockInput
from app.modules.inventory.models import ManualStockRecord, StockBalance, StockMovement
from app.modules.inventory.service import (
    CountPage,
    CountView,
    InventoryConflict,
    InventoryDenied,
    InventoryUnavailable,
    _canonical_decimal,
)
from app.modules.inventory.valuation import StockValue


class ManualMovementInvalid(Exception):
    """Delta exceeds stock bounds or available quantity."""


class ManualMovementView(CountView):
    direction: Literal["IN", "OUT"]


def view(record, actor_id):
    return ManualMovementView(
        **{
            field: getattr(record, field)
            for field in ManualMovementView.model_fields
            if field != "actor_membership_id"
        },
        actor_membership_id=actor_id,
    )


def manual_fingerprint(data):
    content = {
        "kind": "manual_stock_v1",
        "product_id": str(data.product_id),
        "direction": data.direction,
        "quantity": _canonical_decimal(data.quantity),
        "unit_cost": _canonical_decimal(data.unit_cost),
        "reason": data.reason,
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def record_manual(
    service,
    token: SecretStr,
    *,
    data: ManualStockInput,
    operation_key: UUID,
    expected_actor: UUID,
    actor_bound: bool = False,
) -> ManualMovementView:
    if type(data) is not ManualStockInput or not isinstance(operation_key, UUID):
        raise AccessDenied("Access denied")
    # Also reject invalid copies constructed by internal callers bypassing
    # Pydantic validation; never trust computed/previous stock as input.
    data = ManualStockInput.model_validate(data.model_dump())
    fingerprint = manual_fingerprint(data)
    try:
        with service._factory.begin() as session:
            _limit_transaction(session)
            actor = service._authorize(session, token, expected_actor)
            previous = session.scalar(
                select(ManualStockRecord).where(
                    ManualStockRecord.tenant_id == actor.tenant_id,
                    ManualStockRecord.operation_key == operation_key,
                )
            )
            if previous is not None:
                if actor_bound and previous.actor_membership_id != actor.membership_id:
                    raise InventoryDenied("Access denied")
                if previous.request_fingerprint != fingerprint:
                    raise InventoryConflict("Operation key conflict")
                service._authorize(session, token, expected_actor)
                result = view(previous, actor.membership_id)
            else:
                product = session.scalar(
                    select(Product)
                    .where(
                        Product.tenant_id == actor.tenant_id,
                        Product.id == data.product_id,
                        Product.is_active.is_(True),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if product is None:
                    raise InventoryDenied("Access denied")
                balance = session.scalar(
                    select(StockBalance)
                    .where(
                        StockBalance.tenant_id == actor.tenant_id,
                        StockBalance.product_id == product.id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                before = (
                    StockValue(balance.quantity, balance.value)
                    if balance
                    else StockValue(Decimal(0), Decimal(0))
                )
                try:
                    after = (
                        before.receive(data.quantity, data.unit_cost)
                        if data.direction == "IN"
                        else before.withdraw(data.quantity)
                    )
                except ValueError:
                    raise ManualMovementInvalid("Invalid stock movement") from None
                with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
                    quantity_delta = after.quantity - before.quantity
                    value_delta = after.value - before.value
                record = ManualStockRecord(
                    tenant_id=actor.tenant_id,
                    product_id=product.id,
                    actor_membership_id=actor.membership_id,
                    operation_key=operation_key,
                    request_fingerprint=fingerprint,
                    reason=data.reason,
                    declared_unit_cost=data.unit_cost,
                    direction=data.direction,
                    before_quantity=before.quantity,
                    before_value=before.value,
                    after_quantity=after.quantity,
                    after_value=after.value,
                    quantity_delta=quantity_delta,
                    value_delta=value_delta,
                    created_at=_database_now(session),
                )
                session.add(record)
                session.flush()
                if quantity_delta != 0:
                    session.add(
                        StockMovement(
                            tenant_id=actor.tenant_id,
                            product_id=product.id,
                            manual_record_id=record.id,
                            quantity_delta=quantity_delta,
                            value_delta=value_delta,
                            created_at=record.created_at,
                        )
                    )
                if balance is None:
                    balance = StockBalance(
                        tenant_id=actor.tenant_id,
                        product_id=product.id,
                        quantity=after.quantity,
                        value=after.value,
                    )
                    session.add(balance)
                else:
                    balance.quantity, balance.value = after.quantity, after.value
                session.flush()
                service._authorize(session, token, expected_actor)
                result = view(record, actor.membership_id)
        return result
    except SQLAlchemyError:
        raise InventoryUnavailable("Inventory unavailable") from None


class ManualPage(CountPage):
    items: tuple[ManualMovementView, ...]


def list_manual(
    service,
    token: SecretStr,
    *,
    product_id: UUID,
    query: CountPageQuery,
    expected_actor: UUID | None = None,
) -> ManualPage:
    if type(query) is not CountPageQuery:
        raise InventoryDenied("Access denied")
    query = CountPageQuery.model_validate(query.model_dump())
    try:
        with service._factory.begin() as session:
            _limit_transaction(session)
            actor = service._authorize(session, token, expected_actor, recent=False)
            service._require_product(session, actor.tenant_id, product_id)
            statement = select(ManualStockRecord).where(
                ManualStockRecord.tenant_id == actor.tenant_id,
                ManualStockRecord.product_id == product_id,
            )
            if query.after is not None:
                cursor = session.scalar(
                    statement.where(ManualStockRecord.id == query.after)
                )
                if cursor is None:
                    raise InventoryDenied("Access denied")
                statement = statement.where(
                    tuple_(ManualStockRecord.created_at, ManualStockRecord.id)
                    < (cursor.created_at, cursor.id)
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        ManualStockRecord.created_at.desc(),
                        ManualStockRecord.id.desc(),
                    ).limit(query.limit + 1)
                )
            )
            items = tuple(view(row, actor.membership_id) for row in rows[: query.limit])
            result = ManualPage(
                items=items,
                next_after=items[-1].id if len(rows) > query.limit else None,
                actor_membership_id=actor.membership_id,
            )
        return result
    except SQLAlchemyError:
        raise InventoryUnavailable("Inventory unavailable") from None
