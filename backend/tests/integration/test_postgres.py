"""Only runs against the explicitly enabled, fresh Compose test database."""

import secrets
import traceback
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database import (
    Base,
    DatabaseUnavailable,
    build_engine,
    connect_safely,
    create_session_factory,
)
from app.main import create_app

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]


def test_migrations_upgrade_check_downgrade_upgrade(databases) -> None:
    _, owner, _ = databases
    config = Config(str(BACKEND / "alembic.ini"))
    with owner.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        assert MigrationContext.configure(connection).get_current_revision() == (
            "0007_membership_creation_audit"
        )
        command.check(config)
        command.downgrade(config, "base")
        assert MigrationContext.configure(connection).get_current_revision() is None
        command.upgrade(config, "head")
        assert set(inspect(connection).get_table_names(schema="public")) == (
            set(Base.metadata.tables) | {"alembic_version"}
        )


def test_runtime_and_migration_roles_are_separate_and_not_superusers(databases) -> None:
    runtime, owner, _ = databases
    for engine, expected_role, can_create in (
        (runtime, "carrillo_app", False),
        (owner, "carrillo_owner", True),
    ):
        with engine.connect() as connection:
            role = connection.execute(
                text(
                    "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            assert tuple(role) == (expected_role, False, False, False)
            assert (
                connection.scalar(
                    text(
                        "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
                    )
                )
                is can_create
            )
            assert connection.scalar(text("SHOW timezone")) == "UTC"


def test_session_transaction_rolls_back(databases) -> None:
    runtime, _, _ = databases
    factory = create_session_factory(runtime)
    with runtime.connect() as connection:
        connection.execute(text("CREATE TEMP TABLE transaction_probe (value integer)"))
        connection.commit()
        try:
            with (
                factory(bind=connection) as session,
                pytest.raises(RuntimeError, match="deliberate failure"),
                session.begin(),
            ):
                session.execute(text("INSERT INTO transaction_probe VALUES (1)"))
                raise RuntimeError("deliberate failure")
            assert (
                connection.scalar(text("SELECT count(*) FROM transaction_probe")) == 0
            )
        finally:
            connection.execute(text("DROP TABLE transaction_probe"))
            connection.commit()


def test_application_starts_with_real_postgresql(databases) -> None:
    _, _, settings = databases
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_invalid_credentials_fail_without_exposing_connection_details(
    databases,
) -> None:
    _, _, settings = databases
    invalid_password = secrets.token_urlsafe(48)
    url = make_url(settings.database_url.get_secret_value()).set(
        password=invalid_password
    )
    invalid_settings = Settings(
        app_env="test",
        database_url=url.render_as_string(hide_password=False),
        database_url_file=None,
        _env_file=None,
    )
    engine = build_engine(invalid_settings)
    try:
        with pytest.raises(DatabaseUnavailable) as caught:
            connect_safely(engine)
        rendered = "".join(traceback.format_exception(caught.value))
        assert invalid_password not in rendered
        assert "postgresql+psycopg://" not in rendered
        assert caught.value.__suppress_context__ is True
    finally:
        engine.dispose()
