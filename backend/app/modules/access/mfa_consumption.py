"""Low-level transactional consumption, NOT a login/authorization service.

Trusted callers supply IDs from server state and own begin/commit/rollback.
Do not expose these helpers before pending proofs, quotas and session guards
are integrated.
Lock order: user -> credential -> recovery set -> code. No tenant locks afterward.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.mfa_crypto import (
    EncryptedSeed,
    RecoveryCode,
    SeedKeyring,
    match_totp,
    recovery_digest,
)
from app.modules.access.models import (
    MfaCredential,
    MfaRecoveryCode,
    MfaRecoverySet,
    SecurityEvent,
)
from app.modules.access.sessions import _database_now, _lock_user


@dataclass(frozen=True)
class MfaConsumption:
    """Uncommitted evidence only; never a session or transferable proof."""

    credential_id: UUID
    verified_at: datetime


def _locked_credential(
    session: Session, *, user_id: UUID, credential_id: UUID
) -> MfaCredential | None:
    if not isinstance(user_id, UUID) or not isinstance(credential_id, UUID):
        raise ValueError("Invalid MFA identity")
    user = _lock_user(session, user_id)
    if user is None or not user.is_active:
        return None
    record = session.scalar(
        select(MfaCredential)
        .where(MfaCredential.user_id == user_id, MfaCredential.id == credential_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return record if record is not None and record.revoked_at is None else None


def consume_totp(
    session: Session,
    *,
    user_id: UUID,
    credential_id: UUID,
    code: SecretStr,
    keyring: SeedKeyring,
) -> MfaConsumption | None:
    """Consume globally for this factor, regardless of future caller purpose.

    Missing/invalid keys propagate a generic protection error, never reset state.
    No commit: failure of later work must roll back this consumption too.
    """
    record = _locked_credential(session, user_id=user_id, credential_id=credential_id)
    if record is None:
        return None
    now = _database_now(session)
    if now < record.created_at:
        return None
    seed = keyring.decrypt(
        EncryptedSeed(
            version=record.encryption_version,
            key_id=record.key_id,
            nonce=record.nonce,
            ciphertext=record.ciphertext,
        ),
        user_id=user_id,
        credential_id=record.id,
    )
    counter = match_totp(
        seed,
        code,
        now=now,
        last_counter=record.last_counter,
        last_verified_at=record.last_verified_at,
    )
    if counter is None:
        return None
    record.last_counter = counter
    record.last_verified_at = now
    session.add(
        SecurityEvent(
            user_id=user_id,
            credential_id=record.id,
            action="mfa_totp_consumed",
            occurred_at=now,
        )
    )
    session.flush()
    return MfaConsumption(credential_id=record.id, verified_at=now)


def consume_recovery_code(
    session: Session,
    *,
    user_id: UUID,
    credential_id: UUID,
    set_id: UUID,
    code: RecoveryCode,
) -> MfaConsumption | None:
    """Consume once; does NOT grant recovery, replace a factor or issue a session."""
    if not isinstance(set_id, UUID):
        raise ValueError("Invalid MFA identity")
    record = _locked_credential(session, user_id=user_id, credential_id=credential_id)
    if record is None:
        return None
    recovery_set = session.scalar(
        select(MfaRecoverySet)
        .where(
            MfaRecoverySet.id == set_id,
            MfaRecoverySet.user_id == user_id,
            MfaRecoverySet.credential_id == record.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if recovery_set is None or recovery_set.invalidated_at is not None:
        return None
    digest = recovery_digest(code, user_id=user_id, set_id=recovery_set.id)
    stored = session.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.set_id == recovery_set.id,
            MfaRecoveryCode.code_hash == digest,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # Read after every potentially blocking lock, never transaction_timestamp().
    now = _database_now(session)
    if (
        stored is None
        or stored.used_at is not None
        or now < record.created_at
        or now < recovery_set.created_at
    ):
        return None
    stored.used_at = now
    session.add(
        SecurityEvent(
            user_id=user_id,
            credential_id=record.id,
            action="mfa_recovery_consumed",
            occurred_at=now,
        )
    )
    session.flush()
    return MfaConsumption(credential_id=record.id, verified_at=now)
