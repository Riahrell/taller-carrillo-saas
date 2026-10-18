"""Append-only login/reauthentication audit actions; no payload or IP copying."""

from alembic import op

revision = "0027_identity_audit"
down_revision = "0026_identity_surface"
branch_labels = None
depends_on = None
OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated', 'password_reset_completed', "
    "'mailbox_verified')"
)
OLD_STATE = (
    "(action = 'security_revision_advanced' AND credential_id IS "
    "NULL AND security_revision IS NOT NULL AND "
    "security_revision > 0) OR (action = "
    "'mfa_enrollment_email_confirmed' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision >= "
    "0) OR (action = 'mfa_enrolled' AND credential_id IS NOT "
    "NULL AND security_revision IS NOT NULL AND "
    "security_revision > 0) OR (action = 'mfa_recovery_started' "
    "AND credential_id IS NOT NULL AND security_revision IS NOT "
    "NULL AND security_revision >= 0) OR (action = "
    "'password_reset_completed' AND credential_id IS NULL AND "
    "security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action IN ('mfa_replaced', 'mfa_codes_regenerated') AND "
    "credential_id IS NOT NULL AND security_revision IS NOT NULL "
    "AND security_revision > 0) OR (action IN "
    "('mfa_totp_consumed', 'mfa_recovery_consumed') AND "
    "credential_id IS NOT NULL AND security_revision IS NULL) OR "
    "(action = 'mailbox_verified' AND credential_id IS NULL AND "
    "security_revision IS NOT NULL AND security_revision >= 0)"
)
NEW_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated', 'password_reset_completed', "
    "'mailbox_verified', 'login_succeeded', 'login_failed', "
    "'identity_reauthenticated')"
)
NEW_STATE = (
    "(action = 'security_revision_advanced' AND credential_id IS "
    "NULL AND security_revision IS NOT NULL AND "
    "security_revision > 0) OR (action = "
    "'mfa_enrollment_email_confirmed' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision >= "
    "0) OR (action = 'mfa_enrolled' AND credential_id IS NOT "
    "NULL AND security_revision IS NOT NULL AND "
    "security_revision > 0) OR (action = 'mfa_recovery_started' "
    "AND credential_id IS NOT NULL AND security_revision IS NOT "
    "NULL AND security_revision >= 0) OR (action IN "
    "('login_succeeded', 'login_failed', "
    "'identity_reauthenticated') AND credential_id IS NULL AND "
    "security_revision IS NOT NULL AND security_revision >= 0) "
    "OR (action = 'mailbox_verified' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision >= "
    "0) OR (action = 'password_reset_completed' AND "
    "credential_id IS NULL AND security_revision IS NOT NULL AND "
    "security_revision > 0) OR (action IN ('mfa_replaced', "
    "'mfa_codes_regenerated') AND credential_id IS NOT NULL AND "
    "security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action IN ('mfa_totp_consumed', 'mfa_recovery_consumed') "
    "AND credential_id IS NOT NULL AND security_revision IS NULL)"
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


def upgrade():
    _events(NEW_ACTIONS, NEW_STATE)


def downgrade():
    op.execute("LOCK TABLE security_events IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM security_events WHERE "
        "action IN ('login_succeeded','login_failed','identity_reauth"
        "enticated')) THEN RAISE EXCEPTION 'Downgrade refused: "
        "identity audit evidence must be preserved'; END IF; END $$"
    )
    _events(OLD_ACTIONS, OLD_STATE)
