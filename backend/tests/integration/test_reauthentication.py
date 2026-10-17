"""Internal confirmation, rotation, expiry, limits and rollback in PostgreSQL."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from argon2.exceptions import HashingError
from pydantic import SecretStr
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import authentication as auth
from app.modules.access import reauthentication as recent
from app.modules.access import sessions
from app.modules.access.models import (
    LoginThrottle,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password

pytestmark = pytest.mark.integration


@pytest.fixture
def state(http_identity):
    state = dict(http_identity)
    state["auth"] = auth.Authenticator(state["runtime"])
    with Session(state["runtime"]) as session, session.begin():
        issued = sessions.issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        )
        state["token"], state["expiry"] = issued.token, issued.expires_at
    return state


def confirm(state, **changes):
    arguments = dict(
        data=recent.PasswordConfirmation(password=state["password"]),
        expected_actor=state["membership"],
        client_ip=ip_address("127.0.0.71"),
    )
    arguments.update(changes)
    token = arguments.pop("token", state["token"])
    return state["auth"].reauthenticate(token, **arguments)


def snapshot(state):
    with Session(state["runtime"]) as session:
        row = session.scalar(
            select(SessionRecord).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
        return (
            row.id,
            row.token_hash,
            row.created_at,
            row.last_seen_at,
            row.expires_at,
            row.revoked_at,
            row.reauthenticated_at,
            row.reauthentication_fingerprint,
        )


def guard(state, token):
    with Session(state["runtime"]) as session, session.begin():
        return recent.require_recent_administrator(
            session, token, expected_actor=state["membership"]
        )


def failures(state, scope):
    key = (
        auth._digest(state["email"])
        if scope == "account"
        else auth._ip_digest(ip_address("127.0.0.71"))
    )
    with Session(state["runtime"]) as session:
        row = session.get(LoginThrottle, (scope, key))
        return row.failures if row else 0


def test_rotation_is_committed_hidden_and_does_not_extend_absolute_lifetime(state):
    before = snapshot(state)
    with pytest.raises(recent.RecentAuthenticationRequired):
        guard(state, state["token"])
    assert snapshot(state) == before
    issued = confirm(state)
    assert issued is not None and issued.token != state["token"]
    assert issued.token.get_secret_value() not in repr(issued)
    after = snapshot(state)
    assert after[0] == before[0] and after[2] == before[2]
    assert after[4] == before[4] == issued.expires_at
    assert after[1] == sessions._token_hash(issued.token)
    assert after[1] != issued.token.get_secret_value()
    assert after[3] == after[6] and after[6] >= before[3]
    assert len(after[7]) == 64
    actor = guard(state, issued.token)
    assert (actor.user_id, actor.tenant_id, actor.membership_id, actor.role) == (
        state["user"],
        state["tenant"],
        state["membership"],
        "admin",
    )
    with pytest.raises(sessions.AccessDenied):
        guard(state, state["token"])
    assert confirm(state) is None  # Replay cannot obtain the rotated token.


def test_wrong_password_commits_limits_without_touching_or_rotating_session(state):
    before = snapshot(state)
    assert confirm(state, data=recent.PasswordConfirmation(password="wrong")) is None
    assert snapshot(state) == before
    assert failures(state, "account") == failures(state, "ip") == 1


@pytest.mark.parametrize(
    "target",
    ["user", "tenant", "membership", "role", "revoked", "unknown", "foreign_actor"],
)
def test_invalid_or_unauthorized_context_never_confirms_or_touches(state, target):
    with Session(state["runtime"]) as session, session.begin():
        if target in {"user", "tenant", "membership"}:
            model, identifier = {
                "user": (User, state["user"]),
                "tenant": (Tenant, state["tenant"]),
                "membership": (TenantMembership, state["membership"]),
            }[target]
            session.execute(
                update(model).where(model.id == identifier).values(is_active=False)
            )
        elif target == "role":
            session.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["membership"])
                .values(role="mechanic")
            )
        elif target == "revoked":
            sessions.revoke_session(session, state["token"])
    before = snapshot(state)
    changes = {}
    if target == "unknown":
        changes["token"] = SecretStr(secrets.token_urlsafe(32))
    if target == "foreign_actor":
        changes["expected_actor"] = state["foreign_membership"]
    assert confirm(state, **changes) is None
    assert snapshot(state) == before


@pytest.mark.parametrize("deadline", ["idle", "absolute", "backwards"])
def test_expired_session_cannot_be_resurrected(state, monkeypatch, deadline):
    before = snapshot(state)
    now = {
        "idle": before[3] + sessions.IDLE_TIMEOUT,
        "absolute": before[4],
        "backwards": before[2] - timedelta(microseconds=1),
    }[deadline]
    monkeypatch.setattr(sessions, "_database_now", lambda _: now)
    assert confirm(state) is None
    assert snapshot(state) == before


@pytest.mark.parametrize("deadline", ["idle", "absolute", "backwards"])
def test_expiry_during_password_verification_rolls_back_touch(
    state, monkeypatch, deadline
):
    before = snapshot(state)
    now = {
        "idle": before[3] + sessions.IDLE_TIMEOUT,
        "absolute": before[4],
        "backwards": before[3] - timedelta(seconds=1),
    }[deadline]
    monkeypatch.setattr(auth, "_database_now", lambda _: now)
    assert confirm(state) is None
    assert snapshot(state) == before


@pytest.mark.parametrize(
    "offset,allowed",
    [
        (timedelta(minutes=5) - timedelta(microseconds=1), True),
        (timedelta(minutes=5), False),
        (timedelta(minutes=6), False),
        (-timedelta(microseconds=1), False),
    ],
)
def test_freshness_uses_server_clock_and_exact_five_minute_deadline(
    state, monkeypatch, offset, allowed
):
    issued = confirm(state)
    confirmed = snapshot(state)[6]
    monkeypatch.setattr(recent, "_database_now", lambda _: confirmed + offset)
    if allowed:
        guard(state, issued.token)
    else:
        with pytest.raises(recent.RecentAuthenticationRequired):
            guard(state, issued.token)


def test_ordinary_activity_never_renews_confirmation(state, monkeypatch):
    issued = confirm(state)
    confirmed = snapshot(state)[6]
    for minutes in (1, 3, 4):
        now = confirmed + timedelta(minutes=minutes)
        monkeypatch.setattr(sessions, "_database_now", lambda _, now=now: now)
        guard(state, issued.token)
    assert snapshot(state)[6] == confirmed
    monkeypatch.setattr(
        recent, "_database_now", lambda _: confirmed + timedelta(minutes=5)
    )
    with pytest.raises(recent.RecentAuthenticationRequired):
        guard(state, issued.token)


@pytest.mark.parametrize("change", ["password", "role", "revoked", "other_actor"])
def test_guard_rechecks_password_permissions_and_revocation(state, change):
    issued = confirm(state)
    with Session(state["runtime"]) as session, session.begin():
        if change == "password":
            session.execute(
                update(User)
                .where(User.id == state["user"])
                .values(
                    password_hash=hash_password(SecretStr(secrets.token_urlsafe(32)))
                )
            )
        elif change == "role":
            session.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["membership"])
                .values(role="read_only")
            )
        elif change == "revoked":
            sessions.revoke_user_sessions(session, state["user"])
    with (
        Session(state["runtime"]) as session,
        pytest.raises((sessions.AccessDenied, recent.RecentAuthenticationRequired)),
        session.begin(),
    ):
        recent.require_recent_administrator(
            session,
            issued.token,
            expected_actor=state["foreign_membership"]
            if change == "other_actor"
            else state["membership"],
        )


def test_confirmation_is_not_shared_with_another_session(state):
    with Session(state["runtime"]) as session, session.begin():
        other = sessions.issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        )
    issued = confirm(state)
    guard(state, issued.token)
    with pytest.raises(recent.RecentAuthenticationRequired):
        guard(state, other.token)


def test_login_and_confirmation_share_persistent_account_limit(state, monkeypatch):
    bad = recent.PasswordConfirmation(password="wrong")
    for _ in range(5):
        assert confirm(state, data=bad) is None
    before = snapshot(state)
    monkeypatch.setattr(
        auth, "verify_password", lambda *_: pytest.fail("Blocked hash must not run")
    )
    assert confirm(state) is None
    credentials = auth.Credentials(email=state["email"], password=state["password"])
    assert (
        state["auth"].authenticate(
            credentials,
            client_ip=ip_address("127.0.0.72"),
            selected_tenant_id=state["tenant"],
        )
        is None
    )
    assert failures(state, "account") == 5
    assert snapshot(state) == before


def test_shared_ip_limit_prevents_hashing_and_counter_overflow(state, monkeypatch):
    with Session(state["runtime"]) as session, session.begin():
        bucket = auth._lock_bucket(
            session, "ip", auth._ip_digest(ip_address("127.0.0.71"))
        )
        bucket.failures = 30
    monkeypatch.setattr(
        auth, "verify_password", lambda *_: pytest.fail("Blocked hash must not run")
    )
    assert confirm(state) is None
    assert failures(state, "ip") == 30


def test_success_clears_account_failures_but_not_shared_ip_history(state):
    assert confirm(state, data=recent.PasswordConfirmation(password="wrong")) is None
    assert confirm(state) is not None
    assert failures(state, "account") == 0
    assert failures(state, "ip") == 1


def test_malformed_stored_hash_uses_dummy_verification_and_cannot_confirm(
    state, monkeypatch
):
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(User).where(User.id == state["user"]).values(password_hash="bad")
        )
    before = snapshot(state)
    original, calls = auth.verify_password, []

    def observe(password, encoded):
        calls.append(encoded == state["auth"]._dummy_hash)
        return original(password, encoded)

    monkeypatch.setattr(auth, "verify_password", observe)
    assert confirm(state) is None
    assert calls == [True] and snapshot(state) == before


def test_shared_throttle_window_reopens_at_exact_boundary(state, monkeypatch):
    with Session(state["runtime"]) as session, session.begin():
        start = auth._now(session)
        for scope, key, limit in (
            ("ip", auth._ip_digest(ip_address("127.0.0.71")), 30),
            ("account", auth._digest(state["email"]), 5),
        ):
            bucket = auth._lock_bucket(session, scope, key)
            bucket.failures, bucket.window_started_at = limit, start
    monkeypatch.setattr(auth, "_now", lambda _: start + auth.WINDOW)
    assert confirm(state) is not None
    assert failures(state, "account") == failures(state, "ip") == 0


def test_confirmation_cannot_cross_companies_even_for_the_same_user(state):
    with Session(state["runtime"]) as session, session.begin():
        member = TenantMembership(
            user_id=state["user"], tenant_id=state["other"], role="admin"
        )
        session.add(member)
        session.flush()
        other_actor = member.id
        other = sessions.issue_session(
            session, user_id=state["user"], tenant_id=state["other"]
        )
    assert confirm(state, token=other.token) is None
    issued = confirm(state, token=other.token, expected_actor=other_actor)
    with Session(state["runtime"]) as session, session.begin():
        actor = recent.require_recent_administrator(
            session, issued.token, expected_actor=other_actor
        )
        assert actor.tenant_id == state["other"]
    with pytest.raises(recent.RecentAuthenticationRequired):
        guard(state, state["token"])


def test_guard_rejection_rolls_back_the_whole_sensitive_transaction(state):
    with (
        Session(state["runtime"]) as session,
        pytest.raises(recent.RecentAuthenticationRequired),
        session.begin(),
    ):
        session.execute(
            update(Tenant).where(Tenant.id == state["tenant"]).values(name="changed")
        )
        recent.require_recent_administrator(
            session, state["token"], expected_actor=state["membership"]
        )
    with Session(state["runtime"]) as session:
        assert session.get(Tenant, state["tenant"]).name == "HTTP company"


@pytest.mark.parametrize("failure", ["hash", "commit"])
def test_infrastructure_failure_rolls_back_rotation_proof_and_counters(
    state, monkeypatch, failure
):
    before = snapshot(state)
    if failure == "hash":

        def fail(*_):
            raise HashingError("private failure")

        monkeypatch.setattr(auth, "verify_password", fail)
    else:

        def fail(session):
            raise SQLAlchemyError("private failure")

        event.listen(state["runtime"], "commit", fail)
    try:
        with pytest.raises(
            auth.AuthenticationUnavailable, match="^Authentication unavailable$"
        ):
            confirm(state)
    finally:
        if failure == "commit":
            event.remove(state["runtime"], "commit", fail)
    assert snapshot(state) == before
    assert failures(state, "account") == failures(state, "ip") == 0


def wait_for_lock(owner):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with owner.connect() as connection:
            if connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted)")
            ):
                return
        time.sleep(0.01)
    pytest.fail("Contender did not actually wait for a database lock")


def test_two_workers_rotating_same_token_cannot_both_succeed(state, monkeypatch):
    second_worker = auth.Authenticator(state["runtime"])
    entered, release = Event(), Event()
    original = auth.verify_password
    calls = []

    def pause(password, encoded):
        calls.append(True)
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Synchronization timed out")
        return original(password, encoded)

    monkeypatch.setattr(auth, "verify_password", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm, state)
        try:
            assert entered.wait(3)
            second = pool.submit(
                confirm,
                dict(state, auth=second_worker),
                client_ip=ip_address("127.0.0.72"),
            )
            wait_for_lock(state["owner"])
        finally:
            release.set()
        result = first.result(timeout=5)
        assert result is not None
        assert second.result(timeout=5) is None
    assert len(calls) == 1
    guard(state, result.token)


@pytest.mark.parametrize("first", ["revoke", "confirm"])
def test_user_revocation_and_confirmation_serialize_without_resurrection(
    state, monkeypatch, first
):
    if first == "revoke":
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(state["runtime"]) as holder, holder.begin():
                sessions.revoke_user_sessions(holder, state["user"])
                pending = pool.submit(confirm, state)
                wait_for_lock(state["owner"])
            assert pending.result(timeout=5) is None
        return
    entered, release = Event(), Event()
    original = auth.verify_password

    def pause(password, encoded):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Synchronization timed out")
        return original(password, encoded)

    def revoke():
        with Session(state["runtime"]) as session, session.begin():
            sessions.revoke_user_sessions(session, state["user"])

    monkeypatch.setattr(auth, "verify_password", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(confirm, state)
        try:
            assert entered.wait(3)
            revocation = pool.submit(revoke)
            wait_for_lock(state["owner"])
        finally:
            release.set()
        issued = pending.result(timeout=5)
        revocation.result(timeout=5)
    with pytest.raises(sessions.AccessDenied):
        guard(state, issued.token)


def test_migration_revokes_confirmed_tokens_on_downgrade_and_does_not_backfill(state):
    with Session(state["runtime"]) as session, session.begin():
        ordinary = sessions.issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        )
    issued = confirm(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0007_membership_creation_audit")
    # Exercise the historical 0008 rollback using synthetic pre-0015 sessions.
    # This is isolated migration test setup, never an operational downgrade recipe.
    with state["owner"].begin() as connection:
        connection.execute(
            text(
                "UPDATE sessions SET security_revision = NULL WHERE "
                "mfa_pending_id IS NULL AND membership_id IN "
                "(SELECT id FROM tenant_memberships WHERE user_id = :user)"
            ),
            {"user": state["user"]},
        )
    with state["owner"].begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0007_membership_creation_audit")
        row = connection.execute(
            text(
                "SELECT revoked_at, expires_at FROM sessions WHERE token_hash = :digest"
            ),
            {"digest": sessions._token_hash(issued.token)},
        ).one()
        assert row.revoked_at is not None and row.expires_at == state["expiry"]
        command.upgrade(config, "head")
        command.check(config)
    with Session(state["runtime"]) as session, session.begin():
        assert all(
            row.reauthenticated_at is None and row.reauthentication_fingerprint is None
            for row in session.scalars(select(SessionRecord))
        )
        # Neither historical cookie receives a security revision on upgrade.
        with pytest.raises(sessions.AccessDenied):
            sessions.resolve_session(session, ordinary.token)
    with pytest.raises(sessions.AccessDenied):
        guard(state, issued.token)


@pytest.mark.parametrize(
    "changes",
    [
        dict(reauthenticated_at=func.clock_timestamp()),
        dict(reauthentication_fingerprint="a" * 64),
        dict(
            reauthenticated_at=func.clock_timestamp(),
            reauthentication_fingerprint="bad",
        ),
        dict(
            reauthenticated_at=func.clock_timestamp() + text("interval '9 hours'"),
            reauthentication_fingerprint="a" * 64,
        ),
    ],
)
def test_database_rejects_partial_invalid_or_out_of_lifetime_confirmation(
    state, changes
):
    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.execute(
            update(SessionRecord)
            .where(SessionRecord.membership_id == state["membership"])
            .values(**changes)
        )
