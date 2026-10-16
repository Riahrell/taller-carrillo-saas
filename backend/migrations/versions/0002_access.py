"""Global users, tenants and explicit tenant memberships; no seed credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0002_access"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def record_columns():
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "tenants",
        *record_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name=op.f("ck_tenants_name_not_blank")
        ),
    )
    op.create_table(
        "users",
        *record_columns(),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint(
            "email = lower(trim(email)) AND length(email) > 0",
            name=op.f("ck_users_email_canonical"),
        ),
        sa.CheckConstraint(
            "length(trim(password_hash)) > 0",
            name=op.f("ck_users_password_hash_not_blank"),
        ),
    )
    op.create_table(
        "tenant_memberships",
        *record_columns(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenant_memberships"),
        sa.UniqueConstraint(
            "tenant_id", "user_id", name="uq_tenant_memberships_tenant_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_memberships_tenant_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_tenant_memberships_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_tenant_memberships_user_id_users",
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'cashier', 'mechanic', 'read_only')",
            name=op.f("ck_tenant_memberships_role_allowed"),
        ),
    )
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_memberships_user_id", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_table("users")
    op.drop_table("tenants")
