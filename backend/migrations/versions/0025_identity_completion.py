"""Reset audit/notice and general session-bound mailbox verification."""

import sqlalchemy as sa
from alembic import op

revision = "0025_identity_completion"
down_revision = "0024_password_reset_requests"
branch_labels = None
depends_on = None

OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated')"
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
    "NULL AND security_revision >= 0) OR (action IN "
    "('mfa_replaced', 'mfa_codes_regenerated') AND credential_id "
    "IS NOT NULL AND security_revision IS NOT NULL AND "
    "security_revision > 0) OR (action IN ('mfa_totp_consumed', "
    "'mfa_recovery_consumed') AND credential_id IS NOT NULL AND "
    "security_revision IS NULL)"
)
NEW_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated', 'password_reset_completed')"
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
    "NULL AND security_revision >= 0) OR (action = "
    "'password_reset_completed' AND credential_id IS NULL AND "
    "security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action IN ('mfa_replaced', 'mfa_codes_regenerated') AND "
    "credential_id IS NOT NULL AND security_revision IS NOT NULL "
    "AND security_revision > 0) OR (action IN "
    "('mfa_totp_consumed', 'mfa_recovery_consumed') AND "
    "credential_id IS NOT NULL AND security_revision IS NULL)"
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
    op.create_table(
        "security_notices",
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("security_events.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(9), nullable=False, server_default="pending"),
        sa.CheckConstraint(
            "address_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_security_notices_address_hash_sha256"),
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '24 hours'",
            name=op.f("ck_security_notices_absolute_lifetime"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'accepted', 'uncertain', 'rejected', 'expired')",
            name=op.f("ck_security_notices_state_allowed"),
        ),
    )
    op.execute("REVOKE DELETE ON security_notices FROM carrillo_app")
    _events(NEW_ACTIONS, NEW_STATE)


def downgrade():
    op.execute("LOCK TABLE security_events, security_notices IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM security_notices) OR "
        "EXISTS\n        (SELECT 1 FROM security_events WHERE action "
        "= 'password_reset_completed') THEN\n        RAISE EXCEPTION "
        "'Downgrade refused: identity evidence must be preserved'; "
        "END IF; END $$"
    )
    _events(OLD_ACTIONS, OLD_STATE)
    op.drop_table("security_notices")
