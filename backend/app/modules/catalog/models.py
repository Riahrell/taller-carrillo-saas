"""Minimal catalog persistence with tenant-bound references and no stock fields."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.catalog import image_models  # noqa: F401 -- register image tables


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
        CheckConstraint("revision >= 0", name="revision_bounds"),
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

    revision: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    category_id: Mapped[UUID | None] = mapped_column()
    sku: Mapped[str | None] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 2))


class ProductChangeRecord(Base):
    __tablename__ = "product_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "product_id", "after_revision"),
        CheckConstraint(
            "before_revision >= 0 AND after_revision = before_revision + 1",
            name="revision_step",
        ),
        CheckConstraint(
            "ROW(before_name, before_sku, before_category_id, before_price, "
            "before_active) IS DISTINCT FROM ROW(after_name, after_sku, "
            "after_category_id, after_price, after_active)",
            name="actual_change",
        ),
        *[
            CheckConstraint(
                f"{side}_name = trim({side}_name) AND length({side}_name) > 0 "
                f"AND ({side}_sku IS NULL OR ({side}_sku = trim({side}_sku) "
                f"AND length({side}_sku) > 0)) AND {side}_price >= 0 "
                f"AND {side}_price < 'Infinity'::numeric",
                name=f"{side}_values",
            )
            for side in ("before", "after")
        ],
        *[
            ForeignKeyConstraint(
                ["tenant_id", f"{side}_category_id"],
                ["categories.tenant_id", "categories.id"],
                name=f"fk_product_changes_{side}_category",
                ondelete="RESTRICT",
            )
            for side in ("before", "after")
        ],
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    before_revision: Mapped[int] = mapped_column(BigInteger)
    after_revision: Mapped[int] = mapped_column(BigInteger)
    before_name: Mapped[str] = mapped_column(String(200))
    after_name: Mapped[str] = mapped_column(String(200))
    before_sku: Mapped[str | None] = mapped_column(String(100))
    after_sku: Mapped[str | None] = mapped_column(String(100))
    before_category_id: Mapped[UUID | None] = mapped_column()
    after_category_id: Mapped[UUID | None] = mapped_column()
    before_price: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    after_price: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    before_active: Mapped[bool] = mapped_column(Boolean)
    after_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
