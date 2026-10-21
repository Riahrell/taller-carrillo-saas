"""Normalize the mailbox check name on databases upgraded before the naming fix."""

from alembic import op

revision = "0038_mailbox_constraint_name"
down_revision = "0037_email_browser_login"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""DO $$ BEGIN
IF EXISTS (SELECT 1 FROM pg_constraint
WHERE conrelid = 'verified_mailboxes'::regclass
AND conname = 'ck_verified_mailboxes_ck_verified_mailboxes_one_source') THEN
ALTER TABLE verified_mailboxes RENAME CONSTRAINT
ck_verified_mailboxes_ck_verified_mailboxes_one_source TO
ck_verified_mailboxes_one_source;
END IF;
END $$""")


def downgrade():
    # Keep the canonical name expected by the corrected 0037 downgrade.
    # This revision changes no data or constraint expression.
    pass
