"""Restricted pending state against guarded PostgreSQL, never operational users."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.access import mfa_pending as service
from app.modules.access import security_state
from app.modules.access.membership import AccessDenied
from app.modules.access.models import MfaPending, User

pytestmark = pytest.mark.integration
P = service.PendingPurpose


@pytest.fixture
def state(mfa_state, monkeypatch):
    state = dict(mfa_state, binding=SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(service, "_database_now", lambda session: state["now"])
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(MfaPending).where(MfaPending.user_id == state["user"])
            )


def issue(state, purpose=P.LOGIN_TOTP):
    with Session(state["runtime"]) as session, session.begin():
        return service._issue_after_password(
            session,
            user_id=state["user"],
            binding=state["binding"],
            purpose=purpose,
            expected_revision=0,
        )


def consume(
    state, token, purpose=P.LOGIN_TOTP, binding=None, operation=service._consume
):
    with Session(state["runtime"]) as session, session.begin():
        return operation(
            session, token=token, binding=binding or state["binding"], purpose=purpose
        )


@pytest.mark.parametrize("purpose,minutes", [(P.LOGIN_TOTP, 5), (P.ENROLLMENT, 15)])
def test_hash_only_state_and_separate_deadlines(state, purpose, minutes):
    pending = issue(state, purpose)
    assert pending.expires_at == state["now"] + timedelta(minutes=minutes)
    with Session(state["runtime"]) as session:
        row = session.scalar(
            select(MfaPending).where(MfaPending.user_id == state["user"])
        )
        assert row.token_hash != pending.token.get_secret_value()
        assert row.binding_hash != state["binding"].get_secret_value()
        assert len(row.token_hash) == len(row.binding_hash) == 64
        assert row.security_revision == 0
        assert row.purpose == purpose.value
    assert pending.token.get_secret_value() not in repr(pending)
    context = consume(state, pending.token, purpose)
    assert context.user_id == state["user"]
    assert context.password_verified_at == state["now"]
    assert context.expires_at == pending.expires_at
    assert not hasattr(context, "tenant_id")
    assert consume(state, pending.token, purpose) is None


def test_wrong_browser_or_purpose_cannot_consume_or_cancel(state):
    pending = issue(state)
    assert consume(state, pending.token, P.ENROLLMENT) is None
    assert (
        consume(state, pending.token, binding=SecretStr(secrets.token_urlsafe(32)))
        is None
    )
    assert consume(state, pending.token) is not None


@pytest.mark.parametrize(
    "offset,accepted", [(-1, False), (299, True), (300, False), (301, False)]
)
def test_exact_expiry_and_clock_regression(state, offset, accepted):
    pending = issue(state)
    state["now"] += timedelta(seconds=offset)
    assert (consume(state, pending.token) is not None) is accepted


def test_rotation_preserves_original_deadline_and_consumes_old_token(state):
    pending = issue(state)
    original = state["now"]
    state["now"] += timedelta(minutes=4)
    rotated = consume(state, pending.token, operation=service._rotate)
    assert rotated.expires_at == pending.expires_at
    assert rotated.token != pending.token
    assert consume(state, pending.token) is None
    context = consume(state, rotated.token)
    assert context.password_verified_at == original
    assert context.expires_at == original + timedelta(minutes=5)


@pytest.mark.parametrize("change", ["password", "inactive", "revision"])
def test_stale_identity_state_rejects_pending(state, change):
    pending = issue(state)
    changes = {
        "password": {"password_hash": "changed-synthetic"},
        "inactive": {"is_active": False},
        "revision": {"security_revision": 1},
    }
    with state["runtime"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["user"]).values(**changes[change])
        )
    assert consume(state, pending.token) is None


def test_global_revision_advance_closes_both_purposes(state):
    purposes = (P.LOGIN_TOTP, P.ENROLLMENT)
    pendings = [issue(state, p) for p in purposes]
    with Session(state["runtime"]) as session, session.begin():
        security_state.advance_security_revision(
            session, user_id=state["user"], expected_revision=0
        )
    with Session(state["runtime"]) as session:
        rows = list(
            session.scalars(
                select(MfaPending).where(MfaPending.user_id == state["user"])
            )
        )
        assert len(rows) == 2 and all(row.finished_at is not None for row in rows)
    for pending, purpose in zip(pendings, purposes, strict=True):
        assert consume(state, pending.token, purpose) is None


def test_failed_transition_rolls_back_consumption_and_rotation(state):
    pending = issue(state)
    for operation in (service._consume, service._rotate):
        with (
            pytest.raises(RuntimeError),
            Session(state["runtime"]) as session,
            session.begin(),
        ):
            assert operation(
                session,
                token=pending.token,
                binding=state["binding"],
                purpose=P.LOGIN_TOTP,
            )
            raise RuntimeError("Synthetic later failure")
    assert consume(state, pending.token) is not None


@pytest.mark.parametrize("invalid", ["", "A" * 42, "A" * 44, "é" * 43])
def test_malformed_token_is_rejected(state, invalid):
    assert consume(state, SecretStr(invalid)) is None


def test_issuer_rejects_raw_purpose_and_stale_revision(state):
    with Session(state["runtime"]) as session, session.begin():
        with pytest.raises(ValueError):
            service._issue_after_password(
                session,
                user_id=state["user"],
                binding=state["binding"],
                purpose="login_totp",
                expected_revision=0,
            )
        with pytest.raises(AccessDenied):
            service._issue_after_password(
                session,
                user_id=state["user"],
                binding=state["binding"],
                purpose=P.LOGIN_TOTP,
                expected_revision=1,
            )


def test_database_rejects_extended_deadline_and_runtime_deletion(state):
    pending = issue(state)
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(
            update(MfaPending)
            .where(MfaPending.user_id == state["user"])
            .values(expires_at=pending.expires_at + timedelta(seconds=1))
        )
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(
            delete(MfaPending).where(MfaPending.user_id == state["user"])
        )
    assert consume(state, pending.token) is not None


def test_downgrade_preserves_even_consumed_pendings(state):
    pending = issue(state)
    assert consume(state, pending.token)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0012_user_security_state")
    assert consume(state, pending.token) is None


def test_concurrent_consumers_have_one_winner_after_real_lock_wait(state):
    pending = issue(state)
    entered, release, started = Event(), Event(), Event()
    pids = []

    def first():
        with Session(state["runtime"]) as session, session.begin():
            result = service._consume(
                session,
                token=pending.token,
                binding=state["binding"],
                purpose=P.LOGIN_TOTP,
            )
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic lock wait")
            return result

    def second():
        with Session(state["runtime"]) as session, session.begin():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            return service._consume(
                session,
                token=pending.token,
                binding=state["binding"],
                purpose=P.LOGIN_TOTP,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(first)
        try:
            assert entered.wait(5)
            loser = pool.submit(second)
            assert started.wait(5)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with state["owner"].connect() as connection:
                    if connection.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pids[0]},
                    ):
                        break
                time.sleep(0.01)
            else:
                pytest.fail("Contender did not block")
        finally:
            release.set()
        assert winner.result(timeout=5) is not None
        assert loser.result(timeout=5) is None
