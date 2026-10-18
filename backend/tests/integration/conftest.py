"""Guarded, shared PostgreSQL engines for the isolated Compose test database."""

import os
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from app.core.config import Settings, load_settings
from app.core.database import Base, build_engine, connect_safely
from app.modules.access import models  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401


@pytest.fixture(scope="session")
def databases():
    if os.environ.get("RUN_DB_INTEGRATION") != "1":
        pytest.skip("Enable integration tests only through the isolated Compose runner")
    settings = load_settings()
    if settings.app_env != "test":
        pytest.fail("Integration tests require APP_ENV=test")
    owner_file = os.environ.get("MIGRATION_DATABASE_URL_FILE")
    if not owner_file:
        pytest.fail("An isolated migration credential file is required")
    owner_secret = Path(owner_file).read_text().strip()
    owner_settings = Settings(
        app_env="test",
        database_url=owner_secret,
        database_url_file=None,
        _env_file=None,
    )
    runtime_url = make_url(settings.database_url.get_secret_value())
    owner_url = make_url(owner_secret)
    if (
        runtime_url.database != "carrillo_test"
        or runtime_url.host != "db"
        or owner_url.database != runtime_url.database
        or owner_url.host != runtime_url.host
        or owner_url.port != runtime_url.port
        or runtime_url.username != "carrillo_app"
        or owner_url.username != "carrillo_owner"
    ):
        pytest.fail("Refusing integration tests outside the isolated test database")
    runtime = build_engine(settings)
    owner = build_engine(owner_settings)
    try:
        with connect_safely(owner) as connection:
            tables = inspect(connection).get_table_names(schema="public")
            if set(tables) - (set(Base.metadata.tables) | {"alembic_version"}):
                pytest.fail(
                    "Test database contains unexpected tables; refusing changes"
                )
        yield runtime, owner, settings
    finally:
        runtime.dispose()
        owner.dispose()


@pytest.fixture
def db_session(databases):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.orm import Session

    runtime, owner, _ = databases
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with owner.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with runtime.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(
                bind=connection, join_transaction_mode="create_savepoint"
            ) as session:
                yield session
        finally:
            transaction.rollback()


@pytest.fixture
def http_identity(db_session, databases):
    """Committed synthetic identities, exclusively in the guarded ephemeral DB."""
    import secrets
    from uuid import uuid4

    from pydantic import SecretStr
    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session

    from app.modules.access.models import (
        AuditEvent,
        EnrollmentMailThrottle,
        LoginChallenge,
        LoginThrottle,
        MfaThrottle,
        PasswordResetThrottle,
        SessionRecord,
        Tenant,
        TenantMembership,
        User,
    )
    from app.modules.access.passwords import hash_password

    runtime, owner, settings = databases
    password, email = (
        SecretStr(secrets.token_urlsafe(32)),
        f"http-{uuid4().hex}@example.com",
    )
    with Session(runtime) as session, session.begin():
        tenant, other = Tenant(name="HTTP company"), Tenant(name="HTTP foreign company")
        user, foreign = (
            User(email=email, password_hash=hash_password(password)),
            User(email=f"foreign-{uuid4().hex}@example.com", password_hash="synthetic"),
        )
        session.add_all([tenant, other, user, foreign])
        session.flush()
        member = TenantMembership(tenant_id=tenant.id, user_id=user.id, role="admin")
        foreign_member = TenantMembership(
            tenant_id=other.id, user_id=foreign.id, role="read_only"
        )
        session.add_all([member, foreign_member])
        session.flush()
        state = dict(
            runtime=runtime,
            owner=owner,
            settings=settings,
            password=password,
            email=email,
            user=user.id,
            foreign=foreign.id,
            tenant=tenant.id,
            other=other.id,
            membership=member.id,
            foreign_membership=foreign_member.id,
        )
    try:
        yield state
    finally:
        with owner.begin() as connection:
            from identity_cleanup import cleanup_identity

            cleanup_identity(connection, [state["user"], state["foreign"]])
            connection.execute(
                delete(AuditEvent).where(
                    AuditEvent.tenant_id.in_([state["tenant"], state["other"]])
                )
            )
            memberships = select(TenantMembership.id).where(
                TenantMembership.tenant_id.in_([state["tenant"], state["other"]])
            )
            connection.execute(
                delete(SessionRecord).where(
                    SessionRecord.membership_id.in_(memberships)
                )
            )
            connection.execute(
                delete(LoginChallenge).where(
                    LoginChallenge.user_id.in_([state["user"], state["foreign"]])
                )
            )
            connection.execute(
                delete(TenantMembership).where(
                    TenantMembership.tenant_id.in_([state["tenant"], state["other"]])
                )
            )
            connection.execute(
                delete(User).where(User.id.in_([state["user"], state["foreign"]]))
            )
            connection.execute(
                delete(Tenant).where(Tenant.id.in_([state["tenant"], state["other"]]))
            )
            connection.execute(delete(LoginThrottle))
            connection.execute(delete(MfaThrottle))
            connection.execute(delete(EnrollmentMailThrottle))
            connection.execute(delete(PasswordResetThrottle))


@pytest.fixture
def recent_session(databases):
    """Arrange confirmed state for legacy business tests, never shipped at runtime.

    The guarded databases fixture restricts this helper to ephemeral PostgreSQL.
    New confirmation/HTTP tests exercise real password verification and rotation.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.modules.access.login_challenges import _password_fingerprint
    from app.modules.access.models import SessionRecord, TenantMembership, User
    from app.modules.access.sessions import _database_now, _token_hash

    runtime, _, _ = databases

    def mark(token):
        with Session(runtime) as session, session.begin():
            row = session.scalar(
                select(SessionRecord).where(
                    SessionRecord.token_hash == _token_hash(token)
                )
            )
            assert row is not None
            user = session.scalar(
                select(User)
                .join(TenantMembership, TenantMembership.user_id == User.id)
                .where(TenantMembership.id == row.membership_id)
            )
            row.reauthenticated_at = _database_now(session)
            row.reauthentication_fingerprint = _password_fingerprint(user)

    return mark


@pytest.fixture
def new_identity_scope(http_identity):
    """Track HTTP-created synthetic users without changing operational accounts."""
    from uuid import uuid4

    from sqlalchemy import delete, select

    from app.modules.access.models import (
        AuditEvent,
        LoginChallenge,
        SessionRecord,
        TenantMembership,
        User,
    )

    state = dict(http_identity, prefix="web-" + uuid4().hex + "-")
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            from identity_cleanup import cleanup_identity

            users = select(User.id).where(User.email.startswith(state["prefix"]))
            cleanup_identity(connection, users)
            members = select(TenantMembership.id).where(
                TenantMembership.user_id.in_(users)
            )
            connection.execute(
                delete(AuditEvent).where(AuditEvent.target_membership_id.in_(members))
            )
            connection.execute(
                delete(SessionRecord).where(SessionRecord.membership_id.in_(members))
            )
            connection.execute(
                delete(LoginChallenge).where(LoginChallenge.user_id.in_(users))
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.user_id.in_(users))
            )
            connection.execute(delete(User).where(User.id.in_(users)))


@pytest.fixture
def mfa_state(db_session, databases, monkeypatch):
    """Committed synthetic factors only in the guarded ephemeral PostgreSQL DB."""
    import secrets
    from datetime import timedelta
    from uuid import uuid4

    import pyotp
    from pydantic import SecretBytes
    from sqlalchemy import delete, func, select
    from sqlalchemy.orm import Session

    from app.modules.access import mfa_consumption as service
    from app.modules.access import mfa_crypto as crypto
    from app.modules.access import mfa_verification as verification
    from app.modules.access.models import (
        MfaCredential,
        MfaRecoveryCode,
        MfaRecoverySet,
        MfaThrottle,
        SecurityEvent,
        User,
    )

    runtime, owner, _ = databases
    keyring = crypto.SeedKeyring(
        {"test-only": SecretBytes(secrets.token_bytes(32))}, active_key_id="test-only"
    )
    with Session(runtime) as session, session.begin():
        now = session.scalar(select(func.clock_timestamp()))
        users = [
            User(email=f"mfa-{uuid4().hex}@example.com", password_hash="synthetic")
            for _ in range(2)
        ]
        session.add_all(users)
        session.flush()
        user_ids = [u.id for u in users]
        credential_id, set_id = uuid4(), uuid4()
        # Avoid probabilistic collisions in success tests; dedicated crypto tests
        # already force ambiguous counters and verify their rejection.
        while True:
            seed = crypto.generate_totp_seed()
            otp = pyotp.TOTP(seed.base32().get_secret_value())
            counter = int(now.timestamp()) // 30
            if (
                len({otp.generate_otp(counter + offset) for offset in range(-4, 40)})
                == 44
            ):
                break
        envelope = keyring.encrypt(
            seed, user_id=user_ids[0], credential_id=credential_id
        )
        session.add(
            MfaCredential(
                id=credential_id,
                user_id=user_ids[0],
                encryption_version=envelope.version,
                key_id=envelope.key_id,
                nonce=envelope.nonce,
                ciphertext=envelope.ciphertext,
                created_at=now - timedelta(minutes=2),
            )
        )
        session.flush()
        session.add(
            MfaRecoverySet(
                id=set_id,
                user_id=user_ids[0],
                credential_id=credential_id,
                created_at=now - timedelta(minutes=1),
            )
        )
        session.flush()
        codes = crypto.generate_recovery_codes()
        session.add_all(
            [
                MfaRecoveryCode(
                    set_id=set_id,
                    position=i,
                    code_hash=crypto.recovery_digest(
                        code, user_id=user_ids[0], set_id=set_id
                    ),
                )
                for i, code in enumerate(codes)
            ]
        )
    result = dict(
        runtime=runtime,
        owner=owner,
        user=user_ids[0],
        other=user_ids[1],
        credential=credential_id,
        set=set_id,
        keyring=keyring,
        seed=seed,
        codes=codes,
        now=now,
    )
    monkeypatch.setattr(service, "_database_now", lambda session: result["now"])
    monkeypatch.setattr(verification, "_now", lambda session: result["now"])
    try:
        yield result
    finally:
        with owner.begin() as connection:
            from identity_cleanup import cleanup_identity

            cleanup_identity(connection, user_ids)
            connection.execute(delete(MfaThrottle))
            connection.execute(
                delete(SecurityEvent).where(SecurityEvent.user_id.in_(user_ids))
            )
            connection.execute(
                delete(MfaRecoveryCode).where(MfaRecoveryCode.set_id == set_id)
            )
            connection.execute(
                delete(MfaRecoverySet).where(
                    MfaRecoverySet.credential_id == credential_id
                )
            )
            connection.execute(
                delete(MfaCredential).where(MfaCredential.user_id.in_(user_ids))
            )
            connection.execute(delete(User).where(User.id.in_(user_ids)))


@pytest.fixture
def eligible_administrator(mfa_state):
    """Retention-only admin membership backed by an existing confirmed factor."""
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.modules.access.models import TenantMembership

    created = []

    def add(tenant_id):
        with Session(mfa_state["runtime"]) as session, session.begin():
            member = TenantMembership(
                tenant_id=tenant_id, user_id=mfa_state["user"], role="admin"
            )
            session.add(member)
            session.flush()
            created.append(member.id)
            return member.id

    try:
        yield add
    finally:
        with mfa_state["owner"].begin() as connection:
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id.in_(created))
            )
