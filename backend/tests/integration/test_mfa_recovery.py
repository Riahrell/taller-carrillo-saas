"""Restricted offline recovery, only synthetic identities and guarded PostgreSQL."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
from threading import Event

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import (
    authentication,
    login_challenges,
    mfa_consumption,
    mfa_pending,
    mfa_recovery,
    mfa_verification,
    recovery_state,
    security_state,
    sessions,
)
from app.modules.access.mfa_crypto import generate_recovery_codes
from app.modules.access.mfa_enrollment import MfaEnrollmentService
from app.modules.access.mfa_sessions import MfaSessionIssuer, MfaSessionUnavailable
from app.modules.access.models import (
    LoginChallenge,
    LoginThrottle,
    MfaCredential,
    MfaPending,
    MfaRecoveryCode,
    MfaRecoveryPermit,
    MfaRecoverySet,
    MfaThrottle,
    SecurityEvent,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password

pytestmark = pytest.mark.integration


@pytest.fixture
def state(mfa_state, monkeypatch):
    state = dict(
        mfa_state,
        password=SecretStr(secrets.token_urlsafe(32)),
        binding=SecretStr(secrets.token_urlsafe(32)),
    )
    for module, name in (
        (authentication, "_now"),
        (authentication, "_database_now"),
        (login_challenges, "_database_now"),
        (mfa_pending, "_database_now"),
        (mfa_consumption, "_database_now"),
        (mfa_verification, "_now"),
        (security_state, "_database_now"),
        (sessions, "_database_now"),
        (mfa_recovery, "_database_now"),
        (recovery_state, "_now"),
    ):
        monkeypatch.setattr(module, name, lambda session: state["now"])
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, state["user"])
        user.password_hash = hash_password(state["password"])
        state["email"] = user.email
        tenant = Tenant(name="Offline recovery fixture")
        session.add(tenant)
        session.flush()
        member = TenantMembership(tenant_id=tenant.id, user_id=user.id, role="admin")
        session.add(member)
        session.flush()
        state["tenant"], state["member"] = tenant.id, member.id
    state["auth"] = authentication.Authenticator(state["runtime"])
    state["service"] = mfa_recovery.MfaRecoveryService(state["runtime"])
    state["source"] = start(state)
    assert state["source"].purpose == mfa_pending.PendingPurpose.LOGIN_TOTP
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(MfaRecoveryPermit).where(
                    MfaRecoveryPermit.user_id == state["user"]
                )
            )
            connection.execute(
                delete(SessionRecord).where(
                    SessionRecord.membership_id == state["member"]
                )
            )
            connection.execute(
                delete(MfaPending).where(MfaPending.user_id == state["user"])
            )
            connection.execute(
                delete(LoginChallenge).where(LoginChallenge.user_id == state["user"])
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id == state["member"])
            )
            connection.execute(delete(Tenant).where(Tenant.id == state["tenant"]))
            connection.execute(delete(LoginThrottle))


def start(state, binding=None):
    return state["auth"].begin_mfa_login(
        authentication.Credentials(email=state["email"], password=state["password"]),
        client_ip=ip_address("127.0.0.1"),
        browser_binding=binding or state["binding"],
    )


def begin(state, **changes):
    return state["service"].begin(
        **(
            dict(
                password_token=state["source"].token,
                browser_binding=state["binding"],
                code=state["codes"][0].value,
                client_ip=ip_address("127.0.0.1"),
            )
            | changes
        )
    )


def inspect(state, permit, **changes):
    return state["service"].inspect(
        **(
            dict(
                permit_token=permit.token,
                browser_binding=state["binding"],
            )
            | changes
        )
    )


def records(state):
    with Session(state["runtime"]) as session:
        permits = list(
            session.scalars(
                select(MfaRecoveryPermit).where(
                    MfaRecoveryPermit.user_id == state["user"]
                )
            )
        )
        codes = list(
            session.scalars(
                select(MfaRecoveryCode)
                .where(MfaRecoveryCode.set_id == state["set"])
                .order_by(MfaRecoveryCode.position)
            )
        )
        factor = session.get(MfaCredential, state["credential"])
        recovery_set = session.get(MfaRecoverySet, state["set"])
        return (
            permits,
            [c.used_at for c in codes],
            factor.revoked_at,
            recovery_set.invalidated_at,
        )


def legacy_session(state):
    with Session(state["runtime"]) as session, session.begin():
        record = SessionRecord(
            tenant_id=state["tenant"],
            membership_id=state["member"],
            token_hash=secrets.token_hex(32),
            security_revision=1,
            created_at=state["now"],
            last_seen_at=state["now"],
            expires_at=state["now"] + timedelta(hours=8),
        )
        session.add(record)
        session.flush()
        return record.id


def test_offline_recovery_consumes_one_code_revokes_access_and_retains_current_factor(
    state,
):
    old_session = legacy_session(state)
    other = start(state, SecretStr(secrets.token_urlsafe(32)))
    permit = begin(state)
    assert permit is not None and permit.expires_at == state["now"] + timedelta(
        minutes=15
    )
    rows, used, revoked, invalidated = records(state)
    assert len(rows) == 1 and used == [state["now"]] + [None] * 9
    assert revoked is invalidated is None
    assert rows[0].token_hash != permit.token.get_secret_value()
    assert permit.token.get_secret_value() not in repr(permit)
    assert begin(state) is None  # Same password stage cannot mint another permit.
    assert inspect(state, permit) is True and inspect(state, permit) is True
    with Session(state["runtime"]) as session:
        assert session.get(User, state["user"]).security_revision == 1
        assert session.get(SessionRecord, old_session).revoked_at == state["now"]
        assert all(
            p.finished_at is not None
            for p in session.scalars(
                select(MfaPending).where(MfaPending.user_id == state["user"])
            )
        )
        actions = list(
            session.scalars(
                select(SecurityEvent.action).where(
                    SecurityEvent.user_id == state["user"]
                )
            )
        )
        assert (
            actions.count("mfa_recovery_consumed")
            == actions.count("mfa_recovery_started")
            == 1
        )
    assert (
        MfaSessionIssuer(state["runtime"]).select_membership(
            proof_token=permit.token,
            browser_binding=state["binding"],
            membership_id=state["member"],
        )
        is None
    )
    assert (
        MfaEnrollmentService(state["runtime"], keyring=state["keyring"]).begin(
            permit_token=permit.token, browser_binding=state["binding"]
        )
        is None
    )
    assert other.token != permit.token


@pytest.mark.parametrize("kind", ["malformed", "unknown", "used"])
def test_bad_offline_code_spends_shared_quota_without_revocation_or_permission(
    state, kind
):
    old_session = legacy_session(state)
    if kind == "malformed":
        code = SecretStr("invalid")
    elif kind == "unknown":
        code = generate_recovery_codes()[0].value
        assert code not in {value.value for value in state["codes"]}
    else:
        with Session(state["runtime"]) as session, session.begin():
            session.execute(
                update(MfaRecoveryCode)
                .where(
                    MfaRecoveryCode.set_id == state["set"],
                    MfaRecoveryCode.position == 0,
                )
                .values(used_at=state["now"])
            )
        code = state["codes"][0].value
    assert begin(state, code=code) is None
    with Session(state["runtime"]) as session:
        assert {r.failures for r in session.scalars(select(MfaThrottle))} == {1}
        assert session.get(SessionRecord, old_session).revoked_at is None
    assert not records(state)[0]


def test_offline_quota_blocks_even_correct_code_without_consuming_it(state):
    for _ in range(5):
        assert begin(state, code=SecretStr("invalid")) is None
    assert begin(state) is None
    assert records(state)[1] == [None] * 10


@pytest.mark.parametrize("wrong", ["token", "binding"])
def test_recovery_requires_the_bound_verified_password_stage(state, wrong):
    values = {
        "password_token" if wrong == "token" else "browser_binding": SecretStr(
            secrets.token_urlsafe(32)
        )
    }
    assert begin(state, **values) is None
    assert not records(state)[0] and records(state)[1] == [None] * 10


@pytest.mark.parametrize("late", [False, True])
def test_password_stage_expiry_before_or_after_work_rolls_back_consumption(
    state, monkeypatch, late
):
    old_session = legacy_session(state)
    if late:
        monkeypatch.setattr(
            mfa_recovery, "_database_now", lambda session: state["source"].expires_at
        )
    else:
        state["now"] = state["source"].expires_at
    assert begin(state) is None
    assert not records(state)[0] and records(state)[1] == [None] * 10
    with Session(state["runtime"]) as session:
        assert session.get(SessionRecord, old_session).revoked_at is None


@pytest.mark.parametrize(
    "table,operation",
    [
        ("security_events", "INSERT"),
        ("mfa_recovery_permits", "INSERT"),
        ("sessions", "UPDATE"),
    ],
)
def test_real_write_failures_roll_back_code_revocation_permission_and_audit(
    state, table, operation
):
    old_session = legacy_session(state)
    with state["owner"].begin() as connection:
        connection.execute(text(f"REVOKE {operation} ON {table} FROM carrillo_app"))
    try:
        with pytest.raises(mfa_recovery.MfaRecoveryUnavailable):
            begin(state)
        assert not records(state)[0] and records(state)[1] == [None] * 10
        with Session(state["runtime"]) as session:
            assert session.get(SessionRecord, old_session).revoked_at is None
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text(f"GRANT {operation} ON {table} TO carrillo_app"))
    assert begin(state) is not None


def test_commit_failure_does_not_publish_permission_or_spend_code(state):
    def fail(session):
        raise SQLAlchemyError("Synthetic recovery commit failure")

    event.listen(state["service"]._factory, "before_commit", fail, once=True)
    with pytest.raises(mfa_recovery.MfaRecoveryUnavailable):
        begin(state)
    assert not records(state)[0] and records(state)[1] == [None] * 10
    assert begin(state) is not None


def test_lost_permission_requires_fresh_password_and_another_unused_code(state):
    first = begin(state)
    state["source"] = start(state)
    assert begin(state) is None  # First code remains consumed.
    second = begin(state, code=state["codes"][1].value)
    assert second is not None and second.token != first.token
    assert not inspect(state, first) and inspect(state, second)
    assert records(state)[1] == [state["now"], state["now"]] + [None] * 8


@pytest.mark.parametrize(
    "change", ["revision", "password", "inactive", "factor", "set", "code", "source"]
)
def test_recovery_permission_revalidates_all_originating_proof(state, change):
    permit = begin(state)
    with Session(state["runtime"]) as session, session.begin():
        if change in ("revision", "password", "inactive"):
            user = session.get(User, state["user"])
            if change == "revision":
                user.security_revision += 1
            elif change == "password":
                user.password_hash = hash_password(SecretStr(secrets.token_urlsafe(32)))
            else:
                user.is_active = False
        elif change == "factor":
            session.get(MfaCredential, state["credential"]).revoked_at = state["now"]
        elif change == "set":
            session.get(MfaRecoverySet, state["set"]).invalidated_at = state["now"]
        elif change == "code":
            session.execute(
                update(MfaRecoveryCode)
                .where(
                    MfaRecoveryCode.set_id == state["set"],
                    MfaRecoveryCode.position == 0,
                )
                .values(used_at=None)
            )
        else:
            session.execute(
                update(MfaPending)
                .where(MfaPending.user_id == state["user"])
                .values(finished_at=None)
            )
    assert inspect(state, permit) is False


@pytest.mark.parametrize(
    "offset,valid", [(899, True), (900, False), (901, False), (-1, False)]
)
def test_recovery_permission_deadline_is_from_emission_without_renewal(
    state, offset, valid
):
    permit = begin(state)
    state["now"] += timedelta(seconds=offset)
    assert inspect(state, permit) is valid
    assert records(state)[0][0].expires_at == permit.expires_at


@pytest.mark.parametrize(
    "scope,valid", [("same", False), ("other", True), ("global", False)]
)
def test_recovery_cancellation_on_browser_restart_and_global_revocation(
    state, scope, valid
):
    permit = begin(state)
    if scope == "global":
        with Session(state["runtime"]) as session, session.begin():
            login_challenges.revoke_user_challenges(session, state["user"])
    else:
        start(
            state,
            state["binding"]
            if scope == "same"
            else SecretStr(secrets.token_urlsafe(32)),
        )
    assert inspect(state, permit) is valid
    assert records(state)[1] == [state["now"]] + [None] * 9


def normal_proof(state, binding):
    source = start(state, binding)
    return mfa_verification.MfaVerifier(
        state["runtime"], keyring=state["keyring"]
    ).verify_login_totp(
        token=source.token,
        browser_binding=binding,
        code=SecretStr(
            pyotp.TOTP(state["seed"].base32().get_secret_value()).at(state["now"])
        ),
        client_ip=ip_address("127.0.0.1"),
    )


def test_only_committed_normal_mfa_session_cancels_recovery_across_browsers(state):
    permit = begin(state)
    binding = SecretStr(secrets.token_urlsafe(32))
    proof = normal_proof(state, binding)
    assert proof is not None and inspect(state, permit) is True
    issuer = MfaSessionIssuer(state["runtime"])

    def fail(session):
        raise SQLAlchemyError("Synthetic session commit failure")

    event.listen(issuer._factory, "before_commit", fail, once=True)
    with pytest.raises(MfaSessionUnavailable):
        issuer.select_membership(
            proof_token=proof.token,
            browser_binding=binding,
            membership_id=state["member"],
        )
    assert inspect(state, permit) is True
    assert (
        issuer.select_membership(
            proof_token=proof.token,
            browser_binding=binding,
            membership_id=state["member"],
        )
        is not None
    )
    assert inspect(state, permit) is False
    assert records(state)[1] == [state["now"]] + [None] * 9


def test_private_permission_consumption_is_one_use_and_rolls_back_with_later_work(
    state,
):
    permit = begin(state)
    with (
        pytest.raises(RuntimeError, match="Synthetic later failure"),
        Session(state["runtime"]) as session,
        session.begin(),
    ):
        assert (
            mfa_recovery._consume_recovery_permit(
                session, token=permit.token, browser_binding=state["binding"]
            )
            is not None
        )
        raise RuntimeError("Synthetic later failure")
    assert inspect(state, permit) is True
    with Session(state["runtime"]) as session, session.begin():
        context = mfa_recovery._consume_recovery_permit(
            session, token=permit.token, browser_binding=state["binding"]
        )
        assert (
            context.user_id == state["user"]
            and context.credential_id == state["credential"]
        )
    assert inspect(state, permit) is False


def test_two_recovery_entries_have_one_consumption_and_one_permission(
    state, monkeypatch
):
    other_binding = SecretStr(secrets.token_urlsafe(32))
    other_source = start(state, other_binding)
    entered, release, started = Event(), Event(), Event()
    pids = []
    original = mfa_verification._lock_bucket

    def observe(session, scope, key_hash):
        if entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
        return original(session, scope, key_hash)

    def pause(session):
        if not entered.is_set():
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic recovery contention")

    event.listen(state["service"]._factory, "before_commit", pause)
    monkeypatch.setattr(mfa_verification, "_lock_bucket", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(begin, state)
        try:
            assert entered.wait(5)
            second = pool.submit(
                begin,
                state,
                password_token=other_source.token,
                browser_binding=other_binding,
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
                pytest.fail("Second recovery entry did not block")
        finally:
            release.set()
        assert first.result(timeout=5) is not None and second.result(timeout=5) is None
    assert (
        len(records(state)[0]) == 1 and records(state)[1] == [state["now"]] + [None] * 9
    )


def test_permissions_checks_and_downgrade_preserve_recovery_evidence(state):
    begin(state)
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(delete(MfaRecoveryPermit))
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(
            update(MfaRecoveryPermit).values(
                expires_at=state["now"] + timedelta(minutes=16)
            )
        )
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(MfaRecoveryPermit).values(user_id=state["other"]))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0019_mfa_enrollment")
