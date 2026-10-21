"""Isolated PostgreSQL consumption, contention and rollback; no operational MFA."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from pydantic import SecretBytes, SecretStr
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.access import mfa_consumption as service
from app.modules.access import mfa_crypto as crypto
from app.modules.access.models import (
    MfaCredential,
    MfaRecoveryCode,
    MfaRecoverySet,
    SecurityEvent,
    User,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def state(mfa_state):
    return mfa_state


def totp_code(state, offset=0):
    otp = pyotp.TOTP(state["seed"].base32().get_secret_value())
    return SecretStr(otp.generate_otp(int(state["now"].timestamp()) // 30 + offset))


def consume(state, kind, session, **changes):
    values = dict(user_id=state["user"], credential_id=state["credential"])
    if kind == "totp":
        values.update(code=totp_code(state), keyring=state["keyring"])
        return service.consume_totp(session, **(values | changes))
    values.update(set_id=state["set"], code=state["codes"][0])
    return service.consume_recovery_code(session, **(values | changes))


def committed(state, kind, **changes):
    with Session(state["runtime"]) as session, session.begin():
        return consume(state, kind, session, **changes)


def saved(state):
    with Session(state["runtime"]) as session:
        credential = session.get(MfaCredential, state["credential"])
        codes = list(
            session.scalars(
                select(MfaRecoveryCode)
                .where(MfaRecoveryCode.set_id == state["set"])
                .order_by(MfaRecoveryCode.position)
            )
        )
        return (
            credential.last_counter,
            credential.last_verified_at,
            tuple(c.used_at for c in codes),
        )


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_consume_once_and_reject_replay_across_sessions(state, kind):
    result = committed(state, kind)
    assert result == service.MfaConsumption(state["credential"], state["now"])
    before = saved(state)
    assert committed(state, kind) is None
    assert len(security_events(state)) == 1
    assert saved(state) == before
    assert (before[0] is not None) == (kind == "totp")
    assert sum(t is not None for t in before[2]) == (1 if kind == "recovery" else 0)


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_rollback_after_flush_restores_consumption_and_other_work(state, kind):
    before = saved(state)
    with (
        Session(state["runtime"]) as session,
        pytest.raises(RuntimeError),
        session.begin(),
    ):
        assert consume(state, kind, session) is not None
        session.execute(
            update(User).where(User.id == state["other"]).values(is_active=False)
        )
        raise RuntimeError("Synthetic failure after consumption")
    assert saved(state) == before
    assert security_events(state) == []
    with Session(state["runtime"]) as session:
        assert session.get(User, state["other"]).is_active
    assert committed(state, kind) is not None


def wait_blocked(owner, pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with owner.connect() as connection:
            if connection.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
            ):
                return
        time.sleep(0.01)
    pytest.fail("Second connection did not actually block in PostgreSQL")


@pytest.mark.parametrize("kind", ["totp", "recovery"])
@pytest.mark.parametrize("rollback_first", [False, True])
def test_two_connections_only_one_commit_or_retry_after_rollback(
    state, kind, rollback_first
):
    entered, release, second_started = Event(), Event(), Event()
    second_pid = []

    def first():
        with Session(state["runtime"]) as session:
            try:
                with session.begin():
                    result = consume(state, kind, session)
                    assert result is not None
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError("Synthetic contention timed out")
                    if rollback_first:
                        raise RuntimeError("Synthetic rollback")
                return True
            except RuntimeError:
                if not rollback_first:
                    raise
                return False

    def second():
        with Session(state["runtime"]) as session, session.begin():
            second_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            second_started.set()
            return consume(state, kind, session) is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(first)
        try:
            assert entered.wait(5)
            contender = pool.submit(second)
            assert second_started.wait(5)
            wait_blocked(state["owner"], second_pid[0])
        finally:
            release.set()
        assert winner.result(timeout=10) is (not rollback_first)
        assert contender.result(timeout=10) is rollback_first
    assert committed(state, kind) is None
    assert len(security_events(state)) == 1


@pytest.mark.parametrize("kind", ["totp", "recovery"])
@pytest.mark.parametrize(
    "reason",
    [
        "wrong_user",
        "missing_user",
        "missing_credential",
        "inactive_user",
        "revoked_credential",
        "future_credential",
    ],
)
def test_rejected_context_never_consumes(state, kind, reason):
    changes = {}
    with Session(state["runtime"]) as session, session.begin():
        if reason == "wrong_user":
            changes["user_id"] = state["other"]
        elif reason == "missing_user":
            changes["user_id"] = uuid4()
        elif reason == "missing_credential":
            changes["credential_id"] = uuid4()
        elif reason == "inactive_user":
            session.get(User, state["user"]).is_active = False
        elif reason == "revoked_credential":
            session.get(MfaCredential, state["credential"]).revoked_at = state["now"]
        else:
            session.get(MfaCredential, state["credential"]).created_at = state[
                "now"
            ] + timedelta(seconds=1)
    before = saved(state)
    assert committed(state, kind, **changes) is None
    assert saved(state) == before


@pytest.mark.parametrize(
    "reason", ["missing_set", "invalidated_set", "future_set", "wrong_code"]
)
def test_recovery_set_checks(state, reason):
    changes = {}
    with Session(state["runtime"]) as session, session.begin():
        if reason == "missing_set":
            changes["set_id"] = uuid4()
        elif reason == "invalidated_set":
            session.get(MfaRecoverySet, state["set"]).invalidated_at = state["now"]
        elif reason == "future_set":
            session.get(MfaRecoverySet, state["set"]).created_at = state[
                "now"
            ] + timedelta(seconds=1)
        else:
            changes["code"] = crypto.generate_recovery_codes()[0]
    before = saved(state)
    assert committed(state, "recovery", **changes) is None
    assert saved(state) == before


def test_each_recovery_code_consumed_independently_and_password_change_preserves_it(
    state,
):
    with Session(state["runtime"]) as session, session.begin():
        session.get(User, state["user"]).password_hash = "changed-synthetic"
    for code in state["codes"]:
        assert committed(state, "recovery", code=code) is not None
    assert all(saved(state)[2])


def test_next_totp_step_and_backward_clock(state):
    assert committed(state, "totp") is not None
    previous = saved(state)
    state["now"] -= timedelta(microseconds=1)
    assert committed(state, "totp", code=totp_code(state, 1)) is None
    assert saved(state) == previous
    state["now"] += timedelta(seconds=30, microseconds=1)
    assert committed(state, "totp") is not None
    assert saved(state)[0] == previous[0] + 1


@pytest.mark.parametrize("failure", ["missing_key", "tampered_tag"])
def test_decryption_failure_never_consumes_or_disables_factor(state, failure):
    changes = {}
    if failure == "missing_key":
        changes["keyring"] = crypto.SeedKeyring(
            {"other": SecretBytes(secrets.token_bytes(32))}, active_key_id="other"
        )
    else:
        with Session(state["runtime"]) as session, session.begin():
            row = session.get(MfaCredential, state["credential"])
            row.ciphertext = row.ciphertext[:-1] + bytes([row.ciphertext[-1] ^ 1])
    before = saved(state)
    with pytest.raises(crypto.SeedProtectionError):
        committed(state, "totp", **changes)
    assert saved(state) == before
    with Session(state["runtime"]) as session:
        assert session.get(MfaCredential, state["credential"]).revoked_at is None


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_refreshes_preloaded_orm_state_after_another_transaction(state, kind):
    with Session(state["runtime"]) as stale:
        cached = stale.get(
            MfaCredential if kind == "totp" else MfaRecoveryCode,
            state["credential"] if kind == "totp" else (state["set"], 0),
        )
        assert cached is not None
        assert committed(state, kind) is not None
        assert consume(state, kind, stale) is None
        stale.rollback()


def test_clock_read_after_waiting_user_lock(state):
    code = totp_code(state)
    started, pid = Event(), []

    def worker():
        with Session(state["runtime"]) as session, session.begin():
            pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            return consume(state, "totp", session, code=code)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(state["runtime"]) as blocker, blocker.begin():
            blocker.execute(
                select(User).where(User.id == state["user"]).with_for_update()
            )
            contender = pool.submit(worker)
            assert started.wait(5)
            wait_blocked(state["owner"], pid[0])
            state["now"] += timedelta(minutes=2)
        assert contender.result(timeout=10) is None
    assert saved(state)[0] is None


def test_migration_refuses_downgrade_with_mfa_state_even_revoked(state):
    with Session(state["runtime"]) as session, session.begin():
        session.get(MfaCredential, state["credential"]).revoked_at = state["now"]
    before = saved(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0008_session_reauthentication")
    with state["owner"].connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0042_stock_reversals"
        )
    assert saved(state) == before


@pytest.mark.parametrize(
    "changes",
    [
        dict(encryption_version=2),
        dict(nonce=bytes(11)),
        dict(ciphertext=bytes(35)),
        dict(key_id="bad:id"),
        dict(last_counter=-1),
        dict(last_counter=1),
        dict(last_verified_at=func.clock_timestamp()),
    ],
)
def test_database_rejects_malformed_credentials(state, changes):
    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.execute(
            update(MfaCredential)
            .where(MfaCredential.id == state["credential"])
            .values(**changes)
        )


@pytest.mark.parametrize(
    "changes",
    [
        dict(position=-1),
        dict(position=10),
        dict(code_hash=bytes(31)),
        dict(used_at=text("'1969-01-01 00:00:00+00'::timestamptz")),
    ],
)
def test_database_rejects_malformed_recovery_code(state, changes):
    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.execute(
            update(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.set_id == state["set"], MfaRecoveryCode.position == 0
            )
            .values(**changes)
        )


def test_database_rejects_cross_user_recovery_set(state):
    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.execute(
            update(MfaRecoverySet)
            .where(MfaRecoverySet.id == state["set"])
            .values(user_id=state["other"])
        )


def test_database_rejects_two_current_credentials_or_sets(state):
    with Session(state["runtime"]) as session:
        original = session.get(MfaCredential, state["credential"])
        values = {
            col.name: getattr(original, col.name)
            for col in MfaCredential.__table__.columns
            if col.name != "id"
        }
    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.add(MfaCredential(id=uuid4(), **values))
        session.flush()
    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.add(
            MfaRecoverySet(
                user_id=state["user"],
                credential_id=state["credential"],
                created_at=state["now"],
            )
        )
        session.flush()


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_commit_failure_rolls_back_consumption(state, kind):
    before = saved(state)

    def fail_commit(session):
        raise RuntimeError("Synthetic commit failure")

    with (
        Session(state["runtime"]) as session,
        pytest.raises(RuntimeError, match="Synthetic commit failure"),
    ):
        event.listen(session, "before_commit", fail_commit, once=True)
        with session.begin():
            assert consume(state, kind, session) is not None
    assert saved(state) == before
    assert committed(state, kind) is not None


@pytest.mark.parametrize("offset", [-2, 2])
def test_expired_or_too_early_totp_does_not_consume(state, offset):
    before = saved(state)
    assert committed(state, "totp", code=totp_code(state, offset)) is None
    assert saved(state) == before


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_revocation_committed_while_verifier_waits_is_respected(state, kind):
    started, pid = Event(), []

    def worker():
        with Session(state["runtime"]) as session, session.begin():
            pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            return consume(state, kind, session)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(state["runtime"]) as blocker, blocker.begin():
            blocker.execute(
                select(User).where(User.id == state["user"]).with_for_update()
            )
            blocker.get(MfaCredential, state["credential"]).revoked_at = state["now"]
            contender = pool.submit(worker)
            assert started.wait(5)
            wait_blocked(state["owner"], pid[0])
        assert contender.result(timeout=10) is None
    assert saved(state) == (None, None, (None,) * 10)


@pytest.mark.parametrize(
    "table", ["mfa_credentials", "mfa_recovery_sets", "mfa_recovery_codes"]
)
def test_runtime_cannot_delete_mfa_state(state, table):
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        # Table is from this fixed parameter list, never request data.
        connection.execute(text(f"DELETE FROM {table} WHERE false"))


def security_events(state):
    with Session(state["runtime"]) as session:
        return [
            (row.id, row.user_id, row.credential_id, row.action, row.occurred_at)
            for row in session.scalars(
                select(SecurityEvent)
                .where(SecurityEvent.user_id == state["user"])
                .order_by(SecurityEvent.occurred_at, SecurityEvent.id)
            )
        ]


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_committed_event_identifies_consumption_not_login_or_tenant(state, kind):
    assert security_events(state) == []
    assert committed(state, kind) is not None
    rows = security_events(state)
    assert len(rows) == 1
    assert rows[0][1:] == (
        state["user"],
        state["credential"],
        "mfa_totp_consumed" if kind == "totp" else "mfa_recovery_consumed",
        state["now"],
    )
    # The audit timestamp is the exact server verification time, not commit time.
    assert rows[0][0] is not None


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_event_and_consumption_invisible_before_commit_and_rolled_back_together(
    state, kind
):
    before = saved(state)
    with Session(state["runtime"]) as session:
        with session.begin():
            assert consume(state, kind, session) is not None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SecurityEvent)
                    .where(SecurityEvent.user_id == state["user"])
                )
                == 1
            )
            assert security_events(state) == []
            assert saved(state) == before
        assert len(security_events(state)) == 1


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_missing_audit_insert_privilege_rolls_back_the_code(state, kind):
    before = saved(state)
    with state["owner"].begin() as connection:
        connection.execute(text("REVOKE INSERT ON security_events FROM carrillo_app"))
    try:
        with pytest.raises(DBAPIError) as caught:
            committed(state, kind)
        assert caught.value.orig.sqlstate == "42501"
        assert saved(state) == before
        assert security_events(state) == []
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text("GRANT INSERT ON security_events TO carrillo_app"))
    assert committed(state, kind) is not None
    assert len(security_events(state)) == 1


@pytest.mark.parametrize(
    "command_text",
    [
        "UPDATE security_events SET action = 'mfa_totp_consumed' WHERE id = :id",
        "DELETE FROM security_events WHERE id = :id",
        "TRUNCATE security_events",
        "DROP TABLE security_events",
    ],
)
def test_runtime_cannot_change_remove_or_drop_evidence(state, command_text):
    assert committed(state, "totp") is not None
    before = security_events(state)
    with pytest.raises(DBAPIError) as caught, state["runtime"].begin() as connection:
        connection.execute(text(command_text), {"id": before[0][0]})
    assert caught.value.orig.sqlstate == "42501"
    assert security_events(state) == before


@pytest.mark.parametrize(
    "changes",
    [
        dict(action="login_succeeded"),
        dict(action="mfa_totp_consumed\n"),
        dict(occurred_at=text("'1969-01-01 00:00:00+00'::timestamptz")),
        dict(user_id=uuid4()),
        dict(credential_id=uuid4()),
    ],
)
def test_audit_rejects_unsupported_action_invalid_time_or_owner(state, changes):
    values = dict(
        user_id=state["user"],
        credential_id=state["credential"],
        action="mfa_totp_consumed",
        occurred_at=state["now"],
    )
    with (
        Session(state["runtime"]) as session,
        pytest.raises(IntegrityError),
        session.begin(),
    ):
        session.add(SecurityEvent(**(values | changes)))
        session.flush()
    assert security_events(state) == []


@pytest.mark.parametrize("consume_first", [False, True])
def test_audit_downgrade_refuses_factors_with_or_without_events(state, consume_first):
    if consume_first:
        assert committed(state, "recovery") is not None
    before = security_events(state)
    consumption = saved(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0009_mfa_credentials")
    with state["owner"].connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0042_stock_reversals"
        )
    assert security_events(state) == before
    assert saved(state) == consumption


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_commit_failure_discards_inserted_audit_evidence(state, kind):
    def fail_commit(session):
        raise RuntimeError("Synthetic commit failure with audit")

    with (
        Session(state["runtime"]) as session,
        pytest.raises(RuntimeError, match="with audit"),
    ):
        event.listen(session, "before_commit", fail_commit, once=True)
        with session.begin():
            assert consume(state, kind, session) is not None
    assert security_events(state) == []
    assert saved(state) == (None, None, (None,) * 10)


@pytest.mark.parametrize("kind", ["totp", "recovery"])
def test_rejected_attempts_do_not_fabricate_success_events(state, kind):
    changes = (
        dict(code=SecretStr("invalid"))
        if kind == "totp"
        else dict(code=crypto.generate_recovery_codes()[0])
    )
    assert committed(state, kind, **changes) is None
    assert security_events(state) == []
    assert saved(state) == (None, None, (None,) * 10)
