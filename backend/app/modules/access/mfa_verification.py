"""Internal quota-owning verification, with a composed login pending transition.

Each method owns its transaction. Do not call inside a business transaction or
expose low-level factor methods before authentication guards. Login verification
checks persisted pending state. All results are published after commit.
Future purposes must share this gate; there is deliberately no purpose quota key.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import mfa_consumption as consumption
from app.modules.access import mfa_pending as pending
from app.modules.access.membership import AccessDenied
from app.modules.access.mfa_consumption import MfaConsumption
from app.modules.access.mfa_crypto import RecoveryCode, SeedKeyring, SeedProtectionError
from app.modules.access.models import MfaCredential, MfaPending, MfaThrottle

WINDOW = timedelta(minutes=15)
ACCOUNT_LIMIT = 5
IP_LIMIT = 30


class MfaVerificationUnavailable(RuntimeError):
    """Generic infrastructure/key/time failure, never a credential rejection."""


def _account_key(user_id: UUID) -> str:
    if not isinstance(user_id, UUID):
        raise ValueError("A server user identity is required")
    return hashlib.sha256(
        b"carrillo:mfa:quota:account:v1\x00" + user_id.bytes
    ).hexdigest()


def _ip_key(address: IPv4Address | IPv6Address) -> str:
    if not isinstance(address, (IPv4Address, IPv6Address)):
        raise ValueError("A validated peer IP address is required")
    if isinstance(address, IPv6Address):
        # Interface zones are routing metadata, not a new remote identity.
        address = IPv6Address(address.packed)
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
    return hashlib.sha256(
        b"carrillo:mfa:quota:ip:v1\x00" + address.compressed.encode("ascii")
    ).hexdigest()


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _lock_bucket(session: Session, scope: str, key_hash: str) -> MfaThrottle:
    session.execute(
        insert(MfaThrottle)
        .values(
            scope=scope, key_hash=key_hash, window_started_at=_now(session), failures=0
        )
        .on_conflict_do_nothing(index_elements=["scope", "key_hash"])
    )
    return session.scalar(
        select(MfaThrottle)
        .where(MfaThrottle.scope == scope, MfaThrottle.key_hash == key_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _refresh(bucket: MfaThrottle, now: datetime) -> None:
    if now < bucket.window_started_at:
        raise MfaVerificationUnavailable("MFA verification unavailable")
    if now >= bucket.window_started_at + WINDOW:
        bucket.failures = 0
        bucket.window_started_at = now


class _LoginDeadlineExpired(Exception):
    """Internal rollback signal; expiry must not consume a valid TOTP."""


def _lock_quotas(
    session: Session, user_id: UUID, address: IPv4Address | IPv6Address
) -> tuple[MfaThrottle, MfaThrottle, datetime] | None:
    ip = _lock_bucket(session, "ip", _ip_key(address))
    _refresh(ip, _now(session))
    if ip.failures >= IP_LIMIT:
        return None
    account = _lock_bucket(session, "account", _account_key(user_id))
    gated_at = _now(session)
    _refresh(ip, gated_at)
    _refresh(account, gated_at)
    if account.failures >= ACCOUNT_LIMIT:
        return None
    return ip, account, gated_at


def _finish_attempt(
    session: Session,
    buckets: tuple[MfaThrottle, MfaThrottle, datetime],
    *,
    failed: bool,
) -> datetime:
    ip, account, gated_at = buckets
    finished_at = _now(session)
    if finished_at < gated_at:
        raise MfaVerificationUnavailable("MFA verification unavailable")
    if failed:
        for bucket in (ip, account):
            _refresh(bucket, finished_at)
            if bucket.failures == 0:
                bucket.window_started_at = finished_at
            bucket.failures += 1
        session.flush()
    return finished_at


def _timeouts(session: Session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '3s'"))
    session.execute(text("SET LOCAL statement_timeout = '10s'"))


class MfaVerifier:
    """Commit failures across workers; atomically commit consumption and its audit.

    This is not full authentication or a transferable proof. IDs must come from
    trusted server state, peer IP from the trusted proxy. No arbitrary callback is
    exposed to callers. Malformed/denied factor checks count as failures; blocked
    requests do not evaluate the factor or slide the fixed window.
    """

    def __init__(self, engine: Engine, *, keyring: SeedKeyring) -> None:
        self._factory = create_session_factory(engine)
        self._keyring = keyring

    def verify_totp(
        self,
        *,
        user_id: UUID,
        credential_id: UUID,
        code: SecretStr,
        client_ip: IPv4Address | IPv6Address,
    ) -> MfaConsumption | None:
        if not isinstance(credential_id, UUID) or not isinstance(code, SecretStr):
            raise ValueError("Invalid internal MFA input")
        return self._verify(
            user_id,
            client_ip,
            lambda session: consumption.consume_totp(
                session,
                user_id=user_id,
                credential_id=credential_id,
                code=code,
                keyring=self._keyring,
            ),
        )

    def verify_recovery_code(
        self,
        *,
        user_id: UUID,
        credential_id: UUID,
        set_id: UUID,
        code: RecoveryCode,
        client_ip: IPv4Address | IPv6Address,
    ) -> MfaConsumption | None:
        if (
            not isinstance(credential_id, UUID)
            or not isinstance(set_id, UUID)
            or not isinstance(code, RecoveryCode)
        ):
            raise ValueError("Invalid internal MFA input")
        return self._verify(
            user_id,
            client_ip,
            lambda session: consumption.consume_recovery_code(
                session,
                user_id=user_id,
                credential_id=credential_id,
                set_id=set_id,
                code=code,
            ),
        )

    def _verify(
        self,
        user_id: UUID,
        address: IPv4Address | IPv6Address,
        check: Callable[[Session], MfaConsumption | None],
    ) -> MfaConsumption | None:
        _account_key(user_id)
        _ip_key(address)
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                buckets = _lock_quotas(session, user_id, address)
                if buckets is None:
                    return None
                result = check(session)
                _finish_attempt(session, buckets, failed=result is None)
                # Factor success alone does not reset either quota.
            return result
        except (SQLAlchemyError, SeedProtectionError):
            raise MfaVerificationUnavailable("MFA verification unavailable") from None

    def verify_login_totp(
        self,
        *,
        token: SecretStr,
        browser_binding: SecretStr,
        code: SecretStr,
        client_ip: IPv4Address | IPv6Address,
    ) -> pending.PendingToken | None:
        """Consume a password-stage pending and TOTP, rotate to persisted evidence.

        No user/factor/tenant supplied by the client. Not connected to HTTP and
        does not issue a session. The returned opaque token is published only
        after the pending, counter, event and quota transaction commits.
        """
        if not all(
            isinstance(value, SecretStr) for value in (token, browser_binding, code)
        ):
            raise ValueError("Invalid internal MFA input")
        _ip_key(client_ip)
        try:
            token_hash = pending._hash(token, "mfa-pending-token-v1:")
            binding_hash = pending._hash(browser_binding, "mfa-pending-browser-v1:")
        except AccessDenied:
            return None
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                # Discovery takes no locks and grants no authority; recheck under
                # user/pending locks after IP -> account quota locks.
                user_id = session.scalar(
                    select(MfaPending.user_id).where(
                        MfaPending.token_hash == token_hash,
                        MfaPending.binding_hash == binding_hash,
                        MfaPending.purpose == pending.PendingPurpose.LOGIN_TOTP.value,
                        MfaPending.finished_at.is_(None),
                    )
                )
                if user_id is None:
                    return None
                buckets = _lock_quotas(session, user_id, client_ip)
                if buckets is None:
                    return None
                locked = pending._lock_pending(
                    session,
                    token=token,
                    binding=browser_binding,
                    purpose=pending.PendingPurpose.LOGIN_TOTP,
                )
                if locked is None:
                    return None
                user, row, checked_at = locked
                if checked_at < buckets[2]:
                    raise MfaVerificationUnavailable("MFA verification unavailable")
                credential_id = session.scalar(
                    select(MfaCredential.id).where(
                        MfaCredential.user_id == user.id,
                        MfaCredential.revoked_at.is_(None),
                    )
                )
                result = (
                    None
                    if credential_id is None
                    else consumption.consume_totp(
                        session,
                        user_id=user.id,
                        credential_id=credential_id,
                        code=code,
                        keyring=self._keyring,
                    )
                )
                finished_at = _finish_attempt(session, buckets, failed=result is None)
                if finished_at < checked_at or (
                    result is not None
                    and not checked_at <= result.verified_at <= finished_at
                ):
                    raise MfaVerificationUnavailable("MFA verification unavailable")
                if finished_at >= row.expires_at:
                    raise _LoginDeadlineExpired
                if result is None:
                    # Persist failures; retain the pending within its deadline.
                    return None
                row.finished_at = finished_at
                issued = pending._insert(
                    session,
                    user,
                    row.binding_hash,
                    pending.PendingPurpose.LOGIN_VERIFIED,
                    row.password_verified_at,
                    row.expires_at,
                    finished_at,
                    result.credential_id,
                    result.verified_at,
                )
            return issued
        except _LoginDeadlineExpired:
            return None
        except (SQLAlchemyError, SeedProtectionError):
            raise MfaVerificationUnavailable("MFA verification unavailable") from None
