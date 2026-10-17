"""Optional server-only confirmation; existing sessions are never backfilled."""

import sqlalchemy as sa
from alembic import op

revision = "0008_session_reauthentication"
down_revision = "0007_membership_creation_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions", sa.Column("reauthenticated_at", sa.DateTime(timezone=True))
    )
    op.add_column("sessions", sa.Column("reauthentication_fingerprint", sa.String(64)))
    op.create_check_constraint(
        op.f("ck_sessions_reauthentication_state"),
        "sessions",
        "(reauthenticated_at IS NULL AND reauthentication_fingerprint IS NULL) OR "
        "(reauthenticated_at IS NOT NULL AND reauthentication_fingerprint IS NOT NULL "
        "AND reauthenticated_at >= created_at AND reauthenticated_at < expires_at "
        "AND reauthentication_fingerprint ~ '^[0-9a-f]{64}$')",
    )


def downgrade() -> None:
    # A downgraded application must not inherit tokens elevated by this service.
    op.execute("LOCK TABLE sessions IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "UPDATE sessions SET revoked_at = GREATEST(created_at, clock_timestamp()) "
        "WHERE reauthenticated_at IS NOT NULL AND revoked_at IS NULL"
    )
    op.drop_constraint(
        op.f("ck_sessions_reauthentication_state"), "sessions", type_="check"
    )
    op.drop_column("sessions", "reauthentication_fingerprint")
    op.drop_column("sessions", "reauthenticated_at")
