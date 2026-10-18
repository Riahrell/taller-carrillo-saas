"""Audit current-factor offline code regeneration; preserve evidence on rollback."""

from alembic import op

revision = "0022_mfa_code_regeneration"
down_revision = "0021_mfa_replacement"
branch_labels = None
depends_on = None

OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', 'mfa_enrollment_email_confirmed', "
    "'mfa_enrolled', 'mfa_recovery_started')"
)
OLD_STATE = (
    "(action = 'security_revision_advanced' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action = 'mfa_enrollment_email_confirmed' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision >= 0) OR "
    "(action = 'mfa_enrolled' AND credential_id IS NOT NULL AND "
    "security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action = 'mfa_recovery_started' AND credential_id IS NOT NULL AND "
    "security_revision IS NOT NULL AND security_revision >= 0) OR "
    "(action IN ('mfa_totp_consumed', 'mfa_recovery_consumed') AND "
    "credential_id IS NOT NULL AND security_revision IS NULL)"
)

OLD_ACTIONS = OLD_ACTIONS[:-1] + ", 'mfa_replaced')"
OLD_STATE += (
    " OR (action = 'mfa_replaced' AND credential_id IS NOT NULL AND "
    "security_revision IS NOT NULL AND security_revision > 0)"
)


def _events(actions, state):
    for name in ("action_allowed", "action_state"):
        op.drop_constraint(
            op.f("ck_security_events_" + name), "security_events", type_="check"
        )
    op.create_check_constraint(
        op.f("ck_security_events_action_allowed"), "security_events", actions
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_state"), "security_events", state
    )


def upgrade() -> None:
    _events(
        OLD_ACTIONS[:-1] + ", 'mfa_codes_regenerated')",
        OLD_STATE
        + " OR (action = 'mfa_codes_regenerated' AND credential_id IS NOT NULL AND "
        "security_revision IS NOT NULL AND security_revision > 0)",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE security_events IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM security_events WHERE "
        "action = 'mfa_codes_regenerated') THEN RAISE EXCEPTION "
        "'Downgrade refused: MFA regeneration evidence must be preserved'; "
        "END IF; END $$"
    )
    _events(OLD_ACTIONS, OLD_STATE)
