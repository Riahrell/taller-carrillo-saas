"""Explicit per-product reorder notices; no stock, purchase or email side effects."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.authentication import _limit_transaction
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import StockMinimumChange
from app.modules.inventory.models import StockBalance, StockMinimumRecord
from app.modules.inventory.service import (
    InventoryDenied,
    InventoryStale,
    InventoryUnavailable,
)


class MinimumView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: UUID
    minimum: Decimal | None
    quantity: Decimal
    is_active: bool
    is_low: bool
    revision: int = Field(exclude=True)
    actor_membership_id: UUID = Field(exclude=True, repr=False)

    @field_serializer("minimum", "quantity", when_used="json")
    def quantity_json(self, value):
        return format(value, ".3f") if value is not None else None

    @property
    def etag(self):
        return f'"stock-minimum-v1-{self.product_id}-{self.revision}"'


def minimum(
    service,
    token,
    *,
    product_id: UUID,
    expected_actor=None,
    data=None,
    expected_etag=None,
):
    if not isinstance(product_id, UUID):
        raise InventoryDenied("Access denied")
    if data is not None:
        if type(data) is not StockMinimumChange:
            raise InventoryDenied("Access denied")
        data = StockMinimumChange.model_validate(data.model_dump())
    try:
        with service._factory.begin() as session:
            _limit_transaction(session)
            actor = service._authorize(
                session, token, expected_actor, recent=data is not None
            )
            product = session.scalar(
                select(Product)
                .where(Product.tenant_id == actor.tenant_id, Product.id == product_id)
                .with_for_update()
            )
            if product is None:
                raise InventoryDenied("Access denied")
            record = session.scalar(
                select(StockMinimumRecord)
                .where(
                    StockMinimumRecord.tenant_id == actor.tenant_id,
                    StockMinimumRecord.product_id == product_id,
                )
                .order_by(StockMinimumRecord.revision.desc())
                .limit(1)
            )
            configured = record.minimum if record else None
            revision = record.revision if record else 0
            etag = f'"stock-minimum-v1-{product_id}-{revision}"'
            if data is not None:
                if expected_etag != etag:
                    raise InventoryStale("Inventory settings changed")
                if data.minimum != configured:
                    session.add(
                        StockMinimumRecord(
                            tenant_id=actor.tenant_id,
                            product_id=product_id,
                            actor_membership_id=actor.membership_id,
                            revision=revision + 1,
                            before_minimum=configured,
                            minimum=data.minimum,
                        )
                    )
                    configured, revision = data.minimum, revision + 1
                    session.flush()
                service._authorize(session, token, expected_actor)
            balance = session.get(StockBalance, (actor.tenant_id, product_id))
            quantity = balance.quantity if balance else Decimal("0.000")
            result = MinimumView(
                product_id=product_id,
                minimum=configured,
                quantity=quantity,
                is_active=product.is_active,
                is_low=product.is_active
                and configured is not None
                and quantity <= configured,
                revision=revision,
                actor_membership_id=actor.membership_id,
            )
        return result
    except SQLAlchemyError:
        raise InventoryUnavailable("Inventory unavailable") from None
