"""Typed category/product creation evidence in existing append-only audit."""

import sqlalchemy as sa
from alembic import op

revision = "0031_catalog_audit"
down_revision = "0030_catalog"
branch_labels = None
depends_on = None

ACTION_ALLOWED = (
    "action IN ('membership_changed', 'membership_created', "
    "'catalog_category_created', 'catalog_product_created')"
)
ROLES_ALLOWED = (
    "(action IN ('membership_changed', 'membership_created') AND after_role "
    "IS NOT NULL AND (before_role IS NULL OR before_role IN ('admin', "
    "'cashier', 'mechanic', 'read_only')) AND after_role IN ('admin', "
    "'cashier', 'mechanic', 'read_only')) OR (action IN "
    "('catalog_category_created', 'catalog_product_created') AND before_role "
    "IS NULL AND after_role IS NULL)"
)
STATE_CHANGED = (
    "(action = 'membership_changed' AND before_role IS NOT NULL AND "
    "before_active IS NOT NULL AND (before_role <> after_role OR "
    "before_active <> after_active)) OR (action = 'membership_created' AND "
    "before_role IS NULL AND before_active IS NULL AND after_active IS TRUE) "
    "OR (action IN ('catalog_category_created', 'catalog_product_created') "
    "AND before_active IS NULL AND after_active IS TRUE)"
)
TARGET_SHAPE = (
    "(action IN ('membership_changed', 'membership_created') AND "
    "target_membership_id IS NOT NULL AND catalog_category_id IS NULL AND "
    "catalog_product_id IS NULL AND catalog_name IS NULL AND catalog_sku IS "
    "NULL AND catalog_price IS NULL) OR (action = 'catalog_category_created' "
    "AND target_membership_id IS NULL AND catalog_category_id IS NOT NULL AND"
    " catalog_product_id IS NULL AND catalog_name IS NOT NULL AND "
    "length(trim(catalog_name)) > 0 AND catalog_sku IS NULL AND catalog_price"
    " IS NULL) OR (action = 'catalog_product_created' AND "
    "target_membership_id IS NULL AND catalog_product_id IS NOT NULL AND "
    "catalog_name IS NOT NULL AND length(trim(catalog_name)) > 0 AND "
    "catalog_price IS NOT NULL AND catalog_price >= 0 AND catalog_price < "
    "'Infinity'::numeric AND (catalog_sku IS NULL OR (catalog_sku = "
    "trim(catalog_sku) AND length(catalog_sku) > 0)))"
)


def _drop_checks(names):
    for name in names:
        op.drop_constraint(
            op.f("ck_audit_events_" + name), "audit_events", type_="check"
        )


def upgrade():
    _drop_checks(("action_allowed", "roles_allowed", "state_changed"))
    op.alter_column(
        "audit_events", "target_membership_id", existing_type=sa.Uuid(), nullable=True
    )
    op.alter_column(
        "audit_events", "after_role", existing_type=sa.String(16), nullable=True
    )
    for name in ("catalog_category_id", "catalog_product_id"):
        op.add_column("audit_events", sa.Column(name, sa.Uuid(), nullable=True))
    op.add_column(
        "audit_events", sa.Column("catalog_name", sa.String(200), nullable=True)
    )
    op.add_column(
        "audit_events", sa.Column("catalog_sku", sa.String(100), nullable=True)
    )
    op.add_column(
        "audit_events", sa.Column("catalog_price", sa.Numeric(20, 2), nullable=True)
    )
    for kind, table in (("category", "categories"), ("product", "products")):
        op.create_foreign_key(
            "fk_audit_events_catalog_" + kind,
            "audit_events",
            table,
            ["tenant_id", "catalog_" + kind + "_id"],
            ["tenant_id", "id"],
            ondelete="RESTRICT",
        )
    for name, sql in (
        ("action_allowed", ACTION_ALLOWED),
        ("roles_allowed", ROLES_ALLOWED),
        ("state_changed", STATE_CHANGED),
        ("target_shape", TARGET_SHAPE),
    ):
        op.create_check_constraint(op.f("ck_audit_events_" + name), "audit_events", sql)


def downgrade():
    op.execute("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM audit_events WHERE action IN
                   ('catalog_category_created', 'catalog_product_created')) THEN
            RAISE EXCEPTION 'Downgrade refused: catalog audit requires revision 0031';
        END IF;
    END $$""")
    _drop_checks(("action_allowed", "roles_allowed", "state_changed", "target_shape"))
    for kind in ("product", "category"):
        op.drop_constraint(
            "fk_audit_events_catalog_" + kind, "audit_events", type_="foreignkey"
        )
    for name in (
        "catalog_price",
        "catalog_sku",
        "catalog_name",
        "catalog_product_id",
        "catalog_category_id",
    ):
        op.drop_column("audit_events", name)
    op.alter_column(
        "audit_events", "target_membership_id", existing_type=sa.Uuid(), nullable=False
    )
    op.alter_column(
        "audit_events", "after_role", existing_type=sa.String(16), nullable=False
    )
    op.create_check_constraint(
        op.f("ck_audit_events_action_allowed"),
        "audit_events",
        "action IN ('membership_changed', 'membership_created')",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_roles_allowed"),
        "audit_events",
        "(before_role IS NULL OR before_role IN "
        "('admin', 'cashier', 'mechanic', 'read_only')) AND "
        "after_role IN ('admin', 'cashier', 'mechanic', 'read_only')",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_state_changed"),
        "audit_events",
        "(action = 'membership_changed' AND before_role IS NOT NULL "
        "AND before_active IS NOT NULL AND "
        "(before_role <> after_role OR before_active <> after_active)) OR "
        "(action = 'membership_created' AND before_role IS NULL "
        "AND before_active IS NULL AND after_active IS TRUE)",
    )
