"""Voluntary rotation and storage-key maintenance evidence."""

from alembic import op

revision = "0029_mfa_rotation"
down_revision = "0028_blind_reset_requests"
branch_labels = None
depends_on = None
OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated', 'password_reset_completed', "
    "'mailbox_verified', 'login_succeeded', 'login_failed', "
    "'identity_reauthenticated')"
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
NEW_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated', 'password_reset_completed', "
    "'mailbox_verified', 'login_succeeded', 'login_failed', "
    "'identity_reauthenticated', 'mfa_rotated', "
    "'mfa_seed_rewrapped')"
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
    "security_revision > 0) OR (action = 'mfa_seed_rewrapped' "
    "AND credential_id IS NOT NULL AND security_revision IS NOT "
    "NULL AND security_revision >= 0) OR (action IN "
    "('mfa_replaced', 'mfa_codes_regenerated', 'mfa_rotated') "
    "AND credential_id IS NOT NULL AND security_revision IS NOT "
    "NULL AND security_revision > 0) OR (action IN "
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
    op.execute(
        "\nCREATE TABLE mfa_rotations (\n\tid UUID NOT NULL, \n\tuser_id "
        "UUID NOT NULL, \n\tsession_id UUID NOT NULL, \n\tcredential_id "
        "UUID NOT NULL, \n\tsession_hash VARCHAR(64) NOT NULL, "
        "\n\ttoken_hash VARCHAR(64) NOT NULL, \n\tsecurity_revision "
        "BIGINT NOT NULL, \n\tpassword_verified_at TIMESTAMP WITH TIME "
        "ZONE NOT NULL, \n\tfactor_verified_at TIMESTAMP WITH TIME "
        "ZONE NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT "
        "NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\tclosed_at TIMESTAMP WITH TIME ZONE, \n\tactivated_at "
        "TIMESTAMP WITH TIME ZONE, \n\tkey_id VARCHAR(64), \n\tnonce "
        "BYTEA, \n\tciphertext BYTEA, \n\tCONSTRAINT pk_mfa_rotations "
        "PRIMARY KEY (id), \n\tCONSTRAINT "
        "fk_rotation_current_factor_owner FOREIGN KEY(user_id, "
        "credential_id) REFERENCES mfa_credentials (user_id, id) ON "
        "DELETE RESTRICT, \n\tCONSTRAINT "
        "ck_mfa_rotations_hashes_sha256 CHECK (token_hash ~ "
        "'^[0-9a-f]{64}$' AND session_hash ~ '^[0-9a-f]{64}$'), "
        "\n\tCONSTRAINT ck_mfa_rotations_revision_bounds CHECK "
        "(security_revision >= 0), \n\tCONSTRAINT "
        "ck_mfa_rotations_proof_lifetime CHECK (password_verified_at "
        "<= factor_verified_at AND factor_verified_at <= created_at "
        "AND created_at < expires_at AND expires_at <= "
        "password_verified_at + interval '5 minutes'), \n\tCONSTRAINT "
        "ck_mfa_rotations_closure_state CHECK ((closed_at IS NULL OR "
        "closed_at >= created_at) AND (activated_at IS NULL OR "
        "(closed_at IS NOT NULL AND activated_at=closed_at AND "
        "activated_at<expires_at))), \n\tCONSTRAINT "
        "ck_mfa_rotations_payload_state CHECK ((closed_at IS NULL "
        "AND key_id IS NOT NULL AND key_id ~ '^[A-Za-z0-9_-]{1,64}$' "
        "AND nonce IS NOT NULL AND octet_length(nonce)=12 AND "
        "ciphertext IS NOT NULL AND octet_length(ciphertext)=36) OR "
        "(closed_at IS NOT NULL AND key_id IS NULL AND nonce IS NULL "
        "AND ciphertext IS NULL)), \n\tCONSTRAINT "
        "fk_mfa_rotations_user_id_users FOREIGN KEY(user_id) "
        "REFERENCES users (id) ON DELETE RESTRICT, \n\tCONSTRAINT "
        "fk_mfa_rotations_session_id_sessions FOREIGN "
        "KEY(session_id) REFERENCES sessions (id) ON DELETE "
        "RESTRICT, \n\tCONSTRAINT uq_mfa_rotations_token_hash UNIQUE "
        "(token_hash)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_mfa_rotations_expires_at ON mfa_rotations (expires_at)")
    op.execute(
        "CREATE UNIQUE INDEX uq_mfa_rotations_open_user ON "
        "mfa_rotations (user_id) WHERE closed_at IS NULL"
    )
    op.execute("REVOKE DELETE ON mfa_rotations FROM carrillo_app")
    _events(NEW_ACTIONS, NEW_STATE)


def downgrade():
    op.execute(
        "LOCK TABLE users, mfa_rotations, security_events IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mfa_rotations) OR "
        "EXISTS (SELECT 1 FROM security_events WHERE action IN "
        "('mfa_rotated','mfa_seed_rewrapped')) THEN RAISE EXCEPTION "
        "'Downgrade refused: rotation evidence must be preserved'; "
        "END IF; END $$"
    )
    _events(OLD_ACTIONS, OLD_STATE)
    op.drop_table("mfa_rotations")
