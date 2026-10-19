"""Tenant-bound suppliers and immutable purchases; no stock/payment effects."""

import sqlalchemy as sa
from alembic import op

revision = "0035_purchases"
down_revision = "0034_product_images"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE suppliers (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    actor_membership_id UUID NOT NULL,
    name VARCHAR(200) NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_suppliers PRIMARY KEY (id),
    CONSTRAINT uq_suppliers_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT fk_suppliers_tenant_id_tenant_memberships FOREIGN KEY(tenant_id,
        actor_membership_id) REFERENCES tenant_memberships (tenant_id, id) ON DELETE
        RESTRICT,
    CONSTRAINT ck_suppliers_name_present CHECK (name = trim(name) AND length(name) >
        0),
    CONSTRAINT fk_suppliers_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES
        tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_suppliers_tenant_id_id ON suppliers (tenant_id, id)")
    op.execute("""
CREATE TABLE purchases (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    supplier_id UUID NOT NULL,
    supplier_name VARCHAR(200) NOT NULL,
    actor_membership_id UUID NOT NULL,
    operation_key UUID NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    line_count INTEGER NOT NULL,
    total NUMERIC(20, 2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_purchases PRIMARY KEY (id),
    CONSTRAINT uq_purchases_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_purchases_operation UNIQUE (tenant_id, operation_key),
    CONSTRAINT fk_purchases_tenant_id_suppliers FOREIGN KEY(tenant_id, supplier_id)
        REFERENCES suppliers (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchases_actor FOREIGN KEY(tenant_id, actor_membership_id)
        REFERENCES tenant_memberships (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_purchases_supplier_name_present CHECK (supplier_name =
        trim(supplier_name) AND length(supplier_name) > 0),
    CONSTRAINT ck_purchases_fingerprint_shape CHECK (request_fingerprint ~
        '^[0-9a-f]{64}$'),
    CONSTRAINT ck_purchases_line_count_bounds CHECK (line_count BETWEEN 1 AND 100),
    CONSTRAINT ck_purchases_total_bounds CHECK (total >= 0 AND total <
        'Infinity'::numeric)
)
""")
    op.execute(
        "CREATE INDEX ix_purchases_history ON purchases (tenant_id, created_at, id)"
    )
    op.execute(
        "CREATE INDEX ix_purchases_supplier ON purchases (tenant_id, supplier_id)"
    )
    op.execute("""
CREATE TABLE purchase_items (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    purchase_id UUID NOT NULL,
    product_id UUID NOT NULL,
    position INTEGER NOT NULL,
    product_name VARCHAR(200) NOT NULL,
    quantity NUMERIC(18, 3) NOT NULL,
    unit_cost NUMERIC(28, 6) NOT NULL,
    stock_value NUMERIC(28, 6) NOT NULL,
    amount NUMERIC(20, 2) NOT NULL,
    CONSTRAINT pk_purchase_items PRIMARY KEY (id),
    CONSTRAINT uq_purchase_items_product UNIQUE (tenant_id, purchase_id,
        product_id),
    CONSTRAINT uq_purchase_items_position UNIQUE (tenant_id, purchase_id, position),
    CONSTRAINT uq_purchase_items_source UNIQUE (tenant_id, purchase_id, id),
    CONSTRAINT fk_purchase_items_tenant_id_purchases FOREIGN KEY(tenant_id,
        purchase_id) REFERENCES purchases (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchase_items_product FOREIGN KEY(tenant_id, product_id)
        REFERENCES products (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_purchase_items_position_bounds CHECK (position BETWEEN 1 AND 100),
    CONSTRAINT ck_purchase_items_product_name_present CHECK (product_name =
        trim(product_name) AND length(product_name) > 0),
    CONSTRAINT ck_purchase_items_quantity_bounds CHECK (quantity > 0 AND quantity <
        'Infinity'::numeric),
    CONSTRAINT ck_purchase_items_cost_bounds CHECK (unit_cost >= 0 AND unit_cost <
        'Infinity'::numeric),
    CONSTRAINT ck_purchase_items_stock_value_matches CHECK (stock_value >= 0 AND
        stock_value < 'Infinity'::numeric AND stock_value = round(quantity *
        unit_cost, 6)),
    CONSTRAINT ck_purchase_items_amount_matches CHECK (amount >= 0 AND amount <
        'Infinity'::numeric AND amount = round(quantity * unit_cost, 2))
)
""")
    op.execute(
        "CREATE INDEX ix_purchase_items_product "
        "ON purchase_items (tenant_id, product_id)"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON suppliers, purchases, purchase_items "
        "FROM carrillo_app"
    )


def downgrade():
    op.execute(
        "LOCK TABLE purchase_items, purchases, suppliers IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM suppliers) OR EXISTS "
            "(SELECT 1 FROM purchases) OR EXISTS (SELECT 1 FROM purchase_items)"
        )
    ):
        raise RuntimeError("Purchase evidence exists; refusing destructive downgrade")
    op.drop_table("purchase_items")
    op.drop_table("purchases")
    op.drop_table("suppliers")
