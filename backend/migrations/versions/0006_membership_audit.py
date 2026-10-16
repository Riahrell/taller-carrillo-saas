"""Typed, insert-only application audit for membership privilege changes."""

import sqlalchemy as sa
from alembic import op

revision = "0006_membership_audit"
down_revision = "0005_login_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("target_membership_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before_role", sa.String(16), nullable=False),
        sa.Column("after_role", sa.String(16), nullable=False),
        sa.Column("before_active", sa.Boolean(), nullable=False),
        sa.Column("after_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_actor_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "target_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_target_membership",
        ),
        sa.CheckConstraint(
            "action = 'membership_changed'", name=op.f("ck_audit_events_action_allowed")
        ),
        sa.CheckConstraint(
            "before_role IN ('admin', 'cashier', 'mechanic', 'read_only') AND "
            "after_role IN ('admin', 'cashier', 'mechanic', 'read_only')",
            name=op.f("ck_audit_events_roles_allowed"),
        ),
        sa.CheckConstraint(
            "before_role <> after_role OR before_active <> after_active",
            name=op.f("ck_audit_events_state_changed"),
        ),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])
    # Default privileges grant DML to the app; evidence permits only SELECT/INSERT.
    op.execute("REVOKE UPDATE, DELETE ON TABLE audit_events FROM carrillo_app")


def downgrade() -> None:
    op.drop_index("ix_audit_events_tenant_id", table_name="audit_events")
    op.drop_table("audit_events")
