"""Persist short-lived verified identities before selecting a membership."""

import sqlalchemy as sa
from alembic import op

revision = "0005_login_challenges"
down_revision = "0004_login_throttles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("password_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_login_challenges"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_login_challenges_user_id_users",
        ),
        sa.UniqueConstraint("token_hash", name="uq_login_challenges_token_hash"),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_login_challenges_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "binding_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_login_challenges_binding_hash_sha256"),
        ),
        sa.CheckConstraint(
            "password_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_login_challenges_password_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '5 minutes'",
            name=op.f("ck_login_challenges_absolute_lifetime"),
        ),
        sa.CheckConstraint(
            "finished_at >= created_at", name=op.f("ck_login_challenges_finish_window")
        ),
    )
    op.create_index("ix_login_challenges_user_id", "login_challenges", ["user_id"])
    op.create_index(
        "ix_login_challenges_expires_at", "login_challenges", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_login_challenges_expires_at", table_name="login_challenges")
    op.drop_index("ix_login_challenges_user_id", table_name="login_challenges")
    op.drop_table("login_challenges")
