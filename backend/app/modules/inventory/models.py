"""Tenant-bound count evidence, its ledger entry and materialized stock."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
from app.modules.purchases import models as purchase_models  # noqa: F401 -- FK targets


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


class ManualStockRecord(Base):
    __tablename__ = "manual_stock_records"
    __table_args__ = (
        Index(
            "ix_manual_stock_records_history",
            "tenant_id",
            "product_id",
            "created_at",
            "id",
        ),
        UniqueConstraint("tenant_id", "operation_key"),
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "id",
            "quantity_delta",
            "value_delta",
            name="uq_manual_stock_records_movement_source",
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
            name="fk_manual_stock_records_actor",
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
            "(direction = 'IN' AND quantity_delta > 0 AND value_delta >= 0 "
            "AND declared_unit_cost IS NOT NULL) OR (direction = 'OUT' AND "
            "quantity_delta < 0 AND value_delta <= 0 AND declared_unit_cost IS NULL)",
            name="direction_cost",
        ),
        CheckConstraint(
            "(direction = 'IN' AND value_delta = "
            "round(quantity_delta * declared_unit_cost, 6)) OR "
            "(direction = 'OUT' AND value_delta = CASE WHEN after_quantity = 0 "
            "THEN -before_value ELSE -round(before_value * (-quantity_delta) "
            "/ nullif(before_quantity, 0), 6) END)",
            name="valuation_matches",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    operation_key: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(3))
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
        UniqueConstraint("receipt_line_id"),
        UniqueConstraint("reversal_id"),
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "id",
            "quantity_delta",
            "value_delta",
            name="uq_stock_movements_reversal_original",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "reversal_id", "quantity_delta", "value_delta"],
            [
                "stock_reversals.tenant_id",
                "stock_reversals.product_id",
                "stock_reversals.id",
                "stock_reversals.quantity_delta",
                "stock_reversals.value_delta",
            ],
            name="fk_stock_movements_reversal_source",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        UniqueConstraint("manual_record_id"),
        CheckConstraint(
            "num_nonnulls(count_id, receipt_line_id, manual_record_id, "
            "reversal_id) = 1",
            name="one_source",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "product_id",
                "manual_record_id",
                "quantity_delta",
                "value_delta",
            ],
            [
                "manual_stock_records.tenant_id",
                "manual_stock_records.product_id",
                "manual_stock_records.id",
                "manual_stock_records.quantity_delta",
                "manual_stock_records.value_delta",
            ],
            ondelete="RESTRICT",
            name="fk_stock_movements_manual_source",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "product_id",
                "receipt_line_id",
                "quantity_delta",
                "value_delta",
            ],
            [
                "purchase_receipt_lines.tenant_id",
                "purchase_receipt_lines.product_id",
                "purchase_receipt_lines.id",
                "purchase_receipt_lines.quantity_delta",
                "purchase_receipt_lines.value_delta",
            ],
            ondelete="RESTRICT",
            name="fk_stock_movements_receipt_source",
        ),
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
    count_id: Mapped[UUID | None] = mapped_column()
    receipt_line_id: Mapped[UUID | None] = mapped_column()
    manual_record_id: Mapped[UUID | None] = mapped_column()
    reversal_id: Mapped[UUID | None] = mapped_column()
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    value_delta: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StockMinimumRecord(Base):
    __tablename__ = "stock_minimum_records"
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
        UniqueConstraint("tenant_id", "product_id", "revision"),
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint(
            "before_minimum IS DISTINCT FROM minimum", name="actual_change"
        ),
        CheckConstraint(
            "(before_minimum IS NULL OR (before_minimum >= 0 AND "
            "before_minimum < 'Infinity'::numeric)) AND (minimum IS NULL OR "
            "(minimum >= 0 AND minimum < 'Infinity'::numeric))",
            name="minimum_bounds",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(BigInteger)
    before_minimum: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    minimum: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StockReversalRecord(Base):
    __tablename__ = "stock_reversals"
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
        ForeignKeyConstraint(
            [
                "tenant_id",
                "product_id",
                "original_movement_id",
                "original_quantity_delta",
                "original_value_delta",
            ],
            [
                "stock_movements.tenant_id",
                "stock_movements.product_id",
                "stock_movements.id",
                "stock_movements.quantity_delta",
                "stock_movements.value_delta",
            ],
            name="fk_stock_reversals_original",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "operation_key"),
        UniqueConstraint("original_movement_id"),
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "id",
            "quantity_delta",
            "value_delta",
            name="uq_stock_reversals_movement_source",
        ),
        CheckConstraint(
            "original_source IN ('COUNT', 'MANUAL')", name="source_allowed"
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
            "quantity_delta <> 0 AND quantity_delta = -original_quantity_delta "
            "AND value_delta = -original_value_delta",
            name="exact_inverse",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    operation_key: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    original_movement_id: Mapped[UUID] = mapped_column()
    original_source: Mapped[str] = mapped_column(String(6))
    original_id: Mapped[UUID] = mapped_column()
    original_quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    original_value_delta: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    reason: Mapped[str] = mapped_column(String(500))
    before_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    after_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    before_value: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    after_value: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    value_delta: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
