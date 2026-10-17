"""Atomic encrypted MFA mail staging and independent account/IP quotas."""

import sqlalchemy as sa
from alembic import op

revision = "0017_enrollment_mail_outbox"
down_revision = "0016_enrollment_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "enrollment_mail_outbox",
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key_id", sa.String(64)),
        sa.Column("nonce", sa.LargeBinary()),
        sa.Column("ciphertext", sa.LargeBinary()),
        sa.Column("discarded_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("challenge_id", name="pk_enrollment_mail_outbox"),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["enrollment_email_challenges.id"],
            ondelete="RESTRICT",
            name="fk_mail_outbox_challenge",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "interval '15 minutes'",
            name=op.f("ck_enrollment_mail_outbox_lifetime_bounds"),
        ),
        sa.CheckConstraint(
            "(discarded_at IS NULL AND key_id IS NOT NULL AND "
            "key_id ~ '^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
            "octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
            "(discarded_at IS NOT NULL AND discarded_at >= created_at AND "
            "key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL)",
            name=op.f("ck_enrollment_mail_outbox_payload_state"),
        ),
    )
    op.create_index(
        "ix_enrollment_mail_outbox_expires_at", "enrollment_mail_outbox", ["expires_at"]
    )
    op.execute("REVOKE DELETE ON enrollment_mail_outbox FROM carrillo_app")
    op.create_table(
        "enrollment_mail_throttles",
        sa.Column("scope", sa.String(7), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued", sa.Integer(), nullable=False),
        sa.Column("last_issued_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(
            "scope", "key_hash", name="pk_enrollment_mail_throttles"
        ),
        sa.CheckConstraint(
            "scope IN ('account', 'ip')",
            name=op.f("ck_enrollment_mail_throttles_scope_allowed"),
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_enrollment_mail_throttles_key_hash_sha256"),
        ),
        sa.CheckConstraint(
            "issued >= 0 AND issued <= CASE WHEN scope = 'account' THEN 3 ELSE 20 END",
            name=op.f("ck_enrollment_mail_throttles_issued_bounds"),
        ),
        sa.CheckConstraint(
            "(issued = 0 AND last_issued_at IS NULL) OR "
            "(issued > 0 AND last_issued_at IS NOT NULL AND "
            "last_issued_at >= window_started_at)",
            name=op.f("ck_enrollment_mail_throttles_issuance_state"),
        ),
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE users, mfa_pendings, enrollment_mail_throttles, "
        "enrollment_email_challenges, enrollment_mail_outbox "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM enrollment_mail_outbox) OR "
        "EXISTS (SELECT 1 FROM enrollment_mail_throttles) THEN RAISE EXCEPTION "
        "'Downgrade refused: mail evidence and quotas must be preserved'; "
        "END IF; END $$"
    )
    op.drop_table("enrollment_mail_throttles")
    op.drop_index(
        "ix_enrollment_mail_outbox_expires_at", table_name="enrollment_mail_outbox"
    )
    op.drop_table("enrollment_mail_outbox")
