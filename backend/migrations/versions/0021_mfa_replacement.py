"""Offline-recovery replacement setup and atomic successor evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0021_mfa_replacement"
down_revision = "0020_mfa_recovery_permits"
branch_labels = None
depends_on = None

OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced', 'mfa_enrollment_email_confirmed', "
    "'mfa_enrolled', 'mfa_recovery_started')"
)
OLD_STATE = (
    "(action = 'security_revision_advanced' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action = 'mfa_enrollment_email_confirmed' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision >= 0) OR "
    "(action = 'mfa_enrolled' AND credential_id IS NOT NULL AND "
    "security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action = 'mfa_recovery_started' AND credential_id IS NOT NULL AND "
    "security_revision IS NOT NULL AND security_revision >= 0) OR "
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
        "mfa_replacements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recovery_permit_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key_id", sa.String(64)),
        sa.Column("nonce", sa.LargeBinary()),
        sa.Column("ciphertext", sa.LargeBinary()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_mfa_replacements"),
        sa.UniqueConstraint(
            "recovery_permit_id", name="uq_mfa_replacements_recovery_permit_id"
        ),
        sa.UniqueConstraint("token_hash", name="uq_mfa_replacements_token_hash"),
        sa.ForeignKeyConstraint(
            ["recovery_permit_id"],
            ["mfa_recovery_permits.id"],
            ondelete="RESTRICT",
            name="fk_mfa_replacement_permit",
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_mfa_replacements_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "interval '15 minutes'",
            name=op.f("ck_mfa_replacements_lifetime_bounds"),
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND activated_at IS NULL AND key_id IS NOT NULL "
            "AND key_id ~ '^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
            "octet_length(ciphertext) = 36) OR (closed_at IS NOT NULL AND "
            "closed_at >= created_at AND key_id IS NULL AND nonce IS NULL AND "
            "ciphertext IS NULL AND (activated_at IS NULL OR "
            "(activated_at = closed_at AND activated_at < expires_at)))",
            name=op.f("ck_mfa_replacements_stage_state"),
        ),
    )
    op.create_index(
        "ix_mfa_replacements_expires_at", "mfa_replacements", ["expires_at"]
    )
    op.execute("REVOKE DELETE ON mfa_replacements FROM carrillo_app")
    _events(
        OLD_ACTIONS[:-1] + ", 'mfa_replaced')",
        OLD_STATE + " OR (action = 'mfa_replaced' AND credential_id IS NOT NULL AND "
        "security_revision IS NOT NULL AND security_revision > 0)",
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE mfa_throttles, users, mfa_pendings, mfa_credentials, "
        "mfa_recovery_sets, mfa_recovery_codes, mfa_recovery_permits, "
        "mfa_replacements, "
        "security_events IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mfa_replacements) OR EXISTS "
        "(SELECT 1 FROM security_events WHERE action = 'mfa_replaced') THEN RAISE "
        "EXCEPTION 'Downgrade refused: MFA replacement evidence must be preserved'; "
        "END IF; END $$"
    )
    _events(OLD_ACTIONS, OLD_STATE)
    op.drop_index("ix_mfa_replacements_expires_at", table_name="mfa_replacements")
    op.drop_table("mfa_replacements")
