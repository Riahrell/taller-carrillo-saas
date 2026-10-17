"""Proof-bound session issuance, central gates and recent-auth windows in PostgreSQL."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
from threading import Event
from uuid import uuid4

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import (
    mfa_consumption,
    mfa_pending,
    mfa_sessions,
    mfa_verification,
    reauthentication,
    sessions,
)
from app.modules.access.authentication import Authenticator, Credentials
from app.modules.access.login_challenges import (
    MembershipChoice,
    _binding_digest,
    _issue_challenge,
)
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    LoginChallenge,
    LoginThrottle,
    MfaCredential,
    MfaPending,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password

pytestmark = pytest.mark.integration
PEER = ip_address("127.0.0.1")


@pytest.fixture
def state(mfa_state, monkeypatch):
    state = dict(
        mfa_state,
        binding=SecretStr(secrets.token_urlsafe(32)),
        password=SecretStr(secrets.token_urlsafe(32)),
    )
    for module, name in (
        (mfa_pending, "_database_now"),
        (mfa_consumption, "_database_now"),
        (mfa_verification, "_now"),
        (sessions, "_database_now"),
        (reauthentication, "_database_now"),
    ):
        monkeypatch.setattr(module, name, lambda session: state["now"])
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, state["user"])
        user.password_hash = hash_password(state["password"])
        state["email"] = user.email
        tenants = [Tenant(name=f"MFA session {i}") for i in range(2)]
        session.add_all(tenants)
        session.flush()
        members = [
            TenantMembership(tenant_id=tenants[0].id, user_id=user.id, role="admin"),
            TenantMembership(tenant_id=tenants[1].id, user_id=user.id, role="mechanic"),
            TenantMembership(
                tenant_id=tenants[1].id, user_id=state["other"], role="read_only"
            ),
        ]
        session.add_all(members)
        session.flush()
        state["tenants"], state["members"] = (
            [t.id for t in tenants],
            [m.id for m in members],
        )
        original = mfa_pending._issue_after_password(
            session,
            user_id=user.id,
            binding=state["binding"],
            purpose=mfa_pending.PendingPurpose.LOGIN_TOTP,
            expected_revision=0,
        )
        state["original"] = original
    code = SecretStr(
        pyotp.TOTP(state["seed"].base32().get_secret_value()).at(state["now"])
    )
    proof = mfa_verification.MfaVerifier(
        state["runtime"], keyring=state["keyring"]
    ).verify_login_totp(
        token=original.token,
        browser_binding=state["binding"],
        code=code,
        client_ip=PEER,
    )
    assert proof is not None
    state["proof"] = proof
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(SessionRecord).where(
                    SessionRecord.membership_id.in_(state["members"])
                )
            )
            connection.execute(
                delete(LoginChallenge).where(
                    LoginChallenge.user_id.in_([state["user"], state["other"]])
                )
            )
            connection.execute(
                delete(MfaPending).where(
                    MfaPending.user_id.in_([state["user"], state["other"]])
                )
            )
            connection.execute(
                delete(TenantMembership).where(
                    TenantMembership.id.in_(state["members"])
                )
            )
            connection.execute(delete(Tenant).where(Tenant.id.in_(state["tenants"])))
            connection.execute(delete(LoginThrottle))


def issuer(state):
    return mfa_sessions.MfaSessionIssuer(state["runtime"])


def issue(state, worker=None, **changes):
    values = dict(
        proof_token=state["proof"].token,
        browser_binding=state["binding"],
        membership_id=state["members"][0],
    )
    return (worker or issuer(state)).select_membership(**(values | changes))


def resolve(state, token, recent=False):
    with Session(state["runtime"]) as session, session.begin():
        if recent:
            return reauthentication.require_recent_administrator(
                session, token, expected_actor=state["members"][0]
            )
        return sessions.resolve_session(session, token)


def snapshot(state):
    with Session(state["runtime"]) as session:
        rows = list(
            session.scalars(
                select(SessionRecord).where(
                    SessionRecord.membership_id.in_(state["members"])
                )
            )
        )
        proof = session.scalar(
            select(MfaPending).where(
                MfaPending.token_hash
                == mfa_pending._hash(state["proof"].token, "mfa-pending-token-v1:")
            )
        )
        return proof.finished_at, [
            (
                r.id,
                r.token_hash,
                r.security_revision,
                r.mfa_pending_id,
                r.created_at,
                r.expires_at,
                r.last_seen_at,
            )
            for r in rows
        ]


def test_issue_and_resolve_persisted_proof_with_current_context_and_no_replay(state):
    issued = issue(state)
    assert issued.expires_at == state["now"] + timedelta(hours=8)
    assert issued.token != state["proof"].token
    assert issued.token.get_secret_value() not in repr(issued)
    context = resolve(state, issued.token)
    assert (
        context.user_id,
        context.tenant_id,
        context.membership_id,
        context.role,
    ) == (state["user"], state["tenants"][0], state["members"][0], "admin")
    assert resolve(state, issued.token, recent=True) == context
    data = snapshot(state)
    assert data[0] == data[1][0][4] == state["now"]
    assert data[1][0][1] != issued.token.get_secret_value()
    assert data[1][0][2] == 0 and data[1][0][3] is not None
    assert issue(state, membership_id=state["members"][1]) is None
    assert len(snapshot(state)[1]) == 1
    with pytest.raises(AccessDenied):
        resolve(state, state["proof"].token)


@pytest.mark.parametrize("delta,recent", [(299, True), (300, False), (360, False)])
def test_selection_and_activity_do_not_extend_recent_proof_or_shorten_session(
    state, delta, recent
):
    start = state["now"]
    state["now"] = start + timedelta(minutes=4)
    issued = issue(state)
    state["now"] = start + timedelta(seconds=delta)
    assert resolve(state, issued.token).membership_id == state["members"][0]
    if recent:
        assert resolve(state, issued.token, recent=True)
    else:
        with pytest.raises(reauthentication.RecentAuthenticationRequired):
            resolve(state, issued.token, recent=True)
    assert issued.expires_at == start + timedelta(minutes=4, hours=8)


@pytest.mark.parametrize("mode", ["factor", "required"])
def test_central_password_issuer_cannot_skip_second_factor(state, mode):
    user_id = state["user"] if mode == "factor" else state["other"]
    if mode == "required":
        with state["runtime"].begin() as connection:
            connection.execute(
                update(User)
                .where(User.id == user_id)
                .values(mfa_required=True, security_revision=1)
            )
    with (
        pytest.raises(AccessDenied),
        Session(state["runtime"]) as session,
        session.begin(),
    ):
        sessions.issue_session(session, user_id=user_id, tenant_id=state["tenants"][1])
    assert snapshot(state)[1] == []


def test_every_legacy_authentication_path_rejects_factor_account(state):
    auth = Authenticator(state["runtime"])
    credentials = Credentials(email=state["email"], password=state["password"])
    assert (
        auth.authenticate(
            credentials, client_ip=PEER, selected_tenant_id=state["tenants"][1]
        )
        is None
    )
    assert (
        auth.begin_login(credentials, client_ip=PEER, browser_binding=state["binding"])
        is None
    )
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, state["user"])
        challenge = _issue_challenge(
            session,
            user=user,
            binding_hash=_binding_digest(state["binding"]),
            choices=(MembershipChoice(state["members"][0], "Synthetic"),),
        )
    assert (
        auth.select_membership(
            challenge_token=challenge.token,
            browser_binding=state["binding"],
            membership_id=state["members"][0],
        )
        is None
    )
    assert snapshot(state)[1] == []


@pytest.mark.parametrize("kind", ["binding", "password_pending", "unknown"])
def test_wrong_browser_or_stage_cannot_issue_or_consume_valid_proof(state, kind):
    changes = {
        "binding": dict(browser_binding=SecretStr(secrets.token_urlsafe(32))),
        "password_pending": dict(proof_token=state["original"].token),
        "unknown": dict(proof_token=SecretStr(secrets.token_urlsafe(32))),
    }
    before = snapshot(state)
    assert issue(state, **changes[kind]) is None
    assert snapshot(state) == before
    assert issue(state) is not None


@pytest.mark.parametrize(
    "kind", ["foreign", "unknown", "inactive_member", "inactive_tenant"]
)
def test_bound_invalid_membership_consumes_one_attempt_without_session(state, kind):
    member = (
        state["members"][2]
        if kind == "foreign"
        else uuid4()
        if kind == "unknown"
        else state["members"][0]
    )
    if kind in ("inactive_member", "inactive_tenant"):
        model, identity = (
            (TenantMembership, state["members"][0])
            if kind == "inactive_member"
            else (Tenant, state["tenants"][0])
        )
        with state["runtime"].begin() as connection:
            connection.execute(
                update(model).where(model.id == identity).values(is_active=False)
            )
    assert issue(state, membership_id=member) is None
    assert snapshot(state) == (state["now"], [])
    assert issue(state, membership_id=state["members"][1]) is None


@pytest.mark.parametrize(
    "change",
    ["password", "revision", "factor", "inactive_user", "membership", "tenant"],
)
def test_resolution_rechecks_security_and_current_membership(state, change):
    issued = issue(state)
    if change in ("password", "revision", "inactive_user"):
        model, identity = User, state["user"]
        values = {
            "password": dict(password_hash="changed-synthetic"),
            "revision": dict(security_revision=1),
            "inactive_user": dict(is_active=False),
        }[change]
    elif change == "factor":
        model, identity, values = (
            MfaCredential,
            state["credential"],
            dict(revoked_at=state["now"]),
        )
    else:
        model, identity, values = (
            (TenantMembership, state["members"][0], dict(is_active=False))
            if change == "membership"
            else (Tenant, state["tenants"][0], dict(is_active=False))
        )
    with state["runtime"].begin() as connection:
        connection.execute(update(model).where(model.id == identity).values(**values))
    with pytest.raises(AccessDenied):
        resolve(state, issued.token)


def test_historical_cookie_without_revision_is_rejected_without_backfill(state):
    issued = issue(state)
    with state["runtime"].begin() as connection:
        # Remove the proof as an old session had neither new field.
        connection.execute(
            update(SessionRecord)
            .where(SessionRecord.membership_id == state["members"][0])
            .values(security_revision=None, mfa_pending_id=None)
        )
    with pytest.raises(AccessDenied):
        resolve(state, issued.token)


def test_proof_cannot_be_moved_to_another_membership_owner(state):
    issued = issue(state)
    with state["runtime"].begin() as connection:
        connection.execute(
            update(SessionRecord)
            .where(SessionRecord.membership_id == state["members"][0])
            .values(membership_id=state["members"][2], tenant_id=state["tenants"][1])
        )
    with pytest.raises(AccessDenied):
        resolve(state, issued.token)


def test_exact_expiry_rejects_selection_and_no_session_is_inserted(state):
    state["now"] = state["proof"].expires_at
    assert issue(state) is None
    assert snapshot(state) == (None, [])


def test_expiry_after_context_read_cannot_issue_with_an_old_timestamp(
    state, monkeypatch
):
    original = mfa_sessions.resolve_membership

    def expire(*args, **kwargs):
        result = original(*args, **kwargs)
        state["now"] = state["proof"].expires_at
        return result

    monkeypatch.setattr(mfa_sessions, "resolve_membership", expire)
    assert issue(state) is None
    assert snapshot(state) == (None, [])


@pytest.mark.parametrize(
    "target,operation", [("sessions", "INSERT"), ("mfa_pendings", "UPDATE")]
)
def test_actual_write_failure_rolls_back_proof_and_session(state, target, operation):
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(text(f"REVOKE {operation} ON {target} FROM carrillo_app"))
    try:
        with pytest.raises(
            mfa_sessions.MfaSessionUnavailable, match="^MFA session unavailable$"
        ):
            issue(state)
        assert snapshot(state) == before
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text(f"GRANT {operation} ON {target} TO carrillo_app"))
    assert issue(state) is not None


def test_commit_failure_publishes_no_session_and_pending_remains_usable(state):
    w = issuer(state)

    def fail(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(w._factory, "before_commit", fail, once=True)
    before = snapshot(state)
    with pytest.raises(mfa_sessions.MfaSessionUnavailable):
        issue(state, worker=w)
    assert snapshot(state) == before
    assert issue(state) is not None


def test_password_only_reauthentication_cannot_renew_mfa_session(state):
    issued = issue(state)
    before = snapshot(state)
    state["now"] += timedelta(minutes=5)
    auth = Authenticator(state["runtime"])
    assert (
        auth.reauthenticate(
            issued.token,
            data=reauthentication.PasswordConfirmation(password=state["password"]),
            expected_actor=state["members"][0],
            client_ip=PEER,
        )
        is None
    )
    assert snapshot(state) == before
    with pytest.raises(reauthentication.RecentAuthenticationRequired):
        resolve(state, issued.token, recent=True)


def test_database_unique_proof_and_downgrade_protect_evidence(state):
    issue(state)
    with pytest.raises(IntegrityError) as error, state["runtime"].begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, tenant_id, membership_id, token_hash, "
                "created_at, last_seen_at, expires_at, security_revision, "
                "mfa_pending_id) "
                "SELECT gen_random_uuid(), tenant_id, membership_id, :hash, "
                "created_at, "
                "last_seen_at, expires_at, security_revision, mfa_pending_id "
                "FROM sessions WHERE membership_id = :member"
            ),
            dict(hash="f" * 64, member=state["members"][0]),
        )
    assert error.value.orig.diag.constraint_name == "uq_sessions_mfa_pending_id"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0014_mfa_login_proof")
    assert len(snapshot(state)[1]) == 1


def test_concurrent_selections_cannot_reuse_a_proof_in_two_tenants(state, monkeypatch):
    entered, release, started = Event(), Event(), Event()
    pids = []
    original_create, original_lock = sessions._create_session, mfa_pending._lock_user

    def pause(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Synthetic contention")
        return original_create(*args, **kwargs)

    def observe(session, user_id):
        if entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
        return original_lock(session, user_id)

    monkeypatch.setattr(sessions, "_create_session", pause)
    monkeypatch.setattr(mfa_pending, "_lock_user", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(issue, state)
        try:
            assert entered.wait(5)
            second = pool.submit(issue, state, membership_id=state["members"][1])
            assert started.wait(5)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with state["owner"].connect() as connection:
                    if connection.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pids[0]},
                    ):
                        break
                time.sleep(0.01)
            else:
                pytest.fail("Second worker did not block")
        finally:
            release.set()
        assert first.result(timeout=5) is not None
        assert second.result(timeout=5) is None
    assert len(snapshot(state)[1]) == 1


def test_downgrade_cannot_remove_mfa_gate_before_any_session_exists(state):
    assert snapshot(state)[1] == []
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0014_mfa_login_proof")
    assert issue(state) is not None


@pytest.mark.parametrize("required", [True, False])
def test_downgrade_cannot_forget_user_security_without_factors_or_sessions(
    db_session, databases, required
):
    runtime, owner, _ = databases
    with Session(runtime) as session, session.begin():
        user = User(
            email=f"rollback-{uuid4().hex}@example.com",
            password_hash="synthetic",
            security_revision=1,
            mfa_required=required,
        )
        session.add(user)
        session.flush()
        user_id = user.id
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        with (
            pytest.raises(DBAPIError, match="Downgrade refused"),
            owner.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0014_mfa_login_proof")
    finally:
        with owner.begin() as connection:
            connection.execute(delete(User).where(User.id == user_id))
