"""Global revision and sticky MFA requirement; no email verification or login gate."""

import sqlalchemy as sa
from alembic import op

revision = "0012_user_security_state"
down_revision = "0011_mfa_throttles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "security_revision", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "mfa_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_check_constraint(
        op.f("ck_users_security_revision_bounds"), "users", "security_revision >= 0"
    )
    op.execute("""
        CREATE FUNCTION guard_user_security_state() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.security_revision < OLD.security_revision
               OR (OLD.mfa_required AND NOT NEW.mfa_required)
               OR (NEW.mfa_required IS DISTINCT FROM OLD.mfa_required
                   AND NEW.security_revision <= OLD.security_revision) THEN
                RAISE EXCEPTION 'Invalid security state transition';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION guard_user_security_state() FROM PUBLIC, carrillo_app"
    )
    op.execute(
        "CREATE TRIGGER users_security_state_guard BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION guard_user_security_state()"
    )
    op.alter_column(
        "security_events", "credential_id", existing_type=sa.Uuid(), nullable=True
    )
    op.add_column("security_events", sa.Column("security_revision", sa.BigInteger()))
    op.create_foreign_key(
        "fk_security_events_user_id_users",
        "security_events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_security_events_action_allowed"), "security_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_allowed"),
        "security_events",
        "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
        "'security_revision_advanced')",
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_state"),
        "security_events",
        "(action = 'security_revision_advanced' AND credential_id IS NULL "
        "AND security_revision IS NOT NULL AND security_revision > 0) OR "
        "(action IN ('mfa_totp_consumed', 'mfa_recovery_consumed') AND "
        "credential_id IS NOT NULL AND security_revision IS NULL)",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE users, security_events IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM users WHERE "
        "security_revision <> 0 OR mfa_required) OR EXISTS (SELECT 1 FROM "
        "security_events WHERE action = 'security_revision_advanced') THEN"
        " RAISE EXCEPTION 'Downgrade refused: user security state must be "
        "preserved'; END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_security_events_action_state"), "security_events", type_="check"
    )
    op.drop_constraint(
        op.f("ck_security_events_action_allowed"), "security_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_allowed"),
        "security_events",
        "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed')",
    )
    op.drop_constraint(
        "fk_security_events_user_id_users", "security_events", type_="foreignkey"
    )
    op.drop_column("security_events", "security_revision")
    op.alter_column(
        "security_events", "credential_id", existing_type=sa.Uuid(), nullable=False
    )
    op.execute("DROP TRIGGER users_security_state_guard ON users")
    op.execute("DROP FUNCTION guard_user_security_state()")
    op.drop_constraint(
        op.f("ck_users_security_revision_bounds"), "users", type_="check"
    )
    op.drop_column("users", "mfa_required")
    op.drop_column("users", "security_revision")
