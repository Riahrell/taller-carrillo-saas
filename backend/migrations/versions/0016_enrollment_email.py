"""Browser-bound initial MFA mailbox challenges; no email sending or backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0016_enrollment_email"
down_revision = "0015_mfa_sessions"
branch_labels = None
depends_on = None

OLD_ACTIONS = (
    "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
    "'security_revision_advanced')"
)
OLD_STATE = (
    "(action = 'security_revision_advanced' AND credential_id IS NULL "
    "AND security_revision IS NOT NULL AND security_revision > 0) OR "
    "(action IN ('mfa_totp_consumed', 'mfa_recovery_consumed') "
    "AND credential_id IS NOT NULL AND security_revision IS NULL)"
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_mfa_pendings_user_id_id", "mfa_pendings", ["user_id", "id"]
    )
    op.create_table(
        "enrollment_email_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("pending_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("permit_token_hash", sa.String(64)),
        sa.Column("permit_used_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_enrollment_email_challenges"),
        sa.ForeignKeyConstraint(
            ["user_id", "pending_id"],
            ["mfa_pendings.user_id", "mfa_pendings.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_email_challenges_pending_owner",
        ),
        sa.UniqueConstraint(
            "pending_id", name="uq_enrollment_email_challenges_pending_id"
        ),
        sa.UniqueConstraint(
            "token_hash", name="uq_enrollment_email_challenges_token_hash"
        ),
        sa.UniqueConstraint(
            "permit_token_hash", name="uq_enrollment_email_challenges_permit_token_hash"
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_enrollment_email_challenges_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "email_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_enrollment_email_challenges_email_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval "
            "'15 minutes'",
            name=op.f("ck_enrollment_email_challenges_lifetime_bounds"),
        ),
        sa.CheckConstraint(
            "closed_at >= created_at AND (consumed_at IS NULL OR closed_at "
            ">= consumed_at)",
            name=op.f("ck_enrollment_email_challenges_closure_window"),
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL AND permit_token_hash IS NULL AND "
            "permit_used_at IS NULL) OR (consumed_at IS NOT NULL AND "
            "consumed_at >= created_at AND consumed_at < expires_at AND "
            "permit_token_hash IS NOT NULL AND permit_token_hash ~ "
            "'^[0-9a-f]{64}$' AND (permit_used_at IS NULL OR (permit_used_at "
            ">= consumed_at AND permit_used_at < expires_at)))",
            name=op.f("ck_enrollment_email_challenges_proof_state"),
        ),
    )
    op.create_index(
        "ix_enrollment_email_challenges_user_id",
        "enrollment_email_challenges",
        ["user_id"],
    )
    op.create_index(
        "ix_enrollment_email_challenges_expires_at",
        "enrollment_email_challenges",
        ["expires_at"],
    )
    op.execute("REVOKE DELETE ON enrollment_email_challenges FROM carrillo_app")
    op.drop_constraint(
        op.f("ck_security_events_action_allowed"), "security_events", type_="check"
    )
    op.drop_constraint(
        op.f("ck_security_events_action_state"), "security_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_allowed"),
        "security_events",
        "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
        "'security_revision_advanced', 'mfa_enrollment_email_confirmed')",
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_state"),
        "security_events",
        OLD_STATE
        + " OR (action = 'mfa_enrollment_email_confirmed' AND credential_id IS NULL "
        "AND security_revision IS NOT NULL AND security_revision >= 0)",
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE users, mfa_pendings, enrollment_email_challenges, "
        "security_events IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM "
        "enrollment_email_challenges) OR EXISTS (SELECT 1 FROM "
        "security_events WHERE action = "
        "'mfa_enrollment_email_confirmed') THEN RAISE EXCEPTION "
        "'Downgrade refused: enrollment mailbox evidence must be "
        "preserved'; END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_security_events_action_allowed"), "security_events", type_="check"
    )
    op.drop_constraint(
        op.f("ck_security_events_action_state"), "security_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_allowed"), "security_events", OLD_ACTIONS
    )
    op.create_check_constraint(
        op.f("ck_security_events_action_state"), "security_events", OLD_STATE
    )
    op.drop_index(
        "ix_enrollment_email_challenges_expires_at",
        table_name="enrollment_email_challenges",
    )
    op.drop_index(
        "ix_enrollment_email_challenges_user_id",
        table_name="enrollment_email_challenges",
    )
    op.drop_table("enrollment_email_challenges")
    op.drop_constraint("uq_mfa_pendings_user_id_id", "mfa_pendings", type_="unique")
