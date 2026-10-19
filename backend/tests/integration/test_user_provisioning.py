"""New identities, tenant authority and audit tested in guarded PostgreSQL only."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from argon2.exceptions import HashingError
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import administration as module
from app.modules.access.administration import (
    AdministrationUnavailable,
    LastAdministratorRequired,
    MembershipAdministrator,
    MembershipChange,
    UserCreation,
    UserCreationConflict,
)
from app.modules.access.authentication import Authenticator, Credentials
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    AuditEvent,
    LoginChallenge,
    MembershipRole,
    SecurityEvent,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import verify_password
from app.modules.access.sessions import issue_session, resolve_session

pytestmark = pytest.mark.integration


@pytest.fixture
def provisioning(http_identity, recent_session):
    state = dict(http_identity)
    state["prefix"] = "new-" + uuid4().hex + "-"
    state["service"] = MembershipAdministrator(state["runtime"])
    with Session(state["runtime"]) as session, session.begin():
        state["token"] = issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        ).token
        session.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["foreign_membership"])
            .values(role="admin")
        )
        state["other_token"] = issue_session(
            session, user_id=state["foreign"], tenant_id=state["other"]
        ).token
        target = TenantMembership(
            user_id=state["foreign"], tenant_id=state["tenant"], role="cashier"
        )
        session.add(target)
        session.flush()
        state["target"] = target.id
    recent_session(state["token"])
    recent_session(state["other_token"])
    try:
        yield state
    finally:
        # Owner cleanup is confined to synthetic identities in this guarded DB.
        with state["owner"].begin() as connection:
            from identity_cleanup import cleanup_identity

            users = select(User.id).where(User.email.startswith(state["prefix"]))
            cleanup_identity(connection, users)
            members = select(TenantMembership.id).where(
                TenantMembership.user_id.in_(users)
            )
            connection.execute(
                delete(AuditEvent).where(
                    AuditEvent.tenant_id.in_([state["tenant"], state["other"]])
                )
            )
            connection.execute(
                delete(SessionRecord).where(SessionRecord.membership_id.in_(members))
            )
            connection.execute(
                delete(LoginChallenge).where(LoginChallenge.user_id.in_(users))
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.user_id.in_(users))
            )
            connection.execute(delete(User).where(User.id.in_(users)))


def data(state, **changes):
    return UserCreation(
        **{
            "email": state["prefix"] + uuid4().hex + "@example.com",
            "password": SecretStr(secrets.token_urlsafe(24)),
            "role": "cashier",
        }
        | changes
    )


def create(state, value=None, *, other=False):
    return state["service"].create_user(
        state["other_token"] if other else state["token"],
        data=value or data(state),
        expected_actor=state["foreign_membership"] if other else state["membership"],
    )


def counts(state):
    with Session(state["runtime"]) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                User,
                TenantMembership,
                AuditEvent,
                SessionRecord,
                LoginChallenge,
            )
        )


def wait_blocked(owner, pid):
    deadline = time.monotonic() + 3
    with owner.connect() as connection:
        while time.monotonic() < deadline:
            if connection.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
            ):
                return
            time.sleep(0.02)
    pytest.fail("Expected a real PostgreSQL lock wait")


@pytest.mark.parametrize("role", list(MembershipRole))
def test_new_user_membership_and_audit_commit_together(provisioning, role):
    state = provisioning
    value = data(state, role=role)
    before = counts(state)
    result = create(state, value)
    assert result.role == role and result.is_active
    assert result.actor_membership_id == state["membership"]
    assert set(result.model_dump()) == {"membership_id", "role", "is_active"}
    assert counts(state) == (
        before[0] + 1,
        before[1] + 1,
        before[2] + 1,
        before[3],
        before[4],
    )
    with Session(state["runtime"]) as session, session.begin():
        member = session.get(TenantMembership, result.membership_id)
        user = session.get(User, member.user_id)
        assert member.tenant_id == state["tenant"] and user.is_active
        assert user.email == value.email
        assert verify_password(value.password, user.password_hash)
        assert user.password_hash != value.password.get_secret_value()
        audit = session.scalar(select(AuditEvent))
        assert (
            audit.action,
            audit.before_role,
            audit.before_active,
            audit.after_role,
            audit.after_active,
        ) == ("membership_created", None, None, role, True)
        assert (
            audit.tenant_id,
            audit.actor_membership_id,
            audit.target_membership_id,
        ) == (state["tenant"], state["membership"], member.id)
        assert audit.created_at.utcoffset().total_seconds() == 0
        assert resolve_session(session, state["token"]).role == "admin"
    # Real verifier can use the new identity; creation itself never emits a session.
    from ipaddress import ip_address

    logged = Authenticator(state["runtime"]).authenticate(
        Credentials(email=value.email, password=value.password),
        client_ip=ip_address("192.0.2.87"),
        selected_tenant_id=state["tenant"],
    )
    if role == "admin":
        from app.modules.access.authentication import PendingMfaLogin
        from app.modules.access.mfa_pending import PendingPurpose

        assert logged is None
        pending = Authenticator(state["runtime"]).begin_mfa_login(
            Credentials(email=value.email, password=value.password),
            client_ip=ip_address("192.0.2.87"),
            browser_binding=SecretStr(secrets.token_urlsafe(32)),
        )
        assert isinstance(pending, PendingMfaLogin)
        assert pending.purpose == PendingPurpose.ENROLLMENT
        return
    assert logged is not None
    with Session(state["runtime"]) as session, session.begin():
        assert (
            resolve_session(session, logged.token).membership_id == result.membership_id
        )


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_non_admin_cannot_create_or_probe_existing_email(
    provisioning, monkeypatch, role
):
    state = provisioning
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    before = counts(state)
    hasher = Mock(side_effect=AssertionError("Must authorize before hash"))
    monkeypatch.setattr(module, "hash_password", hasher)
    with pytest.raises(AccessDenied):
        create(state, data(state, email=state["email"], role="admin"))
    hasher.assert_not_called()
    assert counts(state) == before


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "malformed",
        "revoked",
        "expired",
        "idle",
        "tenant",
        "user",
        "membership",
        "context",
    ],
)
def test_current_identity_and_context_required(provisioning, monkeypatch, invalid):
    state = provisioning
    token, expected = state["token"], state["membership"]
    with state["owner"].begin() as connection:
        if invalid in {"tenant", "user", "membership"}:
            model, key = {
                "tenant": (Tenant, "tenant"),
                "user": (User, "user"),
                "membership": (TenantMembership, "membership"),
            }[invalid]
            connection.execute(
                update(model).where(model.id == state[key]).values(is_active=False)
            )
        elif invalid in {"revoked", "expired", "idle"}:
            clause = {
                "revoked": "revoked_at = ts.now",
                "expired": (
                    "created_at=ts.now-interval '9 hours', "
                    "last_seen_at=ts.now-interval '9 hours', "
                    "expires_at=ts.now-interval '1 hour'"
                ),
                "idle": (
                    "created_at=ts.now-interval '1 hour', "
                    "last_seen_at=ts.now-interval '31 minutes', "
                    "expires_at=ts.now+interval '7 hours'"
                ),
            }[invalid]
            connection.execute(
                text(
                    "WITH ts AS (SELECT clock_timestamp() AS now) UPDATE sessions SET "
                    "reauthenticated_at=NULL, reauthentication_fingerprint=NULL, "
                    + clause
                    + " FROM ts WHERE membership_id=:id"
                ),
                {"id": expected},
            )
    if invalid == "missing":
        token = SecretStr(secrets.token_urlsafe(32))
    elif invalid == "malformed":
        token = SecretStr("invalid")
    elif invalid == "context":
        expected = state["foreign_membership"]
    before = counts(state)
    hasher = Mock(side_effect=AssertionError("Must authorize before hash"))
    monkeypatch.setattr(module, "hash_password", hasher)
    with pytest.raises(AccessDenied):
        state["service"].create_user(token, data=data(state), expected_actor=expected)
    hasher.assert_not_called()
    assert counts(state) == before


@pytest.mark.parametrize("identity", ["own", "foreign", "inactive"])
def test_existing_global_identity_never_attached_or_reset(
    provisioning, monkeypatch, identity
):
    state = provisioning
    with Session(state["runtime"]) as session:
        foreign = session.get(User, state["foreign"])
        email = state["email"] if identity == "own" else foreign.email
        previous = foreign.password_hash
    if identity != "own":
        with state["owner"].begin() as connection:
            # This identity belongs exclusively to the other company.
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id == state["target"])
            )
            if identity == "inactive":
                connection.execute(
                    update(User)
                    .where(User.id == state["foreign"])
                    .values(is_active=False)
                )
    before = counts(state)
    hasher = Mock(side_effect=AssertionError("Must reject duplicate before hash"))
    monkeypatch.setattr(module, "hash_password", hasher)
    with pytest.raises(UserCreationConflict, match="^User creation unavailable$"):
        create(state, data(state, email=email.upper()))
    hasher.assert_not_called()
    assert counts(state) == before
    with Session(state["runtime"]) as session:
        assert session.get(User, state["foreign"]).password_hash == previous


@pytest.mark.parametrize("point", ["hash", "membership", "audit", "commit"])
def test_failure_rolls_back_whole_creation(provisioning, monkeypatch, point):
    state = provisioning
    before = counts(state)
    canary = secrets.token_urlsafe(24)

    def fail(*_):
        raise SQLAlchemyError(canary)

    def inject(_connection, _cursor, statement, _params, _context, _many):
        if statement.startswith(
            "INSERT INTO "
            + {"membership": "tenant_memberships", "audit": "audit_events"}.get(
                point, "unused"
            )
        ):
            fail()

    if point == "hash":
        monkeypatch.setattr(
            module, "hash_password", Mock(side_effect=HashingError(canary))
        )
    elif point == "commit":
        event.listen(state["runtime"], "commit", fail)
    else:
        event.listen(state["runtime"], "before_cursor_execute", inject)
    try:
        with pytest.raises(AdministrationUnavailable) as caught:
            create(state)
        assert str(caught.value) == "Administration unavailable" and canary not in str(
            caught.value
        )
    finally:
        if point == "commit":
            event.remove(state["runtime"], "commit", fail)
        elif point != "hash":
            event.remove(state["runtime"], "before_cursor_execute", inject)
    assert counts(state) == before


@pytest.mark.parametrize("other", [False, True])
def test_simultaneous_same_email_has_one_winner_without_partial_membership(
    provisioning, other
):
    state = provisioning
    value = data(state)
    reached, release, started, waiter = Event(), Event(), Event(), {}
    before = counts(state)

    def observe(connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO audit_events") and not reached.is_set():
            reached.set()
            assert release.wait(5)
        elif reached.is_set() and (
            (other and statement.startswith("INSERT INTO users"))
            or (not other and "FROM tenants" in statement and "FOR UPDATE" in statement)
        ):
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    def contender():
        with pytest.raises(UserCreationConflict):
            create(state, value, other=other)

    event.listen(state["runtime"], "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(create, state, value)
            try:
                assert reached.wait(3)
                second = pool.submit(contender)
                assert started.wait(2)
                wait_blocked(state["owner"], waiter["pid"])
            finally:
                release.set()
            winner = first.result(timeout=5)
            second.result(timeout=5)
    finally:
        release.set()
        event.remove(state["runtime"], "before_cursor_execute", observe)
    assert counts(state) == (
        before[0] + 1,
        before[1] + 1,
        before[2] + 1,
        before[3],
        before[4],
    )
    with Session(state["runtime"]) as session:
        assert (
            session.get(TenantMembership, winner.membership_id).tenant_id
            == state["tenant"]
        )


def test_permission_rechecked_after_waiting_for_actor(provisioning):
    state = provisioning
    started, attempting, waiter = Event(), Event(), {}
    before = counts(state)

    def observe(connection, _cursor, statement, _params, _context, _many):
        if (
            attempting.is_set()
            and "FROM users" in statement
            and "FOR UPDATE" in statement
        ):
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    def attempt():
        attempting.set()
        with pytest.raises(AccessDenied):
            create(state)

    event.listen(state["runtime"], "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(state["runtime"]) as holder, holder.begin():
                holder.execute(
                    select(User).where(User.id == state["user"]).with_for_update()
                )
                holder.execute(
                    update(TenantMembership)
                    .where(TenantMembership.id == state["membership"])
                    .values(role="mechanic")
                )
                future = pool.submit(attempt)
                assert started.wait(2)
                wait_blocked(state["owner"], waiter["pid"])
            future.result(timeout=5)
    finally:
        event.remove(state["runtime"], "before_cursor_execute", observe)
    assert counts(state) == before


def test_created_admin_awaiting_mfa_cannot_replace_last_confirmed_admin(provisioning):
    state = provisioning
    value = data(state, role="admin")
    new = create(state, value)
    with pytest.raises(LastAdministratorRequired):
        state["service"].change_membership(
            state["token"],
            membership_id=state["membership"],
            change=MembershipChange(role="read_only", is_active=True),
        )
    with Session(state["runtime"]) as session, session.begin():
        member = session.get(TenantMembership, new.membership_id)
        assert session.get(User, member.user_id).mfa_required is True
        with pytest.raises(AccessDenied):
            issue_session(session, user_id=member.user_id, tenant_id=state["tenant"])


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "unknown"},
        {"before_role": "cashier"},
        {"before_active": False},
        {"after_active": False},
        {"after_role": "root"},
        {"action": "membership_changed"},
    ],
)
def test_creation_audit_checks_reject_invalid_evidence(provisioning, changes):
    state = provisioning
    values = dict(
        tenant_id=state["tenant"],
        actor_membership_id=state["membership"],
        target_membership_id=state["target"],
        action="membership_created",
        before_role=None,
        before_active=None,
        after_role="cashier",
        after_active=True,
    )
    with (
        Session(state["runtime"]) as session,
        session.begin(),
        pytest.raises(IntegrityError),
    ):
        session.add(AuditEvent(**(values | changes), created_at=func.clock_timestamp()))
        session.flush()


def test_downgrade_refuses_to_discard_creation_evidence(provisioning):
    state = provisioning
    create(state)
    before = counts(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0006_membership_audit")
    with state["owner"].connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0034_product_images"
        )
    assert counts(state) == before


def test_migration_roundtrip_preserves_existing_change_audit(provisioning):
    state = provisioning
    # Arrange an actual pre-0015 audit row. Modern permission changes advance
    # security revision and deliberately cannot be rolled back past that evidence.
    with Session(state["runtime"]) as session, session.begin():
        member = session.get(TenantMembership, state["target"])
        member.role = "mechanic"
        session.add(
            AuditEvent(
                tenant_id=state["tenant"],
                actor_membership_id=state["membership"],
                target_membership_id=state["target"],
                action="membership_changed",
                before_role="cashier",
                after_role="mechanic",
                before_active=True,
                after_active=True,
                created_at=session.scalar(select(func.clock_timestamp())),
            )
        )
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    # Preserve the historical audit roundtrip using pre-0015 synthetic sessions.
    # New session evidence is separately covered by downgrade refusal tests.
    with state["owner"].begin() as connection:
        connection.execute(
            delete(SecurityEvent).where(
                SecurityEvent.user_id.in_([state["user"], state["foreign"]]),
                SecurityEvent.action == "login_succeeded",
            )
        )
        connection.execute(
            text(
                "UPDATE sessions SET security_revision = NULL WHERE "
                "mfa_pending_id IS NULL AND membership_id IN "
                "(SELECT id FROM tenant_memberships WHERE tenant_id IN "
                "(:tenant, :other))"
            ),
            {"tenant": state["tenant"], "other": state["other"]},
        )
    with state["owner"].begin() as connection:
        before = connection.execute(text("SELECT * FROM audit_events")).all()
        config.attributes["connection"] = connection
        command.downgrade(config, "0006_membership_audit")
        command.upgrade(config, "head")
        command.check(config)
        assert connection.execute(text("SELECT * FROM audit_events")).all() == before
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege('carrillo_app', 'audit_events', 'UPDATE') "
                "OR has_table_privilege('carrillo_app', 'audit_events', 'DELETE')"
            )
        )
