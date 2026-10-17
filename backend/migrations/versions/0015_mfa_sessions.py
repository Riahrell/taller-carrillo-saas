"""Session revision and one-to-one persisted MFA evidence; no backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0015_mfa_sessions"
down_revision = "0014_mfa_login_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("security_revision", sa.BigInteger()))
    op.add_column("sessions", sa.Column("mfa_pending_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_sessions_mfa_pending_id_mfa_pendings",
        "sessions",
        "mfa_pendings",
        ["mfa_pending_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_sessions_mfa_pending_id", "sessions", ["mfa_pending_id"]
    )
    op.create_check_constraint(
        op.f("ck_sessions_security_revision_bounds"),
        "sessions",
        "security_revision >= 0",
    )
    op.create_check_constraint(
        op.f("ck_sessions_mfa_revision_present"),
        "sessions",
        "mfa_pending_id IS NULL OR security_revision IS NOT NULL",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE users, mfa_credentials, sessions IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM sessions WHERE "
        "security_revision IS NOT NULL OR mfa_pending_id IS NOT NULL) OR "
        "EXISTS (SELECT 1 FROM mfa_credentials) OR EXISTS (SELECT 1 FROM users "
        "WHERE mfa_required OR security_revision <> 0) THEN "
        "RAISE EXCEPTION 'Downgrade refused: session security evidence "
        "must be preserved'; END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_sessions_mfa_revision_present"), "sessions", type_="check"
    )
    op.drop_constraint(
        op.f("ck_sessions_security_revision_bounds"), "sessions", type_="check"
    )
    op.drop_constraint("uq_sessions_mfa_pending_id", "sessions", type_="unique")
    op.drop_constraint(
        "fk_sessions_mfa_pending_id_mfa_pendings", "sessions", type_="foreignkey"
    )
    op.drop_column("sessions", "mfa_pending_id")
    op.drop_column("sessions", "security_revision")
