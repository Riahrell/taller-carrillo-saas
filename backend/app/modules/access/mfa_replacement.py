"""Offline recovery replacement only; no voluntary rotation, HTTP or auto-login."""

import hmac
import secrets
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import mfa_pending, mfa_recovery
from app.modules.access import mfa_verification as quota
from app.modules.access.enrollment_email import _hash
from app.modules.access.mfa_crypto import (
    EncryptedSeed,
    SeedKeyring,
    SeedProtectionError,
    generate_recovery_codes,
    generate_totp_seed,
    match_totp,
    recovery_digest,
)
from app.modules.access.mfa_enrollment import EnrollmentSetup, RecoveryBundle
from app.modules.access.models import (
    MfaCredential,
    MfaPending,
    MfaRecoveryCode,
    MfaRecoveryPermit,
    MfaRecoverySet,
    MfaReplacement,
    SecurityEvent,
)
from app.modules.access.security_state import advance_security_revision
from app.modules.access.sessions import _database_now


class MfaReplacementUnavailable(RuntimeError):
    """No seed, code, SQL or key details in infrastructure errors."""


class _Expired(Exception):
    """Roll back exchange or replacement if the original recovery lease expires."""


def _clock(session: Session, checked_at: datetime, expires_at: datetime) -> datetime:
    now = _database_now(session)
    if now < checked_at:
        raise MfaReplacementUnavailable("MFA replacement unavailable")
    if now >= expires_at:
        raise _Expired
    return now


def _locked(session: Session, token_hash: str, binding_hash: str):
    permit_id = session.scalar(
        select(MfaReplacement.recovery_permit_id).where(
            MfaReplacement.token_hash == token_hash
        )
    )
    if permit_id is None:
        return None
    origin = mfa_recovery._lock_permit_record(
        session,
        permit_id=permit_id,
        binding_hash=binding_hash,
        exchanged=True,
    )
    if origin is None:
        return None
    user, permit, checked_at = origin
    stage = session.scalar(
        select(MfaReplacement)
        .where(
            MfaReplacement.recovery_permit_id == permit.id,
            MfaReplacement.token_hash == token_hash,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _database_now(session)
    if now < checked_at:
        raise MfaReplacementUnavailable("MFA replacement unavailable")
    if (
        stage is None
        or stage.closed_at is not None
        or stage.activated_at is not None
        or stage.created_at != permit.used_at
        or not permit.created_at
        <= stage.created_at
        <= now
        < stage.expires_at
        == permit.expires_at
        or not hmac.compare_digest(stage.token_hash, token_hash)
    ):
        return None
    return user, permit, stage, now


class MfaReplacementService:
    """Own bounded transactions; never reveal setup or codes again after commit."""

    def __init__(self, engine: Engine, *, keyring: SeedKeyring):
        self._factory = create_session_factory(engine)
        self._keyring = keyring

    def begin(
        self, *, permit_token: SecretStr, browser_binding: SecretStr
    ) -> EnrollmentSetup | None:
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                context = mfa_recovery._consume_recovery_permit(
                    session,
                    token=permit_token,
                    browser_binding=browser_binding,
                )
                if context is None:
                    return None
                permit = session.get(MfaRecoveryPermit, context.permit_id)
                created_at = permit.used_at
                identity = uuid4()
                seed = generate_totp_seed()
                envelope = self._keyring.encrypt(
                    seed, user_id=context.user_id, credential_id=identity
                )
                token = SecretStr(secrets.token_urlsafe(32))
                _clock(session, created_at, context.expires_at)
                session.add(
                    MfaReplacement(
                        id=identity,
                        recovery_permit_id=permit.id,
                        token_hash=mfa_pending._hash(
                            token, "mfa-replacement-setup-v1:"
                        ),
                        created_at=created_at,
                        expires_at=context.expires_at,
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                    )
                )
                session.flush()
                result = EnrollmentSetup(token, seed, context.expires_at)
            return result
        except _Expired:
            return None
        except SQLAlchemyError:
            raise MfaReplacementUnavailable("MFA replacement unavailable") from None
        except SeedProtectionError:
            raise MfaReplacementUnavailable("MFA replacement unavailable") from None

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
        token_hash = _hash(setup_token, "mfa-replacement-setup-v1:")
        binding_hash = _hash(browser_binding, "mfa-pending-browser-v1:")
        if token_hash is None or binding_hash is None:
            return None
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                user_id = session.scalar(
                    select(MfaRecoveryPermit.user_id)
                    .join(
                        MfaReplacement,
                        MfaReplacement.recovery_permit_id == MfaRecoveryPermit.id,
                    )
                    .join(MfaPending, MfaPending.id == MfaRecoveryPermit.pending_id)
                    .where(
                        MfaReplacement.token_hash == token_hash,
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
                user, permit, stage, checked_at = locked
                if checked_at < buckets[2]:
                    raise MfaReplacementUnavailable("MFA replacement unavailable")
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
                    raise MfaReplacementUnavailable("MFA replacement unavailable")
                if finished_at >= stage.expires_at:
                    raise _Expired
                if counter is None:
                    return None
                # Both ancestors are already locked/revalidated by the origin gate.
                old_factor = session.get(MfaCredential, permit.credential_id)
                old_set = session.get(MfaRecoverySet, permit.set_id)
                envelope = self._keyring.encrypt(
                    seed, user_id=user.id, credential_id=stage.id
                )
                codes, set_id = generate_recovery_codes(), uuid4()
                old_factor.revoked_at = old_set.invalidated_at = finished_at
                # Release partial uniqueness before inserting the successor.
                session.flush()
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
                    session, user_id=user.id, expected_revision=user.security_revision
                )
                session.add(
                    SecurityEvent(
                        user_id=user.id,
                        credential_id=stage.id,
                        action="mfa_replaced",
                        security_revision=state.revision,
                        occurred_at=finished_at,
                    )
                )
                session.flush()
                _clock(session, finished_at, stage.expires_at)
                result = RecoveryBundle(stage.id, state.revision, codes)
            return result
        except _Expired:
            return None
        except (SQLAlchemyError, SeedProtectionError, quota.MfaVerificationUnavailable):
            raise MfaReplacementUnavailable("MFA replacement unavailable") from None


def purge_expired_replacements(engine: Engine, *, limit: int = 100) -> int:
    """Explicit maintenance, child locks only. Does not refund offline codes."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid replacement maintenance limit")
    try:
        with create_session_factory(engine).begin() as session:
            quota._timeouts(session)
            now = _database_now(session)
            rows = session.scalars(
                select(MfaReplacement)
                .where(
                    MfaReplacement.closed_at.is_(None),
                    MfaReplacement.expires_at <= now,
                )
                .order_by(MfaReplacement.expires_at, MfaReplacement.id)
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
        raise MfaReplacementUnavailable("MFA replacement unavailable") from None
