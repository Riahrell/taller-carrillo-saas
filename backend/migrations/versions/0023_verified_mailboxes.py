"""Explicit exact-address verification, with no historical backfill or reset token."""

import sqlalchemy as sa
from alembic import op

revision = "0023_verified_mailboxes"
down_revision = "0022_mfa_code_regeneration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_enrollment_mail_user_id_id",
        "enrollment_email_challenges",
        ["user_id", "id"],
    )
    op.create_table(
        "verified_mailboxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_verified_mailboxes"),
        sa.UniqueConstraint("challenge_id", name="uq_verified_mailboxes_challenge_id"),
        sa.ForeignKeyConstraint(
            ["user_id", "challenge_id"],
            ["enrollment_email_challenges.user_id", "enrollment_email_challenges.id"],
            ondelete="RESTRICT",
            name="fk_verified_mailbox_challenge_owner",
        ),
        sa.CheckConstraint(
            "address_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_verified_mailboxes_address_hash_sha256"),
        ),
        sa.CheckConstraint(
            "verified_at >= '1970-01-01 00:00:00+00'::timestamptz AND "
            "(invalidated_at IS NULL OR invalidated_at >= verified_at)",
            name=op.f("ck_verified_mailboxes_verification_window"),
        ),
    )
    op.create_index(
        "uq_verified_mailboxes_current_user",
        "verified_mailboxes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("invalidated_at IS NULL"),
    )
    op.execute("REVOKE DELETE ON verified_mailboxes FROM carrillo_app")
    op.execute("""
        CREATE FUNCTION invalidate_changed_mailbox() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.email IS DISTINCT FROM OLD.email THEN
                UPDATE verified_mailboxes
                SET invalidated_at = greatest(clock_timestamp(), verified_at)
                WHERE user_id = OLD.id AND invalidated_at IS NULL;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION invalidate_changed_mailbox() FROM PUBLIC, carrillo_app"
    )
    op.execute(
        "CREATE TRIGGER users_mailbox_change AFTER UPDATE OF email ON users "
        "FOR EACH ROW EXECUTE FUNCTION invalidate_changed_mailbox()"
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE users, enrollment_email_challenges, verified_mailboxes "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM verified_mailboxes) THEN RAISE "
        "EXCEPTION 'Downgrade refused: verified mailbox evidence must be preserved'; "
        "END IF; END $$"
    )
    op.execute("DROP TRIGGER users_mailbox_change ON users")
    op.execute("DROP FUNCTION invalidate_changed_mailbox()")
    op.drop_index("uq_verified_mailboxes_current_user", table_name="verified_mailboxes")
    op.drop_table("verified_mailboxes")
    op.drop_constraint(
        "uq_enrollment_mail_user_id_id", "enrollment_email_challenges", type_="unique"
    )
