"""Initial MFA setup/activation after fresh mailbox proof; internal, no HTTP.

Setup is not a credential. Confirm consumes the first TOTP, installs ten offline
hashes and revokes global access atomically. Results containing secrets are only
returned after commit; lost responses cannot be replayed to reveal them again.
"""

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import enrollment_email as mail
from app.modules.access import mfa_pending
from app.modules.access import mfa_verification as quota
from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.mfa_crypto import (
    EncryptedSeed,
    RecoveryCode,
    SeedKeyring,
    SeedProtectionError,
    TotpSeed,
    generate_recovery_codes,
    generate_totp_seed,
    match_totp,
    recovery_digest,
)
from app.modules.access.models import (
    EnrollmentEmailChallenge,
    MfaCredential,
    MfaEnrollment,
    MfaPending,
    MfaRecoveryCode,
    MfaRecoverySet,
    SecurityEvent,
)
from app.modules.access.security_state import advance_security_revision
from app.modules.access.sessions import _database_now, _lock_user


class MfaEnrollmentUnavailable(RuntimeError):
    """Generic infrastructure/key/time error without secret detail."""


class _Expired(Exception):
    """Rollback all changes; expiration never leaves a partially enabled factor."""


@dataclass(frozen=True)
class EnrollmentSetup:
    token: SecretStr
    seed: TotpSeed
    expires_at: datetime


@dataclass(frozen=True)
class RecoveryBundle:
    credential_id: UUID
    security_revision: int
    codes: tuple[RecoveryCode, ...]


def _clock(session, checked_at, expires_at):
    now = _database_now(session)
    if now < checked_at:
        raise MfaEnrollmentUnavailable("MFA enrollment unavailable")
    if now >= expires_at:
        raise _Expired
    return now


def _locked(session: Session, token_hash: str, binding_hash: str):
    hint = session.execute(
        select(
            MfaEnrollment.id,
            EnrollmentEmailChallenge.user_id,
            EnrollmentEmailChallenge.pending_id,
        )
        .join(
            EnrollmentEmailChallenge,
            EnrollmentEmailChallenge.id == MfaEnrollment.challenge_id,
        )
        .where(MfaEnrollment.token_hash == token_hash)
    ).one_or_none()
    if hint is None or (user := _lock_user(session, hint.user_id)) is None:
        return None
    pending = session.scalar(
        select(MfaPending)
        .where(MfaPending.id == hint.pending_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    challenge = session.scalar(
        select(EnrollmentEmailChallenge)
        .join(MfaEnrollment, MfaEnrollment.challenge_id == EnrollmentEmailChallenge.id)
        .where(MfaEnrollment.id == hint.id)
        .with_for_update(of=EnrollmentEmailChallenge)
        .execution_options(populate_existing=True)
    )
    stage = session.scalar(
        select(MfaEnrollment)
        .where(MfaEnrollment.id == hint.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _database_now(session)
    if (
        pending is None
        or challenge is None
        or stage is None
        or not mail._initial_factor_allowed(session, user)
        or pending.user_id != user.id
        or challenge.user_id != user.id
        or challenge.pending_id != pending.id
        or stage.closed_at is not None
        or stage.activated_at is not None
        or challenge.closed_at is not None
        or challenge.consumed_at is None
        or pending.purpose != mfa_pending.PendingPurpose.ENROLLMENT.value
        or pending.security_revision != user.security_revision
        or pending.finished_at != challenge.consumed_at
        or challenge.permit_used_at != stage.created_at
        or not pending.password_verified_at
        <= pending.created_at
        <= challenge.created_at
        <= challenge.consumed_at
        <= stage.created_at
        <= now
        < stage.expires_at
        == challenge.expires_at
        == pending.expires_at
        or not hmac.compare_digest(stage.token_hash, token_hash)
        or not hmac.compare_digest(pending.binding_hash, binding_hash)
        or not hmac.compare_digest(
            pending.password_fingerprint, _password_fingerprint(user)
        )
        or not hmac.compare_digest(
            challenge.email_fingerprint, mail._email_fingerprint(user)
        )
    ):
        return None
    return user, stage, now


class MfaEnrollmentService:
    def __init__(self, engine: Engine, *, keyring: SeedKeyring):
        self._factory = create_session_factory(engine)
        self._keyring = keyring

    def begin(
        self, *, permit_token: SecretStr, browser_binding: SecretStr
    ) -> EnrollmentSetup | None:
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                authorized = mail._consume_permit(
                    session,
                    token=permit_token,
                    browser_binding=browser_binding,
                )
                if authorized is None:
                    return None
                challenge = session.get(
                    EnrollmentEmailChallenge, authorized.challenge_id
                )
                created_at = challenge.permit_used_at
                identity = uuid4()
                seed = generate_totp_seed()
                envelope = self._keyring.encrypt(
                    seed, user_id=authorized.user_id, credential_id=identity
                )
                token = SecretStr(secrets.token_urlsafe(32))
                _clock(session, created_at, authorized.expires_at)
                session.add(
                    MfaEnrollment(
                        id=identity,
                        challenge_id=authorized.challenge_id,
                        token_hash=mfa_pending._hash(token, "mfa-enrollment-setup-v1:"),
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                        created_at=created_at,
                        expires_at=authorized.expires_at,
                    )
                )
                session.flush()
                result = EnrollmentSetup(token, seed, authorized.expires_at)
            return result
        except _Expired:
            return None
        except (SQLAlchemyError, SeedProtectionError):
            raise MfaEnrollmentUnavailable("MFA enrollment unavailable") from None

    def activate(
        self,
        *,
        setup_token: SecretStr,
        browser_binding: SecretStr,
        code: SecretStr,
        client_ip: IPv4Address | IPv6Address,
    ) -> RecoveryBundle | None:
        if not isinstance(code, SecretStr):
            raise ValueError("Invalid internal MFA input")
        quota._ip_key(client_ip)
        token_hash = mail._hash(setup_token, "mfa-enrollment-setup-v1:")
        binding_hash = mail._hash(browser_binding, "mfa-pending-browser-v1:")
        if token_hash is None or binding_hash is None:
            return None
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                # Discovery is not authority. Lock shared quotas BEFORE identity.
                user_id = session.scalar(
                    select(EnrollmentEmailChallenge.user_id)
                    .join(
                        MfaEnrollment,
                        MfaEnrollment.challenge_id == EnrollmentEmailChallenge.id,
                    )
                    .join(
                        MfaPending, MfaPending.id == EnrollmentEmailChallenge.pending_id
                    )
                    .where(
                        MfaEnrollment.token_hash == token_hash,
                        MfaPending.binding_hash == binding_hash,
                    )
                )
                if user_id is None:
                    return None
                buckets = quota._lock_quotas(session, user_id, client_ip)
                if buckets is None:
                    return None
                locked = _locked(session, token_hash, binding_hash)
                if locked is None:
                    return None
                user, stage, checked_at = locked
                if checked_at < buckets[2]:
                    raise MfaEnrollmentUnavailable("MFA enrollment unavailable")
                seed = self._keyring.decrypt(
                    EncryptedSeed(
                        key_id=stage.key_id,
                        nonce=stage.nonce,
                        ciphertext=stage.ciphertext,
                    ),
                    user_id=user.id,
                    credential_id=stage.id,
                )
                verified_at = _clock(session, checked_at, stage.expires_at)
                counter = match_totp(
                    seed,
                    code,
                    now=verified_at,
                    last_counter=None,
                    last_verified_at=None,
                )
                finished_at = quota._finish_attempt(
                    session, buckets, failed=counter is None
                )
                if finished_at < verified_at:
                    raise MfaEnrollmentUnavailable("MFA enrollment unavailable")
                if finished_at >= stage.expires_at:
                    raise _Expired
                if counter is None:
                    return None
                envelope = self._keyring.encrypt(
                    seed, user_id=user.id, credential_id=stage.id
                )
                codes, set_id = generate_recovery_codes(), uuid4()
                session.add(
                    MfaCredential(
                        id=stage.id,
                        user_id=user.id,
                        encryption_version=envelope.version,
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                        created_at=verified_at,
                        last_counter=counter,
                        last_verified_at=verified_at,
                    )
                )
                session.flush()
                session.add(
                    MfaRecoverySet(
                        id=set_id,
                        user_id=user.id,
                        credential_id=stage.id,
                        created_at=finished_at,
                    )
                )
                session.flush()
                session.add_all(
                    [
                        MfaRecoveryCode(
                            set_id=set_id,
                            position=position,
                            code_hash=recovery_digest(
                                value, user_id=user.id, set_id=set_id
                            ),
                        )
                        for position, value in enumerate(codes)
                    ]
                )
                stage.closed_at = stage.activated_at = finished_at
                stage.key_id = stage.nonce = stage.ciphertext = None
                session.flush()
                state = advance_security_revision(
                    session,
                    user_id=user.id,
                    expected_revision=user.security_revision,
                )
                from app.modules.access.security_notices import enqueue_notice

                enqueue_notice(
                    session,
                    user=user,
                    event=SecurityEvent(
                        user_id=user.id,
                        credential_id=stage.id,
                        action="mfa_enrolled",
                        security_revision=state.revision,
                        occurred_at=finished_at,
                    ),
                    now=finished_at,
                )
                session.flush()
                _clock(session, finished_at, stage.expires_at)
                result = RecoveryBundle(stage.id, state.revision, codes)
            return result
        except _Expired:
            return None
        except (SQLAlchemyError, SeedProtectionError, quota.MfaVerificationUnavailable):
            raise MfaEnrollmentUnavailable("MFA enrollment unavailable") from None


def purge_expired_enrollments(engine: Engine, *, limit: int = 100) -> int:
    """Explicit bounded cleanup; only child locks, no schedule or secure erasure."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid enrollment maintenance limit")
    try:
        with create_session_factory(engine).begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            now = _database_now(session)
            rows = session.scalars(
                select(MfaEnrollment)
                .where(
                    MfaEnrollment.closed_at.is_(None),
                    MfaEnrollment.expires_at <= now,
                )
                .order_by(MfaEnrollment.expires_at, MfaEnrollment.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                row.closed_at = max(now, row.created_at)
                row.key_id = row.nonce = row.ciphertext = None
            session.flush()
            result = len(rows)
        return result
    except SQLAlchemyError:
        raise MfaEnrollmentUnavailable("MFA enrollment unavailable") from None
