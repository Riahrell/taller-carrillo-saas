"""Internal restricted state. Callers own credential verification and transaction.

No HTTP entry point, MFA consumption, email proof or session issuance. Publish
opaque tokens only after commit. Never pass purpose/identity from a request body.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.membership import AccessDenied
from app.modules.access.models import MfaPending, User
from app.modules.access.sessions import _database_now, _lock_user


class PendingPurpose(StrEnum):
    LOGIN_TOTP = "login_totp"
    ENROLLMENT = "enrollment"


@dataclass(frozen=True)
class PendingToken:
    token: SecretStr
    expires_at: datetime


@dataclass(frozen=True)
class PendingContext:
    """Restricted state only; does not prove a factor, mailbox or membership."""

    user_id: UUID
    security_revision: int
    purpose: PendingPurpose
    password_verified_at: datetime
    expires_at: datetime


def _hash(secret: SecretStr, domain: str) -> str:
    value = secret.get_secret_value()
    if len(value) != 43 or not all(
        c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for c in value
    ):
        raise AccessDenied("Access denied")
    return hashlib.sha256((domain + value).encode("ascii")).hexdigest()


def _purpose(purpose: PendingPurpose) -> None:
    if not isinstance(purpose, PendingPurpose):
        raise ValueError("Invalid internal pending purpose")


def _insert(
    session: Session,
    user: User,
    binding_hash: str,
    purpose: PendingPurpose,
    verified_at: datetime,
    expires_at: datetime,
    now: datetime,
) -> PendingToken:
    token = SecretStr(secrets.token_urlsafe(32))
    session.add(
        MfaPending(
            user_id=user.id,
            token_hash=_hash(token, "mfa-pending-token-v1:"),
            binding_hash=binding_hash,
            purpose=purpose.value,
            security_revision=user.security_revision,
            password_fingerprint=_password_fingerprint(user),
            password_verified_at=verified_at,
            created_at=now,
            expires_at=expires_at,
        )
    )
    session.flush()
    return PendingToken(token, expires_at)


def _issue_after_password(
    session: Session,
    *,
    user_id: UUID,
    binding: SecretStr,
    purpose: PendingPurpose,
    expected_revision: int,
) -> PendingToken:
    """Only directly after checking password under this user's lock.

    Internal primitive, not an authentication facade. Caller must retain that lock
    and own commit; it cannot present this result as MFA or email verification.
    """
    _purpose(purpose)
    if (
        not isinstance(user_id, UUID)
        or type(expected_revision) is not int
        or expected_revision < 0
    ):
        raise ValueError("Invalid internal security context")
    binding_hash = _hash(binding, "mfa-pending-browser-v1:")
    user = _lock_user(session, user_id)
    if (
        user is None
        or not user.is_active
        or user.security_revision != expected_revision
    ):
        raise AccessDenied("Access denied")
    now = _database_now(session)
    lifetime = timedelta(minutes=5 if purpose is PendingPurpose.LOGIN_TOTP else 15)
    return _insert(session, user, binding_hash, purpose, now, now + lifetime, now)


def _take(
    session: Session, *, token: SecretStr, binding: SecretStr, purpose: PendingPurpose
) -> tuple[User, MfaPending, datetime] | None:
    _purpose(purpose)
    try:
        token_hash = _hash(token, "mfa-pending-token-v1:")
        binding_hash = _hash(binding, "mfa-pending-browser-v1:")
    except AccessDenied:
        return None
    user_id = session.scalar(
        select(MfaPending.user_id).where(MfaPending.token_hash == token_hash)
    )
    if user_id is None or (user := _lock_user(session, user_id)) is None:
        return None
    row = session.scalar(
        select(MfaPending)
        .where(MfaPending.token_hash == token_hash, MfaPending.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _database_now(session)
    if (
        row is None
        or row.finished_at is not None
        or row.purpose != purpose.value
        or not hmac.compare_digest(row.binding_hash, binding_hash)
        or not row.password_verified_at <= row.created_at <= now < row.expires_at
        or not user.is_active
        or row.security_revision != user.security_revision
        or not hmac.compare_digest(
            row.password_fingerprint, _password_fingerprint(user)
        )
    ):
        return None
    row.finished_at = now
    session.flush()
    return user, row, now


def _consume(
    session: Session, *, token: SecretStr, binding: SecretStr, purpose: PendingPurpose
) -> PendingContext | None:
    """Compose with authorized factor/mailbox transition in the SAME transaction.

    This only consumes the pending; it must never authorize a session by itself.
    A later transition failure must roll back the enclosing transaction.
    """
    taken = _take(session, token=token, binding=binding, purpose=purpose)
    if taken is None:
        return None
    user, row, _ = taken
    return PendingContext(
        user.id,
        row.security_revision,
        purpose,
        row.password_verified_at,
        row.expires_at,
    )


def _rotate(
    session: Session, *, token: SecretStr, binding: SecretStr, purpose: PendingPurpose
) -> PendingToken | None:
    """Rotate within the same purpose; preserve original password time/deadline."""
    taken = _take(session, token=token, binding=binding, purpose=purpose)
    if taken is None:
        return None
    user, row, now = taken
    return _insert(
        session,
        user,
        row.binding_hash,
        purpose,
        row.password_verified_at,
        row.expires_at,
        now,
    )
