"""Global state/revision, atomic revocation and sticky requirement in isolated DB."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.access import security_state as security
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    LoginChallenge,
    MfaCredential,
    SecurityEvent,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.sessions import issue_session

pytestmark = pytest.mark.integration


@pytest.fixture
def state(mfa_state):
    state = mfa_state
    with Session(state["runtime"]) as session, session.begin():
        tenants = [Tenant(name=f"Security fixture {i}") for i in range(2)]
        session.add_all(tenants)
        session.flush()
        members = [
            TenantMembership(tenant_id=t.id, user_id=state["user"], role="read_only")
            for t in tenants
        ]
        session.add_all(members)
        session.flush()
        tenant_ids = [t.id for t in tenants]
        member_ids = [m.id for m in members]
        # Arrange historical password sessions before the synthetic factor is
        # current. This test setup is never a runtime authentication bypass.
        credential = session.get(MfaCredential, state["credential"])
        credential.revoked_at = session.scalar(select(func.clock_timestamp()))
        session.flush()
        for t in tenants:
            issue_session(session, user_id=state["user"], tenant_id=t.id)
        credential.revoked_at = None
        session.flush()
        now = session.scalar(select(func.clock_timestamp()))
        session.add(
            LoginChallenge(
                user_id=state["user"],
                token_hash="a" * 64,
                binding_hash="b" * 64,
                password_fingerprint="c" * 64,
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
    state = dict(state, tenants=tenant_ids, members=member_ids)
    try:
        yield state
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


def run(state, operation=security.advance_security_revision, expected=0):
    with Session(state["runtime"]) as session, session.begin():
        return operation(session, user_id=state["user"], expected_revision=expected)


def snapshot(state):
    with Session(state["runtime"]) as session:
        user = session.get(User, state["user"])
        sessions = list(
            session.scalars(
                select(SessionRecord.revoked_at).where(
                    SessionRecord.membership_id.in_(state["members"])
                )
            )
        )
        pending = session.scalar(
            select(LoginChallenge.finished_at).where(
                LoginChallenge.user_id == state["user"]
            )
        )
        events = list(
            session.scalars(
                select(SecurityEvent).where(SecurityEvent.user_id == state["user"])
            )
        )
        return (
            user.security_revision,
            user.mfa_required,
            sessions,
            pending,
            [(e.action, e.credential_id, e.security_revision) for e in events],
        )


def test_default_is_not_verified_email_or_mfa_activation(state):
    with Session(state["runtime"]) as session, session.begin():
        assert security.read_security_state(
            session, user_id=state["user"]
        ) == security.SecurityState(state["user"], 0, False)
    assert snapshot(state)[4] == []


def test_revision_rejects_old_context_and_revokes_all_tenants_and_pending(state):
    assert run(state).revision == 1
    revision, required, sessions, pending, events = snapshot(state)
    assert revision == 1 and not required
    assert len(sessions) == 2 and all(sessions) and pending is not None
    assert events == [("security_revision_advanced", None, 1)]
    with pytest.raises(security.StaleSecurityRevision):
        run(state)
    assert run(state, expected=1).revision == 2


def test_active_factor_latches_requirement_and_revocation_does_not_clear_it(state):
    assert run(state, security.synchronize_mfa_requirement).mfa_required
    with Session(state["runtime"]) as session, session.begin():
        session.get(MfaCredential, state["credential"]).revoked_at = session.scalar(
            select(func.clock_timestamp())
        )
    before = snapshot(state)
    assert run(state, security.synchronize_mfa_requirement, expected=1).mfa_required
    assert snapshot(state) == before


@pytest.mark.parametrize("inactive", [None, "membership", "tenant"])
def test_admin_requirement_uses_active_global_memberships_and_is_sticky(
    state, inactive
):
    with Session(state["runtime"]) as session, session.begin():
        session.get(MfaCredential, state["credential"]).revoked_at = session.scalar(
            select(func.clock_timestamp())
        )
        member = session.get(TenantMembership, state["members"][0])
        member.role = "admin"
        if inactive == "membership":
            member.is_active = False
        elif inactive == "tenant":
            session.get(Tenant, state["tenants"][0]).is_active = False
    result = run(state, security.synchronize_mfa_requirement)
    assert result.mfa_required is (inactive is None)
    assert result.revision == (1 if inactive is None else 0)
    with Session(state["runtime"]) as session, session.begin():
        session.get(TenantMembership, state["members"][0]).role = "read_only"
    assert (
        run(state, security.synchronize_mfa_requirement, expected=result.revision)
        == result
    )


def test_later_failure_rolls_back_revision_requirement_revocations_and_event(state):
    before = snapshot(state)
    with (
        Session(state["runtime"]) as session,
        pytest.raises(RuntimeError),
        session.begin(),
    ):
        security.synchronize_mfa_requirement(
            session, user_id=state["user"], expected_revision=0
        )
        raise RuntimeError("Synthetic failure")
    assert snapshot(state) == before


def test_audit_permission_failure_rolls_back_every_effect(state):
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(text("REVOKE INSERT ON security_events FROM carrillo_app"))
    try:
        with pytest.raises(DBAPIError):
            run(state)
        assert snapshot(state) == before
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text("GRANT INSERT ON security_events TO carrillo_app"))


@pytest.mark.parametrize("invalid", [-1, True, "0"])
def test_expected_revision_must_be_server_integer(state, invalid):
    with pytest.raises(ValueError):
        run(state, expected=invalid)
    assert snapshot(state)[0] == 0


def test_inactive_subject_is_denied(state):
    with Session(state["runtime"]) as session, session.begin():
        session.get(User, state["user"]).is_active = False
    with pytest.raises(AccessDenied):
        run(state)


@pytest.mark.parametrize(
    "change",
    [
        dict(security_revision=-1),
        dict(security_revision=0),
        dict(mfa_required=False),
        dict(mfa_required=False, security_revision=2),
    ],
)
def test_database_blocks_revision_rewind_or_clearing_requirement(state, change):
    run(state, security.synchronize_mfa_requirement)
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["user"]).values(**change)
        )
    assert snapshot(state)[:2] == (1, True)


def test_database_requires_revision_change_when_setting_requirement(state):
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["user"]).values(mfa_required=True)
        )


def test_two_workers_with_same_expected_revision_have_one_winner(state):
    entered, release, started = Event(), Event(), Event()
    pids = []

    def first():
        with Session(state["runtime"]) as session, session.begin():
            result = security.advance_security_revision(
                session, user_id=state["user"], expected_revision=0
            )
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic contention")
            return result

    def second():
        with Session(state["runtime"]) as session, session.begin():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            return security.advance_security_revision(
                session, user_id=state["user"], expected_revision=0
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
        assert winner.result(timeout=5).revision == 1
        with pytest.raises(security.StaleSecurityRevision):
            loser.result(timeout=5)
    assert len(snapshot(state)[4]) == 1


def test_downgrade_preserves_nonzero_state_and_evidence(state):
    run(state)
    before = snapshot(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0011_mfa_throttles")
    assert snapshot(state) == before
