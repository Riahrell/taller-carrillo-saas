"""Committed transactions and concurrent bootstrap in the guarded ephemeral DB."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import Session

from app.modules.access.bootstrap import (
    BOOTSTRAP_LOCK,
    BootstrapInput,
    BootstrapUnavailable,
    bootstrap,
)
from app.modules.access.models import MembershipRole, Tenant, TenantMembership, User
from app.modules.access.passwords import verify_password

pytestmark = pytest.mark.integration
ACCESS_TABLES = (TenantMembership, User, Tenant)


@pytest.fixture
def bootstrap_db(db_session, databases):
    # db_session applies migrations; databases already enforces the test DB guard.
    runtime, owner, _ = databases
    with owner.connect() as connection:
        assert all(
            connection.scalar(select(func.count()).select_from(model)) == 0
            for model in ACCESS_TABLES
        )
    try:
        yield runtime, owner
    finally:
        # Owns committed test identities only in ephemeral carrillo_test.
        with owner.begin() as connection:
            from identity_cleanup import cleanup_identity

            cleanup_identity(connection, select(User.id))
            for model in ACCESS_TABLES:
                connection.execute(delete(model))


def bootstrap_data():
    return BootstrapInput(
        tenant_name="Taller Carrillo",
        email=" ADMIN@EXAMPLE.COM ",
        password=SecretStr(secrets.token_urlsafe(32)),
    )


def counts(owner):
    with owner.connect() as connection:
        return tuple(
            connection.scalar(select(func.count()).select_from(model))
            for model in ACCESS_TABLES
        )


def test_bootstrap_commits_admin_and_rejects_repetition_without_changes(bootstrap_db):
    runtime, owner = bootstrap_db
    data = bootstrap_data()
    result = bootstrap(runtime, data)
    assert counts(owner) == (1, 1, 1)
    with Session(runtime) as session:
        user = session.get(User, result.user_id)
        tenant = session.get(Tenant, result.tenant_id)
        membership = session.get(TenantMembership, result.membership_id)
        assert user.email == "admin@example.com"
        assert verify_password(data.password, user.password_hash)
        assert tenant.name == "Taller Carrillo"
        assert (user.is_active, tenant.is_active, membership.is_active) == (True,) * 3
        assert membership.role == MembershipRole.ADMIN
        assert (membership.tenant_id, membership.user_id) == (tenant.id, user.id)
    with pytest.raises(BootstrapUnavailable, match="empty access tables"):
        bootstrap(runtime, bootstrap_data())
    assert counts(owner) == (1, 1, 1)


@pytest.mark.parametrize("existing", ["tenant", "user"])
def test_any_existing_identity_even_inactive_prevents_bootstrap(bootstrap_db, existing):
    runtime, owner = bootstrap_db
    with Session(runtime) as session, session.begin():
        if existing == "tenant":
            session.add(Tenant(name="Existing", is_active=False))
        else:
            session.add(
                User(
                    email="existing@example.com",
                    password_hash="synthetic",
                    is_active=False,
                )
            )
    before = counts(owner)
    with pytest.raises(BootstrapUnavailable):
        bootstrap(runtime, bootstrap_data())
    assert counts(owner) == before


def test_failure_after_identity_flush_rolls_back_everything_and_allows_retry(
    bootstrap_db,
):
    runtime, owner = bootstrap_db

    def fail_membership(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO tenant_memberships"):
            raise RuntimeError("injected persistence failure")

    event.listen(runtime, "before_cursor_execute", fail_membership)
    try:
        with pytest.raises(RuntimeError, match="injected persistence failure"):
            bootstrap(runtime, bootstrap_data())
    finally:
        event.remove(runtime, "before_cursor_execute", fail_membership)
    assert counts(owner) == (0, 0, 0)
    bootstrap(runtime, bootstrap_data())
    assert counts(owner) == (1, 1, 1)


def test_outdated_migration_refuses_bootstrap_before_writes(bootstrap_db):
    runtime, owner = bootstrap_db
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = '0001_foundation'")
        )
    try:
        with pytest.raises(BootstrapUnavailable, match="current migrations"):
            bootstrap(runtime, bootstrap_data())
        assert counts(owner) == (0, 0, 0)
    finally:
        with owner.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :head"),
                {
                    "head": ScriptDirectory(
                        str(Path(__file__).resolve().parents[2] / "migrations")
                    ).get_current_head()
                },
            )


def test_two_simultaneous_bootstraps_commit_only_one_admin(bootstrap_db):
    runtime, owner = bootstrap_db
    first_at_membership, release_first = Event(), Event()

    def pause_first(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO tenant_memberships"):
            first_at_membership.set()
            if not release_first.wait(10):
                raise RuntimeError("Concurrent test synchronization timed out")

    def attempt():
        try:
            bootstrap(runtime, bootstrap_data())
            return "created"
        except BootstrapUnavailable:
            return "refused"

    event.listen(runtime, "before_cursor_execute", pause_first)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(attempt)
            try:
                assert first_at_membership.wait(5)
                second = pool.submit(attempt)
                # Observe the actual PostgreSQL waiter to prove overlap.
                deadline = time.monotonic() + 4
                waiting = False
                while time.monotonic() < deadline:
                    with owner.connect() as connection:
                        waiting = bool(
                            connection.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                                    "WHERE locktype = 'advisory' "
                                    "AND classid = :namespace AND objid = :operation "
                                    "AND NOT granted)"
                                ),
                                {
                                    "namespace": BOOTSTRAP_LOCK[0],
                                    "operation": BOOTSTRAP_LOCK[1],
                                },
                            )
                        )
                    if waiting:
                        break
                    time.sleep(0.01)
                assert waiting, "Second connection must wait for the bootstrap lock"
            finally:
                release_first.set()
            assert first.result(timeout=10) == "created"
            assert second.result(timeout=10) == "refused"
    finally:
        release_first.set()
        event.remove(runtime, "before_cursor_execute", pause_first)
    assert counts(owner) == (1, 1, 1)
