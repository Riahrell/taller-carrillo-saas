"""Minimal catalog persistence with tenant-bound references and no stock fields."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CatalogRecord:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Category(CatalogRecord, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )


class Product(CatalogRecord, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            ["categories.tenant_id", "categories.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint(
            "sku IS NULL OR (sku = trim(sku) AND length(sku) > 0)",
            name="sku_canonical",
        ),
        CheckConstraint(
            "price >= 0 AND price < 'Infinity'::numeric", name="price_bounds"
        ),
        Index("ix_products_tenant_id_category_id", "tenant_id", "category_id"),
    )

    category_id: Mapped[UUID | None] = mapped_column()
    sku: Mapped[str | None] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 2))
