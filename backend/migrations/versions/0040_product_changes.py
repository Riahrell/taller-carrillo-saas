"""Versioned product edits with immutable before/after evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0040_product_changes"
down_revision = "0039_manual_stock_movements"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "products",
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_products_revision_bounds"), "products", "revision >= 0"
    )
    op.execute("""CREATE TABLE product_changes (
id UUID NOT NULL,
tenant_id UUID NOT NULL,
product_id UUID NOT NULL,
actor_membership_id UUID NOT NULL,
before_revision BIGINT NOT NULL,
after_revision BIGINT NOT NULL,
before_name VARCHAR(200) NOT NULL,
after_name VARCHAR(200) NOT NULL,
before_sku VARCHAR(100),
after_sku VARCHAR(100),
before_category_id UUID,
after_category_id UUID,
before_price NUMERIC(20, 2) NOT NULL,
after_price NUMERIC(20, 2) NOT NULL,
before_active BOOLEAN NOT NULL,
after_active BOOLEAN NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
CONSTRAINT pk_product_changes PRIMARY KEY (id),
CONSTRAINT fk_product_changes_tenant_id_products FOREIGN KEY(tenant_id,
product_id) REFERENCES products (tenant_id, id) ON DELETE RESTRICT,
CONSTRAINT fk_product_changes_tenant_id_tenant_memberships FOREIGN
KEY(tenant_id, actor_membership_id) REFERENCES tenant_memberships (tenant_id,
id) ON DELETE RESTRICT,
CONSTRAINT uq_product_changes_tenant_id UNIQUE (tenant_id, product_id,
after_revision),
CONSTRAINT ck_product_changes_revision_step CHECK (before_revision >= 0 AND
after_revision = before_revision + 1),
CONSTRAINT ck_product_changes_actual_change CHECK (ROW(before_name, before_sku,
before_category_id, before_price, before_active) IS DISTINCT FROM
ROW(after_name, after_sku, after_category_id, after_price, after_active)),
CONSTRAINT ck_product_changes_before_values CHECK (before_name =
trim(before_name) AND length(before_name) > 0 AND (before_sku IS NULL OR
(before_sku = trim(before_sku) AND length(before_sku) > 0)) AND before_price >=
0 AND before_price < 'Infinity'::numeric),
CONSTRAINT ck_product_changes_after_values CHECK (after_name = trim(after_name)
AND length(after_name) > 0 AND (after_sku IS NULL OR (after_sku =
trim(after_sku) AND length(after_sku) > 0)) AND after_price >= 0 AND after_price
< 'Infinity'::numeric),
CONSTRAINT fk_product_changes_before_category FOREIGN KEY(tenant_id,
before_category_id) REFERENCES categories (tenant_id, id) ON DELETE RESTRICT,
CONSTRAINT fk_product_changes_after_category FOREIGN KEY(tenant_id,
after_category_id) REFERENCES categories (tenant_id, id) ON DELETE RESTRICT
)""")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON product_changes FROM carrillo_app")


def downgrade():
    op.execute("LOCK TABLE products, product_changes IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM product_changes) OR EXISTS "
            "(SELECT 1 FROM products WHERE revision <> 0)"
        )
    ):
        raise RuntimeError(
            "Product change evidence exists; refusing destructive downgrade"
        )
    op.drop_table("product_changes")
    op.drop_constraint(op.f("ck_products_revision_bounds"), "products", type_="check")
    op.drop_column("products", "revision")
