"""Explicit append-only inventory consultation grants."""

import sqlalchemy as sa
from alembic import op

revision = "0045_inventory_read_grants"
down_revision = "0044_employee_registration"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE inventory_read_grants ( id UUID NOT NULL, tenant_id
UUID NOT NULL, membership_id UUID NOT NULL, actor_membership_id UUID
NOT NULL, revision BIGINT NOT NULL, before_granted BOOLEAN NOT NULL,
granted BOOLEAN NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT
NULL, CONSTRAINT pk_inventory_read_grants PRIMARY KEY (id),
CONSTRAINT uq_inventory_read_grants_tenant_id UNIQUE (tenant_id,
membership_id, revision), CONSTRAINT fk_inventory_read_grants_target
FOREIGN KEY(tenant_id, membership_id) REFERENCES tenant_memberships
(tenant_id, id) ON DELETE RESTRICT, CONSTRAINT
fk_inventory_read_grants_actor FOREIGN KEY(tenant_id,
actor_membership_id) REFERENCES tenant_memberships (tenant_id, id)
ON DELETE RESTRICT, CONSTRAINT
ck_inventory_read_grants_positive_revision CHECK (revision > 0),
CONSTRAINT ck_inventory_read_grants_actual_change CHECK
(before_granted <> granted) )""")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON inventory_read_grants FROM carrillo_app"
    )


def downgrade():
    op.execute("LOCK TABLE inventory_read_grants IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM inventory_read_grants)")
    ):
        raise RuntimeError(
            "Inventory permission evidence exists; refusing destructive downgrade"
        )
    op.drop_table("inventory_read_grants")
