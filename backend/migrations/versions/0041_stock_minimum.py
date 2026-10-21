"""Explicit stock minimum with append-only configuration history."""

import sqlalchemy as sa
from alembic import op

revision = "0041_stock_minimum"
down_revision = "0040_product_changes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE stock_minimum_records (
id UUID NOT NULL,
tenant_id UUID NOT NULL,
product_id UUID NOT NULL,
actor_membership_id UUID NOT NULL,
revision BIGINT NOT NULL,
before_minimum NUMERIC(18, 3),
minimum NUMERIC(18, 3),
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
CONSTRAINT pk_stock_minimum_records PRIMARY KEY (id),
CONSTRAINT fk_stock_minimum_records_tenant_id_products FOREIGN KEY(tenant_id,
product_id) REFERENCES products (tenant_id, id) ON DELETE RESTRICT,
CONSTRAINT fk_stock_minimum_records_tenant_id_tenant_memberships FOREIGN
KEY(tenant_id, actor_membership_id) REFERENCES tenant_memberships (tenant_id,
id) ON DELETE RESTRICT,
CONSTRAINT uq_stock_minimum_records_tenant_id UNIQUE (tenant_id, product_id,
revision),
CONSTRAINT ck_stock_minimum_records_positive_revision CHECK (revision > 0),
CONSTRAINT ck_stock_minimum_records_actual_change CHECK (before_minimum IS
DISTINCT FROM minimum),
CONSTRAINT ck_stock_minimum_records_minimum_bounds CHECK ((before_minimum IS
NULL OR (before_minimum >= 0 AND before_minimum < 'Infinity'::numeric)) AND
(minimum IS NULL OR (minimum >= 0 AND minimum < 'Infinity'::numeric)))
)""")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON stock_minimum_records FROM carrillo_app"
    )


def downgrade():
    op.execute("LOCK TABLE stock_minimum_records IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM stock_minimum_records)")
    ):
        raise RuntimeError(
            "Stock minimum evidence exists; refusing destructive downgrade"
        )
    op.drop_table("stock_minimum_records")
