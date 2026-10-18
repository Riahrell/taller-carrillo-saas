"""Tenant-bound count evidence, its ledger entry and materialized stock."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockBalance(Base):
    __tablename__ = "stock_balances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "quantity >= 0 AND quantity < 'Infinity'::numeric", name="quantity_bounds"
        ),
        CheckConstraint(
            "value >= 0 AND value < 'Infinity'::numeric", name="value_bounds"
        ),
        CheckConstraint("quantity <> 0 OR value = 0", name="empty_value"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    product_id: Mapped[UUID] = mapped_column(primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    value: Mapped[Decimal] = mapped_column(Numeric(28, 6))


class StockCountRecord(Base):
    __tablename__ = "stock_counts"
    __table_args__ = (
        Index("ix_stock_counts_history", "tenant_id", "product_id", "created_at", "id"),
        UniqueConstraint("tenant_id", "operation_key"),
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "id",
            "quantity_delta",
            "value_delta",
            name="uq_stock_counts_movement_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_stock_counts_actor",
        ),
        CheckConstraint(
            "reason = trim(reason) AND length(reason) > 0", name="reason_present"
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_shape"
        ),
        CheckConstraint(
            "before_quantity >= 0 AND before_quantity < 'Infinity'::numeric "
            "AND after_quantity >= 0 AND after_quantity < 'Infinity'::numeric",
            name="quantity_bounds",
        ),
        CheckConstraint(
            "before_value >= 0 AND before_value < 'Infinity'::numeric AND "
            "after_value >= 0 AND after_value < 'Infinity'::numeric",
            name="value_bounds",
        ),
        CheckConstraint(
            "(before_quantity <> 0 OR before_value = 0) AND (after_quantity <> "
            "0 OR after_value = 0)",
            name="empty_value",
        ),
        CheckConstraint(
            "quantity_delta = after_quantity - before_quantity AND value_delta "
            "= after_value - before_value",
            name="delta_matches",
        ),
        CheckConstraint(
            "(quantity_delta = 0 AND value_delta = 0) OR (quantity_delta > 0 "
            "AND value_delta >= 0) OR (quantity_delta < 0 AND value_delta <= "
            "0)",
            name="direction_matches",
        ),
        CheckConstraint(
            "declared_unit_cost IS NULL OR (declared_unit_cost >= 0 AND "
            "declared_unit_cost < 'Infinity'::numeric)",
            name="declared_cost_bounds",
        ),
        CheckConstraint(
            "(quantity_delta > 0 AND (before_quantity = 0 OR before_value = 0) "
            "AND declared_unit_cost IS NOT NULL) OR ((quantity_delta <= 0 OR "
            "(before_quantity > 0 AND before_value > 0)) AND "
            "declared_unit_cost IS NULL)",
            name="declared_cost_required",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    operation_key: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(500))
    before_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    after_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    before_value: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    after_value: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    value_delta: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    declared_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "count_id", "quantity_delta", "value_delta"],
            [
                "stock_counts.tenant_id",
                "stock_counts.product_id",
                "stock_counts.id",
                "stock_counts.quantity_delta",
                "stock_counts.value_delta",
            ],
            ondelete="RESTRICT",
            name="fk_stock_movements_count_source",
        ),
        UniqueConstraint("count_id"),
        CheckConstraint(
            "quantity_delta <> 0 AND quantity_delta > '-Infinity'::numeric AND "
            "quantity_delta < 'Infinity'::numeric",
            name="quantity_nonzero",
        ),
        CheckConstraint(
            "value_delta > '-Infinity'::numeric AND value_delta < 'Infinity'::numeric",
            name="value_finite",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    count_id: Mapped[UUID] = mapped_column()
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    value_delta: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
