"""Atomic pending/quota/TOTP login stage, against guarded ephemeral PostgreSQL."""

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
from pydantic import SecretBytes, SecretStr
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import mfa_consumption as consumption
from app.modules.access import mfa_crypto as crypto
from app.modules.access import mfa_pending as pending
from app.modules.access import mfa_verification as verification
from app.modules.access import security_state
from app.modules.access.models import (
    MfaCredential,
    MfaPending,
    MfaThrottle,
    SecurityEvent,
    SessionRecord,
    User,
)

pytestmark = pytest.mark.integration
P = pending.PendingPurpose
PEER = ip_address("127.0.0.1")


@pytest.fixture
def state(mfa_state, monkeypatch):
    state = dict(mfa_state, binding=SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(pending, "_database_now", lambda session: state["now"])
    # mfa_state's clocks close over a different dict; bind all three to this one.
    monkeypatch.setattr(consumption, "_database_now", lambda session: state["now"])
    monkeypatch.setattr(verification, "_now", lambda session: state["now"])
    state["pending"] = issue(state)
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(MfaPending).where(MfaPending.user_id == state["user"])
            )


def issue(state, purpose=P.LOGIN_TOTP):
    with Session(state["runtime"]) as session, session.begin():
        return pending._issue_after_password(
            session,
            user_id=state["user"],
            binding=state["binding"],
            purpose=purpose,
            expected_revision=0,
        )


def otp(state, valid=True):
    generator = pyotp.TOTP(state["seed"].base32().get_secret_value())
    step = int(state["now"].timestamp()) // 30
    if valid:
        return SecretStr(generator.generate_otp(step))
    candidates = {generator.generate_otp(step + i) for i in (-1, 0, 1)}
    return SecretStr(next(f"{n:06d}" for n in range(4) if f"{n:06d}" not in candidates))


def worker(state, keyring=None):
    return verification.MfaVerifier(
        state["runtime"], keyring=keyring or state["keyring"]
    )


def verify(state, *, valid=True, verifier=None, **changes):
    values = dict(
        token=state["pending"].token,
        browser_binding=state["binding"],
        code=otp(state, valid),
        client_ip=PEER,
    )
    return (verifier or worker(state)).verify_login_totp(**(values | changes))


def snapshot(state):
    with Session(state["runtime"]) as session:
        credential = session.get(MfaCredential, state["credential"])
        rows = list(
            session.scalars(
                select(MfaPending)
                .where(MfaPending.user_id == state["user"])
                .order_by(MfaPending.created_at, MfaPending.purpose)
            )
        )
        quotas = {
            (r.scope, r.key_hash): (r.failures, r.window_started_at)
            for r in session.scalars(select(MfaThrottle))
        }
        events = session.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.user_id == state["user"])
        )
        return (
            credential.last_counter,
            credential.last_verified_at,
            events,
            [
                (
                    r.purpose,
                    r.finished_at,
                    r.password_verified_at,
                    r.expires_at,
                    r.credential_id,
                    r.factor_verified_at,
                )
                for r in rows
            ],
            quotas,
        )


def proof_context(state, token, *, rotate=False):
    with Session(state["runtime"]) as session, session.begin():
        action = pending._rotate if rotate else pending._consume
        return action(
            session, token=token, binding=state["binding"], purpose=P.LOGIN_VERIFIED
        )


def test_success_commits_rotated_proof_counter_event_without_session(state):
    original_time = state["now"]
    original_deadline = state["pending"].expires_at
    state["now"] += timedelta(minutes=2)
    result = verify(state)
    assert result.expires_at == original_deadline
    assert result.token != state["pending"].token
    assert result.token.get_secret_value() not in repr(result)
    data = snapshot(state)
    assert data[0] == int(state["now"].timestamp()) // 30
    assert data[1:3] == (state["now"], 1)
    assert data[3][0][1] == state["now"]
    assert data[3][1] == (
        P.LOGIN_VERIFIED.value,
        None,
        original_time,
        original_deadline,
        state["credential"],
        state["now"],
    )
    assert all(value[0] == 0 for value in data[4].values())
    assert verify(state) is None
    assert verify(state, token=result.token) is None  # Cannot re-enter TOTP stage.
    context = proof_context(state, result.token)
    assert context.credential_id == state["credential"]
    assert context.factor_verified_at == state["now"]
    assert context.password_verified_at == original_time
    assert proof_context(state, result.token) is None
    with Session(state["runtime"]) as session:
        assert session.scalar(select(func.count()).select_from(SessionRecord)) == 0


def test_failed_codes_persist_quota_and_leave_pending_open_then_block(
    state, monkeypatch
):
    for attempt in range(5):
        assert verify(state, valid=False) is None
        data = snapshot(state)
        assert data[:3] == (None, None, 0)
        assert data[3][0][1] is None
        assert sorted(value[0] for value in data[4].values()) == [attempt + 1] * 2
    before = snapshot(state)

    def forbidden(*args, **kwargs):
        pytest.fail("Exhausted quota evaluated factor")

    monkeypatch.setattr(consumption, "consume_totp", forbidden)
    assert verify(state) is None
    assert snapshot(state) == before
    # A newly issued password pending does not reset the independent MFA quota.
    replacement = issue(state)
    assert verify(state, token=replacement.token) is None
    assert snapshot(state)[4] == before[4]


def test_other_factor_purposes_share_budget_and_success_keeps_failures(state):
    w = worker(state)
    assert (
        w.verify_totp(
            user_id=state["user"],
            credential_id=state["credential"],
            code=otp(state, False),
            client_ip=PEER,
        )
        is None
    )
    before = snapshot(state)[4]
    assert verify(state) is not None
    assert snapshot(state)[4] == before
    # New pending cannot reuse a globally consumed TOTP.
    replacement = issue(state)
    assert verify(state, token=replacement.token) is None
    assert all(value[0] == 2 for value in snapshot(state)[4].values())


@pytest.mark.parametrize("kind", ["binding", "unknown", "malformed", "enrollment"])
def test_wrong_pending_cannot_evaluate_factor_or_charge_victim(
    state, monkeypatch, kind
):
    changes = {
        "binding": dict(browser_binding=SecretStr(secrets.token_urlsafe(32))),
        "unknown": dict(token=SecretStr(secrets.token_urlsafe(32))),
        "malformed": dict(token=SecretStr("invalid")),
        "enrollment": dict(token=issue(state, P.ENROLLMENT).token),
    }
    before = snapshot(state)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid pending evaluated factor")

    monkeypatch.setattr(consumption, "consume_totp", forbidden)
    assert verify(state, **changes[kind]) is None
    assert snapshot(state) == before


@pytest.mark.parametrize("change", ["password", "inactive", "revision"])
def test_fresh_identity_state_is_required_before_factor(state, change, monkeypatch):
    values = {
        "password": dict(password_hash="changed-synthetic"),
        "inactive": dict(is_active=False),
        "revision": dict(security_revision=1),
    }
    with state["runtime"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["user"]).values(**values[change])
        )

    def forbidden(*args, **kwargs):
        pytest.fail("Stale pending evaluated factor")

    monkeypatch.setattr(consumption, "consume_totp", forbidden)
    assert verify(state) is None
    assert snapshot(state)[:3] == (None, None, 0)
    assert all(value[0] == 0 for value in snapshot(state)[4].values())


@pytest.mark.parametrize("offset", [timedelta(minutes=5), timedelta(microseconds=-1)])
def test_expired_or_regressed_pending_cannot_consume_code(state, offset):
    state["now"] += offset
    assert verify(state) is None
    assert snapshot(state)[:3] == (None, None, 0)


def test_expiry_during_factor_check_rolls_back_code_event_and_quota(state, monkeypatch):
    original = consumption.consume_totp

    def expire(session, **kwargs):
        result = original(session, **kwargs)
        state["now"] = state["pending"].expires_at
        return result

    monkeypatch.setattr(consumption, "consume_totp", expire)
    before = snapshot(state)
    assert verify(state) is None
    assert snapshot(state) == before


def test_clock_regression_after_factor_check_fails_closed(state, monkeypatch):
    original = consumption.consume_totp

    def regress(session, **kwargs):
        result = original(session, **kwargs)
        state["now"] -= timedelta(microseconds=1)
        return result

    monkeypatch.setattr(consumption, "consume_totp", regress)
    before = snapshot(state)
    with pytest.raises(verification.MfaVerificationUnavailable):
        verify(state)
    assert snapshot(state) == before


@pytest.mark.parametrize("target", ["security_events", "mfa_pendings"])
def test_real_late_permission_failure_rolls_back_everything(state, target):
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(text(f"REVOKE INSERT ON {target} FROM carrillo_app"))
    try:
        with pytest.raises(
            verification.MfaVerificationUnavailable,
            match="^MFA verification unavailable$",
        ):
            verify(state)
        assert snapshot(state) == before
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text(f"GRANT INSERT ON {target} TO carrillo_app"))
    assert verify(state) is not None


@pytest.mark.parametrize("valid", [True, False])
def test_commit_failure_never_publishes_result_or_persists_partial_state(state, valid):
    w = worker(state)

    def fail(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(w._factory, "before_commit", fail, once=True)
    before = snapshot(state)
    with pytest.raises(verification.MfaVerificationUnavailable):
        verify(state, valid=valid, verifier=w)
    assert snapshot(state) == before
    assert verify(state) is not None


def test_missing_seed_key_rolls_back_without_charging_wrong_code(state):
    keys = crypto.SeedKeyring(
        {"missing": SecretBytes(bytes(32))}, active_key_id="missing"
    )
    before = snapshot(state)
    with pytest.raises(verification.MfaVerificationUnavailable):
        verify(state, verifier=worker(state, keys))
    assert snapshot(state) == before


def test_password_issuer_cannot_forge_verified_stage(state):
    with (
        pytest.raises(ValueError, match="Password alone"),
        Session(state["runtime"]) as session,
        session.begin(),
    ):
        pending._issue_after_password(
            session,
            user_id=state["user"],
            binding=state["binding"],
            purpose=P.LOGIN_VERIFIED,
            expected_revision=0,
        )
    assert snapshot(state)[:3] == (None, None, 0)


def test_verified_rotation_retains_evidence_and_deadline(state):
    proof = verify(state)
    original = state["now"]
    state["now"] += timedelta(minutes=1)
    rotated = proof_context(state, proof.token, rotate=True)
    assert rotated.expires_at == proof.expires_at
    assert proof_context(state, proof.token) is None
    context = proof_context(state, rotated.token)
    assert context.factor_verified_at == context.password_verified_at == original


@pytest.mark.parametrize("change", ["factor", "revision"])
def test_verified_stage_is_invalidated_by_revocation_or_security_change(state, change):
    proof = verify(state)
    with Session(state["runtime"]) as session, session.begin():
        if change == "factor":
            credential = session.get(MfaCredential, state["credential"])
            credential.revoked_at = state["now"]
        else:
            security_state.advance_security_revision(
                session, user_id=state["user"], expected_revision=0
            )
    assert proof_context(state, proof.token) is None


def test_database_cannot_turn_password_pending_into_proof_without_evidence(state):
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(
            update(MfaPending)
            .where(MfaPending.user_id == state["user"])
            .values(purpose=P.LOGIN_VERIFIED.value)
        )


def test_downgrade_preserves_verified_evidence(state):
    proof = verify(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0013_mfa_pendings")
    assert proof_context(state, proof.token) is not None


def test_two_workers_publish_one_proof_under_real_contention(state, monkeypatch):
    entered, release, started = Event(), Event(), Event()
    pids = []
    original_check, original_lock = consumption.consume_totp, verification._lock_bucket

    def pause(session, **kwargs):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Synthetic contention")
        return original_check(session, **kwargs)

    def observe(session, scope, key):
        if scope == "ip" and entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
        return original_lock(session, scope, key)

    monkeypatch.setattr(consumption, "consume_totp", pause)
    monkeypatch.setattr(verification, "_lock_bucket", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(verify, state)
        try:
            assert entered.wait(5)
            second = pool.submit(verify, state)
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
                pytest.fail("Second connection did not block")
        finally:
            release.set()
        assert first.result(timeout=5) is not None
        assert second.result(timeout=5) is None
    data = snapshot(state)
    assert data[2] == 1
    assert len([row for row in data[3] if row[0] == P.LOGIN_VERIFIED.value]) == 1
    assert all(value[0] == 0 for value in data[4].values())
