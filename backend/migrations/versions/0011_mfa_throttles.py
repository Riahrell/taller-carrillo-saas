"""Independent account/IP MFA failure quotas; no login activation or backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0011_mfa_throttles"
down_revision = "0010_mfa_security_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mfa_throttles",
        sa.Column("scope", sa.String(7), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "key_hash", name="pk_mfa_throttles"),
        sa.CheckConstraint(
            "scope IN ('account', 'ip')", name=op.f("ck_mfa_throttles_scope_allowed")
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_mfa_throttles_key_hash_sha256")
        ),
        sa.CheckConstraint(
            "failures >= 0 AND failures <= "
            "CASE WHEN scope = 'account' THEN 5 ELSE 30 END",
            name=op.f("ck_mfa_throttles_failure_bounds"),
        ),
    )
    op.create_index(
        "ix_mfa_throttles_window_started_at", "mfa_throttles", ["window_started_at"]
    )
    op.execute("REVOKE DELETE ON mfa_throttles FROM carrillo_app")


def downgrade() -> None:
    op.execute("LOCK TABLE mfa_throttles, mfa_credentials IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mfa_throttles) "
        "OR EXISTS (SELECT 1 FROM mfa_credentials) THEN "
        "RAISE EXCEPTION 'Downgrade refused: MFA quota state must be preserved'; "
        "END IF; END $$"
    )
    op.drop_index("ix_mfa_throttles_window_started_at", table_name="mfa_throttles")
    op.drop_table("mfa_throttles")
