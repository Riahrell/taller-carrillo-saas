"""Opaque revocable sessions, scoped to a tenant membership."""

import sqlalchemy as sa
from alembic import op

revision = "0003_sessions"
down_revision = "0002_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sessions_tenant_membership",
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_sessions_token_hash_sha256")
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '8 hours'",
            name=op.f("ck_sessions_absolute_lifetime"),
        ),
        sa.CheckConstraint(
            "last_seen_at >= created_at AND last_seen_at < expires_at",
            name=op.f("ck_sessions_activity_window"),
        ),
        sa.CheckConstraint(
            "revoked_at >= created_at", name=op.f("ck_sessions_revocation_window")
        ),
    )
    op.create_index("ix_sessions_membership_id", "sessions", ["membership_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_membership_id", table_name="sessions")
    op.drop_table("sessions")
