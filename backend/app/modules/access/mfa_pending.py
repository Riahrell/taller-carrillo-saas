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
from app.modules.access.models import MfaCredential, MfaPending, User
from app.modules.access.sessions import _database_now, _lock_user


class PendingPurpose(StrEnum):
    LOGIN_TOTP = "login_totp"
    ENROLLMENT = "enrollment"
    LOGIN_VERIFIED = "login_verified"


@dataclass(frozen=True)
class PendingToken:
    token: SecretStr
    expires_at: datetime


@dataclass(frozen=True)
class PendingContext:
    """Consumed stage data, not a transferable proof, mailbox or membership."""

    user_id: UUID
    security_revision: int
    purpose: PendingPurpose
    password_verified_at: datetime
    expires_at: datetime
    credential_id: UUID | None
    factor_verified_at: datetime | None


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
    credential_id: UUID | None = None,
    factor_verified_at: datetime | None = None,
) -> PendingToken:
    token = SecretStr(secrets.token_urlsafe(32))
    session.add(
        MfaPending(
            user_id=user.id,
            token_hash=_hash(token, "mfa-pending-token-v1:"),
            binding_hash=binding_hash,
            purpose=purpose.value,
            security_revision=user.security_revision,
            credential_id=credential_id,
            factor_verified_at=factor_verified_at,
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
    password_verified_at: datetime | None = None,
) -> PendingToken:
    """Only directly after checking password under this user's lock.

    Internal primitive, not an authentication facade. Caller must retain that lock
    and own commit; it cannot present this result as MFA or email verification.
    """
    _purpose(purpose)
    if purpose is PendingPurpose.LOGIN_VERIFIED:
        raise ValueError("Password alone cannot issue a verified pending")
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
    if password_verified_at is not None and (
        not isinstance(password_verified_at, datetime)
        or password_verified_at.utcoffset() is None
    ):
        raise ValueError("Invalid internal password time")
    verified_at = now if password_verified_at is None else password_verified_at
    lifetime = timedelta(minutes=5 if purpose is PendingPurpose.LOGIN_TOTP else 15)
    expires_at = verified_at + lifetime
    if not verified_at <= now < expires_at:
        raise AccessDenied("Access denied")
    return _insert(session, user, binding_hash, purpose, verified_at, expires_at, now)


def _close_browser_pendings(
    session: Session, *, user_id: UUID, binding: SecretStr
) -> None:
    """Trusted caller already holds the user lock; never acquire it after children."""
    binding_hash = _hash(binding, "mfa-pending-browser-v1:")
    rows = session.scalars(
        select(MfaPending)
        .where(
            MfaPending.user_id == user_id,
            MfaPending.binding_hash == binding_hash,
            MfaPending.finished_at.is_(None),
        )
        .order_by(MfaPending.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if rows:
        now = _database_now(session)
        for row in rows:
            row.finished_at = max(now, row.created_at)
        session.flush()


def _lock_pending(
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
    if purpose is PendingPurpose.LOGIN_VERIFIED and (
        row.factor_verified_at is None
        or row.factor_verified_at > now
        or session.scalar(
            select(MfaCredential.id).where(
                MfaCredential.id == row.credential_id,
                MfaCredential.user_id == user_id,
                MfaCredential.revoked_at.is_(None),
            )
        )
        is None
    ):
        return None
    return user, row, now


def _take(
    session: Session, *, token: SecretStr, binding: SecretStr, purpose: PendingPurpose
) -> tuple[User, MfaPending, datetime] | None:
    taken = _lock_pending(session, token=token, binding=binding, purpose=purpose)
    if taken is None:
        return None
    _, row, now = taken
    row.finished_at = now
    session.flush()
    return taken


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
        row.credential_id,
        row.factor_verified_at,
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
        row.credential_id,
        row.factor_verified_at,
    )
