"""Voluntary factor rotation with current session-bound fresh password and TOTP."""

import secrets
from datetime import timedelta
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access import mfa_pending
from app.modules.access import mfa_verification as quota
from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.membership import AccessDenied
from app.modules.access.mfa_crypto import (
    EncryptedSeed,
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
    MfaRecoverySet,
    MfaRotation,
    SecurityEvent,
    SessionRecord,
    User,
)
from app.modules.access.rotation_state import close_rotations
from app.modules.access.security_notices import enqueue_notice
from app.modules.access.security_state import advance_security_revision
from app.modules.access.sessions import _database_now, _token_hash, resolve_session


class MfaRotationUnavailable(RuntimeError):
    pass


class _Expired(Exception):
    pass


def _recent(session, token, expected_actor):
    actor = resolve_session(session, token)
    if actor.membership_id != expected_actor:
        raise AccessDenied("Access denied")
    record = session.scalar(
        select(SessionRecord).where(SessionRecord.token_hash == _token_hash(token))
    )
    user = session.get(User, actor.user_id)
    if record.mfa_pending_id is None:
        raise AccessDenied("Access denied")
    proof = session.get(MfaPending, record.mfa_pending_id)
    password_at, factor_at = proof.password_verified_at, proof.factor_verified_at
    if record.mfa_reauthenticated_at is not None:
        if (
            record.mfa_reauthentication_credential_id != proof.credential_id
            or record.reauthenticated_at is None
            or record.reauthentication_fingerprint != _password_fingerprint(user)
        ):
            raise AccessDenied("Access denied")
        password_at, factor_at = (
            record.reauthenticated_at,
            record.mfa_reauthenticated_at,
        )
    now = _database_now(session)
    expires = min(record.expires_at, password_at + timedelta(minutes=5))
    if not proof.password_verified_at <= password_at <= factor_at <= now < expires:
        raise AccessDenied("Access denied")
    return user, record, proof.credential_id, password_at, factor_at, now, expires


class MfaRotationService:
    def __init__(self, engine, *, keyring):
        self._factory, self.keyring = create_session_factory(engine), keyring

    def begin(self, token, *, expected_actor):
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                user, record, credential, password_at, factor_at, now, expires = (
                    _recent(session, token, expected_actor)
                )
                close_rotations(session, user.id)
                identity, seed, secret = (
                    uuid4(),
                    generate_totp_seed(),
                    SecretStr(secrets.token_urlsafe(32)),
                )
                envelope = self.keyring.encrypt(
                    seed, user_id=user.id, credential_id=identity
                )
                session.add(
                    MfaRotation(
                        id=identity,
                        user_id=user.id,
                        session_id=record.id,
                        credential_id=credential,
                        session_hash=_token_hash(token),
                        token_hash=mfa_pending._hash(secret, "mfa-rotation-setup-v1:"),
                        security_revision=user.security_revision,
                        password_verified_at=password_at,
                        factor_verified_at=factor_at,
                        created_at=now,
                        expires_at=expires,
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                    )
                )
                session.flush()
                if not now <= _database_now(session) < expires:
                    raise _Expired
                result = EnrollmentSetup(secret, seed, expires)
            return result
        except (AccessDenied, _Expired):
            return None
        except (SQLAlchemyError, SeedProtectionError):
            raise MfaRotationUnavailable("MFA rotation unavailable") from None

    def activate(self, token, *, setup_token, code, expected_actor, client_ip):
        try:
            digest, source_hash = (
                mfa_pending._hash(setup_token, "mfa-rotation-setup-v1:"),
                _token_hash(token),
            )
        except AccessDenied:
            return None
        try:
            with self._factory.begin() as session:
                quota._timeouts(session)
                user_id = session.scalar(
                    select(MfaRotation.user_id).where(
                        MfaRotation.token_hash == digest,
                        MfaRotation.session_hash == source_hash,
                    )
                )
                if user_id is None:
                    return None
                buckets = quota._lock_quotas(session, user_id, client_ip)
                if buckets is None:
                    return None
                user, record, credential, password_at, factor_at, checked, expires = (
                    _recent(session, token, expected_actor)
                )
                row = session.scalar(
                    select(MfaRotation)
                    .where(
                        MfaRotation.token_hash == digest, MfaRotation.user_id == user.id
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                now = _database_now(session)
                if (
                    row is None
                    or row.closed_at is not None
                    or row.session_id != record.id
                    or row.credential_id != credential
                    or row.session_hash != source_hash
                    or row.security_revision != user.security_revision
                    or row.password_verified_at != password_at
                    or row.factor_verified_at != factor_at
                    or not max(checked, buckets[2], row.created_at)
                    <= now
                    < row.expires_at
                    <= expires
                ):
                    raise AccessDenied("Access denied")
                seed = self.keyring.decrypt(
                    EncryptedSeed(
                        key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext
                    ),
                    user_id=user.id,
                    credential_id=row.id,
                )
                verified_at = _database_now(session)
                if not now <= verified_at < row.expires_at:
                    raise _Expired
                counter = match_totp(
                    seed,
                    code,
                    now=verified_at,
                    last_counter=None,
                    last_verified_at=None,
                )
                finished = quota._finish_attempt(
                    session, buckets, failed=counter is None
                )
                if not verified_at <= finished < row.expires_at:
                    raise _Expired
                if counter is None:
                    return None
                old = session.scalar(
                    select(MfaCredential)
                    .where(
                        MfaCredential.id == credential,
                        MfaCredential.user_id == user.id,
                        MfaCredential.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
                old_set = session.scalar(
                    select(MfaRecoverySet)
                    .where(
                        MfaRecoverySet.credential_id == credential,
                        MfaRecoverySet.user_id == user.id,
                        MfaRecoverySet.invalidated_at.is_(None),
                    )
                    .with_for_update()
                )
                if old is None:
                    raise AccessDenied("Access denied")
                envelope = self.keyring.encrypt(
                    seed, user_id=user.id, credential_id=row.id
                )
                codes, set_id = generate_recovery_codes(), uuid4()
                old.revoked_at = finished
                if old_set is not None:
                    old_set.invalidated_at = finished
                session.flush()
                session.add(
                    MfaCredential(
                        id=row.id,
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
                        credential_id=row.id,
                        created_at=finished,
                    )
                )
                session.flush()
                session.add_all(
                    MfaRecoveryCode(
                        set_id=set_id,
                        position=i,
                        code_hash=recovery_digest(
                            value, user_id=user.id, set_id=set_id
                        ),
                    )
                    for i, value in enumerate(codes)
                )
                row.closed_at = row.activated_at = finished
                row.key_id = row.nonce = row.ciphertext = None
                session.flush()
                state = advance_security_revision(
                    session, user_id=user.id, expected_revision=user.security_revision
                )
                enqueue_notice(
                    session,
                    user=user,
                    event=SecurityEvent(
                        user_id=user.id,
                        credential_id=row.id,
                        action="mfa_rotated",
                        security_revision=state.revision,
                        occurred_at=finished,
                    ),
                    now=finished,
                )
                session.flush()
                if not finished <= _database_now(session) < row.expires_at:
                    raise _Expired
                result = RecoveryBundle(row.id, state.revision, codes)
            return result
        except (AccessDenied, _Expired):
            return None
        except (SQLAlchemyError, SeedProtectionError, quota.MfaVerificationUnavailable):
            raise MfaRotationUnavailable("MFA rotation unavailable") from None
