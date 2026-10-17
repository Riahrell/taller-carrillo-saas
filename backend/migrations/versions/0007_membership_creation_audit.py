"""Preserve typed membership changes and audit newly created memberships."""

import sqlalchemy as sa
from alembic import op

revision = "0007_membership_creation_audit"
down_revision = "0006_membership_audit"
branch_labels = None
depends_on = None


def _drop_checks() -> None:
    for name in ("action_allowed", "roles_allowed", "state_changed"):
        op.drop_constraint(
            op.f("ck_audit_events_" + name), "audit_events", type_="check"
        )


def upgrade() -> None:
    _drop_checks()
    op.alter_column(
        "audit_events", "before_role", existing_type=sa.String(16), nullable=True
    )
    op.alter_column(
        "audit_events", "before_active", existing_type=sa.Boolean(), nullable=True
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


def downgrade() -> None:
    # Protect evidence before making any DDL change; the lock closes insert races.
    op.execute("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM audit_events WHERE action = 'membership_created') THEN
            RAISE EXCEPTION 'Downgrade refused: creation audit requires revision 0007';
        END IF;
    END $$""")
    _drop_checks()
    op.alter_column(
        "audit_events", "before_role", existing_type=sa.String(16), nullable=False
    )
    op.alter_column(
        "audit_events", "before_active", existing_type=sa.Boolean(), nullable=False
    )
    op.create_check_constraint(
        op.f("ck_audit_events_action_allowed"),
        "audit_events",
        "action = 'membership_changed'",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_roles_allowed"),
        "audit_events",
        "before_role IN ('admin', 'cashier', 'mechanic', 'read_only') AND "
        "after_role IN ('admin', 'cashier', 'mechanic', 'read_only')",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_state_changed"),
        "audit_events",
        "before_role <> after_role OR before_active <> after_active",
    )
