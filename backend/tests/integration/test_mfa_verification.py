"""Persistent independent MFA quotas in guarded ephemeral PostgreSQL."""

import secrets
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
from pydantic import SecretBytes, SecretStr
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import mfa_consumption as consumption
from app.modules.access import mfa_crypto as crypto
from app.modules.access import mfa_verification as verification
from app.modules.access.models import (
    LoginChallenge,
    LoginThrottle,
    MfaCredential,
    MfaRecoveryCode,
    MfaThrottle,
    SecurityEvent,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)

pytestmark = pytest.mark.integration
PEER = ip_address("127.0.0.1")


@pytest.fixture
def state(mfa_state):
    return mfa_state


def otp(state, *, valid):
    generator = pyotp.TOTP(state["seed"].base32().get_secret_value())
    step = int(state["now"].timestamp()) // 30
    if valid:
        return SecretStr(generator.generate_otp(step))
    candidates = {generator.generate_otp(step + offset) for offset in [-1, 0, 1]}
    return SecretStr(next(f"{n:06d}" for n in range(4) if f"{n:06d}" not in candidates))


def verify(state, kind="totp", *, valid=False, verifier=None, peer=PEER, **changes):
    worker = verifier or verification.MfaVerifier(
        state["runtime"], keyring=state["keyring"]
    )
    values = dict(
        user_id=state["user"], credential_id=state["credential"], client_ip=peer
    )
    if kind == "totp":
        return worker.verify_totp(
            **(values | dict(code=otp(state, valid=valid)) | changes)
        )
    if valid:
        code = state["codes"][0]
    else:
        # Canonical high-entropy input guaranteed different from this set's codes.
        existing = {c.value.get_secret_value() for c in state["codes"]}
        while True:
            code = crypto.generate_recovery_codes()[0]
            if code.value.get_secret_value() not in existing:
                break
    return worker.verify_recovery_code(
        **(values | dict(set_id=state["set"], code=code) | changes)
    )


def buckets(state):
    with Session(state["runtime"]) as session:
        return {
            (r.scope, r.key_hash): (r.failures, r.window_started_at)
            for r in session.scalars(select(MfaThrottle))
        }


def failures(state, *, user=None, peer=PEER):
    rows = buckets(state)
    return (
        rows.get(
            ("account", verification._account_key(user or state["user"])), (0, None)
        )[0],
        rows.get(("ip", verification._ip_key(peer)), (0, None))[0],
    )


def snapshot(state):
    with Session(state["runtime"]) as session:
        cred = session.get(MfaCredential, state["credential"])
        used = tuple(
            session.scalars(
                select(MfaRecoveryCode.used_at)
                .where(MfaRecoveryCode.set_id == state["set"])
                .order_by(MfaRecoveryCode.position)
            )
        )
        count = session.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.user_id == state["user"])
        )
        return cred.last_counter, cred.last_verified_at, used, count


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_account_five_failures_shared_by_workers_then_valid_code_blocked(
    state, kind, monkeypatch
):
    for n in range(5):
        assert verify(state, kind) is None  # New worker instance each call.
        assert failures(state) == (n + 1, n + 1)
    before, counters = snapshot(state), buckets(state)

    def forbidden(*args, **kwargs):
        pytest.fail("An exhausted bucket must not evaluate a factor")

    monkeypatch.setattr(consumption, "consume_totp", forbidden)
    monkeypatch.setattr(consumption, "consume_recovery_code", forbidden)
    state["now"] += timedelta(minutes=1)
    assert verify(state, kind, valid=True) is None
    assert buckets(state) == counters  # No sliding of the window.
    assert snapshot(state) == before


def test_totp_and_recovery_share_same_account_budget(state):
    for kind in ["totp", "recovery", "totp", "recovery", "totp"]:
        assert verify(state, kind) is None
    assert failures(state) == (5, 5)
    assert verify(state, "recovery", valid=True) is None
    assert snapshot(state) == (None, None, (None,) * 10, 0)


def test_new_ip_does_not_bypass_account_limit(state):
    for n in range(5):
        assert verify(state, peer=ip_address(f"127.0.0.{n + 1}")) is None
    assert verify(state, valid=True, peer=ip_address("127.0.0.50")) is None
    assert failures(state, peer=ip_address("127.0.0.50")) == (5, 0)


def test_ip_thirty_failures_across_accounts_blocks_without_creating_more_buckets(state):
    # These server-side IDs simulate stale identities whose factor no longer exists.
    users = [uuid4() for _ in range(6)]
    for user in users:
        for _ in range(5):
            assert verify(state, user_id=user) is None
    assert failures(state, user=users[-1]) == (5, 30)
    before = buckets(state)
    assert verify(state, valid=True) is None
    assert buckets(state) == before
    assert snapshot(state) == (None, None, (None,) * 10, 0)


@pytest.mark.parametrize(
    "peer", [ip_address("::ffff:127.0.0.1"), ip_address("::ffff:7f00:1")]
)
def test_ipv4_mapped_ipv6_cannot_reset_ip_bucket(state, peer):
    assert verify(state) is None
    assert verify(state, peer=peer) is None
    assert failures(state) == failures(state, peer=peer) == (2, 2)
    assert len(buckets(state)) == 2


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_correct_factor_keeps_both_failure_histories_and_audit_is_atomic(state, kind):
    assert verify(state) is None
    before = buckets(state)
    assert verify(state, kind, valid=True) is not None
    assert buckets(state) == before
    assert snapshot(state)[3] == 1
    assert verify(state, kind, valid=True) is None  # Replay charges one failure.
    assert failures(state) == (2, 2)
    assert snapshot(state)[3] == 1


@pytest.mark.parametrize(
    "offset",
    [
        timedelta(minutes=15) - timedelta(microseconds=1),
        timedelta(minutes=15),
        timedelta(minutes=15, microseconds=1),
    ],
)
def test_exact_window_boundary(state, offset):
    start = state["now"]
    for _ in range(5):
        assert verify(state) is None
    state["now"] = start + offset
    result = verify(state, valid=True)
    if offset < verification.WINDOW:
        assert result is None
        assert failures(state) == (5, 5)
    else:
        assert result is not None
        assert failures(state) == (0, 0)


def test_window_starts_at_first_failure_and_does_not_slide(state):
    start = state["now"]
    assert verify(state, valid=True) is not None  # Initially creates empty buckets.
    state["now"] += timedelta(minutes=2)
    assert verify(state) is None
    first = buckets(state)
    assert all(v == (1, start + timedelta(minutes=2)) for v in first.values())
    state["now"] += timedelta(minutes=10)
    assert verify(state) is None
    assert all(v == (2, start + timedelta(minutes=2)) for v in buckets(state).values())
    state["now"] += timedelta(minutes=5)
    assert verify(state) is None
    assert all(v == (1, state["now"]) for v in buckets(state).values())


def test_server_clock_regression_preserves_counters_and_consumption(state):
    assert verify(state) is None
    before, counter_state = snapshot(state), buckets(state)
    state["now"] -= timedelta(microseconds=1)
    with pytest.raises(
        verification.MfaVerificationUnavailable, match="^MFA verification unavailable$"
    ):
        verify(state, valid=True)
    assert snapshot(state) == before
    assert buckets(state) == counter_state


@pytest.mark.parametrize("initial", [0, 4])
def test_concurrent_workers_serialize_initial_inserts_and_last_allowed_failure(
    state, initial, monkeypatch
):
    for _ in range(initial):
        assert verify(state) is None
    entered, release, second_started = Event(), Event(), Event()
    pids, calls = [], []
    original_check = consumption.consume_totp
    original_lock = verification._lock_bucket

    def pause(session, **kwargs):
        calls.append(True)
        if not entered.is_set():
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic contention timed out")
        return original_check(session, **kwargs)

    def observe(session, scope, key):
        if scope == "ip" and entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            second_started.set()
        return original_lock(session, scope, key)

    monkeypatch.setattr(consumption, "consume_totp", pause)
    monkeypatch.setattr(verification, "_lock_bucket", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(verify, state)
        try:
            assert entered.wait(5)
            second = pool.submit(
                verify, state, "recovery" if initial else "totp", valid=bool(initial)
            )
            assert second_started.wait(5)
            # Reuse the actual blocking assertion, without sleeps as a proof.
            import time

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with state["owner"].connect() as connection:
                    blocked = connection.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pids[0]},
                    )
                if blocked:
                    break
                time.sleep(0.01)
            else:
                pytest.fail("Second worker did not block in PostgreSQL")
        finally:
            release.set()
        assert first.result(timeout=10) is None
        assert second.result(timeout=10) is None
    expected = 5 if initial else 2
    assert failures(state) == (expected, expected)
    assert len(calls) == (1 if initial else 2)
    assert snapshot(state) == (None, None, (None,) * 10, 0)


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_commit_failure_publishes_no_success_and_rolls_back_all_state(state, kind):
    worker = verification.MfaVerifier(state["runtime"], keyring=state["keyring"])

    def fail_commit(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(worker._factory, "before_commit", fail_commit, once=True)
    with pytest.raises(
        verification.MfaVerificationUnavailable, match="^MFA verification unavailable$"
    ):
        verify(state, kind, valid=True, verifier=worker)
    assert buckets(state) == {}
    assert snapshot(state) == (None, None, (None,) * 10, 0)
    assert verify(state, kind, valid=True) is not None


def test_failed_counter_commit_is_unavailable_not_a_persisted_rejection(state):
    worker = verification.MfaVerifier(state["runtime"], keyring=state["keyring"])

    def fail_commit(session):
        raise SQLAlchemyError("Synthetic rejected-attempt commit failure")

    event.listen(worker._factory, "before_commit", fail_commit, once=True)
    with pytest.raises(verification.MfaVerificationUnavailable):
        verify(state, verifier=worker)
    assert buckets(state) == {}


def test_missing_seed_key_is_unavailable_without_charging_wrong_code(state):
    bad_keys = crypto.SeedKeyring(
        {"missing": SecretBytes(bytes(32))}, active_key_id="missing"
    )
    worker = verification.MfaVerifier(state["runtime"], keyring=bad_keys)
    with pytest.raises(
        verification.MfaVerificationUnavailable, match="^MFA verification unavailable$"
    ):
        verify(state, valid=True, verifier=worker)
    assert buckets(state) == {}
    assert snapshot(state) == (None, None, (None,) * 10, 0)


def test_audit_write_failure_reverts_quota_refresh_and_factor_consumption(state):
    assert verify(state) is None
    before = buckets(state)
    with state["owner"].begin() as connection:
        connection.execute(text("REVOKE INSERT ON security_events FROM carrillo_app"))
    try:
        with pytest.raises(verification.MfaVerificationUnavailable):
            verify(state, valid=True)
        assert buckets(state) == before
        assert snapshot(state) == (None, None, (None,) * 10, 0)
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text("GRANT INSERT ON security_events TO carrillo_app"))


@pytest.mark.parametrize("peer", ["127.0.0.1", None, True])
def test_unvalidated_peer_cannot_create_quota_state(state, peer):
    with pytest.raises(ValueError):
        verify(state, peer=peer)
    assert buckets(state) == {}


@pytest.mark.parametrize(
    "scope,failures_value", [("account", 6), ("ip", 31), ("account", -1), ("other", 1)]
)
def test_database_rejects_invalid_scope_or_counter(state, scope, failures_value):
    from sqlalchemy.exc import IntegrityError

    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.add(
            MfaThrottle(
                scope=scope,
                key_hash="a" * 64,
                window_started_at=state["now"],
                failures=failures_value,
            )
        )
        session.flush()


def test_quota_downgrade_and_runtime_delete_refused(state):
    assert verify(state) is None
    before = buckets(state)
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(text("DELETE FROM mfa_throttles"))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0010_mfa_security_events")
    assert buckets(state) == before


def test_password_only_flow_with_factor_is_rejected_without_resetting_mfa_quota(state):
    from app.modules.access.authentication import Authenticator, Credentials
    from app.modules.access.passwords import hash_password

    password = SecretStr(secrets.token_urlsafe(32))
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, state["user"])
        user.password_hash = hash_password(password)
        email = user.email
        tenants = [Tenant(name=f"Quota fixture {n}") for n in range(2)]
        session.add_all(tenants)
        session.flush()
        tenant_ids = [t.id for t in tenants]
        members = [
            TenantMembership(tenant_id=t.id, user_id=state["user"], role="read_only")
            for t in tenants
        ]
        session.add_all(members)
        session.flush()
        member_ids = [m.id for m in members]
    try:
        assert verify(state) is None
        before = buckets(state)
        auth = Authenticator(state["runtime"])
        for _ in range(2):
            pending = auth.begin_login(
                Credentials(email=email, password=password),
                client_ip=PEER,
                browser_binding=SecretStr("A" * 43),
            )
            assert pending is None
            assert buckets(state) == before
        assert failures(state) == (1, 1)
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(SessionRecord).where(SessionRecord.membership_id.in_(member_ids))
            )
            connection.execute(
                delete(LoginChallenge).where(LoginChallenge.user_id == state["user"])
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id.in_(member_ids))
            )
            connection.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
            connection.execute(delete(LoginThrottle))


def test_clock_regression_during_check_rolls_back_success_and_audit(state, monkeypatch):
    original = consumption.consume_totp

    def regressed(session, **kwargs):
        state["now"] -= timedelta(microseconds=1)
        return original(session, **kwargs)

    monkeypatch.setattr(consumption, "consume_totp", regressed)
    with pytest.raises(verification.MfaVerificationUnavailable):
        verify(state, valid=True)
    assert buckets(state) == {}
    assert snapshot(state) == (None, None, (None,) * 10, 0)


def test_actual_database_lock_timeout_fails_closed_without_counter_changes(state):
    assert verify(state) is None
    before = buckets(state)
    with Session(state["runtime"]) as holder, holder.begin():
        holder.execute(
            select(MfaThrottle).where(MfaThrottle.scope == "ip").with_for_update()
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(verify, state, valid=True)
            with pytest.raises(
                verification.MfaVerificationUnavailable,
                match="^MFA verification unavailable$",
            ):
                future.result(timeout=7)
    assert buckets(state) == before
    assert snapshot(state) == (None, None, (None,) * 10, 0)


@pytest.mark.parametrize("variant", ["fe80::1%eth0", "fe80:0:0:0:0:0:0:1%eth1"])
def test_ipv6_interface_zone_does_not_create_an_ip_budget(state, variant):
    canonical = ip_address("fe80::1")
    assert verify(state, peer=canonical) is None
    assert verify(state, peer=ip_address(variant)) is None
    assert failures(state, peer=canonical) == (2, 2)
    assert len(buckets(state)) == 2
