"""Global insert-only evidence of MFA consumption; no historical backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0010_mfa_security_events"
down_revision = "0009_mfa_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_security_events"),
        sa.ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            ondelete="RESTRICT",
            name="fk_security_events_credential_owner",
        ),
        sa.CheckConstraint(
            "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed')",
            name=op.f("ck_security_events_action_allowed"),
        ),
        sa.CheckConstraint(
            "occurred_at >= '1970-01-01 00:00:00+00'::timestamptz",
            name=op.f("ck_security_events_occurrence_window"),
        ),
    )
    op.create_index(
        "ix_security_events_user_time", "security_events", ["user_id", "occurred_at"]
    )
    # Default privileges allow DML; restrict evidence within the DDL transaction.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON security_events FROM carrillo_app")


def downgrade() -> None:
    # Older consumers could continue without audit. Refuse even if no event yet.
    op.execute("LOCK TABLE mfa_credentials, security_events IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM security_events) "
        "OR EXISTS (SELECT 1 FROM mfa_credentials) THEN "
        "RAISE EXCEPTION 'Downgrade refused: MFA security evidence must be preserved'; "
        "END IF; END $$"
    )
    op.drop_index("ix_security_events_user_time", table_name="security_events")
    op.drop_table("security_events")
