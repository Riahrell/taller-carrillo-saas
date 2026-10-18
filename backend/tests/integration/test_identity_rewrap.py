"""External key rotation changes ciphertext, never factor identity or replay state."""

import secrets

import pytest
from pydantic import SecretBytes
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access.mfa_crypto import EncryptedSeed, SeedKeyring
from app.modules.access.models import (
    MfaCredential,
    MfaRecoveryCode,
    SecurityEvent,
    User,
)
from app.tools.rewrap_identity import rewrap

pytestmark = pytest.mark.integration


def rotated(state):
    keys = dict(state["keyring"]._keys)
    keys["next"] = SecretBytes(secrets.token_bytes(32))
    return SeedKeyring(keys, active_key_id="next")


def snapshot(state):
    with Session(state["runtime"]) as session:
        row = session.get(MfaCredential, state["credential"])
        user = session.get(User, state["user"])
        return (
            EncryptedSeed(
                version=row.encryption_version,
                key_id=row.key_id,
                nonce=row.nonce,
                ciphertext=row.ciphertext,
            ),
            (
                row.id,
                row.user_id,
                row.created_at,
                row.last_counter,
                row.last_verified_at,
                row.revoked_at,
                user.security_revision,
                user.mfa_required,
            ),
            session.execute(
                select(
                    MfaRecoveryCode.position,
                    MfaRecoveryCode.code_hash,
                    MfaRecoveryCode.used_at,
                )
                .where(MfaRecoveryCode.set_id == state["set"])
                .order_by(MfaRecoveryCode.position)
            ).all(),
        )


def test_rewrap_preserves_counter_codes_owner_and_old_backup_decryption(mfa_state):
    state = mfa_state
    with Session(state["runtime"]) as session, session.begin():
        row = session.get(MfaCredential, state["credential"])
        row.last_counter = int(state["now"].timestamp()) // 30
        row.last_verified_at = state["now"]
    before = snapshot(state)
    keyring = rotated(state)
    assert rewrap(state["runtime"], keyring, limit=1) == 1
    after = snapshot(state)
    assert after[1:] == before[1:]
    assert after[0].key_id == "next" and after[0].nonce != before[0].nonce
    for envelope in (before[0], after[0]):
        assert (
            keyring.decrypt(
                envelope, user_id=state["user"], credential_id=state["credential"]
            ).value
            == state["seed"].value
        )
    assert rewrap(state["runtime"], keyring) == 0
    with Session(state["runtime"]) as session:
        assert (
            len(
                session.scalars(
                    select(SecurityEvent).where(
                        SecurityEvent.user_id == state["user"],
                        SecurityEvent.action == "mfa_seed_rewrapped",
                    )
                ).all()
            )
            == 1
        )


def test_rewrap_and_audit_rollback_together_if_audit_write_is_refused(mfa_state):
    state = mfa_state
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(text("REVOKE INSERT ON security_events FROM carrillo_app"))
    try:
        with pytest.raises(SQLAlchemyError):
            rewrap(state["runtime"], rotated(state))
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text("GRANT INSERT ON security_events TO carrillo_app"))
    assert snapshot(state) == before
