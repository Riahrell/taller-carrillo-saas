"""Restricted offline recovery permits. No factor replacement or backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0020_mfa_recovery_permits"
down_revision = "0019_mfa_enrollment"
branch_labels = None
depends_on = None

OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', 'mfa_enrollment_email_confirmed', 'mfa_enrolled')"
)
OLD_STATE = (
    "(action = 'security_revision_advanced' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action = 'mfa_enrollment_email_confirmed' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision >= 0) OR "
    "(action = 'mfa_enrolled' AND credential_id IS NOT NULL AND "
    "security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action IN ('mfa_totp_consumed', 'mfa_recovery_consumed') AND "
    "credential_id IS NOT NULL AND security_revision IS NULL)"
)


def _events(actions, state):
    for name in ("action_allowed", "action_state"):
        op.drop_constraint(
            op.f("ck_security_events_" + name), "security_events", type_="check"
        )
    op.create_check_constraint(
        op.f("ck_security_events_action_allowed"), "security_events", actions
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_state"), "security_events", state
    )


def upgrade() -> None:
    op.create_table(
        "mfa_recovery_permits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("pending_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("set_id", sa.Uuid(), nullable=False),
        sa.Column("code_position", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("security_revision", sa.BigInteger(), nullable=False),
        sa.Column("password_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offline_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_mfa_recovery_permits"),
        sa.UniqueConstraint("pending_id", name="uq_mfa_recovery_permits_pending_id"),
        sa.UniqueConstraint("token_hash", name="uq_mfa_recovery_permits_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id", "pending_id"],
            ["mfa_pendings.user_id", "mfa_pendings.id"],
            ondelete="RESTRICT",
            name="fk_recovery_permit_pending_owner",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            ondelete="RESTRICT",
            name="fk_recovery_permit_factor_owner",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["mfa_recovery_sets.id"],
            ondelete="RESTRICT",
            name="fk_mfa_recovery_permits_set_id_mfa_recovery_sets",
        ),
        sa.ForeignKeyConstraint(
            ["set_id", "code_position"],
            ["mfa_recovery_codes.set_id", "mfa_recovery_codes.position"],
            ondelete="RESTRICT",
            name="fk_recovery_permit_consumed_code",
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_mfa_recovery_permits_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "security_revision >= 0",
            name=op.f("ck_mfa_recovery_permits_revision_bounds"),
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '15 minutes'",
            name=op.f("ck_mfa_recovery_permits_absolute_lifetime"),
        ),
        sa.CheckConstraint(
            "password_verified_at <= offline_verified_at AND offline_verified_at "
            "<= created_at AND created_at < password_verified_at + "
            "interval '5 minutes'",
            name=op.f("ck_mfa_recovery_permits_fresh_password"),
        ),
        sa.CheckConstraint(
            "used_at >= created_at AND used_at < expires_at",
            name=op.f("ck_mfa_recovery_permits_use_window"),
        ),
        sa.CheckConstraint(
            "closed_at >= created_at AND (used_at IS NULL OR closed_at >= used_at)",
            name=op.f("ck_mfa_recovery_permits_closure_window"),
        ),
    )
    op.create_index(
        "ix_mfa_recovery_permits_user_id", "mfa_recovery_permits", ["user_id"]
    )
    op.create_index(
        "ix_mfa_recovery_permits_expires_at", "mfa_recovery_permits", ["expires_at"]
    )
    op.create_index(
        "uq_mfa_recovery_permits_open_user",
        "mfa_recovery_permits",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL AND used_at IS NULL"),
    )
    op.execute("REVOKE DELETE ON mfa_recovery_permits FROM carrillo_app")
    _events(
        OLD_ACTIONS[:-1] + ", 'mfa_recovery_started')",
        OLD_STATE
        + " OR (action = 'mfa_recovery_started' AND credential_id IS NOT NULL "
        "AND security_revision IS NOT NULL AND security_revision >= 0)",
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE mfa_throttles, users, mfa_pendings, mfa_credentials, "
        "mfa_recovery_sets, mfa_recovery_codes, mfa_recovery_permits, security_events "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mfa_recovery_permits) OR EXISTS "
        "(SELECT 1 FROM security_events WHERE action = 'mfa_recovery_started') THEN "
        "RAISE EXCEPTION 'Downgrade refused: MFA recovery evidence must be preserved'; "
        "END IF; END $$"
    )
    _events(OLD_ACTIONS, OLD_STATE)
    op.drop_index(
        "uq_mfa_recovery_permits_open_user", table_name="mfa_recovery_permits"
    )
    op.drop_index(
        "ix_mfa_recovery_permits_expires_at", table_name="mfa_recovery_permits"
    )
    op.drop_index("ix_mfa_recovery_permits_user_id", table_name="mfa_recovery_permits")
    op.drop_table("mfa_recovery_permits")
