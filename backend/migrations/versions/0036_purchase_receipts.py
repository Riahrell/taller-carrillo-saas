"""Typed purchase receipt source for immutable stock movements."""

import sqlalchemy as sa
from alembic import op

revision = "0036_purchase_receipts"
down_revision = "0035_purchases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE purchase_items ADD CONSTRAINT uq_purchase_items_receipt_source UNIQUE
    (tenant_id, purchase_id, product_id, id, quantity, stock_value)
""")
    op.execute("""
CREATE TABLE purchase_receipts (
id UUID NOT NULL,
tenant_id UUID NOT NULL,
purchase_id UUID NOT NULL,
operation_key UUID NOT NULL,
actor_membership_id UUID NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
CONSTRAINT pk_purchase_receipts PRIMARY KEY (id),
CONSTRAINT uq_purchase_receipts_purchase UNIQUE (tenant_id, purchase_id),
CONSTRAINT uq_purchase_receipts_operation UNIQUE (tenant_id, operation_key),
CONSTRAINT uq_purchase_receipts_source UNIQUE (tenant_id, purchase_id, id),
CONSTRAINT fk_purchase_receipts_tenant_id_purchases FOREIGN KEY(tenant_id,
    purchase_id) REFERENCES purchases (tenant_id, id) ON DELETE RESTRICT,
CONSTRAINT fk_purchase_receipts_actor FOREIGN KEY(tenant_id, actor_membership_id)
    REFERENCES tenant_memberships (tenant_id, id) ON DELETE RESTRICT
)
""")
    op.execute("""
CREATE TABLE purchase_receipt_lines (
id UUID NOT NULL,
tenant_id UUID NOT NULL,
purchase_id UUID NOT NULL,
receipt_id UUID NOT NULL,
purchase_item_id UUID NOT NULL,
product_id UUID NOT NULL,
before_quantity NUMERIC(18, 3) NOT NULL,
after_quantity NUMERIC(18, 3) NOT NULL,
before_value NUMERIC(28, 6) NOT NULL,
after_value NUMERIC(28, 6) NOT NULL,
quantity_delta NUMERIC(18, 3) NOT NULL,
value_delta NUMERIC(28, 6) NOT NULL,
CONSTRAINT pk_purchase_receipt_lines PRIMARY KEY (id),
CONSTRAINT uq_purchase_receipt_lines_purchase_item_id UNIQUE (purchase_item_id),
CONSTRAINT uq_purchase_receipt_lines_movement_source UNIQUE (tenant_id,
    product_id, id, quantity_delta, value_delta),
CONSTRAINT fk_purchase_receipt_lines_receipt FOREIGN KEY(tenant_id, purchase_id,
    receipt_id) REFERENCES purchase_receipts (tenant_id, purchase_id, id) ON
    DELETE RESTRICT,
CONSTRAINT fk_purchase_receipt_lines_item_source FOREIGN KEY(tenant_id,
    purchase_id, product_id, purchase_item_id, quantity_delta, value_delta)
    REFERENCES purchase_items (tenant_id, purchase_id, product_id, id, quantity,
    stock_value) ON DELETE RESTRICT,
CONSTRAINT ck_purchase_receipt_lines_quantity_bounds CHECK (before_quantity >= 0
    AND before_quantity < 'Infinity'::numeric AND after_quantity > 0 AND
    after_quantity < 'Infinity'::numeric),
CONSTRAINT ck_purchase_receipt_lines_value_bounds CHECK (before_value >= 0 AND
    before_value < 'Infinity'::numeric AND after_value >= 0 AND after_value <
    'Infinity'::numeric),
CONSTRAINT ck_purchase_receipt_lines_empty_value CHECK (before_quantity <> 0 OR
    before_value = 0),
CONSTRAINT ck_purchase_receipt_lines_delta_matches CHECK (quantity_delta =
    after_quantity - before_quantity AND quantity_delta > 0 AND value_delta =
    after_value - before_value AND value_delta >= 0)
)
""")
    op.alter_column(
        "stock_movements", "count_id", existing_type=sa.Uuid(), nullable=True
    )
    op.add_column(
        "stock_movements", sa.Column("receipt_line_id", sa.Uuid(), nullable=True)
    )
    op.execute("""
ALTER TABLE stock_movements ADD CONSTRAINT uq_stock_movements_receipt_line_id
    UNIQUE (receipt_line_id)
""")
    op.execute("""
ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_one_source CHECK
    ((count_id IS NOT NULL AND receipt_line_id IS NULL) OR (count_id IS NULL AND
    receipt_line_id IS NOT NULL))
""")
    op.execute("""
ALTER TABLE stock_movements ADD CONSTRAINT fk_stock_movements_receipt_source
    FOREIGN KEY(tenant_id, product_id, receipt_line_id, quantity_delta,
    value_delta) REFERENCES purchase_receipt_lines (tenant_id, product_id, id,
    quantity_delta, value_delta) ON DELETE RESTRICT
""")
    op.execute(
        "REVOKE UPDATE, DELETE ON purchase_receipts, purchase_receipt_lines "
        "FROM carrillo_app"
    )


def downgrade():
    op.execute(
        "LOCK TABLE stock_movements, purchase_receipt_lines, purchase_receipts "
        "IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM purchase_receipts) OR EXISTS "
            "(SELECT 1 FROM purchase_receipt_lines) OR EXISTS "
            "(SELECT 1 FROM stock_movements WHERE receipt_line_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("Receipt evidence exists; refusing destructive downgrade")
    op.drop_constraint(
        "fk_stock_movements_receipt_source", "stock_movements", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("ck_stock_movements_one_source"), "stock_movements", type_="check"
    )
    op.drop_constraint(
        "uq_stock_movements_receipt_line_id", "stock_movements", type_="unique"
    )
    op.drop_column("stock_movements", "receipt_line_id")
    op.alter_column(
        "stock_movements", "count_id", existing_type=sa.Uuid(), nullable=False
    )
    op.drop_table("purchase_receipt_lines")
    op.drop_table("purchase_receipts")
    op.drop_constraint(
        "uq_purchase_items_receipt_source", "purchase_items", type_="unique"
    )
