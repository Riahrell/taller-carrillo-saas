"""Real credentials, committed rate counters and session issuance in isolated DB."""

import hashlib
import secrets
from ipaddress import ip_address

import pytest
from argon2 import PasswordHasher
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import authentication as auth
from app.modules.access.models import (
    LoginThrottle,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password, password_needs_rehash
from app.modules.access.sessions import resolve_session

pytestmark = pytest.mark.integration


@pytest.fixture
def auth_state(db_session, databases):
    runtime, owner, _ = databases
    password = SecretStr(secrets.token_urlsafe(32))
    with Session(runtime) as session, session.begin():
        tenant, other = Tenant(name="Auth A"), Tenant(name="Auth B")
        user = User(email="auth@example.com", password_hash=hash_password(password))
        session.add_all([tenant, other, user])
        session.flush()
        membership = TenantMembership(
            tenant_id=tenant.id, user_id=user.id, role="admin"
        )
        session.add(membership)
        session.flush()
        ids = tenant.id, other.id, user.id, membership.id
    state = {
        "runtime": runtime,
        "owner": owner,
        "tenant": ids[0],
        "other": ids[1],
        "user": ids[2],
        "membership": ids[3],
        "password": password,
        "authenticator": auth.Authenticator(runtime),
    }
    try:
        yield state
    finally:
        with owner.begin() as connection:
            from identity_cleanup import cleanup_identity

            cleanup_identity(connection, [ids[2]])
            connection.execute(
                delete(SessionRecord).where(SessionRecord.membership_id == ids[3])
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id == ids[3])
            )
            connection.execute(delete(User).where(User.id == ids[2]))
            connection.execute(delete(Tenant).where(Tenant.id.in_(ids[:2])))
            # Test fixture exclusively owns throttle rows in guarded ephemeral DB.
            connection.execute(delete(LoginThrottle))


def attempt(
    state,
    *,
    password=None,
    email="auth@example.com",
    peer="127.0.0.1",
    tenant=None,
    authenticator=None,
):
    return (authenticator or state["authenticator"]).authenticate(
        auth.Credentials(
            email=email, password=state["password"] if password is None else password
        ),
        client_ip=ip_address(peer),
        selected_tenant_id=state["tenant"] if tenant is None else tenant,
    )


def read_buckets(state):
    with Session(state["runtime"]) as session:
        return {
            (row.scope, row.key_hash): (row.failures, row.window_started_at)
            for row in session.scalars(select(LoginThrottle))
        }


def failures(state, scope, value):
    key = hashlib.sha256(value.encode()).hexdigest()
    return read_buckets(state)[scope, key][0]


def session_count(state):
    with Session(state["runtime"]) as session:
        return session.scalar(select(func.count()).select_from(SessionRecord))


def test_success_commits_session_and_uses_server_membership(auth_state):
    issued = attempt(auth_state, email=" AUTH@EXAMPLE.COM ")
    assert issued is not None and session_count(auth_state) == 1
    with Session(auth_state["runtime"]) as session, session.begin():
        context = resolve_session(session, issued.token)
        assert (context.tenant_id, context.user_id, context.role) == (
            auth_state["tenant"],
            auth_state["user"],
            "admin",
        )
    assert failures(auth_state, "account", "auth@example.com") == 0
    assert failures(auth_state, "ip", "127.0.0.1") == 0


@pytest.mark.parametrize(
    "reason",
    [
        "password",
        "unknown",
        "user",
        "tenant",
        "membership",
        "foreign_tenant",
        "malformed_hash",
    ],
)
def test_all_rejections_are_generic_and_commit_failures(
    auth_state, reason, monkeypatch
):
    state = auth_state
    kwargs = {}
    if reason == "password":
        kwargs["password"] = SecretStr(secrets.token_urlsafe(24))
    elif reason == "unknown":
        kwargs["email"] = "unknown@example.com"
    elif reason == "foreign_tenant":
        kwargs["tenant"] = state["other"]
    else:
        model, record_id = {
            "user": (User, state["user"]),
            "tenant": (Tenant, state["tenant"]),
            "membership": (TenantMembership, state["membership"]),
            "malformed_hash": (User, state["user"]),
        }[reason]
        values = (
            {"password_hash": "synthetic-malformed"}
            if reason == "malformed_hash"
            else {"is_active": False}
        )
        with state["owner"].begin() as connection:
            connection.execute(
                update(model).where(model.id == record_id).values(**values)
            )
    verified = []
    original = auth.verify_password

    def spy(password, encoded):
        verified.append(encoded)
        return original(password, encoded)

    monkeypatch.setattr(auth, "verify_password", spy)
    assert attempt(state, **kwargs) is None
    assert len(verified) == 1
    if reason in ("unknown", "malformed_hash"):
        assert verified[0] == state["authenticator"]._dummy_hash
    assert session_count(state) == 0
    assert failures(state, "account", kwargs.get("email", "auth@example.com")) == 1
    assert failures(state, "ip", "127.0.0.1") == 1


def test_account_limit_is_shared_across_ips_and_authenticator_instances(
    auth_state, monkeypatch
):
    state = auth_state
    second = auth.Authenticator(state["runtime"])
    bad = SecretStr(secrets.token_urlsafe(24))
    for n in range(5):
        assert (
            attempt(
                state,
                password=bad,
                peer=f"127.0.0.{n + 1}",
                authenticator=second if n % 2 else None,
            )
            is None
        )
    before = read_buckets(state)
    monkeypatch.setattr(
        auth,
        "verify_password",
        lambda *_: pytest.fail("Blocked attempt must not run Argon2"),
    )
    assert attempt(state, peer="::ffff:127.0.0.1", authenticator=second) is None
    assert read_buckets(state) == before
    assert failures(state, "account", "auth@example.com") == 5


def test_ip_limit_spans_accounts_and_does_not_allocate_more_account_rows(
    auth_state, monkeypatch
):
    state = auth_state
    for n in range(30):
        assert attempt(state, email=f"unknown{n}@example.com") is None
    before = read_buckets(state)
    monkeypatch.setattr(
        auth, "verify_password", lambda *_: pytest.fail("Blocked IP must not hash")
    )
    assert attempt(state, peer="::ffff:127.0.0.1") is None
    assert read_buckets(state) == before
    assert failures(state, "ip", "127.0.0.1") == 30
    assert len(before) == 31


def test_success_resets_account_but_preserves_shared_ip_failures(auth_state):
    state = auth_state
    assert attempt(state, password=SecretStr(secrets.token_urlsafe(24))) is None
    assert attempt(state) is not None
    assert failures(state, "account", "auth@example.com") == 0
    assert failures(state, "ip", "127.0.0.1") == 1
    assert (
        attempt(
            state,
            password=SecretStr(secrets.token_urlsafe(24)),
            email=" AUTH@EXAMPLE.COM ",
        )
        is None
    )
    assert failures(state, "account", "auth@example.com") == 1


def test_expired_windows_allow_login_without_permanent_lockout(auth_state):
    state = auth_state
    with state["owner"].begin() as connection:
        for scope, value, limit in (
            ("account", "auth@example.com", 5),
            ("ip", "127.0.0.1", 30),
        ):
            connection.execute(
                auth.insert(LoginThrottle).values(
                    scope=scope,
                    key_hash=hashlib.sha256(value.encode()).hexdigest(),
                    failures=limit,
                    window_started_at=func.clock_timestamp()
                    - text("interval '15 minutes'"),
                )
            )
    assert attempt(state) is not None
    assert failures(state, "account", "auth@example.com") == 0
    assert failures(state, "ip", "127.0.0.1") == 0


def test_old_hash_is_rehashed_atomically_on_success(auth_state):
    state = auth_state
    old = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(
        state["password"].get_secret_value()
    )
    with state["owner"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["user"]).values(password_hash=old)
        )
    assert attempt(state) is not None
    with Session(state["runtime"]) as session:
        new = session.get(User, state["user"]).password_hash
        assert new != old and not password_needs_rehash(new)


@pytest.mark.parametrize("failure_point", ["insert", "commit"])
def test_infrastructure_failure_never_returns_token_or_partial_changes(
    auth_state, failure_point
):
    state = auth_state
    canary = secrets.token_urlsafe(24)

    def fail_insert(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO sessions"):
            raise SQLAlchemyError(canary)

    def fail_commit(_conn):
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
            attempt(state)
        assert caught.value.__suppress_context__ is True
        assert canary not in str(caught.value)
    finally:
        event.remove(state["runtime"], hook, listener)
    assert session_count(state) == 0 and read_buckets(state) == {}
    assert attempt(state) is not None


@pytest.mark.parametrize(
    "scope,failures_value", [("account", -1), ("account", 6), ("ip", 31)]
)
def test_counter_bounds_are_enforced_by_postgresql(auth_state, scope, failures_value):
    from sqlalchemy.exc import IntegrityError

    state = auth_state
    with pytest.raises(IntegrityError), state["owner"].begin() as connection:
        connection.execute(
            auth.insert(LoginThrottle).values(
                scope=scope,
                key_hash="a" * 64,
                failures=failures_value,
                window_started_at=func.clock_timestamp(),
            )
        )


def wait_for_contender(owner):
    import time

    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        with owner.connect() as connection:
            waiting = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted)")
            )
        if waiting:
            return
        time.sleep(0.01)
    pytest.fail("Independent connection must actually wait in PostgreSQL")


def test_concurrent_last_account_attempt_is_counted_once(auth_state, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    state = auth_state
    bad = SecretStr(secrets.token_urlsafe(24))
    for _ in range(4):
        assert attempt(state, password=bad) is None
    second_worker = auth.Authenticator(state["runtime"])
    entered, release = Event(), Event()
    verified = []
    original = auth.verify_password

    def pause_verification(password, encoded):
        verified.append(True)
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Synchronization timed out")
        return original(password, encoded)

    monkeypatch.setattr(auth, "verify_password", pause_verification)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, state, password=bad, peer="127.0.0.2")
        try:
            assert entered.wait(3)
            second = pool.submit(
                attempt,
                state,
                password=bad,
                peer="127.0.0.3",
                authenticator=second_worker,
            )
            wait_for_contender(state["owner"])
        finally:
            release.set()
        assert first.result(timeout=5) is None
        assert second.result(timeout=5) is None
    assert len(verified) == 1
    assert failures(state, "account", "auth@example.com") == 5
    assert session_count(state) == 0


def test_password_change_cannot_race_verification_of_stale_hash(auth_state):
    from concurrent.futures import ThreadPoolExecutor

    state = auth_state
    changed = SecretStr(secrets.token_urlsafe(32))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(state["runtime"]) as holder, holder.begin():
            user = holder.scalar(
                select(User).where(User.id == state["user"]).with_for_update()
            )
            user.password_hash = hash_password(changed)
            holder.flush()
            result = pool.submit(attempt, state)
            wait_for_contender(state["owner"])
        assert result.result(timeout=5) is None
    assert session_count(state) == 0
    assert attempt(state, password=changed) is not None
