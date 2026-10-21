"""Immutable manual IN/OUT evidence linked exactly to its ledger entry."""

import sqlalchemy as sa
from alembic import op

revision = "0039_manual_stock_movements"
down_revision = "0038_mailbox_constraint_name"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE manual_stock_records (
id UUID NOT NULL,
tenant_id UUID NOT NULL,
product_id UUID NOT NULL,
actor_membership_id UUID NOT NULL,
operation_key UUID NOT NULL,
request_fingerprint VARCHAR(64) NOT NULL,
direction VARCHAR(3) NOT NULL,
reason VARCHAR(500) NOT NULL,
before_quantity NUMERIC(18, 3) NOT NULL,
after_quantity NUMERIC(18, 3) NOT NULL,
before_value NUMERIC(28, 6) NOT NULL,
after_value NUMERIC(28, 6) NOT NULL,
quantity_delta NUMERIC(18, 3) NOT NULL,
value_delta NUMERIC(28, 6) NOT NULL,
declared_unit_cost NUMERIC(28, 6),
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
CONSTRAINT pk_manual_stock_records PRIMARY KEY (id),
CONSTRAINT uq_manual_stock_records_tenant_id UNIQUE (tenant_id, operation_key),
CONSTRAINT uq_manual_stock_records_movement_source UNIQUE (tenant_id, product_id,
id, quantity_delta, value_delta),
CONSTRAINT fk_manual_stock_records_tenant_id_products FOREIGN KEY(tenant_id,
product_id) REFERENCES products (tenant_id, id) ON DELETE RESTRICT,
CONSTRAINT fk_manual_stock_records_actor FOREIGN KEY(tenant_id, actor_membership_id)
REFERENCES tenant_memberships (tenant_id, id) ON DELETE RESTRICT,
CONSTRAINT ck_manual_stock_records_reason_present CHECK (reason = trim(reason) AND
length(reason) > 0),
CONSTRAINT ck_manual_stock_records_fingerprint_shape CHECK (request_fingerprint ~
'^[0-9a-f]{64}$'),
CONSTRAINT ck_manual_stock_records_quantity_bounds CHECK (before_quantity >= 0 AND
before_quantity < 'Infinity'::numeric AND after_quantity >= 0 AND after_quantity <
'Infinity'::numeric),
CONSTRAINT ck_manual_stock_records_value_bounds CHECK (before_value >= 0 AND
before_value < 'Infinity'::numeric AND after_value >= 0 AND after_value <
'Infinity'::numeric),
CONSTRAINT ck_manual_stock_records_empty_value CHECK ((before_quantity <> 0 OR
before_value = 0) AND (after_quantity <> 0 OR after_value = 0)),
CONSTRAINT ck_manual_stock_records_delta_matches CHECK (quantity_delta =
after_quantity - before_quantity AND value_delta = after_value - before_value),
CONSTRAINT ck_manual_stock_records_direction_matches CHECK ((quantity_delta = 0 AND
value_delta = 0) OR (quantity_delta > 0 AND value_delta >= 0) OR (quantity_delta < 0
AND value_delta <= 0)),
CONSTRAINT ck_manual_stock_records_declared_cost_bounds CHECK (declared_unit_cost IS
NULL OR (declared_unit_cost >= 0 AND declared_unit_cost < 'Infinity'::numeric)),
CONSTRAINT ck_manual_stock_records_direction_cost CHECK ((direction = 'IN' AND
quantity_delta > 0 AND value_delta >= 0 AND declared_unit_cost IS NOT NULL) OR
(direction = 'OUT' AND quantity_delta < 0 AND value_delta <= 0 AND
declared_unit_cost IS NULL)),
CONSTRAINT ck_manual_stock_records_valuation_matches CHECK ((direction = 'IN' AND
value_delta = round(quantity_delta * declared_unit_cost, 6)) OR (direction = 'OUT'
AND value_delta = CASE WHEN after_quantity = 0 THEN -before_value ELSE
-round(before_value * (-quantity_delta) / nullif(before_quantity, 0), 6) END))
)""")
    op.execute("""CREATE INDEX ix_manual_stock_records_history
ON manual_stock_records (tenant_id,
product_id, created_at, id)""")
    op.add_column(
        "stock_movements", sa.Column("manual_record_id", sa.Uuid(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_stock_movements_manual_record_id", "stock_movements", ["manual_record_id"]
    )
    op.drop_constraint(
        op.f("ck_stock_movements_one_source"), "stock_movements", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_stock_movements_one_source"),
        "stock_movements",
        "num_nonnulls(count_id, receipt_line_id, manual_record_id) = 1",
    )
    op.create_foreign_key(
        "fk_stock_movements_manual_source",
        "stock_movements",
        "manual_stock_records",
        [
            "tenant_id",
            "product_id",
            "manual_record_id",
            "quantity_delta",
            "value_delta",
        ],
        ["tenant_id", "product_id", "id", "quantity_delta", "value_delta"],
        ondelete="RESTRICT",
    )
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON manual_stock_records FROM carrillo_app"
    )


def downgrade():
    op.execute(
        "LOCK TABLE stock_movements, manual_stock_records IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM manual_stock_records) OR EXISTS "
            "(SELECT 1 FROM stock_movements WHERE manual_record_id IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Manual stock evidence exists; refusing destructive downgrade"
        )
    op.drop_constraint(
        "fk_stock_movements_manual_source", "stock_movements", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("ck_stock_movements_one_source"), "stock_movements", type_="check"
    )
    op.drop_constraint(
        "uq_stock_movements_manual_record_id", "stock_movements", type_="unique"
    )
    op.drop_column("stock_movements", "manual_record_id")
    op.create_check_constraint(
        op.f("ck_stock_movements_one_source"),
        "stock_movements",
        "(count_id IS NOT NULL AND receipt_line_id IS NULL) OR "
        "(count_id IS NULL AND receipt_line_id IS NOT NULL)",
    )
    op.drop_table("manual_stock_records")
