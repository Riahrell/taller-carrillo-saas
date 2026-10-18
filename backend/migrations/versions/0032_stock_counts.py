"""Tenant-bound stock balances and immutable physical count ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0032_stock_counts"
down_revision = "0031_catalog_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stock_balances",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("value", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.CheckConstraint(
            "quantity >= 0 AND quantity < 'Infinity'::numeric",
            name=op.f("ck_stock_balances_quantity_bounds"),
        ),
        sa.CheckConstraint(
            "value >= 0 AND value < 'Infinity'::numeric",
            name=op.f("ck_stock_balances_value_bounds"),
        ),
        sa.CheckConstraint(
            "quantity <> 0 OR value = 0", name=op.f("ck_stock_balances_empty_value")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name=op.f("fk_stock_balances_tenant_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "product_id", name=op.f("pk_stock_balances")
        ),
    )
    op.create_table(
        "stock_counts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("before_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("after_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("before_value", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column("after_value", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("value_delta", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column(
            "declared_unit_cost", sa.Numeric(precision=28, scale=6), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "before_quantity >= 0 AND before_quantity < "
            "'Infinity'::numeric AND after_quantity >= 0 AND "
            "after_quantity < 'Infinity'::numeric",
            name=op.f("ck_stock_counts_quantity_bounds"),
        ),
        sa.CheckConstraint(
            "before_value >= 0 AND before_value < 'Infinity'::numeric AND "
            "after_value >= 0 AND after_value < 'Infinity'::numeric",
            name=op.f("ck_stock_counts_value_bounds"),
        ),
        sa.CheckConstraint(
            "declared_unit_cost IS NULL OR (declared_unit_cost >= 0 AND "
            "declared_unit_cost < 'Infinity'::numeric)",
            name=op.f("ck_stock_counts_declared_cost_bounds"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_stock_counts_fingerprint_shape"),
        ),
        sa.CheckConstraint(
            "(before_quantity <> 0 OR before_value = 0) AND "
            "(after_quantity <> 0 OR after_value = 0)",
            name=op.f("ck_stock_counts_empty_value"),
        ),
        sa.CheckConstraint(
            "(quantity_delta = 0 AND value_delta = 0) OR (quantity_delta "
            "> 0 AND value_delta >= 0) OR (quantity_delta < 0 AND "
            "value_delta <= 0)",
            name=op.f("ck_stock_counts_direction_matches"),
        ),
        sa.CheckConstraint(
            "(quantity_delta > 0 AND (before_quantity = 0 OR before_value "
            "= 0) AND declared_unit_cost IS NOT NULL) OR ((quantity_delta "
            "<= 0 OR (before_quantity > 0 AND before_value > 0)) AND "
            "declared_unit_cost IS NULL)",
            name=op.f("ck_stock_counts_declared_cost_required"),
        ),
        sa.CheckConstraint(
            "quantity_delta = after_quantity - before_quantity AND "
            "value_delta = after_value - before_value",
            name=op.f("ck_stock_counts_delta_matches"),
        ),
        sa.CheckConstraint(
            "reason = trim(reason) AND length(reason) > 0",
            name=op.f("ck_stock_counts_reason_present"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_stock_counts_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name=op.f("fk_stock_counts_tenant_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_counts")),
        sa.UniqueConstraint(
            "tenant_id", "operation_key", name=op.f("uq_stock_counts_tenant_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "product_id",
            "id",
            "quantity_delta",
            "value_delta",
            name="uq_stock_counts_movement_source",
        ),
    )
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("count_id", sa.Uuid(), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("value_delta", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity_delta <> 0 AND quantity_delta > "
            "'-Infinity'::numeric AND quantity_delta < "
            "'Infinity'::numeric",
            name=op.f("ck_stock_movements_quantity_nonzero"),
        ),
        sa.CheckConstraint(
            "value_delta > '-Infinity'::numeric AND value_delta < 'Infinity'::numeric",
            name=op.f("ck_stock_movements_value_finite"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id", "count_id", "quantity_delta", "value_delta"],
            [
                "stock_counts.tenant_id",
                "stock_counts.product_id",
                "stock_counts.id",
                "stock_counts.quantity_delta",
                "stock_counts.value_delta",
            ],
            name="fk_stock_movements_count_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_movements")),
        sa.UniqueConstraint("count_id", name=op.f("uq_stock_movements_count_id")),
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON stock_counts, stock_movements FROM carrillo_app"
    )
    op.execute("REVOKE DELETE ON stock_balances FROM carrillo_app")


def downgrade():
    op.execute(
        "LOCK TABLE stock_movements, stock_counts, stock_balances IN "
        "ACCESS EXCLUSIVE MODE"
    )
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM stock_movements) OR "
                "EXISTS (SELECT 1 FROM stock_counts) OR EXISTS (SELECT 1 FROM "
                "stock_balances)"
            )
        )
        .scalar_one()
    ):
        raise RuntimeError("Stock contains data; refusing destructive downgrade")
    op.drop_table("stock_movements")
    op.drop_table("stock_counts")
    op.drop_table("stock_balances")
