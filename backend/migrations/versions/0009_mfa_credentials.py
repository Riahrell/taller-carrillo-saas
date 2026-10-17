"""Global encrypted MFA storage, without enrollment, backfill or login activation."""

import sqlalchemy as sa
from alembic import op

revision = "0009_mfa_credentials"
down_revision = "0008_session_reauthentication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mfa_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("encryption_version", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_counter", sa.BigInteger()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_mfa_credentials"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_mfa_credentials_user_id_users",
        ),
        sa.UniqueConstraint("user_id", "id", name="uq_mfa_credentials_user_id"),
        sa.CheckConstraint(
            "encryption_version = 1", name=op.f("ck_mfa_credentials_encryption_version")
        ),
        sa.CheckConstraint(
            "key_id ~ '^[A-Za-z0-9_-]{1,64}$'",
            name=op.f("ck_mfa_credentials_key_id_format"),
        ),
        sa.CheckConstraint(
            "octet_length(nonce) = 12", name=op.f("ck_mfa_credentials_nonce_length")
        ),
        sa.CheckConstraint(
            "octet_length(ciphertext) = 36",
            name=op.f("ck_mfa_credentials_ciphertext_length"),
        ),
        sa.CheckConstraint(
            "revoked_at >= created_at",
            name=op.f("ck_mfa_credentials_revocation_window"),
        ),
        sa.CheckConstraint(
            "(last_counter IS NULL AND last_verified_at IS NULL) OR "
            "(last_counter IS NOT NULL AND last_verified_at IS NOT NULL "
            "AND last_counter >= 0 AND last_verified_at >= created_at)",
            name=op.f("ck_mfa_credentials_consumption_state"),
        ),
    )
    op.create_index(
        "uq_mfa_credentials_current_user",
        "mfa_credentials",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "mfa_recovery_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_mfa_recovery_sets"),
        sa.ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            ondelete="RESTRICT",
            name="fk_mfa_recovery_sets_credential_owner",
        ),
        sa.CheckConstraint(
            "invalidated_at >= created_at",
            name=op.f("ck_mfa_recovery_sets_invalidation_window"),
        ),
    )
    op.create_index(
        "uq_mfa_recovery_sets_current_credential",
        "mfa_recovery_sets",
        ["credential_id"],
        unique=True,
        postgresql_where=sa.text("invalidated_at IS NULL"),
    )
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("set_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("set_id", "position", name="pk_mfa_recovery_codes"),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["mfa_recovery_sets.id"],
            ondelete="RESTRICT",
            name="fk_mfa_recovery_codes_set_id_mfa_recovery_sets",
        ),
        sa.UniqueConstraint("set_id", "code_hash", name="uq_mfa_recovery_codes_set_id"),
        sa.CheckConstraint(
            "position >= 0 AND position < 10",
            name=op.f("ck_mfa_recovery_codes_position_bounds"),
        ),
        sa.CheckConstraint(
            "octet_length(code_hash) = 32",
            name=op.f("ck_mfa_recovery_codes_hash_length"),
        ),
        sa.CheckConstraint(
            "used_at >= '1970-01-01 00:00:00+00'::timestamptz",
            name=op.f("ck_mfa_recovery_codes_use_window"),
        ),
    )

    # Retain factors/consumption. Lifecycle uses revocation, not runtime deletion.
    op.execute(
        "REVOKE DELETE ON mfa_credentials, mfa_recovery_sets, mfa_recovery_codes "
        "FROM carrillo_app"
    )


def downgrade() -> None:
    # Preserve even revoked factors: forgetting security state can enable bypass.
    op.execute(
        "LOCK TABLE mfa_credentials, mfa_recovery_sets, mfa_recovery_codes "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mfa_credentials) THEN "
        "RAISE EXCEPTION 'Downgrade refused: MFA state must be preserved'; "
        "END IF; END $$"
    )
    op.drop_table("mfa_recovery_codes")
    op.drop_index(
        "uq_mfa_recovery_sets_current_credential", table_name="mfa_recovery_sets"
    )
    op.drop_table("mfa_recovery_sets")
    op.drop_index("uq_mfa_credentials_current_user", table_name="mfa_credentials")
    op.drop_table("mfa_credentials")
