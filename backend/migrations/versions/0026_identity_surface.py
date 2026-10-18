"""Complete session-bound mailbox, recent MFA evidence and cutover guards."""

import sqlalchemy as sa
from alembic import op

revision = "0026_identity_surface"
down_revision = "0025_identity_completion"
branch_labels = None
depends_on = None

OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated', 'password_reset_completed')"
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
    "credential_id IS NOT NULL AND security_revision IS NULL)"
)
NEW_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', "
    "'mfa_enrollment_email_confirmed', 'mfa_enrolled', "
    "'mfa_recovery_started', 'mfa_replaced', "
    "'mfa_codes_regenerated', 'password_reset_completed', "
    "'mailbox_verified')"
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
    "credential_id IS NOT NULL AND security_revision IS NULL) OR "
    "(action = 'mailbox_verified' AND credential_id IS NULL AND "
    "security_revision IS NOT NULL AND security_revision >= 0)"
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
        "\nCREATE TABLE mailbox_challenges (\n\tid UUID NOT NULL, "
        "\n\tuser_id UUID NOT NULL, \n\tsession_id UUID NOT NULL, "
        "\n\ttoken_hash VARCHAR(64) NOT NULL, \n\temail_fingerprint "
        "VARCHAR(64) NOT NULL, \n\tsecurity_revision BIGINT NOT NULL, "
        "\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\tconsumed_at TIMESTAMP WITH TIME ZONE, \n\tclosed_at "
        "TIMESTAMP WITH TIME ZONE, \n\tdiscarded_at TIMESTAMP WITH "
        "TIME ZONE, \n\tkey_id VARCHAR(64), \n\tnonce BYTEA, "
        "\n\tciphertext BYTEA, \n\tCONSTRAINT pk_mailbox_challenges "
        "PRIMARY KEY (id), \n\tCONSTRAINT "
        "uq_mailbox_challenges_user_id_id UNIQUE (user_id, id), "
        "\n\tCONSTRAINT ck_mailbox_challenges_hashes_sha256 CHECK "
        "(token_hash ~ '^[0-9a-f]{64}$' AND email_fingerprint ~ "
        "'^[0-9a-f]{64}$'), \n\tCONSTRAINT "
        "ck_mailbox_challenges_revision_bounds CHECK "
        "(security_revision >= 0), \n\tCONSTRAINT "
        "ck_mailbox_challenges_lifetime_bounds CHECK (expires_at > "
        "created_at AND expires_at <= created_at + interval '15 "
        "minutes'), \n\tCONSTRAINT ck_mailbox_challenges_closure_state "
        "CHECK ((closed_at IS NULL OR closed_at >= created_at) AND "
        "(consumed_at IS NULL OR (closed_at IS NOT NULL AND "
        "consumed_at = closed_at AND consumed_at >= created_at AND "
        "consumed_at < expires_at))), \n\tCONSTRAINT "
        "ck_mailbox_challenges_payload_state CHECK ((discarded_at IS "
        "NULL AND key_id IS NOT NULL AND key_id ~ "
        "'^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
        "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
        "octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
        "(discarded_at IS NOT NULL AND discarded_at >= created_at "
        "AND key_id IS NULL AND nonce IS NULL AND ciphertext IS "
        "NULL)), \n\tCONSTRAINT fk_mailbox_challenges_user_id_users "
        "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE "
        "RESTRICT, \n\tCONSTRAINT "
        "fk_mailbox_challenges_session_id_sessions FOREIGN "
        "KEY(session_id) REFERENCES sessions (id) ON DELETE "
        "RESTRICT, \n\tCONSTRAINT uq_mailbox_challenges_token_hash "
        "UNIQUE (token_hash)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_mailbox_challenges_expires_at ON "
        "mailbox_challenges (expires_at)"
    )
    op.execute(
        "CREATE INDEX ix_mailbox_challenges_user_id ON mailbox_challenges (user_id)"
    )
    op.execute("REVOKE DELETE ON mailbox_challenges FROM carrillo_app")
    op.execute(
        "\nCREATE TABLE identity_mail_deliveries (\n\tid UUID NOT NULL, "
        "\n\tuser_id UUID NOT NULL, \n\tkind VARCHAR(7) NOT NULL, "
        "\n\tsource_id UUID NOT NULL, \n\tstate VARCHAR(9) NOT NULL, "
        "\n\tattempts INTEGER NOT NULL, \n\tclaim_id UUID, "
        "\n\tlease_expires_at TIMESTAMP WITH TIME ZONE, "
        "\n\tnext_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\tCONSTRAINT pk_identity_mail_deliveries PRIMARY KEY (id), "
        "\n\tCONSTRAINT uq_identity_mail_source UNIQUE (kind, "
        "source_id), \n\tCONSTRAINT "
        "ck_identity_mail_deliveries_kind_allowed CHECK (kind IN "
        "('reset','mailbox','notice')), \n\tCONSTRAINT "
        "ck_identity_mail_deliveries_state_allowed CHECK (state IN "
        "('pending','leased','accepted','uncertain','rejected','cance"
        "lled')), \n\tCONSTRAINT "
        "ck_identity_mail_deliveries_attempt_bounds CHECK (attempts "
        ">= 1 AND attempts <= 3), \n\tCONSTRAINT "
        "ck_identity_mail_deliveries_lease_state CHECK ((state = "
        "'leased' AND claim_id IS NOT NULL AND lease_expires_at IS "
        "NOT NULL) OR (state <> 'leased' AND claim_id IS NULL AND "
        "lease_expires_at IS NULL)), \n\tCONSTRAINT "
        "fk_identity_mail_deliveries_user_id_users FOREIGN "
        "KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute("REVOKE DELETE ON identity_mail_deliveries FROM carrillo_app")
    op.add_column("verified_mailboxes", sa.Column("general_challenge_id", sa.Uuid()))
    op.alter_column(
        "verified_mailboxes", "challenge_id", existing_type=sa.Uuid(), nullable=True
    )
    op.create_unique_constraint(
        "uq_verified_mailboxes_general_challenge_id",
        "verified_mailboxes",
        ["general_challenge_id"],
    )
    op.create_foreign_key(
        "fk_verified_mailbox_general_owner",
        "verified_mailboxes",
        "mailbox_challenges",
        ["user_id", "general_challenge_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_verified_mailboxes_one_source"),
        "verified_mailboxes",
        (
            "(challenge_id IS NOT NULL AND general_challenge_id IS NULL) "
            "OR (challenge_id IS NULL AND general_challenge_id IS NOT "
            "NULL)"
        ),
    )
    op.add_column(
        "sessions", sa.Column("mfa_reauthenticated_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "sessions", sa.Column("mfa_reauthentication_credential_id", sa.Uuid())
    )
    op.create_foreign_key(
        "fk_sessions_mfa_reauthentication_credential_id_mfa_credentials",
        "sessions",
        "mfa_credentials",
        ["mfa_reauthentication_credential_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_sessions_mfa_reauthentication_state"),
        "sessions",
        (
            "(mfa_reauthenticated_at IS NULL AND "
            "mfa_reauthentication_credential_id IS NULL) OR "
            "(mfa_reauthenticated_at IS NOT NULL AND "
            "mfa_reauthentication_credential_id IS NOT NULL AND "
            "mfa_pending_id IS NOT NULL AND reauthenticated_at IS NOT "
            "NULL AND reauthenticated_at <= mfa_reauthenticated_at AND "
            "mfa_reauthenticated_at < expires_at)"
        ),
    )
    _events(NEW_ACTIONS, NEW_STATE)
    # One-time cutover: no legacy active-admin session bypasses required MFA.
    op.execute(
        "WITH changed AS (\n        UPDATE users SET "
        "security_revision = security_revision + 1, mfa_required = "
        "true\n        WHERE mfa_required OR id IN (SELECT m.user_id "
        "FROM tenant_memberships m JOIN tenants t ON "
        "t.id=m.tenant_id WHERE m.role='admin' AND m.is_active AND "
        "t.is_active)\n        RETURNING id, security_revision\n    ) "
        "INSERT INTO security_events "
        "(id,user_id,credential_id,action,security_revision,occurred_"
        "at)\n    SELECT gen_random_uuid(),id,NULL,'security_revision_"
        "advanced',security_revision,clock_timestamp() FROM changed"
    )
    op.execute(
        "UPDATE sessions SET revoked_at=greatest(clock_timestamp(),cr"
        "eated_at) WHERE revoked_at IS NULL AND membership_id IN "
        "(SELECT id FROM tenant_memberships WHERE user_id IN (SELECT "
        "id FROM users WHERE mfa_required))"
    )
    op.execute(
        "UPDATE mfa_pendings SET "
        "finished_at=greatest(clock_timestamp(),created_at) WHERE "
        "finished_at IS NULL AND user_id IN (SELECT id FROM users "
        "WHERE mfa_required)"
    )
    op.execute(
        "UPDATE login_challenges SET "
        "finished_at=greatest(clock_timestamp(),created_at) WHERE "
        "finished_at IS NULL AND user_id IN (SELECT id FROM users "
        "WHERE mfa_required)"
    )


def downgrade():
    op.execute(
        "LOCK TABLE users, sessions, mailbox_challenges, "
        "identity_mail_deliveries, verified_mailboxes, "
        "security_events IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mailbox_challenges) OR "
        "EXISTS (SELECT 1 FROM identity_mail_deliveries) OR EXISTS "
        "(SELECT 1 FROM sessions WHERE mfa_reauthenticated_at IS NOT "
        "NULL) OR EXISTS (SELECT 1 FROM users WHERE mfa_required) OR "
        "EXISTS (SELECT 1 FROM security_events WHERE "
        "action='mailbox_verified') THEN RAISE EXCEPTION 'Downgrade "
        "refused: identity cutover evidence must be preserved'; END "
        "IF; END $$"
    )
    _events(OLD_ACTIONS, OLD_STATE)
    op.drop_constraint(
        op.f("ck_sessions_mfa_reauthentication_state"), "sessions", type_="check"
    )
    op.drop_constraint(
        "fk_sessions_mfa_reauthentication_credential_id_mfa_credentials",
        "sessions",
        type_="foreignkey",
    )
    op.drop_column("sessions", "mfa_reauthentication_credential_id")
    op.drop_column("sessions", "mfa_reauthenticated_at")
    op.drop_constraint(
        op.f("ck_verified_mailboxes_one_source"), "verified_mailboxes", type_="check"
    )
    op.drop_constraint(
        "fk_verified_mailbox_general_owner", "verified_mailboxes", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_verified_mailboxes_general_challenge_id",
        "verified_mailboxes",
        type_="unique",
    )
    op.drop_column("verified_mailboxes", "general_challenge_id")
    op.alter_column(
        "verified_mailboxes", "challenge_id", existing_type=sa.Uuid(), nullable=False
    )
    op.drop_table("identity_mail_deliveries")
    op.drop_table("mailbox_challenges")
