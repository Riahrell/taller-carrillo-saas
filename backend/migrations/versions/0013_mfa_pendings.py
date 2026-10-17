"""Restricted MFA pendings; no authentication/HTTP activation."""

import sqlalchemy as sa
from alembic import op

revision = "0013_mfa_pendings"
down_revision = "0012_user_security_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mfa_pendings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("security_revision", sa.BigInteger(), nullable=False),
        sa.Column("password_fingerprint", sa.String(64), nullable=False),
        sa.Column("password_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_mfa_pendings"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_mfa_pendings_user_id_users",
        ),
        sa.UniqueConstraint("token_hash", name="uq_mfa_pendings_token_hash"),
        sa.CheckConstraint(
            "purpose IN ('login_totp', 'enrollment')",
            name=op.f("ck_mfa_pendings_purpose_allowed"),
        ),
        sa.CheckConstraint(
            "security_revision >= 0", name=op.f("ck_mfa_pendings_revision_bounds")
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_mfa_pendings_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "binding_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_mfa_pendings_binding_hash_sha256"),
        ),
        sa.CheckConstraint(
            "password_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_mfa_pendings_password_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "created_at >= password_verified_at AND created_at < expires_at",
            name=op.f("ck_mfa_pendings_creation_window"),
        ),
        sa.CheckConstraint(
            "expires_at = password_verified_at + CASE WHEN purpose = 'login_totp' "
            "THEN interval '5 minutes' ELSE interval '15 minutes' END",
            name=op.f("ck_mfa_pendings_absolute_lifetime"),
        ),
        sa.CheckConstraint(
            "finished_at >= created_at", name=op.f("ck_mfa_pendings_finish_window")
        ),
    )
    op.create_index("ix_mfa_pendings_user_id", "mfa_pendings", ["user_id"])
    op.create_index("ix_mfa_pendings_expires_at", "mfa_pendings", ["expires_at"])
    op.execute("REVOKE DELETE ON mfa_pendings FROM carrillo_app")


def downgrade() -> None:
    op.execute("LOCK TABLE mfa_pendings IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mfa_pendings) THEN "
        "RAISE EXCEPTION 'Downgrade refused: MFA pending state must be preserved'; "
        "END IF; END $$"
    )
    op.drop_index("ix_mfa_pendings_expires_at", table_name="mfa_pendings")
    op.drop_index("ix_mfa_pendings_user_id", table_name="mfa_pendings")
    op.drop_table("mfa_pendings")
