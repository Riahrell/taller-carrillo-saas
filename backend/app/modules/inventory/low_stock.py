"""Bounded, tenant-scoped list using each product's latest explicit minimum."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.authentication import _limit_transaction
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import CountPageQuery
from app.modules.inventory.models import StockBalance, StockMinimumRecord
from app.modules.inventory.service import InventoryDenied, InventoryUnavailable


class LowStockItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str
    sku: str | None
    quantity: Decimal
    minimum: Decimal

    @field_serializer("quantity", "minimum", when_used="json")
    def quantity_json(self, value: Decimal) -> str:
        return format(value, ".3f")


class LowStockPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[LowStockItem, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


def low_stock(service, token, *, query: CountPageQuery, expected_actor=None):
    if type(query) is not CountPageQuery:
        raise InventoryDenied("Access denied")
    query = CountPageQuery.model_validate(query.model_dump())
    try:
        with service._factory.begin() as session:
            _limit_transaction(session)
            actor = service._authorize(session, token, expected_actor, recent=False)
            if query.after is not None:
                service._require_product(session, actor.tenant_id, query.after)
            latest = (
                select(
                    StockMinimumRecord.product_id,
                    func.max(StockMinimumRecord.revision).label("revision"),
                )
                .where(StockMinimumRecord.tenant_id == actor.tenant_id)
                .group_by(StockMinimumRecord.product_id)
                .subquery()
            )
            quantity = func.coalesce(StockBalance.quantity, Decimal("0.000"))
            statement = (
                select(
                    Product.id,
                    Product.name,
                    Product.sku,
                    quantity.label("quantity"),
                    StockMinimumRecord.minimum,
                )
                .join(latest, latest.c.product_id == Product.id)
                .join(
                    StockMinimumRecord,
                    and_(
                        StockMinimumRecord.tenant_id == Product.tenant_id,
                        StockMinimumRecord.product_id == Product.id,
                        StockMinimumRecord.revision == latest.c.revision,
                    ),
                )
                .outerjoin(
                    StockBalance,
                    and_(
                        StockBalance.tenant_id == Product.tenant_id,
                        StockBalance.product_id == Product.id,
                    ),
                )
                .where(
                    Product.tenant_id == actor.tenant_id,
                    Product.is_active.is_(True),
                    StockMinimumRecord.minimum.is_not(None),
                    quantity <= StockMinimumRecord.minimum,
                )
            )
            if query.after is not None:
                statement = statement.where(Product.id > query.after)
            rows = (
                session.execute(statement.order_by(Product.id).limit(query.limit + 1))
                .mappings()
                .all()
            )
            items = tuple(
                LowStockItem.model_validate(row) for row in rows[: query.limit]
            )
            return LowStockPage(
                items=items,
                next_after=items[-1].id if len(rows) > query.limit else None,
                actor_membership_id=actor.membership_id,
            )
    except SQLAlchemyError:
        raise InventoryUnavailable("Inventory unavailable") from None
