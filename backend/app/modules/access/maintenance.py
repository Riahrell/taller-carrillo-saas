"""Bounded expiry and quota retention.

No ancestor locks, live-window deletion or secrets.
"""

from datetime import timedelta

from sqlalchemy import delete, select

from app.core.database import create_session_factory
from app.modules.access.email_outbox import purge_expired_mail
from app.modules.access.mfa_enrollment import purge_expired_enrollments
from app.modules.access.mfa_replacement import purge_expired_replacements
from app.modules.access.mfa_verification import _timeouts
from app.modules.access.models import (
    EnrollmentMailThrottle,
    IdentityMailDelivery,
    LoginThrottle,
    MailboxChallenge,
    MfaRotation,
    MfaThrottle,
    PasswordResetThrottle,
    ResetRequestInbox,
    SecurityNotice,
)
from app.modules.access.password_reset import purge_expired_resets
from app.modules.access.sessions import _database_now


def maintain(engine, *, limit=100):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid maintenance limit")
    for operation in (
        purge_expired_resets,
        purge_expired_mail,
        purge_expired_enrollments,
        purge_expired_replacements,
    ):
        operation(engine, limit=limit)
    with create_session_factory(engine).begin() as session:
        _timeouts(session)
        now = _database_now(session)
        inbox = session.scalars(
            select(ResetRequestInbox)
            .where(
                ResetRequestInbox.finished_at.is_(None),
                ResetRequestInbox.expires_at <= now,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in inbox:
            row.finished_at = max(now, row.created_at)
            row.key_id = row.nonce = row.ciphertext = None
        rotations = session.scalars(
            select(MfaRotation)
            .where(MfaRotation.closed_at.is_(None), MfaRotation.expires_at <= now)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rotations:
            row.closed_at = max(now, row.created_at)
            row.key_id = row.nonce = row.ciphertext = None
        rows = session.scalars(
            select(MailboxChallenge)
            .where(
                MailboxChallenge.expires_at <= now, MailboxChallenge.closed_at.is_(None)
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.closed_at = row.discarded_at = max(now, row.created_at)
            row.key_id = row.nonce = row.ciphertext = None
        notices = session.scalars(
            select(SecurityNotice)
            .where(SecurityNotice.expires_at <= now, SecurityNotice.state == "pending")
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in notices:
            row.state = "expired"
        leases = session.scalars(
            select(IdentityMailDelivery)
            .where(
                IdentityMailDelivery.state == "leased",
                IdentityMailDelivery.lease_expires_at <= now,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in leases:
            row.state = "uncertain"
            row.claim_id = row.lease_expires_at = None
        session.flush()
    # Expired quota metadata retained for 30 days; never reset a live 15m bucket.
    for model in (
        LoginThrottle,
        MfaThrottle,
        EnrollmentMailThrottle,
        PasswordResetThrottle,
    ):
        with create_session_factory(engine).begin() as session:
            _timeouts(session)
            now = _database_now(session)
            rows = session.execute(
                select(model.scope, model.key_hash)
                .where(model.window_started_at < now - timedelta(days=30))
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for scope, key in rows:
                session.execute(
                    delete(model).where(model.scope == scope, model.key_hash == key)
                )
