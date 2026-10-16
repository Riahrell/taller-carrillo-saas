"""Verified identity and one-use company selection against isolated PostgreSQL."""

import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from threading import Event
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import authentication as auth
from app.modules.access import login_challenges as challenges
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    LoginChallenge,
    LoginThrottle,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password, password_needs_rehash
from app.modules.access.sessions import IssuedSession, resolve_session

pytestmark = pytest.mark.integration


@pytest.fixture
def login_state(db_session, databases):
    runtime, owner, _ = databases
    password = SecretStr(secrets.token_urlsafe(32))
    with Session(runtime) as session, session.begin():
        tenants = [Tenant(name=name) for name in ("Choice A", "Choice B", "Foreign")]
        user = User(email="choices@example.com", password_hash=hash_password(password))
        other = User(email="foreign@example.com", password_hash="synthetic")
        session.add_all([*tenants, user, other])
        session.flush()
        memberships = [
            TenantMembership(tenant_id=tenants[0].id, user_id=user.id, role="admin"),
            TenantMembership(
                tenant_id=tenants[1].id, user_id=user.id, role="read_only"
            ),
            TenantMembership(tenant_id=tenants[2].id, user_id=other.id, role="admin"),
        ]
        session.add_all(memberships)
        session.flush()
        tenant_ids = [tenant.id for tenant in tenants]
        membership_ids = [membership.id for membership in memberships]
        user_ids = [user.id, other.id]
    state = {
        "runtime": runtime,
        "owner": owner,
        "tenants": tenant_ids,
        "memberships": membership_ids,
        "users": user_ids,
        "password": password,
        "binding": SecretStr(secrets.token_urlsafe(32)),
        "auth": auth.Authenticator(runtime),
    }
    try:
        yield state
    finally:
        with owner.begin() as connection:
            connection.execute(
                delete(SessionRecord).where(
                    SessionRecord.membership_id.in_(membership_ids)
                )
            )
            connection.execute(
                delete(LoginChallenge).where(LoginChallenge.user_id.in_(user_ids))
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id.in_(membership_ids))
            )
            connection.execute(delete(User).where(User.id.in_(user_ids)))
            connection.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
            connection.execute(delete(LoginThrottle))


def begin(state, *, password=None, email="choices@example.com", service=None):
    return (service or state["auth"]).begin_login(
        auth.Credentials(email=email, password=password or state["password"]),
        client_ip=ip_address("127.0.0.1"),
        browser_binding=state["binding"],
    )


def finish(state, pending, *, membership=None, binding=None, service=None):
    return (service or state["auth"]).select_membership(
        challenge_token=pending.token,
        browser_binding=binding or state["binding"],
        membership_id=membership or state["memberships"][0],
    )


def count(state, model):
    with Session(state["runtime"]) as session:
        return session.scalar(select(func.count()).select_from(model))


def change(state, model, record_id, **values):
    with state["owner"].begin() as connection:
        connection.execute(update(model).where(model.id == record_id).values(**values))


def record_for(state, pending):
    with Session(state["runtime"]) as session:
        return session.scalar(
            select(LoginChallenge).where(
                LoginChallenge.token_hash
                == hashlib.sha256(pending.token.get_secret_value().encode()).hexdigest()
            )
        )


def test_multiple_companies_require_verified_bound_pending_identity(login_state):
    state = login_state
    pending = begin(state, email=" CHOICES@EXAMPLE.COM ")
    assert isinstance(pending, challenges.PendingLogin)
    assert [
        (choice.membership_id, choice.tenant_name) for choice in pending.choices
    ] == [
        (state["memberships"][0], "Choice A"),
        (state["memberships"][1], "Choice B"),
    ]
    assert count(state, SessionRecord) == 0
    record = record_for(state, pending)
    assert record is not None and record.user_id == state["users"][0]
    assert record.expires_at - record.created_at == timedelta(minutes=5)
    assert record.expires_at == pending.expires_at and record.finished_at is None
    assert len(pending.token.get_secret_value()) == 43
    assert record.token_hash != pending.token.get_secret_value()
    assert record.binding_hash != state["binding"].get_secret_value()
    assert pending.token.get_secret_value() not in repr(pending)
    assert state["password"].get_secret_value() not in repr(pending)
    with (
        Session(state["runtime"]) as session,
        session.begin(),
        pytest.raises(AccessDenied),
    ):
        resolve_session(session, pending.token)


@pytest.mark.parametrize("inactive", ["tenant", "membership"])
def test_sole_active_membership_is_selected_only_by_server(login_state, inactive):
    state = login_state
    model, row = (
        (Tenant, state["tenants"][1])
        if inactive == "tenant"
        else (TenantMembership, state["memberships"][1])
    )
    change(state, model, row, is_active=False)
    issued = begin(state)
    assert isinstance(issued, IssuedSession)
    assert count(state, LoginChallenge) == 0
    with Session(state["runtime"]) as session, session.begin():
        context = resolve_session(session, issued.token)
        assert context.tenant_id == state["tenants"][0]
        assert context.user_id == state["users"][0] and context.role == "admin"


@pytest.mark.parametrize(
    "reason", ["password", "unknown", "user", "memberships", "tenants", "hash"]
)
def test_rejection_returns_neither_company_names_nor_pending_state(login_state, reason):
    state, kwargs = login_state, {}
    if reason == "password":
        kwargs["password"] = SecretStr(secrets.token_urlsafe(32))
    elif reason == "unknown":
        kwargs["email"] = "absent@example.com"
    elif reason == "user":
        change(state, User, state["users"][0], is_active=False)
    elif reason == "hash":
        change(state, User, state["users"][0], password_hash="malformed")
    else:
        model, ids = (
            (Tenant, state["tenants"][:2])
            if reason == "tenants"
            else (TenantMembership, state["memberships"][:2])
        )
        for record_id in ids:
            change(state, model, record_id, is_active=False)
    assert begin(state, **kwargs) is None
    assert count(state, LoginChallenge) == count(state, SessionRecord) == 0
    with Session(state["runtime"]) as session:
        assert list(session.scalars(select(LoginThrottle.failures))) == [1, 1]


def test_begin_uses_existing_shared_limits_without_hashing_when_blocked(
    login_state, monkeypatch
):
    state = login_state
    for _ in range(5):
        assert begin(state, password=SecretStr(secrets.token_urlsafe(32))) is None
    second = auth.Authenticator(state["runtime"])
    monkeypatch.setattr(
        auth, "verify_password", lambda *_: pytest.fail("Must reject before hashing")
    )
    assert begin(state, service=second) is None
    assert count(state, LoginChallenge) == count(state, SessionRecord) == 0


def test_selection_consumes_once_and_uses_current_server_role(login_state):
    state = login_state
    pending = begin(state)
    change(state, TenantMembership, state["memberships"][1], role="mechanic")
    issued = finish(state, pending, membership=state["memberships"][1])
    assert isinstance(issued, IssuedSession)
    assert pending.token != issued.token
    assert record_for(state, pending).finished_at is not None
    with Session(state["runtime"]) as session, session.begin():
        context = resolve_session(session, issued.token)
        assert context.tenant_id == state["tenants"][1] and context.role == "mechanic"
    assert finish(state, pending) is None
    assert count(state, SessionRecord) == 1


@pytest.mark.parametrize("membership_kind", ["foreign", "unknown", "tenant_id"])
def test_foreign_or_arbitrary_selection_is_generic_and_consumes_attempt(
    login_state, membership_kind
):
    state = login_state
    pending = begin(state)
    candidate = {
        "foreign": state["memberships"][2],
        "unknown": uuid4(),
        "tenant_id": state["tenants"][0],
    }[membership_kind]
    assert finish(state, pending, membership=candidate) is None
    assert record_for(state, pending).finished_at is not None
    assert finish(state, pending) is None and count(state, SessionRecord) == 0


def test_other_browser_cannot_use_or_consume_pending_identity(login_state):
    state = login_state
    pending = begin(state)
    assert finish(state, pending, binding=SecretStr(secrets.token_urlsafe(32))) is None
    assert record_for(state, pending).finished_at is None
    assert isinstance(finish(state, pending), IssuedSession)


@pytest.mark.parametrize(
    "reason", ["user", "tenant", "membership", "password", "rehash"]
)
def test_selection_rechecks_account_credentials_and_active_membership(
    login_state, reason
):
    state = login_state
    pending = begin(state)
    if reason in ("password", "rehash"):
        password = (
            SecretStr(secrets.token_urlsafe(32))
            if reason == "password"
            else state["password"]
        )
        change(state, User, state["users"][0], password_hash=hash_password(password))
    else:
        model, row = {
            "user": (User, state["users"][0]),
            "tenant": (Tenant, state["tenants"][0]),
            "membership": (TenantMembership, state["memberships"][0]),
        }[reason]
        change(state, model, row, is_active=False)
    assert finish(state, pending) is None
    assert record_for(state, pending).finished_at is not None
    assert count(state, SessionRecord) == 0


@pytest.mark.parametrize("seconds", [300, 301, -1])
def test_expiration_is_inclusive_and_future_creation_is_rejected(
    login_state, monkeypatch, seconds
):
    state = login_state
    pending = begin(state)
    record = record_for(state, pending)
    monkeypatch.setattr(
        challenges,
        "_database_now",
        lambda _: record.created_at + timedelta(seconds=seconds),
    )
    assert finish(state, pending) is None and count(state, SessionRecord) == 0


def test_new_successful_login_closes_earlier_pending_identity(login_state):
    state = login_state
    first = begin(state)
    second = begin(state)
    assert first.token != second.token
    assert record_for(state, first).finished_at is not None
    assert finish(state, first) is None
    assert isinstance(finish(state, second), IssuedSession)


def test_rejected_credentials_do_not_invalidate_existing_pending_login(login_state):
    state = login_state
    pending = begin(state)
    assert begin(state, password=SecretStr(secrets.token_urlsafe(32))) is None
    assert isinstance(finish(state, pending), IssuedSession)


def test_rehash_and_pending_fingerprint_commit_together(login_state):
    state = login_state
    old = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(
        state["password"].get_secret_value()
    )
    change(state, User, state["users"][0], password_hash=old)
    pending = begin(state)
    with Session(state["runtime"]) as session:
        new = session.get(User, state["users"][0]).password_hash
        assert new != old and not password_needs_rehash(new)
    assert (
        record_for(state, pending).password_fingerprint
        == hashlib.sha256(new.encode()).hexdigest()
    )
    assert isinstance(finish(state, pending), IssuedSession)


@pytest.mark.parametrize(
    "operation,failure_point",
    [
        ("begin", "insert"),
        ("begin", "commit"),
        ("select", "insert"),
        ("select", "commit"),
    ],
)
def test_infrastructure_failure_rolls_back_without_publishing_token(
    login_state, operation, failure_point
):
    state = login_state
    pending = begin(state) if operation == "select" else None
    canary = secrets.token_urlsafe(32)

    def fail_insert(_conn, _cursor, statement, _params, _context, _many):
        table = "login_challenges" if operation == "begin" else "sessions"
        if statement.startswith(f"INSERT INTO {table}"):
            raise SQLAlchemyError(canary)

    def fail_commit(_connection):
        raise SQLAlchemyError(canary)

    hook, listener = (
        ("before_cursor_execute", fail_insert)
        if failure_point == "insert"
        else ("commit", fail_commit)
    )
    event.listen(state["runtime"], hook, listener)
    try:
        with pytest.raises(
            auth.AuthenticationUnavailable, match="^Authentication unavailable$"
        ) as caught:
            finish(state, pending) if pending else begin(state)
        assert caught.value.__suppress_context__ is True and canary not in str(
            caught.value
        )
    finally:
        event.remove(state["runtime"], hook, listener)
    assert count(state, SessionRecord) == 0
    if pending:
        assert record_for(state, pending).finished_at is None
        assert isinstance(finish(state, pending), IssuedSession)
    else:
        assert count(state, LoginChallenge) == count(state, LoginThrottle) == 0
        assert isinstance(begin(state), challenges.PendingLogin)


def test_revocation_is_atomic_with_caller_change_and_rollback(login_state):
    state = login_state
    pending = begin(state)
    with (
        pytest.raises(RuntimeError),
        Session(state["runtime"]) as session,
        session.begin(),
    ):
        challenges.revoke_user_challenges(session, state["users"][0])
        session.get(User, state["users"][0]).is_active = False
        session.flush()
        raise RuntimeError("rollback")
    assert record_for(state, pending).finished_at is None
    with Session(state["runtime"]) as session, session.begin():
        challenges.revoke_user_challenges(session, state["users"][0])
        session.get(User, state["users"][0]).is_active = False
    assert finish(state, pending) is None
    assert record_for(state, pending).finished_at is not None


def wait_for_pid(owner, pid):
    import time

    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        with owner.connect() as connection:
            waiting = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE pid = :pid AND NOT granted)"
                ),
                {"pid": pid},
            )
        if waiting:
            return
        time.sleep(0.01)
    pytest.fail("The second connection must actually wait for the user lock")


def test_two_simultaneous_selections_issue_exactly_one_session(
    login_state, monkeypatch
):
    state = login_state
    pending = begin(state)
    second_service = auth.Authenticator(state["runtime"])
    entered, release = Event(), Event()
    original = challenges.issue_session

    def pause_issue(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Synchronization timed out")
        return original(*args, **kwargs)

    monkeypatch.setattr(challenges, "issue_session", pause_issue)
    second_pid, looked_up = {}, Event()

    def capture_pid(connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("SELECT login_challenges.user_id"):
            second_pid["pid"] = connection.connection.driver_connection.info.backend_pid
            looked_up.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(finish, state, pending)
        listening = False
        try:
            assert entered.wait(3)
            event.listen(state["runtime"], "before_cursor_execute", capture_pid)
            listening = True
            second = pool.submit(finish, state, pending, service=second_service)
            assert looked_up.wait(3)
            wait_for_pid(state["owner"], second_pid["pid"])
        finally:
            release.set()
            if listening:
                event.remove(state["runtime"], "before_cursor_execute", capture_pid)
        assert isinstance(first.result(timeout=5), IssuedSession)
        assert second.result(timeout=5) is None
    assert count(state, SessionRecord) == 1


@pytest.mark.parametrize("change_kind", ["password", "expired", "revoked"])
def test_changes_while_selection_waits_are_rechecked(login_state, change_kind):
    state = login_state
    pending = begin(state)
    entered, waiter = Event(), {}

    def contend():
        def capture(connection, _cursor, statement, _params, _context, _many):
            if statement.startswith("SELECT login_challenges.user_id"):
                waiter["pid"] = connection.connection.driver_connection.info.backend_pid
                entered.set()

        event.listen(state["runtime"], "before_cursor_execute", capture)
        try:
            return finish(state, pending)
        finally:
            event.remove(state["runtime"], "before_cursor_execute", capture)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(state["runtime"]) as holder, holder.begin():
            user = holder.scalar(
                select(User).where(User.id == state["users"][0]).with_for_update()
            )
            if change_kind == "password":
                user.password_hash = hash_password(SecretStr(secrets.token_urlsafe(32)))
            elif change_kind == "revoked":
                challenges.revoke_user_challenges(holder, user.id)
            else:
                row = holder.scalar(
                    select(LoginChallenge).where(LoginChallenge.user_id == user.id)
                )
                row.created_at -= timedelta(minutes=6)
                row.expires_at -= timedelta(minutes=6)
            holder.flush()
            result = pool.submit(contend)
            assert entered.wait(3)
            wait_for_pid(state["owner"], waiter["pid"])
        assert result.result(timeout=5) is None
    assert count(state, SessionRecord) == 0


@pytest.mark.parametrize(
    "invalid", ["token", "binding", "fingerprint", "lifetime", "finished", "user"]
)
def test_challenge_constraints_are_enforced_by_postgresql(login_state, invalid):
    state = login_state
    pending = begin(state)
    record = record_for(state, pending)
    values = {
        "token": {"token_hash": "bad"},
        "binding": {"binding_hash": "A" * 64},
        "fingerprint": {"password_fingerprint": "g" * 64},
        "lifetime": {"expires_at": record.created_at + timedelta(minutes=6)},
        "finished": {"finished_at": record.created_at - timedelta(seconds=1)},
        "user": {"user_id": uuid4()},
    }[invalid]
    with pytest.raises(IntegrityError), state["owner"].begin() as connection:
        connection.execute(
            update(LoginChallenge)
            .where(LoginChallenge.id == record.id)
            .values(**values)
        )
