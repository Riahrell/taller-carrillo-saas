"""Email OTP and fixed fifteen-day browser verification."""

import sqlalchemy as sa
from alembic import op

revision = "0037_email_browser_login"
down_revision = "0036_purchase_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE verified_browsers (
id UUID NOT NULL,
user_id UUID NOT NULL,
token_hash VARCHAR(64) NOT NULL,
security_revision BIGINT NOT NULL,
email_fingerprint VARCHAR(64) NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
revoked_at TIMESTAMP WITH TIME ZONE,
CONSTRAINT pk_verified_browsers PRIMARY KEY (id),
CONSTRAINT ck_verified_browsers_fixed_lifetime CHECK (expires_at = created_at
+ interval '15 days'),
CONSTRAINT ck_verified_browsers_token_hash CHECK (token_hash ~
'^[0-9a-f]{64}$'),
CONSTRAINT fk_verified_browsers_user_id_users FOREIGN KEY(user_id) REFERENCES
users (id) ON DELETE RESTRICT,
CONSTRAINT uq_verified_browsers_token_hash UNIQUE (token_hash)
)""")
    op.execute(
        """CREATE INDEX ix_verified_browsers_user_id ON verified_browsers (user_id)"""
    )
    op.execute("""CREATE TABLE email_login_challenges (
id UUID NOT NULL,
user_id UUID NOT NULL,
token_hash VARCHAR(64) NOT NULL,
binding_hash VARCHAR(64) NOT NULL,
security_revision BIGINT NOT NULL,
password_fingerprint VARCHAR(64) NOT NULL,
email_fingerprint VARCHAR(64) NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
code_hash VARCHAR(64),
failures INTEGER NOT NULL,
browser_id UUID,
verified_at TIMESTAMP WITH TIME ZONE,
finished_at TIMESTAMP WITH TIME ZONE,
invalidated_at TIMESTAMP WITH TIME ZONE,
send_state VARCHAR(8) NOT NULL,
key_id VARCHAR(64),
nonce BYTEA,
ciphertext BYTEA,
CONSTRAINT pk_email_login_challenges PRIMARY KEY (id),
CONSTRAINT ck_email_login_challenges_lifetime CHECK (expires_at > created_at
AND expires_at <= created_at + interval '10 minutes'),
CONSTRAINT ck_email_login_challenges_attempts CHECK (failures BETWEEN 0 AND
5),
CONSTRAINT ck_email_login_challenges_send_state CHECK (send_state IN ('none',
'pending', 'claimed', 'sent', 'failed')),
CONSTRAINT ck_email_login_challenges_opaque_hashes CHECK (token_hash ~
'^[0-9a-f]{64}$' AND binding_hash ~ '^[0-9a-f]{64}$'),
CONSTRAINT ck_email_login_challenges_encrypted_payload CHECK ((key_id IS NULL
AND nonce IS NULL AND ciphertext IS NULL) OR (key_id IS NOT NULL AND
octet_length(nonce) = 12 AND ciphertext IS NOT NULL)),
CONSTRAINT ck_email_login_challenges_verified_state CHECK (verified_at IS NULL
OR (browser_id IS NOT NULL AND verified_at >= created_at AND verified_at <
expires_at)),
CONSTRAINT ck_email_login_challenges_finished_state CHECK (finished_at IS NULL
OR (verified_at IS NOT NULL AND finished_at >= verified_at AND finished_at <
expires_at)),
CONSTRAINT fk_email_login_challenges_user_id_users FOREIGN KEY(user_id)
REFERENCES users (id) ON DELETE RESTRICT,
CONSTRAINT uq_email_login_challenges_token_hash UNIQUE (token_hash),
CONSTRAINT fk_email_login_challenges_browser_id_verified_browsers FOREIGN
KEY(browser_id) REFERENCES verified_browsers (id) ON DELETE RESTRICT
)""")
    op.execute(
        """CREATE INDEX ix_email_login_challenges_user_id ON email_login_challenges
(user_id)"""
    )
    op.execute(
        """CREATE INDEX ix_email_login_challenges_binding_hash ON email_login_challenges
(binding_hash)"""
    )
    op.execute(
        """ALTER TABLE sessions ADD COLUMN email_login_id UUID
CONSTRAINT uq_sessions_email_login_id UNIQUE
REFERENCES email_login_challenges(id) ON DELETE RESTRICT"""
    )
    op.execute(
        """ALTER TABLE sessions ADD CONSTRAINT ck_sessions_single_login_method CHECK
(mfa_pending_id IS NULL OR email_login_id IS NULL)"""
    )
    op.execute(
        """GRANT SELECT, INSERT, UPDATE ON verified_browsers, email_login_challenges TO
carrillo_app"""
    )
    op.execute(
        """REVOKE DELETE, TRUNCATE ON verified_browsers, email_login_challenges FROM
carrillo_app"""
    )

    op.execute(
        "ALTER TABLE verified_mailboxes ADD COLUMN "
        "email_login_challenge_id UUID "
        "CONSTRAINT uq_verified_mailboxes_email_login_challenge_id UNIQUE REFERENCES "
        "email_login_challenges(id) ON DELETE RESTRICT"
    )
    op.drop_constraint("ck_verified_mailboxes_one_source", "verified_mailboxes")
    op.create_check_constraint(
        "ck_verified_mailboxes_one_source",
        "verified_mailboxes",
        "num_nonnulls(challenge_id, general_challenge_id, "
        "email_login_challenge_id) = 1",
    )


def downgrade():
    op.execute(
        "LOCK TABLE sessions, email_login_challenges, verified_browsers"
        " IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM email_login_challenges) OR EXISTS"
            " (SELECT 1 FROM verified_browsers)"
        )
    ):
        raise RuntimeError(
            "Email login evidence exists; refusing destructive downgrade"
        )
    op.drop_constraint("ck_verified_mailboxes_one_source", "verified_mailboxes")
    op.drop_column("verified_mailboxes", "email_login_challenge_id")
    op.create_check_constraint(
        "ck_verified_mailboxes_one_source",
        "verified_mailboxes",
        "num_nonnulls(challenge_id, general_challenge_id) = 1",
    )
    op.drop_constraint("ck_sessions_single_login_method", "sessions")
    op.drop_column("sessions", "email_login_id")
    op.drop_table("email_login_challenges")
    op.drop_table("verified_browsers")
