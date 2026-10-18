"""Password -> restricted MFA outcome, using real hashes and guarded PostgreSQL."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from threading import Event

import pyotp
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import authentication as auth
from app.modules.access import (
    login_challenges,
    mfa_consumption,
    mfa_pending,
    mfa_verification,
    security_state,
    sessions,
)
from app.modules.access.mfa_sessions import MfaSessionIssuer
from app.modules.access.models import (
    LoginChallenge,
    LoginThrottle,
    MfaCredential,
    MfaPending,
    MfaThrottle,
    SecurityEvent,
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
        password=SecretStr(secrets.token_urlsafe(32)),
        binding=SecretStr(secrets.token_urlsafe(32)),
    )
    for module, name in (
        (auth, "_now"),
        (auth, "_database_now"),
        (mfa_pending, "_database_now"),
        (security_state, "_database_now"),
        (sessions, "_database_now"),
        (login_challenges, "_database_now"),
        (mfa_consumption, "_database_now"),
        (mfa_verification, "_now"),
    ):
        monkeypatch.setattr(module, name, lambda session: state["now"])
    with Session(state["runtime"]) as session, session.begin():
        for identity in (state["user"], state["other"]):
            session.get(User, identity).password_hash = hash_password(state["password"])
        state["emails"] = {
            i: session.get(User, i).email for i in (state["user"], state["other"])
        }
        tenants = [Tenant(name=f"MFA start {i}") for i in range(2)]
        session.add_all(tenants)
        session.flush()
        members = [
            TenantMembership(user_id=identity, tenant_id=t.id, role="read_only")
            for identity in (state["user"], state["other"])
            for t in tenants
        ]
        session.add_all(members)
        session.flush()
        state["tenants"], state["members"] = (
            [t.id for t in tenants],
            [m.id for m in members],
        )
        for tenant in tenants:
            sessions.issue_session(session, user_id=state["other"], tenant_id=tenant.id)
    state["auth"] = auth.Authenticator(state["runtime"])
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


def begin(state, *, other=False, password=None, binding=None, worker=None, peer=PEER):
    identity = state["other"] if other else state["user"]
    return (worker or state["auth"]).begin_mfa_login(
        auth.Credentials(
            email=state["emails"][identity], password=password or state["password"]
        ),
        client_ip=peer,
        browser_binding=binding or state["binding"],
    )


def promote(state):
    with state["runtime"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["members"][3])
            .values(role="admin")
        )


def snapshot(state):
    with Session(state["runtime"]) as session:
        users = [session.get(User, i) for i in (state["user"], state["other"])]
        return (
            [(u.security_revision, u.mfa_required, u.password_hash) for u in users],
            [
                (r.id, r.finished_at, r.token_hash)
                for r in session.scalars(
                    select(MfaPending)
                    .where(MfaPending.user_id.in_([state["user"], state["other"]]))
                    .order_by(MfaPending.id)
                )
            ],
            [
                (r.id, r.revoked_at)
                for r in session.scalars(
                    select(SessionRecord)
                    .where(SessionRecord.membership_id.in_(state["members"]))
                    .order_by(SessionRecord.id)
                )
            ],
            [
                (e.user_id, e.security_revision)
                for e in session.scalars(
                    select(SecurityEvent)
                    .where(
                        SecurityEvent.user_id.in_([state["user"], state["other"]]),
                        SecurityEvent.action == "security_revision_advanced",
                    )
                    .order_by(SecurityEvent.id)
                )
            ],
        )


def mfa_quotas(state):
    with Session(state["runtime"]) as session:
        return [
            (r.scope, r.key_hash, r.failures, r.window_started_at)
            for r in session.scalars(
                select(MfaThrottle).order_by(MfaThrottle.scope, MfaThrottle.key_hash)
            )
        ]


def test_active_factor_returns_only_bound_totp_state_and_composes_to_session(state):
    pending = begin(state)
    assert isinstance(pending, auth.PendingMfaLogin)
    assert pending.purpose is mfa_pending.PendingPurpose.LOGIN_TOTP
    assert pending.expires_at == state["now"] + timedelta(minutes=5)
    assert not any(
        hasattr(pending, name) for name in ("choices", "user_id", "tenant_id", "role")
    )
    assert pending.token.get_secret_value() not in repr(pending)
    data = snapshot(state)
    assert data[0][0][:2] == (1, True)
    assert len(data[1]) == len(data[3]) == 1
    code = SecretStr(
        pyotp.TOTP(state["seed"].base32().get_secret_value()).at(state["now"])
    )
    proof = mfa_verification.MfaVerifier(
        state["runtime"], keyring=state["keyring"]
    ).verify_login_totp(
        token=pending.token, browser_binding=state["binding"], code=code, client_ip=PEER
    )
    issued = MfaSessionIssuer(state["runtime"]).select_membership(
        proof_token=proof.token,
        browser_binding=state["binding"],
        membership_id=state["members"][0],
    )
    assert issued is not None
    with Session(state["runtime"]) as session, session.begin():
        assert sessions.resolve_session(session, issued.token).user_id == state["user"]


def test_admin_in_another_company_requires_enrollment_and_revokes_global_sessions(
    state,
):
    promote(state)
    result = begin(state, other=True)
    assert result.purpose is mfa_pending.PendingPurpose.ENROLLMENT
    assert result.expires_at == state["now"] + timedelta(minutes=15)
    data = snapshot(state)
    assert data[0][1][:2] == (1, True)
    assert all(revoked is not None for _, revoked in data[2])
    assert len(data[1]) == len(data[3]) == 1
    # Enrollment does not grant TOTP proof, seed activation or business session.
    assert (
        mfa_verification.MfaVerifier(
            state["runtime"], keyring=state["keyring"]
        ).verify_login_totp(
            token=result.token,
            browser_binding=state["binding"],
            code=SecretStr("000000"),
            client_ip=PEER,
        )
        is None
    )
    assert len(snapshot(state)[2]) == 2


@pytest.mark.parametrize("inactive", ["membership", "tenant"])
def test_inactive_admin_context_does_not_create_a_requirement(state, inactive):
    promote(state)
    model, identity = (
        (TenantMembership, state["members"][3])
        if inactive == "membership"
        else (Tenant, state["tenants"][1])
    )
    with state["runtime"].begin() as connection:
        connection.execute(
            update(model).where(model.id == identity).values(is_active=False)
        )
    result = begin(state, other=True)
    assert isinstance(result, sessions.IssuedSession)
    assert snapshot(state)[0][1][:2] == (0, False)


def test_plain_account_retains_multi_company_and_single_company_login(state):
    result = begin(state, other=True)
    assert (
        isinstance(result, login_challenges.PendingLogin) and len(result.choices) == 2
    )
    with state["runtime"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["members"][3])
            .values(is_active=False)
        )
    assert isinstance(begin(state, other=True), sessions.IssuedSession)
    assert snapshot(state)[0][1][:2] == (0, False)


def test_obligation_stays_after_demotion_and_old_login_cannot_bypass(state):
    promote(state)
    assert begin(state, other=True).purpose is mfa_pending.PendingPurpose.ENROLLMENT
    with state["runtime"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["members"][3])
            .values(role="read_only")
        )
    assert begin(state, other=True).purpose is mfa_pending.PendingPurpose.ENROLLMENT
    assert snapshot(state)[0][1][:2] == (1, True)
    assert len(snapshot(state)[3]) == 1
    assert (
        state["auth"].begin_login(
            auth.Credentials(
                email=state["emails"][state["other"]], password=state["password"]
            ),
            client_ip=PEER,
            browser_binding=state["binding"],
        )
        is None
    )


@pytest.mark.parametrize("target", ["security_events", "mfa_pendings"])
def test_real_write_failure_reverts_requirement_revocation_and_pending(state, target):
    promote(state)
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(text(f"REVOKE INSERT ON {target} FROM carrillo_app"))
    try:
        with pytest.raises(
            auth.AuthenticationUnavailable, match="^Authentication unavailable$"
        ):
            begin(state, other=True)
        assert snapshot(state) == before
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text(f"GRANT INSERT ON {target} TO carrillo_app"))
    assert begin(state, other=True) is not None


def test_failed_commit_publishes_no_pending_and_rolls_back_global_policy(state):
    promote(state)

    def fail(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(state["auth"]._factory, "before_commit", fail, once=True)
    before = snapshot(state)
    with pytest.raises(auth.AuthenticationUnavailable):
        begin(state, other=True)
    assert snapshot(state) == before


def test_valid_password_clears_only_password_account_and_keeps_mfa_failures(state):
    assert begin(state, password=SecretStr(secrets.token_urlsafe(32))) is None
    verifier = mfa_verification.MfaVerifier(state["runtime"], keyring=state["keyring"])
    assert (
        verifier.verify_totp(
            user_id=state["user"],
            credential_id=state["credential"],
            code=SecretStr("invalid"),
            client_ip=PEER,
        )
        is None
    )
    before = mfa_quotas(state)
    assert begin(state) is not None
    assert mfa_quotas(state) == before
    with Session(state["runtime"]) as session:
        assert (
            session.get(
                LoginThrottle, ("account", auth._digest(state["emails"][state["user"]]))
            ).failures
            == 0
        )
        assert session.get(LoginThrottle, ("ip", auth._ip_digest(PEER))).failures == 1


def test_password_quota_shared_with_legacy_blocks_before_hashing(state, monkeypatch):
    credentials = auth.Credentials(
        email=state["emails"][state["user"]],
        password=SecretStr(secrets.token_urlsafe(32)),
    )
    for _ in range(5):
        assert (
            state["auth"].begin_login(
                credentials, client_ip=PEER, browser_binding=state["binding"]
            )
            is None
        )
    before = snapshot(state)

    def forbidden(*args, **kwargs):
        pytest.fail("Exhausted password budget reached hash verification")

    monkeypatch.setattr(auth, "verify_password", forbidden)
    assert begin(state) is None
    assert snapshot(state) == before


def test_unknown_wrong_password_and_inactive_user_never_latch_policy(state):
    promote(state)
    before = snapshot(state)
    assert (
        begin(state, other=True, password=SecretStr(secrets.token_urlsafe(32))) is None
    )
    assert (
        state["auth"].begin_mfa_login(
            auth.Credentials(email="unknown@example.com", password=state["password"]),
            client_ip=PEER,
            browser_binding=state["binding"],
        )
        is None
    )
    assert snapshot(state) == before
    with state["runtime"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["other"]).values(is_active=False)
        )
    assert begin(state, other=True) is None
    assert snapshot(state)[0][1][:2] == (0, False)


@pytest.mark.parametrize("value", ["short", "+" * 43, "é" * 43])
def test_invalid_browser_binding_is_rejected_before_verification(
    state, monkeypatch, value
):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid browser binding reached credentials")

    monkeypatch.setattr(auth, "verify_password", forbidden)
    before = snapshot(state)
    assert begin(state, binding=SecretStr(value)) is None
    assert snapshot(state) == before


def test_repeated_password_replaces_only_this_browser_pending(state):
    first = begin(state)
    second_binding = SecretStr(secrets.token_urlsafe(32))
    second = begin(state, binding=second_binding)
    replacement = begin(state)
    assert len({p.token.get_secret_value() for p in (first, second, replacement)}) == 3
    with Session(state["runtime"]) as session:
        rows = {
            r.token_hash: r.finished_at
            for r in session.scalars(
                select(MfaPending).where(MfaPending.user_id == state["user"])
            )
        }
        assert rows[mfa_pending._hash(first.token, "mfa-pending-token-v1:")] is not None
        assert rows[mfa_pending._hash(second.token, "mfa-pending-token-v1:")] is None
        assert (
            rows[mfa_pending._hash(replacement.token, "mfa-pending-token-v1:")] is None
        )
    assert len(snapshot(state)[3]) == 1


@pytest.mark.parametrize("delay", [timedelta(minutes=2), timedelta(minutes=5)])
def test_time_spent_after_password_cannot_extend_initial_deadline(
    state, monkeypatch, delay
):
    start = state["now"]
    original = security_state.synchronize_mfa_requirement

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        state["now"] += delay
        return result

    monkeypatch.setattr(security_state, "synchronize_mfa_requirement", delayed)
    before = snapshot(state)
    result = begin(state)
    if delay >= timedelta(minutes=5):
        assert result is None and snapshot(state) == before
    else:
        assert result.expires_at == start + timedelta(minutes=5)
        with Session(state["runtime"]) as session:
            row = session.scalar(
                select(MfaPending).where(MfaPending.user_id == state["user"])
            )
            assert row.password_verified_at == start and row.created_at == start + delay


def test_password_rehash_is_bound_into_new_pending(state, monkeypatch):
    old_hash = snapshot(state)[0][0][2]
    monkeypatch.setattr(auth, "password_needs_rehash", lambda _: True)
    assert begin(state) is not None
    with Session(state["runtime"]) as session:
        user = session.get(User, state["user"])
        row = session.scalar(select(MfaPending).where(MfaPending.user_id == user.id))
        assert user.password_hash != old_hash
        assert row.password_fingerprint == login_challenges._password_fingerprint(user)


def test_revoked_factor_history_cannot_restart_mail_only_enrollment(state):
    with state["runtime"].begin() as connection:
        connection.execute(
            update(MfaCredential)
            .where(MfaCredential.id == state["credential"])
            .values(revoked_at=state["now"])
        )
    assert begin(state) is None
    assert snapshot(state)[1] == []


def test_concurrent_password_success_latches_once_and_preserves_each_browser(
    state, monkeypatch
):
    entered, release, started = Event(), Event(), Event()
    pids = []
    original_issue, original_lock = mfa_pending._issue_after_password, auth._lock_bucket
    second_worker = auth.Authenticator(state["runtime"])
    second_binding = SecretStr(secrets.token_urlsafe(32))

    def pause(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic contention")
        return original_issue(*args, **kwargs)

    def observe(session, scope, key):
        if scope == "account" and entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
        return original_lock(session, scope, key)

    monkeypatch.setattr(mfa_pending, "_issue_after_password", pause)
    monkeypatch.setattr(auth, "_lock_bucket", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(begin, state)
        try:
            assert entered.wait(5)
            second = pool.submit(
                begin,
                state,
                worker=second_worker,
                binding=second_binding,
                peer=ip_address("127.0.0.2"),
            )
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
        assert (
            first.result(timeout=5) is not None and second.result(timeout=5) is not None
        )
    data = snapshot(state)
    assert data[0][0][:2] == (1, True)
    assert len(data[1]) == 2 and all(finished is None for _, finished, _ in data[1])
    assert len(data[3]) == 1
