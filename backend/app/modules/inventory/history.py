"""Read-only, tenant-scoped union of existing stock evidence, including no-op counts."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer
from sqlalchemy import String, and_, cast, literal, select, tuple_, union_all
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.authentication import _limit_transaction
from app.modules.access.models import TenantMembership, User
from app.modules.inventory.inputs import HistoryPageQuery
from app.modules.inventory.models import ManualStockRecord, StockCountRecord
from app.modules.inventory.service import InventoryDenied, InventoryUnavailable
from app.modules.purchases.models import PurchaseReceipt, PurchaseReceiptLine


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    product_id: UUID
    source: Literal["COUNT", "MANUAL", "RECEIPT"]
    direction: Literal["IN", "OUT"] | None
    source_document_id: UUID
    recorded_by: str
    reason: str | None
    before_quantity: Decimal
    after_quantity: Decimal
    before_value: Decimal
    after_value: Decimal
    quantity_delta: Decimal
    value_delta: Decimal
    created_at: datetime

    @field_serializer(
        "before_quantity", "after_quantity", "quantity_delta", when_used="json"
    )
    def quantity_json(self, value: Decimal) -> str:
        return format(value, ".3f")

    @field_serializer("before_value", "after_value", "value_delta", when_used="json")
    def value_json(self, value: Decimal) -> str:
        return format(value, ".6f")


class HistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[HistoryEntry, ...]
    next_after: str | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


def evidence(tenant_id: UUID, product_id: UUID):
    statements = []
    for model, source in (
        (StockCountRecord, "COUNT"),
        (ManualStockRecord, "MANUAL"),
        (PurchaseReceiptLine, "RECEIPT"),
    ):
        receipt = source == "RECEIPT"
        actor = (
            PurchaseReceipt.actor_membership_id
            if receipt
            else model.actor_membership_id
        )
        created_at = PurchaseReceipt.created_at if receipt else model.created_at
        statement = select(
            model.id,
            model.product_id,
            literal(source).label("source"),
            (
                model.direction if source == "MANUAL" else cast(literal(None), String)
            ).label("direction"),
            (model.purchase_id if receipt else model.id).label("source_document_id"),
            User.email.label("recorded_by"),
            (cast(literal(None), String) if receipt else model.reason).label("reason"),
            model.before_quantity,
            model.after_quantity,
            model.before_value,
            model.after_value,
            model.quantity_delta,
            model.value_delta,
            created_at.label("created_at"),
        ).select_from(model)
        if receipt:
            statement = statement.join(
                PurchaseReceipt,
                and_(
                    PurchaseReceipt.tenant_id == model.tenant_id,
                    PurchaseReceipt.id == model.receipt_id,
                ),
            )
        statement = (
            statement.join(
                TenantMembership,
                and_(
                    TenantMembership.tenant_id == model.tenant_id,
                    TenantMembership.id == actor,
                ),
            )
            .join(User, User.id == TenantMembership.user_id)
            .where(
                model.tenant_id == tenant_id,
                model.product_id == product_id,
            )
        )
        # Do not filter inactive memberships: historical evidence survives deactivation.
        statements.append(statement)
    return union_all(*statements).subquery()


def list_history(
    service,
    token: SecretStr,
    *,
    product_id: UUID,
    query: HistoryPageQuery,
    expected_actor: UUID | None = None,
) -> HistoryPage:
    if type(query) is not HistoryPageQuery:
        raise InventoryDenied("Access denied")
    query = HistoryPageQuery.model_validate(query.model_dump())
    try:
        with service._factory.begin() as session:
            _limit_transaction(session)
            actor = service._authorize(session, token, expected_actor, recent=False)
            service._require_product(session, actor.tenant_id, product_id)
            rows = evidence(actor.tenant_id, product_id)
            statement = select(rows)
            if query.after is not None:
                source, cursor_id = query.after.split(":")
                cursor = (
                    session.execute(
                        statement.where(
                            rows.c.source == source, rows.c.id == UUID(cursor_id)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if cursor is None:
                    raise InventoryDenied("Access denied")
                statement = statement.where(
                    tuple_(rows.c.created_at, rows.c.source, rows.c.id)
                    < (cursor.created_at, cursor.source, cursor.id)
                )
            values = (
                session.execute(
                    statement.order_by(
                        rows.c.created_at.desc(), rows.c.source.desc(), rows.c.id.desc()
                    ).limit(query.limit + 1)
                )
                .mappings()
                .all()
            )
            items = tuple(
                HistoryEntry.model_validate(row) for row in values[: query.limit]
            )
            return HistoryPage(
                items=items,
                next_after=f"{items[-1].source}:{items[-1].id}"
                if len(values) > query.limit
                else None,
                actor_membership_id=actor.membership_id,
            )
    except SQLAlchemyError:
        raise InventoryUnavailable("Inventory unavailable") from None
