"""Operator-only isolation of restored authentication state. Never a public bypass."""

import argparse
import secrets
import sys

from pydantic import SecretStr
from sqlalchemy import select, text, update

from app.core.config import load_settings
from app.core.database import build_engine, create_session_factory
from app.modules.access.models import (
    IdentityMailDelivery,
    MailboxChallenge,
    MfaRecoverySet,
    ResetRequestInbox,
    SecurityNotice,
    User,
)
from app.modules.access.passwords import hash_password
from app.modules.access.security_state import advance_security_revision
from app.modules.access.sessions import _database_now


def quarantine(engine):
    with create_session_factory(engine).begin() as session:
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        session.execute(text("SET LOCAL statement_timeout = '60s'"))
        if session.scalar(text("SELECT current_user")) != "carrillo_owner":
            raise ValueError("Migration owner required")
        users = session.scalars(select(User).order_by(User.id).with_for_update()).all()
        for user in users:
            now = _database_now(session)
            user.password_hash = hash_password(SecretStr(secrets.token_urlsafe(48)))
            # Kernel requires active user; disabled users stay disabled and get
            # revision/revocation through the same low-level owner operation.
            if user.is_active:
                advance_security_revision(
                    session, user_id=user.id, expected_revision=user.security_revision
                )
            else:
                user.security_revision += 1
                from app.modules.access.login_challenges import revoke_user_challenges
                from app.modules.access.reset_state import close_password_resets
                from app.modules.access.sessions import revoke_user_sessions

                close_password_resets(session, user.id)
                revoke_user_challenges(session, user.id)
                revoke_user_sessions(session, user.id)
            session.execute(
                update(MfaRecoverySet)
                .where(
                    MfaRecoverySet.user_id == user.id,
                    MfaRecoverySet.invalidated_at.is_(None),
                )
                .values(invalidated_at=now)
            )
            rows = session.scalars(
                select(MailboxChallenge)
                .where(
                    MailboxChallenge.user_id == user.id,
                    MailboxChallenge.closed_at.is_(None),
                )
                .with_for_update()
            ).all()
            for row in rows:
                row.closed_at = row.discarded_at = max(now, row.created_at)
                row.key_id = row.nonce = row.ciphertext = None
        inbox = session.scalars(
            select(ResetRequestInbox)
            .where(ResetRequestInbox.finished_at.is_(None))
            .with_for_update()
        ).all()
        for row in inbox:
            row.finished_at = max(_database_now(session), row.created_at)
            row.key_id = row.nonce = row.ciphertext = None
        session.execute(
            update(SecurityNotice)
            .where(SecurityNotice.state == "pending")
            .values(state="expired")
        )
        session.execute(
            update(IdentityMailDelivery)
            .where(IdentityMailDelivery.state.in_(["leased", "pending"]))
            .values(state="uncertain", claim_id=None, lease_expires_at=None)
        )
        # no-acl backups must not resurrect default UPDATE/DELETE privileges.
        for table in ("security_events", "audit_events"):
            session.execute(
                text("REVOKE UPDATE, DELETE ON " + table + " FROM carrillo_app")
            )
        for table in (
            "mfa_credentials",
            "mfa_recovery_sets",
            "mfa_recovery_codes",
            "mfa_pendings",
            "enrollment_email_challenges",
            "enrollment_mail_outbox",
            "mfa_enrollments",
            "mfa_recovery_permits",
            "mfa_replacements",
            "verified_mailboxes",
            "password_reset_challenges",
            "mailbox_challenges",
            "security_notices",
            "identity_mail_deliveries",
            "reset_request_inbox",
            "mfa_rotations",
        ):
            session.execute(text("REVOKE DELETE ON " + table + " FROM carrillo_app"))
        session.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-isolated", action="store_true")
    args = parser.parse_args()
    if not args.confirm_isolated or not sys.stdin.isatty():
        parser.exit(2, "Interactive confirmation of isolated traffic is required.\n")
    if (
        input(
            "Traffic is isolated and the restored backup is verified. Type QUARANTINE: "
        )
        != "QUARANTINE"
    ):
        parser.exit(2, "Refused.\n")
    config = load_settings()
    engine = build_engine(config)
    try:
        quarantine(engine)
    except Exception:
        parser.exit(1, "Restored identity quarantine failed; keep traffic closed.\n")
    finally:
        engine.dispose()
    print(
        "Restored access quarantined. Keep traffic closed until "
        "identity/key/recovery acceptance."
    )


if __name__ == "__main__":
    main()
