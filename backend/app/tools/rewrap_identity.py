"""Bounded TOTP re-encryption under user/factor locks. No factor/counter reset."""

import argparse

from sqlalchemy import select

from app.core.config import load_settings
from app.core.database import build_engine, create_session_factory
from app.modules.access.identity_http import IdentityAccess
from app.modules.access.mfa_crypto import EncryptedSeed
from app.modules.access.mfa_verification import _timeouts
from app.modules.access.models import MfaCredential, SecurityEvent, User
from app.modules.access.sessions import _database_now


def rewrap(engine, keyring, *, limit=100):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid rewrap limit")
    factory = create_session_factory(engine)
    done = 0
    with factory() as session:
        hints = session.execute(
            select(MfaCredential.id, MfaCredential.user_id)
            .where(MfaCredential.key_id != keyring._active_key_id)
            .order_by(MfaCredential.user_id, MfaCredential.id)
            .limit(limit)
        ).all()
    for identity, user_id in hints:
        with factory.begin() as session:
            _timeouts(session)
            user = session.scalar(
                select(User)
                .where(User.id == user_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if user is None:
                continue
            row = session.scalar(
                select(MfaCredential)
                .where(MfaCredential.id == identity, MfaCredential.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None or row.key_id == keyring._active_key_id:
                continue
            seed = keyring.decrypt(
                EncryptedSeed(
                    version=row.encryption_version,
                    key_id=row.key_id,
                    nonce=row.nonce,
                    ciphertext=row.ciphertext,
                ),
                user_id=user.id,
                credential_id=row.id,
            )
            envelope = keyring.encrypt(seed, user_id=user.id, credential_id=row.id)
            row.encryption_version, row.key_id, row.nonce, row.ciphertext = (
                envelope.version,
                envelope.key_id,
                envelope.nonce,
                envelope.ciphertext,
            )
            session.add(
                SecurityEvent(
                    user_id=user.id,
                    credential_id=row.id,
                    action="mfa_seed_rewrapped",
                    security_revision=user.security_revision,
                    occurred_at=_database_now(session),
                )
            )
            session.flush()
            done += 1
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    config = load_settings()
    engine = build_engine(config)
    try:
        count = rewrap(
            engine,
            IdentityAccess(engine, config.identity_keys_file).require().keyring,
            limit=args.limit,
        )
    except Exception:
        parser.exit(1, "Identity rewrap failed; keep all old keys.\n")
    finally:
        engine.dispose()
    print(
        "Identity credentials rewrapped: "
        + str(count)
        + "; old keys must remain for retained backups."
    )


if __name__ == "__main__":
    main()
