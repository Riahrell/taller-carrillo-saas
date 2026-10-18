"""Offline recovery entry only; no replacement, session, HTTP or email bypass.

Password proof + one offline code yield a bound, short-lived restricted permit.
Factor/set remain active; only confirmed future replacement may invalidate them.
"""

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import mfa_consumption, mfa_pending
from app.modules.access import mfa_verification as quota
from app.modules.access.enrollment_email import _hash
from app.modules.access.login_challenges import (
    _password_fingerprint,
    revoke_user_challenges,
)
from app.modules.access.mfa_crypto import RecoveryCode, recovery_digest
from app.modules.access.models import (
    MfaCredential,
    MfaPending,
    MfaRecoveryCode,
    MfaRecoveryPermit,
    MfaRecoverySet,
    SecurityEvent,
)
from app.modules.access.sessions import _database_now, _lock_user, revoke_user_sessions


class MfaRecoveryUnavailable(RuntimeError):
    """Generic infrastructure/time failure, never secret SQL or provider detail."""


class _Expired(Exception):
    """Rollback consumption and revocations if the password stage expires."""


@dataclass(frozen=True)
class RecoveryAuthorization:
    """Uncommitted context, not a transferable proof or business authority."""

    user_id: UUID
    permit_id: UUID
    credential_id: UUID
    set_id: UUID
    expires_at: datetime


class MfaRecoveryService:
    def __init__(self, engine: Engine):
        self._factory = create_session_factory(engine)

    def begin(
        self,
        *,
        password_token: SecretStr,
        browser_binding: SecretStr,
        code: SecretStr,
        client_ip: IPv4Address | IPv6Address,
    ) -> mfa_pending.PendingToken | None:
        if not isinstance(code, SecretStr):
            raise ValueError("Invalid internal MFA input")
        quota._ip_key(client_ip)
        token_hash = _hash(password_token, "mfa-pending-token-v1:")
        binding_hash = _hash(browser_binding, "mfa-pending-browser-v1:")
        if token_hash is None or binding_hash is None:
            return None
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                # Discovery grants no authority; quotas precede user/child locks.
                user_id = session.scalar(
                    select(MfaPending.user_id).where(
                        MfaPending.token_hash == token_hash,
                        MfaPending.binding_hash == binding_hash,
                        MfaPending.purpose
                        == mfa_pending.PendingPurpose.LOGIN_TOTP.value,
                    )
                )
                if user_id is None:
                    return None
                buckets = quota._lock_quotas(session, user_id, client_ip)
                if buckets is None:
                    return None
                locked = mfa_pending._lock_pending(
                    session,
                    token=password_token,
                    binding=browser_binding,
                    purpose=mfa_pending.PendingPurpose.LOGIN_TOTP,
                )
                if locked is None:
                    return None
                user, source, checked_at = locked
                if checked_at < buckets[2]:
                    raise MfaRecoveryUnavailable("MFA recovery unavailable")
                factor_id = session.scalar(
                    select(MfaCredential.id).where(
                        MfaCredential.user_id == user.id,
                        MfaCredential.revoked_at.is_(None),
                    )
                )
                set_id = (
                    session.scalar(
                        select(MfaRecoverySet.id).where(
                            MfaRecoverySet.user_id == user.id,
                            MfaRecoverySet.credential_id == factor_id,
                            MfaRecoverySet.invalidated_at.is_(None),
                        )
                    )
                    if factor_id is not None
                    else None
                )
                try:
                    offline = RecoveryCode(value=code)
                except ValueError:
                    offline = None
                consumed = (
                    mfa_consumption.consume_recovery_code(
                        session,
                        user_id=user.id,
                        credential_id=factor_id,
                        set_id=set_id,
                        code=offline,
                    )
                    if factor_id is not None
                    and set_id is not None
                    and offline is not None
                    else None
                )
                finished_at = quota._finish_attempt(
                    session, buckets, failed=consumed is None
                )
                if finished_at < checked_at or (
                    consumed is not None
                    and not checked_at <= consumed.verified_at <= finished_at
                ):
                    raise MfaRecoveryUnavailable("MFA recovery unavailable")
                if finished_at >= source.expires_at:
                    raise _Expired
                if consumed is None:
                    return None
                position = session.scalar(
                    select(MfaRecoveryCode.position).where(
                        MfaRecoveryCode.set_id == set_id,
                        MfaRecoveryCode.code_hash
                        == recovery_digest(offline, user_id=user.id, set_id=set_id),
                        MfaRecoveryCode.used_at == consumed.verified_at,
                    )
                )
                if position is None:
                    raise MfaRecoveryUnavailable("MFA recovery unavailable")
                revoke_user_challenges(session, user.id)
                revoke_user_sessions(session, user.id)
                now = _database_now(session)
                if now < finished_at:
                    raise MfaRecoveryUnavailable("MFA recovery unavailable")
                if now >= source.expires_at:
                    raise _Expired
                token = SecretStr(secrets.token_urlsafe(32))
                expires_at = now + timedelta(minutes=15)
                session.add(
                    MfaRecoveryPermit(
                        user_id=user.id,
                        pending_id=source.id,
                        credential_id=factor_id,
                        set_id=set_id,
                        code_position=position,
                        token_hash=mfa_pending._hash(
                            token, "mfa-offline-recovery-permit-v1:"
                        ),
                        security_revision=user.security_revision,
                        password_verified_at=source.password_verified_at,
                        offline_verified_at=consumed.verified_at,
                        created_at=now,
                        expires_at=expires_at,
                    )
                )
                session.add(
                    SecurityEvent(
                        user_id=user.id,
                        credential_id=factor_id,
                        action="mfa_recovery_started",
                        security_revision=user.security_revision,
                        occurred_at=now,
                    )
                )
                session.flush()
                result = mfa_pending.PendingToken(token, expires_at)
            return result
        except _Expired:
            return None
        except (SQLAlchemyError, quota.MfaVerificationUnavailable):
            raise MfaRecoveryUnavailable("MFA recovery unavailable") from None

    def inspect(self, *, permit_token: SecretStr, browser_binding: SecretStr) -> bool:
        """Bound informative read; no consumption, side effect or login authority."""
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                return (
                    _lock_permit(
                        session, token=permit_token, browser_binding=browser_binding
                    )
                    is not None
                )
        except SQLAlchemyError:
            raise MfaRecoveryUnavailable("MFA recovery unavailable") from None


def _lock_permit(session: Session, *, token: SecretStr, browser_binding: SecretStr):
    token_hash = _hash(token, "mfa-offline-recovery-permit-v1:")
    binding_hash = _hash(browser_binding, "mfa-pending-browser-v1:")
    if token_hash is None or binding_hash is None:
        return None
    return _lock_permit_record(
        session, token_hash=token_hash, binding_hash=binding_hash
    )


def _lock_permit_record(
    session: Session,
    *,
    binding_hash: str,
    token_hash: str | None = None,
    permit_id: UUID | None = None,
    exchanged: bool = False,
):
    """Trusted composition only. Exchanged mode requires a bound persisted setup.

    Never expose permit_id/exchanged as an HTTP authorization alternative.
    """
    if (token_hash is None) == (permit_id is None):
        return None
    hint = session.execute(
        select(
            MfaRecoveryPermit.id,
            MfaRecoveryPermit.user_id,
            MfaRecoveryPermit.pending_id,
            MfaRecoveryPermit.credential_id,
            MfaRecoveryPermit.set_id,
            MfaRecoveryPermit.code_position,
        ).where(
            MfaRecoveryPermit.id == permit_id
            if permit_id is not None
            else MfaRecoveryPermit.token_hash == token_hash
        )
    ).one_or_none()
    if hint is None or (user := _lock_user(session, hint.user_id)) is None:
        return None
    source = session.scalar(
        select(MfaPending)
        .where(MfaPending.id == hint.pending_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    factor = session.scalar(
        select(MfaCredential)
        .where(MfaCredential.id == hint.credential_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    recovery_set = session.scalar(
        select(MfaRecoverySet)
        .where(MfaRecoverySet.id == hint.set_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    consumed = session.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.set_id == hint.set_id,
            MfaRecoveryCode.position == hint.code_position,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = session.scalar(
        select(MfaRecoveryPermit)
        .where(MfaRecoveryPermit.id == hint.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _database_now(session)
    if (
        source is None
        or factor is None
        or recovery_set is None
        or consumed is None
        or row is None
        or not user.is_active
        or row.closed_at is not None
        or ((row.used_at is None) if exchanged else (row.used_at is not None))
        or factor.user_id != user.id
        or factor.revoked_at is not None
        or recovery_set.user_id != user.id
        or recovery_set.credential_id != factor.id
        or recovery_set.invalidated_at is not None
        or consumed.used_at != row.offline_verified_at
        or row.user_id != user.id
        or row.security_revision != user.security_revision
        or source.user_id != user.id
        or source.id != row.pending_id
        or source.purpose != mfa_pending.PendingPurpose.LOGIN_TOTP.value
        or source.security_revision != row.security_revision
        or source.password_verified_at != row.password_verified_at
        or source.finished_at is None
        or source.finished_at > row.created_at
        or not source.password_verified_at
        <= source.created_at
        <= row.offline_verified_at
        <= source.finished_at
        or factor.created_at > row.offline_verified_at
        or recovery_set.created_at > row.offline_verified_at
        or not row.password_verified_at
        <= row.offline_verified_at
        <= row.created_at
        <= now
        < row.expires_at
        or not hmac.compare_digest(
            source.password_fingerprint, _password_fingerprint(user)
        )
        or not hmac.compare_digest(source.binding_hash, binding_hash)
        or (
            token_hash is not None
            and not hmac.compare_digest(row.token_hash, token_hash)
        )
        or row.credential_id != factor.id
        or row.set_id != recovery_set.id
        or row.code_position != consumed.position
    ):
        return None
    return user, row, now


def _consume_recovery_permit(
    session: Session, *, token: SecretStr, browser_binding: SecretStr
) -> RecoveryAuthorization | None:
    """Private composition boundary, not an endpoint; caller MUST own transaction.

    Future replacement setup must be created in this SAME commit and revalidate
    the originating proof on activation. This method replaces/activates nothing.
    """
    locked = _lock_permit(session, token=token, browser_binding=browser_binding)
    if locked is None:
        return None
    user, row, now = locked
    row.used_at = now
    session.flush()
    return RecoveryAuthorization(
        user.id, row.id, row.credential_id, row.set_id, row.expires_at
    )
