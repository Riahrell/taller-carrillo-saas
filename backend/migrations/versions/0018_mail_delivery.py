"""Bounded mail delivery leases; no provider or automatic production worker."""

import sqlalchemy as sa
from alembic import op

revision = "0018_mail_delivery"
down_revision = "0017_enrollment_mail_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "enrollment_mail_deliveries",
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("challenge_id", name="pk_enrollment_mail_deliveries"),
        sa.UniqueConstraint("claim_id", name="uq_enrollment_mail_deliveries_claim_id"),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["enrollment_mail_outbox.challenge_id"],
            ondelete="RESTRICT",
            name="fk_mail_delivery_outbox",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 1 AND 3",
            name=op.f("ck_enrollment_mail_deliveries_attempt_bounds"),
        ),
        sa.CheckConstraint(
            "lease_until > started_at AND lease_until <= started_at + "
            "interval '30 seconds'",
            name=op.f("ck_enrollment_mail_deliveries_lease_bounds"),
        ),
        sa.CheckConstraint(
            "(state = 'leased' AND finished_at IS NULL AND next_attempt_at IS NULL) OR "
            "(state = 'retry' AND attempts < 3 AND finished_at IS NOT NULL AND "
            "finished_at >= started_at AND next_attempt_at IS NOT NULL AND "
            "next_attempt_at >= finished_at + interval '60 seconds') OR "
            "(state IN ('accepted', 'uncertain', 'rejected', 'cancelled') AND "
            "finished_at IS NOT NULL AND finished_at >= started_at AND "
            "next_attempt_at IS NULL)",
            name=op.f("ck_enrollment_mail_deliveries_delivery_state"),
        ),
    )
    op.execute("REVOKE DELETE ON enrollment_mail_deliveries FROM carrillo_app")


def downgrade() -> None:
    op.execute(
        "LOCK TABLE users, mfa_pendings, enrollment_mail_throttles, "
        "enrollment_email_challenges, enrollment_mail_outbox, "
        "enrollment_mail_deliveries IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM enrollment_mail_deliveries) "
        "THEN RAISE EXCEPTION 'Downgrade refused: mail delivery evidence must be "
        "preserved'; END IF; END $$"
    )
    op.drop_table("enrollment_mail_deliveries")
