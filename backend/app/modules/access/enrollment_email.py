"""Initial MFA mailbox proof kernel. No SMTP, outbox, HTTP or session authority.

Delivery drafts carry secrets only after commit for tests/future transport design.
Production delivery must compose encrypted outbox/quota inside issuance, not send
from this module. A permit authorizes only the future initial factor use case.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine, exists, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import mfa_pending
from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    EnrollmentEmailChallenge,
    MfaCredential,
    MfaPending,
    SecurityEvent,
    User,
)
from app.modules.access.sessions import _database_now, _lock_user


class EnrollmentEmailUnavailable(RuntimeError):
    """Generic DB/time failure; never publish a secret before commit."""


@dataclass(frozen=True)
class EmailDeliveryDraft:
    recipient: SecretStr
    token: SecretStr
    expires_at: datetime


@dataclass(frozen=True)
class EnrollmentAuthorization:
    """Uncommitted restricted context; never a session or transferable proof."""

    user_id: UUID
    challenge_id: UUID
    expires_at: datetime


def _email_fingerprint(user: User) -> str:
    return hashlib.sha256(
        b"carrillo:enrollment-mail-address:v1\x00"
        + user.id.bytes
        + b"\x00"
        + user.email.encode("utf-8")
    ).hexdigest()


def _initial_factor_allowed(session: Session, user: User) -> bool:
    return (
        user.is_active
        and user.mfa_required
        and not session.scalar(select(exists().where(MfaCredential.user_id == user.id)))
    )


def _hash(value: SecretStr, domain: str) -> str | None:
    if not isinstance(value, SecretStr):
        return None
    try:
        return mfa_pending._hash(value, domain)
    except AccessDenied:
        return None


def _limits(session: Session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '3s'"))
    session.execute(text("SET LOCAL statement_timeout = '10s'"))


def _enrollment(session: Session, token: SecretStr, binding: SecretStr):
    if not isinstance(token, SecretStr) or not isinstance(binding, SecretStr):
        return None
    locked = mfa_pending._lock_pending(
        session,
        token=token,
        binding=binding,
        purpose=mfa_pending.PendingPurpose.ENROLLMENT,
    )
    if locked is None or not _initial_factor_allowed(session, locked[0]):
        return None
    return locked


def _challenge(
    session: Session, token: SecretStr, binding: SecretStr, email_token: SecretStr
):
    token_hash = _hash(email_token, "enrollment-mail-token-v1:")
    if token_hash is None or (locked := _enrollment(session, token, binding)) is None:
        return None
    user, pending, checked_at = locked
    row = session.scalar(
        select(EnrollmentEmailChallenge)
        .where(
            EnrollmentEmailChallenge.pending_id == pending.id,
            EnrollmentEmailChallenge.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _database_now(session)
    if now < checked_at:
        raise EnrollmentEmailUnavailable("Enrollment email unavailable")
    if (
        row is None
        or row.closed_at is not None
        or row.consumed_at is not None
        or not row.created_at <= now < row.expires_at == pending.expires_at
        or not hmac.compare_digest(row.token_hash, token_hash)
        or not hmac.compare_digest(row.email_fingerprint, _email_fingerprint(user))
    ):
        return None
    return user, pending, row, now


class EnrollmentEmailService:
    """Own transactions; public transport and mail request quotas are not integrated."""

    def __init__(self, engine: Engine) -> None:
        self._factory = create_session_factory(engine)

    def issue(
        self, *, enrollment_token: SecretStr, browser_binding: SecretStr
    ) -> EmailDeliveryDraft | None:
        try:
            with self._factory.begin() as session:
                _limits(session)
                locked = _enrollment(session, enrollment_token, browser_binding)
                if locked is None:
                    return None
                user, pending, checked_at = locked
                if (
                    session.scalar(
                        select(EnrollmentEmailChallenge.id).where(
                            EnrollmentEmailChallenge.pending_id == pending.id
                        )
                    )
                    is not None
                ):
                    # One issuance per pending, no recoverable token/resend.
                    return None
                now = _database_now(session)
                if now < checked_at:
                    raise EnrollmentEmailUnavailable("Enrollment email unavailable")
                if now >= pending.expires_at:
                    return None
                token = SecretStr(secrets.token_urlsafe(32))
                session.add(
                    EnrollmentEmailChallenge(
                        user_id=user.id,
                        pending_id=pending.id,
                        token_hash=mfa_pending._hash(
                            token, "enrollment-mail-token-v1:"
                        ),
                        email_fingerprint=_email_fingerprint(user),
                        created_at=now,
                        expires_at=pending.expires_at,
                    )
                )
                session.flush()
                result = EmailDeliveryDraft(
                    SecretStr(user.email), token, pending.expires_at
                )
            return result
        except SQLAlchemyError:
            raise EnrollmentEmailUnavailable("Enrollment email unavailable") from None

    def inspect(
        self,
        *,
        enrollment_token: SecretStr,
        browser_binding: SecretStr,
        email_token: SecretStr,
    ) -> bool:
        """Informative bound read, not authority. Never consumes on link inspection.

        This takes bounded locks for a consistent view; no persistent mutation.
        An HTTP GET adapter is not implemented and must not invoke confirm.
        """
        try:
            with self._factory.begin() as session:
                _limits(session)
                return (
                    _challenge(session, enrollment_token, browser_binding, email_token)
                    is not None
                )
        except SQLAlchemyError:
            raise EnrollmentEmailUnavailable("Enrollment email unavailable") from None

    def confirm(
        self,
        *,
        enrollment_token: SecretStr,
        browser_binding: SecretStr,
        email_token: SecretStr,
    ) -> mfa_pending.PendingToken | None:
        try:
            with self._factory.begin() as session:
                _limits(session)
                locked = _challenge(
                    session, enrollment_token, browser_binding, email_token
                )
                if locked is None:
                    return None
                user, pending, row, now = locked
                token = SecretStr(secrets.token_urlsafe(32))
                row.consumed_at = pending.finished_at = now
                row.permit_token_hash = mfa_pending._hash(
                    token, "enrollment-mail-permit-v1:"
                )
                session.add(
                    SecurityEvent(
                        user_id=user.id,
                        credential_id=None,
                        action="mfa_enrollment_email_confirmed",
                        security_revision=user.security_revision,
                        occurred_at=now,
                    )
                )
                session.flush()
                result = mfa_pending.PendingToken(token, row.expires_at)
            return result
        except SQLAlchemyError:
            raise EnrollmentEmailUnavailable("Enrollment email unavailable") from None


def _consume_permit(
    session: Session, *, token: SecretStr, browser_binding: SecretStr
) -> EnrollmentAuthorization | None:
    """Compose with initial-factor activation in the SAME owning transaction.

    This does not activate a factor. Caller must roll back any later failure.
    """
    token_hash = _hash(token, "enrollment-mail-permit-v1:")
    binding_hash = _hash(browser_binding, "mfa-pending-browser-v1:")
    if token_hash is None or binding_hash is None:
        return None
    hint = session.execute(
        select(
            EnrollmentEmailChallenge.id,
            EnrollmentEmailChallenge.user_id,
            EnrollmentEmailChallenge.pending_id,
        ).where(EnrollmentEmailChallenge.permit_token_hash == token_hash)
    ).one_or_none()
    if hint is None or (user := _lock_user(session, hint.user_id)) is None:
        return None
    pending = session.scalar(
        select(MfaPending)
        .where(MfaPending.id == hint.pending_id, MfaPending.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = session.scalar(
        select(EnrollmentEmailChallenge)
        .where(
            EnrollmentEmailChallenge.id == hint.id,
            EnrollmentEmailChallenge.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _database_now(session)
    if (
        pending is None
        or row is None
        or not _initial_factor_allowed(session, user)
        or row.closed_at is not None
        or row.permit_used_at is not None
        or row.consumed_at is None
        or row.pending_id != pending.id
        or pending.purpose != mfa_pending.PendingPurpose.ENROLLMENT.value
        or pending.finished_at != row.consumed_at
        or pending.security_revision != user.security_revision
        or not pending.password_verified_at
        <= pending.created_at
        <= row.created_at
        <= row.consumed_at
        <= now
        < row.expires_at
        == pending.expires_at
        or not hmac.compare_digest(row.permit_token_hash or "", token_hash)
        or not hmac.compare_digest(pending.binding_hash, binding_hash)
        or not hmac.compare_digest(
            pending.password_fingerprint, _password_fingerprint(user)
        )
        or not hmac.compare_digest(row.email_fingerprint, _email_fingerprint(user))
    ):
        return None
    row.permit_used_at = now
    session.flush()
    return EnrollmentAuthorization(user.id, row.id, row.expires_at)
