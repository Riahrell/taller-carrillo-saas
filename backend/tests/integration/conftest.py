"""Guarded, shared PostgreSQL engines for the isolated Compose test database."""

import os
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from app.core.config import Settings, load_settings
from app.core.database import Base, build_engine, connect_safely
from app.modules.access import models  # noqa: F401


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
        LoginChallenge,
        LoginThrottle,
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
