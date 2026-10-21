"""Exact, immutable stock compensations tied to original movements."""

import sqlalchemy as sa
from alembic import op

revision = "0042_stock_reversals"
down_revision = "0041_stock_minimum"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_stock_movements_reversal_original",
        "stock_movements",
        ["tenant_id", "product_id", "id", "quantity_delta", "value_delta"],
    )
    op.execute("""CREATE TABLE stock_reversals (
id UUID NOT NULL,
tenant_id UUID NOT NULL,
product_id UUID NOT NULL,
actor_membership_id UUID NOT NULL,
operation_key UUID NOT NULL,
request_fingerprint VARCHAR(64) NOT NULL,
original_movement_id UUID NOT NULL,
original_source VARCHAR(6) NOT NULL,
original_id UUID NOT NULL,
original_quantity_delta NUMERIC(18, 3) NOT NULL,
original_value_delta NUMERIC(28, 6) NOT NULL,
reason VARCHAR(500) NOT NULL,
before_quantity NUMERIC(18, 3) NOT NULL,
after_quantity NUMERIC(18, 3) NOT NULL,
before_value NUMERIC(28, 6) NOT NULL,
after_value NUMERIC(28, 6) NOT NULL,
quantity_delta NUMERIC(18, 3) NOT NULL,
value_delta NUMERIC(28, 6) NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
CONSTRAINT pk_stock_reversals PRIMARY KEY (id),
CONSTRAINT fk_stock_reversals_tenant_id_products FOREIGN KEY(tenant_id,
product_id) REFERENCES products (tenant_id, id) ON DELETE RESTRICT,
CONSTRAINT fk_stock_reversals_tenant_id_tenant_memberships FOREIGN
KEY(tenant_id, actor_membership_id) REFERENCES tenant_memberships (tenant_id,
id) ON DELETE RESTRICT,
CONSTRAINT fk_stock_reversals_original FOREIGN KEY(tenant_id, product_id,
original_movement_id, original_quantity_delta, original_value_delta) REFERENCES
stock_movements (tenant_id, product_id, id, quantity_delta, value_delta) ON
DELETE RESTRICT,
CONSTRAINT uq_stock_reversals_tenant_id UNIQUE (tenant_id, operation_key),
CONSTRAINT uq_stock_reversals_original_movement_id UNIQUE
(original_movement_id),
CONSTRAINT uq_stock_reversals_movement_source UNIQUE (tenant_id, product_id, id,
quantity_delta, value_delta),
CONSTRAINT ck_stock_reversals_source_allowed CHECK (original_source IN ('COUNT',
'MANUAL')),
CONSTRAINT ck_stock_reversals_reason_present CHECK (reason = trim(reason) AND
length(reason) > 0),
CONSTRAINT ck_stock_reversals_fingerprint_shape CHECK (request_fingerprint ~
'^[0-9a-f]{64}$'),
CONSTRAINT ck_stock_reversals_quantity_bounds CHECK (before_quantity >= 0 AND
before_quantity < 'Infinity'::numeric AND after_quantity >= 0 AND after_quantity
< 'Infinity'::numeric),
CONSTRAINT ck_stock_reversals_value_bounds CHECK (before_value >= 0 AND
before_value < 'Infinity'::numeric AND after_value >= 0 AND after_value <
'Infinity'::numeric),
CONSTRAINT ck_stock_reversals_empty_value CHECK ((before_quantity <> 0 OR
before_value = 0) AND (after_quantity <> 0 OR after_value = 0)),
CONSTRAINT ck_stock_reversals_delta_matches CHECK (quantity_delta =
after_quantity - before_quantity AND value_delta = after_value - before_value),
CONSTRAINT ck_stock_reversals_exact_inverse CHECK (quantity_delta <> 0 AND
quantity_delta = -original_quantity_delta AND value_delta =
-original_value_delta)
)""")
    op.add_column("stock_movements", sa.Column("reversal_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_stock_movements_reversal_id", "stock_movements", ["reversal_id"]
    )
    op.drop_constraint(
        op.f("ck_stock_movements_one_source"), "stock_movements", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_stock_movements_one_source"),
        "stock_movements",
        "num_nonnulls(count_id, receipt_line_id, manual_record_id, reversal_id) = 1",
    )
    op.create_foreign_key(
        "fk_stock_movements_reversal_source",
        "stock_movements",
        "stock_reversals",
        ["tenant_id", "product_id", "reversal_id", "quantity_delta", "value_delta"],
        ["tenant_id", "product_id", "id", "quantity_delta", "value_delta"],
        ondelete="RESTRICT",
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON stock_reversals FROM carrillo_app")


def downgrade():
    op.execute("LOCK TABLE stock_movements, stock_reversals IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM stock_reversals)")):
        raise RuntimeError(
            "Stock reversal evidence exists; refusing destructive downgrade"
        )
    op.drop_constraint(
        "fk_stock_movements_reversal_source", "stock_movements", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_stock_movements_reversal_id", "stock_movements", type_="unique"
    )
    op.drop_constraint(
        op.f("ck_stock_movements_one_source"), "stock_movements", type_="check"
    )
    op.drop_column("stock_movements", "reversal_id")
    op.create_check_constraint(
        op.f("ck_stock_movements_one_source"),
        "stock_movements",
        "num_nonnulls(count_id, receipt_line_id, manual_record_id) = 1",
    )
    op.drop_table("stock_reversals")
    op.drop_constraint(
        "uq_stock_movements_reversal_original", "stock_movements", type_="unique"
    )
