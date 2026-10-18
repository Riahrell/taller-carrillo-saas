"""Tenant-bound categories and products; no stock or public listings."""

import sqlalchemy as sa
from alembic import op

revision = "0030_catalog"
down_revision = "0029_mfa_rotation"
branch_labels = None
depends_on = None


def _columns():
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def _constraints(table):
    return (
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name=f"fk_{table}_tenant_id_tenants",
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name=op.f(f"ck_{table}_name_not_blank")
        ),
    )


def upgrade():
    op.create_table("categories", *_columns(), *_constraints("categories"))
    op.create_table(
        "products",
        *_columns(),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("sku", sa.String(100), nullable=True),
        sa.Column("price", sa.Numeric(20, 2), nullable=False),
        *_constraints("products"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            ["categories.tenant_id", "categories.id"],
            ondelete="RESTRICT",
            name="fk_products_tenant_id_categories",
        ),
        sa.CheckConstraint(
            "sku IS NULL OR (sku = trim(sku) AND length(sku) > 0)",
            name=op.f("ck_products_sku_canonical"),
        ),
        sa.CheckConstraint(
            "price >= 0 AND price < 'Infinity'::numeric",
            name=op.f("ck_products_price_bounds"),
        ),
    )
    op.create_index(
        "ix_products_tenant_id_category_id", "products", ["tenant_id", "category_id"]
    )
    # Business records are retained. Later audited services can deactivate them.
    op.execute("REVOKE DELETE ON categories, products FROM carrillo_app")


def downgrade():
    # Never silently erase an operational catalog to downgrade the application.
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM products) "
            "OR EXISTS (SELECT 1 FROM categories)"
        )
    ).scalar_one():
        raise RuntimeError("Catalog contains data; refusing destructive downgrade")
    op.drop_index("ix_products_tenant_id_category_id", table_name="products")
    op.drop_table("products")
    op.drop_table("categories")
