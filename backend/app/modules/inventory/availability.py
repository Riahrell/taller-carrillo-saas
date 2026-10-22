"""Operational stock projection: explicit grant, active products, never costs."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.authentication import _limit_transaction
from app.modules.access.inventory_permissions import authorize_inventory_read
from app.modules.catalog.inputs import CatalogPageQuery, ProductSearchQuery
from app.modules.catalog.models import Category, Product
from app.modules.inventory.models import StockBalance
from app.modules.inventory.service import InventoryUnavailable


class AvailableProduct(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str
    sku: str | None
    category: str | None
    price: Decimal
    quantity: Decimal

    @field_serializer("price", when_used="json")
    def price_json(self, value):
        return format(value, ".2f")

    @field_serializer("quantity", when_used="json")
    def quantity_json(self, value):
        return format(value, ".3f")


class AvailableCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str


class AvailableProducts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[AvailableProduct, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


class AvailableCategories(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[AvailableCategory, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


class InventoryAvailability:
    def __init__(self, engine):
        self.factory = create_session_factory(engine)

    def products(self, token, *, expected_actor, query):
        query = ProductSearchQuery.model_validate(query.model_dump())
        try:
            with self.factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_inventory_read(session, token, expected_actor)
                # Deliberately project only allowed columns; never load balance.value.
                statement = (
                    select(
                        Product.id,
                        Product.name,
                        Product.sku,
                        Category.name.label("category"),
                        Product.price,
                        func.coalesce(StockBalance.quantity, 0).label("quantity"),
                    )
                    .outerjoin(
                        Category,
                        and_(
                            Category.id == Product.category_id,
                            Category.tenant_id == actor.tenant_id,
                        ),
                    )
                    .outerjoin(
                        StockBalance,
                        and_(
                            StockBalance.product_id == Product.id,
                            StockBalance.tenant_id == actor.tenant_id,
                        ),
                    )
                    .where(
                        Product.tenant_id == actor.tenant_id,
                        Product.is_active.is_(True),
                    )
                )
                if query.category_id:
                    statement = statement.where(
                        Product.category_id == query.category_id
                    )
                if query.q:
                    literal = (
                        query.q.replace("\\", "\\\\")
                        .replace("%", "\\%")
                        .replace("_", "\\_")
                    )
                    statement = statement.where(
                        or_(
                            Product.name.ilike("%" + literal + "%", escape="\\"),
                            Product.sku.ilike("%" + literal + "%", escape="\\"),
                        )
                    )
                if query.after:
                    statement = statement.where(Product.id > query.after)
                rows = (
                    session.execute(
                        statement.order_by(Product.id).limit(query.limit + 1)
                    )
                    .mappings()
                    .all()
                )
                result = AvailableProducts(
                    items=tuple(AvailableProduct(**row) for row in rows[: query.limit]),
                    next_after=rows[query.limit - 1]["id"]
                    if len(rows) > query.limit
                    else None,
                    actor_membership_id=actor.membership_id,
                )
            return result
        except SQLAlchemyError:
            raise InventoryUnavailable("Inventory unavailable") from None

    def categories(self, token, *, expected_actor, query):
        query = CatalogPageQuery.model_validate(query.model_dump())
        try:
            with self.factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_inventory_read(session, token, expected_actor)
                statement = select(Category.id, Category.name).where(
                    Category.tenant_id == actor.tenant_id, Category.is_active.is_(True)
                )
                if query.after:
                    statement = statement.where(Category.id > query.after)
                rows = (
                    session.execute(
                        statement.order_by(Category.id).limit(query.limit + 1)
                    )
                    .mappings()
                    .all()
                )
                return AvailableCategories(
                    items=tuple(
                        AvailableCategory(**row) for row in rows[: query.limit]
                    ),
                    next_after=rows[query.limit - 1]["id"]
                    if len(rows) > query.limit
                    else None,
                    actor_membership_id=actor.membership_id,
                )
        except SQLAlchemyError:
            raise InventoryUnavailable("Inventory unavailable") from None
