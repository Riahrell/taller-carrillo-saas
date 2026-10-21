"""Private email-bound employee invitations and immutable administrative evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0043_employee_invitations"
down_revision = "0042_stock_reversals"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE employee_invitations ( id UUID NOT NULL, tenant_id UUID
NOT NULL, actor_membership_id UUID NOT NULL, email VARCHAR(254) NOT
NULL, token_hash VARCHAR(64) NOT NULL, created_at TIMESTAMP WITH
TIME ZONE NOT NULL, expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
revoked_at TIMESTAMP WITH TIME ZONE, CONSTRAINT
pk_employee_invitations PRIMARY KEY (id), CONSTRAINT
uq_employee_invitations_tenant_id UNIQUE (tenant_id, id), CONSTRAINT
fk_employee_invitations_tenant_id_tenant_memberships FOREIGN
KEY(tenant_id, actor_membership_id) REFERENCES tenant_memberships
(tenant_id, id) ON DELETE RESTRICT, CONSTRAINT
ck_employee_invitations_email_canonical CHECK (email =
lower(trim(email)) AND length(email) BETWEEN 3 AND 254), CONSTRAINT
ck_employee_invitations_token_hash_shape CHECK (token_hash ~
'^[0-9a-f]{64}$'), CONSTRAINT ck_employee_invitations_lifetime CHECK
(expires_at = created_at + interval '24 hours'), CONSTRAINT
ck_employee_invitations_revocation_window CHECK (revoked_at IS NULL
OR revoked_at >= created_at), CONSTRAINT
uq_employee_invitations_token_hash UNIQUE (token_hash) )""")
    op.execute("""CREATE UNIQUE INDEX uq_employee_invitations_open_email ON
employee_invitations (tenant_id, email) WHERE revoked_at IS NULL""")
    op.execute("""CREATE TABLE employee_invitation_events ( id UUID NOT NULL,
tenant_id UUID NOT NULL, invitation_id UUID NOT NULL,
actor_membership_id UUID NOT NULL, action VARCHAR(7) NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL, CONSTRAINT
pk_employee_invitation_events PRIMARY KEY (id), CONSTRAINT
fk_employee_invitation_events_tenant_id_employee_invitations FOREIGN
KEY(tenant_id, invitation_id) REFERENCES employee_invitations
(tenant_id, id) ON DELETE RESTRICT, CONSTRAINT
fk_employee_invitation_events_tenant_id_tenant_memberships FOREIGN
KEY(tenant_id, actor_membership_id) REFERENCES tenant_memberships
(tenant_id, id) ON DELETE RESTRICT, CONSTRAINT
uq_employee_invitation_events_invitation_id UNIQUE (invitation_id,
action), CONSTRAINT ck_employee_invitation_events_action_allowed
CHECK (action IN ('CREATED', 'REVOKED')) )""")
    op.execute("""REVOKE UPDATE, DELETE, TRUNCATE ON employee_invitation_events FROM
carrillo_app""")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON employee_invitations FROM carrillo_app"
    )
    op.execute("GRANT UPDATE (revoked_at) ON employee_invitations TO carrillo_app")


def downgrade():
    op.execute(
        "LOCK TABLE employee_invitations, employee_invitation_events "
        "IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM employee_invitations)")
    ):
        raise RuntimeError(
            "Employee invitation evidence exists; refusing destructive downgrade"
        )
    op.drop_table("employee_invitation_events")
    op.drop_table("employee_invitations")
