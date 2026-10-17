"""Persisted login MFA stage; no HTTP/session activation or proof backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0014_mfa_login_proof"
down_revision = "0013_mfa_pendings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mfa_pendings", sa.Column("credential_id", sa.Uuid()))
    op.add_column(
        "mfa_pendings", sa.Column("factor_verified_at", sa.DateTime(timezone=True))
    )
    op.create_foreign_key(
        "fk_mfa_pendings_credential_owner",
        "mfa_pendings",
        "mfa_credentials",
        ["user_id", "credential_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_mfa_pendings_purpose_allowed"), "mfa_pendings", type_="check"
    )
    op.drop_constraint(
        op.f("ck_mfa_pendings_absolute_lifetime"), "mfa_pendings", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_mfa_pendings_purpose_allowed"),
        "mfa_pendings",
        "purpose IN ('login_totp', 'enrollment', 'login_verified')",
    )
    op.create_check_constraint(
        op.f("ck_mfa_pendings_absolute_lifetime"),
        "mfa_pendings",
        "expires_at = password_verified_at + CASE WHEN purpose = 'enrollment' "
        "THEN interval '15 minutes' ELSE interval '5 minutes' END",
    )
    op.create_check_constraint(
        op.f("ck_mfa_pendings_factor_state"),
        "mfa_pendings",
        "(purpose = 'login_verified' AND credential_id IS NOT NULL "
        "AND factor_verified_at IS NOT NULL "
        "AND factor_verified_at >= password_verified_at "
        "AND factor_verified_at <= created_at) OR "
        "(purpose IN ('login_totp', 'enrollment') AND credential_id IS NULL "
        "AND factor_verified_at IS NULL)",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE mfa_pendings IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mfa_pendings WHERE "
        "purpose = 'login_verified') THEN RAISE EXCEPTION "
        "'Downgrade refused: MFA login evidence must be preserved'; END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_mfa_pendings_factor_state"), "mfa_pendings", type_="check"
    )
    op.drop_constraint(
        op.f("ck_mfa_pendings_purpose_allowed"), "mfa_pendings", type_="check"
    )
    op.drop_constraint(
        op.f("ck_mfa_pendings_absolute_lifetime"), "mfa_pendings", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_mfa_pendings_purpose_allowed"),
        "mfa_pendings",
        "purpose IN ('login_totp', 'enrollment')",
    )
    op.create_check_constraint(
        op.f("ck_mfa_pendings_absolute_lifetime"),
        "mfa_pendings",
        "expires_at = password_verified_at + CASE WHEN purpose = 'login_totp' "
        "THEN interval '5 minutes' ELSE interval '15 minutes' END",
    )
    op.drop_constraint(
        "fk_mfa_pendings_credential_owner", "mfa_pendings", type_="foreignkey"
    )
    op.drop_column("mfa_pendings", "factor_verified_at")
    op.drop_column("mfa_pendings", "credential_id")
