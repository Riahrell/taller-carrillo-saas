"""Synthetic restoration evidence. Refuses every database except isolated CI test."""

import argparse
import os
import secrets
from uuid import UUID

from pydantic import SecretBytes, SecretStr
from sqlalchemy import select, update

from app.core.config import load_settings
from app.core.database import build_engine, create_session_factory
from app.modules.access.mfa_crypto import (
    EncryptedSeed,
    SeedKeyring,
    generate_recovery_codes,
    generate_totp_seed,
    recovery_digest,
)
from app.modules.access.models import (
    MfaCredential,
    MfaRecoveryCode,
    MfaRecoverySet,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password
from app.modules.access.sessions import _database_now
from app.tools.restore_quarantine import quarantine


def main():
    import json
    from pathlib import Path

    from sqlalchemy.engine import make_url

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=["seed", "mutate", "check", "quarantine", "verify-quarantine"]
    )
    args = parser.parse_args()
    config = load_settings()
    url = make_url(config.database_url.get_secret_value())
    if (
        config.app_env != "test"
        or url.host != "db"
        or url.database != "carrillo_test"
        or url.username != "carrillo_owner"
    ):
        parser.exit(2, "Refusing a non-isolated restoration probe.\n")
    data = json.loads(Path(os.environ["RESTORE_PROBE_FILE"]).read_text())
    user_id, credential_id, set_id = map(
        UUID, (data["user"], data["credential"], data["set"])
    )
    keyring = SeedKeyring(
        {"drill": SecretBytes(bytes.fromhex(data["key"]))}, active_key_id="drill"
    )
    engine = build_engine(config)
    try:
        if args.phase == "quarantine":
            quarantine(engine)
        else:
            with create_session_factory(engine).begin() as session:
                now = _database_now(session)
                if args.phase == "seed":
                    tenant = Tenant(name="Isolated restore drill")
                    user = User(
                        id=user_id,
                        email="restore-" + user_id.hex + "@example.invalid",
                        password_hash=hash_password(
                            SecretStr(secrets.token_urlsafe(32))
                        ),
                        mfa_required=True,
                        security_revision=1,
                    )
                    session.add_all([tenant, user])
                    session.flush()
                    session.add(
                        TenantMembership(
                            user_id=user.id, tenant_id=tenant.id, role="read_only"
                        )
                    )
                    seed = generate_totp_seed()
                    envelope = keyring.encrypt(
                        seed, user_id=user_id, credential_id=credential_id
                    )
                    session.add(
                        MfaCredential(
                            id=credential_id,
                            user_id=user_id,
                            encryption_version=envelope.version,
                            key_id=envelope.key_id,
                            nonce=envelope.nonce,
                            ciphertext=envelope.ciphertext,
                            created_at=now,
                        )
                    )
                    session.flush()
                    session.add(
                        MfaRecoverySet(
                            id=set_id,
                            user_id=user_id,
                            credential_id=credential_id,
                            created_at=now,
                        )
                    )
                    session.flush()
                    session.add_all(
                        MfaRecoveryCode(
                            set_id=set_id,
                            position=i,
                            code_hash=recovery_digest(
                                code, user_id=user_id, set_id=set_id
                            ),
                            used_at=now if i == 0 else None,
                        )
                        for i, code in enumerate(generate_recovery_codes())
                    )
                elif args.phase == "mutate":
                    session.execute(
                        update(MfaRecoveryCode)
                        .where(
                            MfaRecoveryCode.set_id == set_id,
                            MfaRecoveryCode.position == 1,
                        )
                        .values(used_at=now)
                    )
                else:
                    record = session.get(MfaCredential, credential_id)
                    if record is None or record.revoked_at is not None:
                        raise ValueError("Factor missing")
                    keyring.decrypt(
                        EncryptedSeed(
                            version=record.encryption_version,
                            key_id=record.key_id,
                            nonce=record.nonce,
                            ciphertext=record.ciphertext,
                        ),
                        user_id=user_id,
                        credential_id=credential_id,
                    )
                    codes = list(
                        session.scalars(
                            select(MfaRecoveryCode)
                            .where(MfaRecoveryCode.set_id == set_id)
                            .order_by(MfaRecoveryCode.position)
                        )
                    )
                    if (
                        len(codes) != 10
                        or codes[0].used_at is None
                        or codes[1].used_at is not None
                    ):
                        raise ValueError("Snapshot evidence mismatch")
                    recovery = session.get(MfaRecoverySet, set_id)
                    if args.phase == "verify-quarantine" and (
                        recovery.invalidated_at is None
                        or not session.get(User, user_id).mfa_required
                    ):
                        raise ValueError("Quarantine mismatch")
        print("Restore probe " + args.phase + ": passed (synthetic values hidden)")
    except Exception:
        parser.exit(1, "Restoration probe failed; keep traffic closed.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
