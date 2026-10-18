"""Purpose-specific reset requests, encrypted staging and independent quotas."""

import sqlalchemy as sa
from alembic import op

revision = "0024_password_reset_requests"
down_revision = "0023_verified_mailboxes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_verified_mailboxes_user_id_id", "verified_mailboxes", ["user_id", "id"]
    )
    op.create_table(
        "password_reset_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("password_fingerprint", sa.String(64), nullable=False),
        sa.Column("security_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("key_id", sa.String(64)),
        sa.Column("nonce", sa.LargeBinary()),
        sa.Column("ciphertext", sa.LargeBinary()),
        sa.Column("discarded_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_password_reset_challenges"),
        sa.UniqueConstraint(
            "token_hash", name="uq_password_reset_challenges_token_hash"
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "verification_id"],
            ["verified_mailboxes.user_id", "verified_mailboxes.id"],
            ondelete="RESTRICT",
            name="fk_password_reset_verified_owner",
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_password_reset_challenges_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "password_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_password_reset_challenges_password_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "security_revision >= 0",
            name=op.f("ck_password_reset_challenges_revision_bounds"),
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '15 minutes'",
            name=op.f("ck_password_reset_challenges_absolute_lifetime"),
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL OR closed_at >= created_at) AND "
            "(consumed_at IS NULL OR (closed_at IS NOT NULL AND consumed_at = "
            "closed_at AND consumed_at >= created_at AND consumed_at < expires_at)) "
            "AND (closed_at IS NULL OR discarded_at IS NOT NULL)",
            name=op.f("ck_password_reset_challenges_closure_state"),
        ),
        sa.CheckConstraint(
            "(discarded_at IS NULL AND key_id IS NOT NULL AND "
            "key_id ~ '^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
            "octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
            "(discarded_at IS NOT NULL AND discarded_at >= created_at AND "
            "key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL)",
            name=op.f("ck_password_reset_challenges_payload_state"),
        ),
    )
    for column in ("user_id", "expires_at"):
        op.create_index(
            "ix_password_reset_challenges_" + column,
            "password_reset_challenges",
            [column],
        )
    op.execute("REVOKE DELETE ON password_reset_challenges FROM carrillo_app")
    op.create_table(
        "password_reset_throttles",
        sa.Column("scope", sa.String(7), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested", sa.Integer(), nullable=False),
        sa.Column("last_requested_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(
            "scope", "key_hash", name="pk_password_reset_throttles"
        ),
        sa.CheckConstraint(
            "scope IN ('address', 'ip')",
            name=op.f("ck_password_reset_throttles_scope_allowed"),
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_password_reset_throttles_key_hash_sha256"),
        ),
        sa.CheckConstraint(
            "requested >= 0 AND requested <= "
            "CASE WHEN scope = 'address' THEN 3 ELSE 20 END",
            name=op.f("ck_password_reset_throttles_requested_bounds"),
        ),
        sa.CheckConstraint(
            "(requested = 0 AND last_requested_at IS NULL) OR "
            "(requested > 0 AND last_requested_at IS NOT NULL AND "
            "last_requested_at >= window_started_at)",
            name=op.f("ck_password_reset_throttles_request_state"),
        ),
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE password_reset_throttles, users, enrollment_email_challenges, "
        "verified_mailboxes, password_reset_challenges IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM password_reset_challenges) OR "
        "EXISTS (SELECT 1 FROM password_reset_throttles) THEN RAISE EXCEPTION "
        "'Downgrade refused: password reset evidence and quotas must be preserved'; "
        "END IF; END $$"
    )
    op.drop_table("password_reset_throttles")
    for column in ("expires_at", "user_id"):
        op.drop_index(
            "ix_password_reset_challenges_" + column,
            table_name="password_reset_challenges",
        )
    op.drop_table("password_reset_challenges")
    op.drop_constraint(
        "uq_verified_mailboxes_user_id_id", "verified_mailboxes", type_="unique"
    )
