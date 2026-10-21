"""Pending mailbox registration, separate from active memberships and approval."""

import sqlalchemy as sa
from alembic import op

revision = "0044_employee_registration"
down_revision = "0043_employee_invitations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE employee_registrations ( id UUID NOT NULL, tenant_id
UUID NOT NULL, invitation_id UUID NOT NULL, token_hash VARCHAR(64)
NOT NULL, binding_hash VARCHAR(64) NOT NULL, password_hash
VARCHAR(512), created_at TIMESTAMP WITH TIME ZONE NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL, verified_at TIMESTAMP
WITH TIME ZONE, user_id UUID, code_hash VARCHAR(64), failures
INTEGER NOT NULL, send_state VARCHAR(8) NOT NULL, key_id
VARCHAR(64), nonce BYTEA, ciphertext BYTEA, CONSTRAINT
pk_employee_registrations PRIMARY KEY (id), CONSTRAINT
uq_employee_registrations_tenant_id UNIQUE (tenant_id, id),
CONSTRAINT fk_employee_registrations_tenant_id_employee_invitations
FOREIGN KEY(tenant_id, invitation_id) REFERENCES
employee_invitations (tenant_id, id) ON DELETE RESTRICT, CONSTRAINT
ck_employee_registrations_opaque_hashes CHECK (token_hash ~
'^[0-9a-f]{64}$' AND binding_hash ~ '^[0-9a-f]{64}$'), CONSTRAINT
ck_employee_registrations_lifetime CHECK (expires_at > created_at
AND expires_at <= created_at + interval '10 minutes'), CONSTRAINT
ck_employee_registrations_attempts CHECK (failures BETWEEN 0 AND 5),
CONSTRAINT ck_employee_registrations_send_state CHECK (send_state IN
('none','pending','claimed','sent','failed')), CONSTRAINT
ck_employee_registrations_envelope CHECK ((key_id IS NULL AND nonce
IS NULL AND ciphertext IS NULL) OR (key_id IS NOT NULL AND nonce IS
NOT NULL AND octet_length(nonce) = 12 AND ciphertext IS NOT NULL)),
CONSTRAINT ck_employee_registrations_verified_state CHECK
((verified_at IS NULL AND user_id IS NULL AND password_hash IS NOT
NULL) OR (verified_at IS NOT NULL AND verified_at >= created_at AND
verified_at < expires_at AND user_id IS NOT NULL AND password_hash
IS NULL AND code_hash IS NULL AND ciphertext IS NULL)), CONSTRAINT
uq_employee_registrations_invitation_id UNIQUE (invitation_id),
CONSTRAINT uq_employee_registrations_token_hash UNIQUE (token_hash),
CONSTRAINT uq_employee_registrations_user_id UNIQUE (user_id),
CONSTRAINT fk_employee_registrations_user_id_users FOREIGN
KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT )""")
    op.execute("""CREATE TABLE employee_approvals ( id UUID NOT NULL, tenant_id UUID
NOT NULL, registration_id UUID NOT NULL, membership_id UUID NOT
NULL, actor_membership_id UUID NOT NULL, created_at TIMESTAMP WITH
TIME ZONE NOT NULL, CONSTRAINT pk_employee_approvals PRIMARY KEY
(id), CONSTRAINT
fk_employee_approvals_tenant_id_employee_registrations FOREIGN
KEY(tenant_id, registration_id) REFERENCES employee_registrations
(tenant_id, id) ON DELETE RESTRICT, CONSTRAINT
fk_employee_approvals_membership FOREIGN KEY(tenant_id,
membership_id) REFERENCES tenant_memberships (tenant_id, id) ON
DELETE RESTRICT, CONSTRAINT fk_employee_approvals_actor FOREIGN
KEY(tenant_id, actor_membership_id) REFERENCES tenant_memberships
(tenant_id, id) ON DELETE RESTRICT, CONSTRAINT
uq_employee_approvals_registration_id UNIQUE (registration_id),
CONSTRAINT uq_employee_approvals_membership_id UNIQUE
(membership_id) )""")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON employee_approvals FROM carrillo_app"
    )


def downgrade():
    op.execute(
        "LOCK TABLE employee_registrations, employee_approvals IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM employee_registrations)")
    ):
        raise RuntimeError(
            "Employee registration evidence exists; refusing destructive downgrade"
        )
    op.drop_table("employee_approvals")
    op.drop_table("employee_registrations")
