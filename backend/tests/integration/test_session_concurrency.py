"""Independent PostgreSQL connections: auth/revocation cannot resurrect sessions."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.modules.access.membership import AccessDenied
from app.modules.access.models import SessionRecord, Tenant, TenantMembership, User
from app.modules.access.sessions import (
    issue_session,
    resolve_session,
    revoke_session,
    revoke_user_sessions,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def committed_session(db_session, databases):
    runtime, owner, _ = databases
    with Session(runtime) as session, session.begin():
        tenant = Tenant(name="Concurrent session test")
        user = User(email="concurrent@example.invalid", password_hash="synthetic")
        session.add_all([tenant, user])
        session.flush()
        membership = TenantMembership(
            tenant_id=tenant.id, user_id=user.id, role="admin"
        )
        session.add(membership)
        session.flush()
        issued = issue_session(session, user_id=user.id, tenant_id=tenant.id)
        user_id, tenant_id, membership_id = user.id, tenant.id, membership.id
    try:
        yield runtime, owner, issued, user_id, tenant_id
    finally:
        with owner.begin() as connection:
            connection.execute(
                delete(SessionRecord).where(
                    SessionRecord.membership_id == membership_id
                )
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id == membership_id)
            )
            connection.execute(delete(User).where(User.id == user_id))
            connection.execute(delete(Tenant).where(Tenant.id == tenant_id))


def wait_for_block(owner, pid):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        with owner.connect() as connection:
            blocked = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE pid = :pid AND NOT granted)"
                ),
                {"pid": pid},
            )
        if blocked:
            return
        time.sleep(0.01)
    pytest.fail("The independent connection must actually wait for the row lock")


@pytest.mark.parametrize("first_operation", ["revoke", "resolve"])
def test_resolve_and_revoke_serialize_in_both_orders(
    committed_session, first_operation
):
    runtime, owner, issued, _, _ = committed_session
    started, waiter = Event(), {}

    def contend():
        with Session(runtime) as session, session.begin():
            waiter["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            if first_operation == "revoke":
                try:
                    resolve_session(session, issued.token)
                    return "accepted"
                except AccessDenied:
                    return "denied"
            revoke_session(session, issued.token)
            return "revoked"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(runtime) as holder, holder.begin():
            holder.scalar(select(SessionRecord).with_for_update())
            if first_operation == "revoke":
                revoke_session(holder, issued.token)
            else:
                resolve_session(holder, issued.token)
            result = pool.submit(contend)
            assert started.wait(3)
            wait_for_block(owner, waiter["pid"])
        assert result.result(timeout=5) == (
            "denied" if first_operation == "revoke" else "revoked"
        )
    with Session(runtime) as session, session.begin():
        assert session.scalar(select(SessionRecord)).revoked_at is not None
        with pytest.raises(AccessDenied):
            resolve_session(session, issued.token)


def test_bulk_revocation_waits_for_new_session_and_includes_it(committed_session):
    runtime, owner, issued, user_id, tenant_id = committed_session
    started, waiter = Event(), {}

    def revoke_all():
        with Session(runtime) as session, session.begin():
            waiter["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            revoke_user_sessions(session, user_id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(runtime) as holder, holder.begin():
            new = issue_session(holder, user_id=user_id, tenant_id=tenant_id)
            result = pool.submit(revoke_all)
            assert started.wait(3)
            wait_for_block(owner, waiter["pid"])
        result.result(timeout=5)
    with Session(runtime) as session, session.begin():
        for token in (issued.token, new.token):
            with pytest.raises(AccessDenied):
                resolve_session(session, token)
