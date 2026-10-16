"""Global account/IP login counters shared by all application processes."""

import sqlalchemy as sa
from alembic import op

revision = "0004_login_throttles"
down_revision = "0003_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("scope", sa.String(7), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "key_hash", name="pk_login_throttles"),
        sa.CheckConstraint(
            "scope IN ('account', 'ip')", name=op.f("ck_login_throttles_scope_allowed")
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_login_throttles_key_hash_sha256"),
        ),
        sa.CheckConstraint(
            "failures >= 0 AND failures <= "
            "CASE WHEN scope = 'account' THEN 5 ELSE 30 END",
            name=op.f("ck_login_throttles_failure_bounds"),
        ),
    )
    op.create_index(
        "ix_login_throttles_window_started_at", "login_throttles", ["window_started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_login_throttles_window_started_at", table_name="login_throttles")
    op.drop_table("login_throttles")
