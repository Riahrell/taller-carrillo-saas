"""Blind reset request staging removes user discovery from the HTTP path."""

from alembic import op

revision = "0028_blind_reset_requests"
down_revision = "0027_identity_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE reset_request_inbox (\n\tid UUID NOT NULL, "
        "\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "\n\tclaimed_at TIMESTAMP WITH TIME ZONE, \n\tfinished_at "
        "TIMESTAMP WITH TIME ZONE, \n\tkey_id VARCHAR(64), \n\tnonce "
        "BYTEA, \n\tciphertext BYTEA, \n\tCONSTRAINT "
        "pk_reset_request_inbox PRIMARY KEY (id), \n\tCONSTRAINT "
        "ck_reset_request_inbox_absolute_lifetime CHECK (expires_at "
        "= created_at + interval '15 minutes'), \n\tCONSTRAINT "
        "ck_reset_request_inbox_claim_window CHECK (claimed_at IS "
        "NULL OR claimed_at >= created_at), \n\tCONSTRAINT "
        "ck_reset_request_inbox_payload_state CHECK ((finished_at IS "
        "NULL AND key_id IS NOT NULL AND nonce IS NOT NULL AND "
        "octet_length(nonce)=12 AND ciphertext IS NOT NULL AND "
        "octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
        "(finished_at IS NOT NULL AND finished_at >= created_at AND "
        "key_id IS NULL AND nonce IS NULL AND ciphertext IS "
        "NULL))\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_reset_request_inbox_expires_at ON "
        "reset_request_inbox (expires_at)"
    )
    op.execute("REVOKE DELETE ON reset_request_inbox FROM carrillo_app")


def downgrade():
    op.execute("LOCK TABLE reset_request_inbox IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM reset_request_inbox) "
        "THEN RAISE EXCEPTION 'Downgrade refused: reset request "
        "evidence must be preserved'; END IF; END $$"
    )
    op.drop_table("reset_request_inbox")
