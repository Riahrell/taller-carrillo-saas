"""Immutable purchase evidence and tenant-bound supplier references."""

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
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("name = trim(name) AND length(name) > 0", name="name_present"),
        Index("ix_suppliers_tenant_id_id", "tenant_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    actor_membership_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "operation_key", name="uq_purchases_operation"),
        ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_purchases_actor",
        ),
        CheckConstraint(
            "supplier_name = trim(supplier_name) AND length(supplier_name) > 0",
            name="supplier_name_present",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_shape"
        ),
        CheckConstraint("line_count BETWEEN 1 AND 100", name="line_count_bounds"),
        CheckConstraint(
            "total >= 0 AND total < 'Infinity'::numeric", name="total_bounds"
        ),
        Index("ix_purchases_history", "tenant_id", "created_at", "id"),
        Index("ix_purchases_supplier", "tenant_id", "supplier_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    supplier_id: Mapped[UUID] = mapped_column()
    supplier_name: Mapped[str] = mapped_column(String(200))
    actor_membership_id: Mapped[UUID] = mapped_column()
    operation_key: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    line_count: Mapped[int] = mapped_column(Integer)
    total: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "purchase_id", "product_id", name="uq_purchase_items_product"
        ),
        UniqueConstraint(
            "tenant_id", "purchase_id", "position", name="uq_purchase_items_position"
        ),
        UniqueConstraint(
            "tenant_id", "purchase_id", "id", name="uq_purchase_items_source"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "purchase_id"],
            ["purchases.tenant_id", "purchases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_purchase_items_product",
        ),
        CheckConstraint("position BETWEEN 1 AND 100", name="position_bounds"),
        CheckConstraint(
            "product_name = trim(product_name) AND length(product_name) > 0",
            name="product_name_present",
        ),
        CheckConstraint(
            "quantity > 0 AND quantity < 'Infinity'::numeric", name="quantity_bounds"
        ),
        CheckConstraint(
            "unit_cost >= 0 AND unit_cost < 'Infinity'::numeric", name="cost_bounds"
        ),
        CheckConstraint(
            "stock_value >= 0 AND stock_value < 'Infinity'::numeric "
            "AND stock_value = round(quantity * unit_cost, 6)",
            name="stock_value_matches",
        ),
        CheckConstraint(
            "amount >= 0 AND amount < 'Infinity'::numeric "
            "AND amount = round(quantity * unit_cost, 2)",
            name="amount_matches",
        ),
        Index("ix_purchase_items_product", "tenant_id", "product_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    purchase_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    position: Mapped[int] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    stock_value: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
