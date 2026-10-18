"""Atomic access changes with restricted PostgreSQL role and real row locks."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address
from threading import Event
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.access.administration import (
    AdministrationUnavailable,
    LastAdministratorRequired,
    MembershipAdministrator,
    MembershipChange,
)
from app.modules.access.authentication import Authenticator
from app.modules.access.login_challenges import (
    MembershipChoice,
    _binding_digest,
    _issue_challenge,
)
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    AuditEvent,
    LoginChallenge,
    MembershipRole,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.sessions import _lock_user, issue_session, resolve_session

pytestmark = pytest.mark.integration


@pytest.fixture
def administration(http_identity, recent_session):
    state = dict(http_identity)
    runtime, owner = state["runtime"], state["owner"]
    with Session(runtime) as session, session.begin():
        member = TenantMembership(
            tenant_id=state["tenant"], user_id=state["foreign"], role="cashier"
        )
        session.add(member)
        session.flush()
        state["target"] = member.id
        state["actor_token"] = issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        ).token
        state["target_tokens"] = tuple(
            issue_session(
                session, user_id=state["foreign"], tenant_id=state["tenant"]
            ).token
            for _ in range(2)
        )
        state["other_token"] = issue_session(
            session, user_id=state["foreign"], tenant_id=state["other"]
        ).token
        user = _lock_user(session, state["foreign"])
        state["binding"] = SecretStr(secrets.token_urlsafe(32))
        pending = _issue_challenge(
            session,
            user=user,
            binding_hash=_binding_digest(state["binding"]),
            choices=(MembershipChoice(member.id, "Synthetic"),),
        )
        state["challenge_hash"] = session.scalar(select(LoginChallenge.token_hash))
        state["pending_token"] = pending.token
        assert pending.token.get_secret_value() != state["challenge_hash"]
    state["service"] = MembershipAdministrator(runtime)
    recent_session(state["actor_token"])
    recent_session(state["other_token"])
    try:
        yield state
    finally:
        # Owner-only cleanup of these synthetic audit records; app cannot delete.
        with owner.begin() as connection:
            connection.execute(
                delete(AuditEvent).where(
                    AuditEvent.tenant_id.in_([state["tenant"], state["other"]])
                )
            )


@pytest.fixture
def confirmed_other_admin(administration, mfa_state, monkeypatch):
    """Create the second administrator through actual password/TOTP consumption."""
    import pyotp
    from identity_cleanup import cleanup_identity

    from app.modules.access import authentication, mfa_pending, mfa_sessions, sessions
    from app.modules.access.mfa_sessions import MfaSessionIssuer
    from app.modules.access.mfa_verification import MfaVerifier
    from app.modules.access.passwords import hash_password

    state = dict(administration)
    password = SecretStr(secrets.token_urlsafe(32))
    identity = uuid4()
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, mfa_state["user"])
        user.password_hash = hash_password(password)
        email = user.email
        session.add(
            TenantMembership(
                id=identity, tenant_id=state["tenant"], user_id=user.id, role="admin"
            )
        )
    for module in (authentication, mfa_pending, mfa_sessions, sessions):
        for name in ("_now", "_database_now"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda session: mfa_state["now"])
    binding = SecretStr(secrets.token_urlsafe(32))
    try:
        source = Authenticator(state["runtime"]).begin_mfa_login(
            authentication.Credentials(email=email, password=password),
            client_ip=ip_address("127.0.0.1"),
            browser_binding=binding,
        )
        proof = MfaVerifier(
            state["runtime"], keyring=mfa_state["keyring"]
        ).verify_login_totp(
            token=source.token,
            browser_binding=binding,
            code=SecretStr(
                pyotp.TOTP(mfa_state["seed"].base32().get_secret_value()).at(
                    mfa_state["now"]
                )
            ),
            client_ip=ip_address("127.0.0.1"),
        )
        assert proof is not None
        issued = MfaSessionIssuer(state["runtime"]).finish(
            proof_token=proof.token, browser_binding=binding
        )
        assert issued is not None
        state["target"], state["target_tokens"] = identity, (issued.token,)
        yield state
    finally:
        with state["owner"].begin() as connection:
            cleanup_identity(connection, [mfa_state["user"]])
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id == identity)
            )


def change(state, *, target=None, role="mechanic", active=True, token=None):
    return state["service"].change_membership(
        token if token is not None else state["actor_token"],
        membership_id=target if target is not None else state["target"],
        change=MembershipChange(role=role, is_active=active),
    )


def snapshot(state):
    with Session(state["runtime"]) as session:
        return (
            session.execute(
                select(TenantMembership.role, TenantMembership.is_active).where(
                    TenantMembership.id == state["target"]
                )
            ).one(),
            tuple(
                session.scalars(
                    select(SessionRecord.revoked_at).order_by(SessionRecord.id)
                )
            ),
            tuple(
                session.scalars(
                    select(LoginChallenge.finished_at).order_by(LoginChallenge.id)
                )
            ),
            session.scalar(select(func.count()).select_from(AuditEvent)),
        )


@pytest.mark.parametrize(
    ("role", "active"), [("mechanic", True), ("cashier", False), ("admin", True)]
)
def test_change_revokes_all_target_user_sessions_and_global_pending_atomically(
    administration, role, active
):
    state = administration
    result = change(state, role=role, active=active)
    assert (result.membership_id, result.role, result.is_active, result.changed) == (
        state["target"],
        MembershipRole(role),
        active,
        True,
    )
    with Session(state["runtime"]) as session, session.begin():
        for token in state["target_tokens"]:
            with pytest.raises(AccessDenied):
                resolve_session(session, token)
        with pytest.raises(AccessDenied):
            resolve_session(session, state["other_token"])
        assert (
            resolve_session(session, state["actor_token"]).role == MembershipRole.ADMIN
        )
        pending = session.scalar(select(LoginChallenge))
        assert pending.finished_at >= pending.created_at
        audit = session.scalar(select(AuditEvent))
        assert (
            audit.tenant_id,
            audit.actor_membership_id,
            audit.target_membership_id,
        ) == (state["tenant"], state["membership"], state["target"])
        assert (
            audit.action,
            audit.before_role,
            audit.after_role,
            audit.before_active,
            audit.after_active,
        ) == ("membership_changed", "cashier", role, True, active)
        assert audit.created_at.utcoffset().total_seconds() == 0


def test_noop_preserves_tokens_pending_and_audit(administration):
    before = snapshot(administration)
    assert not change(administration, role="cashier").changed
    assert snapshot(administration) == before


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_non_admin_cannot_change_even_own_membership(administration, role):
    state = administration
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    before = snapshot(state)
    with pytest.raises(AccessDenied, match="^Access denied$"):
        change(state, target=state["membership"], role="admin")
    assert snapshot(state) == before


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "malformed",
        "revoked",
        "expired",
        "idle",
        "user",
        "membership",
        "tenant",
    ],
)
def test_invalid_current_identity_denied_without_effects(administration, invalid):
    state = administration
    token = state["actor_token"]
    with state["owner"].begin() as connection:
        if invalid in {"user", "membership", "tenant"}:
            model, key = {
                "user": (User, "user"),
                "membership": (TenantMembership, "membership"),
                "tenant": (Tenant, "tenant"),
            }[invalid]
            connection.execute(
                update(model).where(model.id == state[key]).values(is_active=False)
            )
        elif invalid in {"revoked", "expired", "idle"}:
            values = {
                "revoked": "revoked_at = clock_timestamp()",
                "expired": (
                    "created_at = clock_timestamp() - interval '9 hours', "
                    "last_seen_at = clock_timestamp() - interval '9 hours', "
                    "expires_at = clock_timestamp() - interval '1 hour'"
                ),
                "idle": (
                    "created_at = clock_timestamp() - interval '1 hour', "
                    "last_seen_at = clock_timestamp() - interval '31 minutes', "
                    "expires_at = clock_timestamp() + interval '7 hours'"
                ),
            }
            # clock_timestamp values differ: use one CTE timestamp for exact CHECK.
            clause = values[invalid].replace("clock_timestamp()", "ts.now")
            clause = (
                "reauthenticated_at=NULL, reauthentication_fingerprint=NULL, " + clause
            )
            connection.execute(
                text(
                    "WITH ts AS (SELECT clock_timestamp() AS now) "
                    f"UPDATE sessions SET {clause} FROM ts "
                    "WHERE membership_id = :member"
                ),
                {"member": state["membership"]},
            )
    if invalid == "missing":
        token = SecretStr(secrets.token_urlsafe(32))
    elif invalid == "malformed":
        token = SecretStr("invalid")
    before = snapshot(state)
    with pytest.raises(AccessDenied):
        change(state, token=token)
    assert snapshot(state) == before


@pytest.mark.parametrize("target", ["foreign_membership", "missing"])
def test_cross_tenant_or_missing_target_denied(administration, target):
    state = administration
    before = snapshot(state)
    with pytest.raises(AccessDenied, match="^Access denied$"):
        change(state, target=uuid4() if target == "missing" else state[target])
    assert snapshot(state) == before


@pytest.mark.parametrize(("role", "active"), [("read_only", True), ("admin", False)])
@pytest.mark.parametrize(
    "unusable_other",
    ["non_admin", "inactive_membership", "inactive_user", "pending_mfa"],
)
def test_last_admin_cannot_be_removed(administration, role, active, unusable_other):
    state = administration
    with state["owner"].begin() as connection:
        if unusable_other != "non_admin":
            connection.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["target"])
                .values(role="admin", is_active=unusable_other != "inactive_membership")
            )
        if unusable_other == "inactive_user":
            connection.execute(
                update(User).where(User.id == state["foreign"]).values(is_active=False)
            )
    before = snapshot(state)
    with pytest.raises(LastAdministratorRequired):
        change(state, target=state["membership"], role=role, active=active)
    assert snapshot(state) == before
    with Session(state["runtime"]) as session:
        actor = session.get(TenantMembership, state["membership"])
        assert actor.role == "admin" and actor.is_active


def test_self_demotion_allowed_with_confirmed_second_admin_and_revokes_actor(
    administration, mfa_state
):
    state = administration
    extra_id = uuid4()
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            TenantMembership(
                id=extra_id,
                tenant_id=state["tenant"],
                user_id=mfa_state["user"],
                role="admin",
            )
        )
    try:
        assert change(state, target=state["membership"], role="read_only").changed
        with (
            Session(state["runtime"]) as session,
            session.begin(),
            pytest.raises(AccessDenied),
        ):
            resolve_session(session, state["actor_token"])
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id == extra_id)
            )


def test_reactivation_requires_new_login_and_records_transition(administration):
    state = administration
    change(state, active=False)
    assert change(state, role="cashier", active=True).changed
    with Session(state["runtime"]) as session, session.begin():
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2
        for token in state["target_tokens"]:
            with pytest.raises(AccessDenied):
                resolve_session(session, token)


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_injected_failure_rolls_back_change_revocations_and_audit(
    administration, failure
):
    state = administration
    before = snapshot(state)

    def fail_audit(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO audit_events"):
            raise DBAPIError("synthetic", {}, RuntimeError("private failure"))

    def fail_commit(_session):
        raise DBAPIError("synthetic", {}, RuntimeError("private commit failure"))

    target = (
        state["runtime"] if failure == "audit" else state["service"]._factory.class_
    )
    name = "before_cursor_execute" if failure == "audit" else "before_commit"
    callback = fail_audit if failure == "audit" else fail_commit
    event.listen(target, name, callback)
    try:
        with pytest.raises(
            AdministrationUnavailable, match="^Administration unavailable$"
        ) as caught:
            change(state)
        assert caught.value.__suppress_context__
    finally:
        event.remove(target, name, callback)
    assert snapshot(state) == before


def test_audit_is_insert_only_for_runtime_role(administration):
    state = administration
    change(state)
    for statement in (
        "UPDATE audit_events SET action = action",
        "DELETE FROM audit_events",
    ):
        with (
            state["runtime"].connect() as connection,
            pytest.raises(DBAPIError) as caught,
        ):
            connection.execute(text(statement))
        assert caught.value.orig.sqlstate == "42501"
    with state["runtime"].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM audit_events")) == 1


@pytest.mark.parametrize("field", ["actor_membership_id", "target_membership_id"])
def test_audit_composite_fk_rejects_foreign_membership(administration, field):
    state = administration
    change(state)
    with state["owner"].begin() as connection, pytest.raises(IntegrityError) as caught:
        connection.execute(
            update(AuditEvent).values({field: state["foreign_membership"]})
        )
    assert caught.value.orig.sqlstate == "23503"


def wait_blocked(owner, pid):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with owner.connect() as connection:
            if connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE pid = :pid AND NOT granted)"
                ),
                {"pid": pid},
            ):
                return
        time.sleep(0.01)
    pytest.fail("Contender did not actually wait on a PostgreSQL lock")


def test_concurrent_self_demotions_retain_one_active_admin(confirmed_other_admin):
    state = confirmed_other_admin
    reached, release, second_started = Event(), Event(), Event()
    waiter = {}

    def pause_first(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO audit_events"):
            reached.set()
            assert release.wait(5)

    def note_waiter(connection, _cursor, statement, _params, _context, _many):
        if (
            reached.is_set()
            and "FROM tenants" in statement
            and "FOR UPDATE" in statement
        ):
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            second_started.set()

    def second():
        with pytest.raises(LastAdministratorRequired):
            change(
                state,
                target=state["target"],
                token=state["target_tokens"][0],
                role="read_only",
            )

    event.listen(state["runtime"], "before_cursor_execute", pause_first)
    event.listen(state["runtime"], "before_cursor_execute", note_waiter)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                change, state, target=state["membership"], role="read_only"
            )
            try:
                assert reached.wait(3)
                other = pool.submit(second)
                assert second_started.wait(2)
                wait_blocked(state["owner"], waiter["pid"])
            finally:
                release.set()
            assert first.result(timeout=5).changed
            other.result(timeout=5)
    finally:
        release.set()
        event.remove(state["runtime"], "before_cursor_execute", pause_first)
        event.remove(state["runtime"], "before_cursor_execute", note_waiter)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TenantMembership)
                .where(
                    TenantMembership.tenant_id == state["tenant"],
                    TenantMembership.role == "admin",
                    TenantMembership.is_active.is_(True),
                )
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_change_waits_for_new_session_and_challenge_then_revokes_both(administration):
    state = administration
    started, waiter = Event(), {}

    def note_waiter(connection, _cursor, statement, _params, _context, _many):
        if "FROM tenants" in statement and "FOR UPDATE" in statement:
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    event.listen(state["runtime"], "before_cursor_execute", note_waiter)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(state["runtime"]) as holder, holder.begin():
                new = issue_session(
                    holder, user_id=state["foreign"], tenant_id=state["tenant"]
                )
                _issue_challenge(
                    holder,
                    user=_lock_user(holder, state["foreign"]),
                    binding_hash=_binding_digest(state["binding"]),
                    choices=(MembershipChoice(state["target"], "Synthetic"),),
                )
                future = pool.submit(change, state, active=False)
                assert started.wait(2)
                wait_blocked(state["owner"], waiter["pid"])
            assert future.result(timeout=5).changed
    finally:
        event.remove(state["runtime"], "before_cursor_execute", note_waiter)
    with Session(state["runtime"]) as session, session.begin():
        with pytest.raises(AccessDenied):
            resolve_session(session, new.token)
        assert all(
            record.finished_at is not None
            for record in session.scalars(select(LoginChallenge))
        )


@pytest.mark.parametrize("operation", ["issue", "select"])
def test_access_waits_for_deactivation_and_cannot_emit_session(
    administration, operation
):
    state = administration
    reached, release, started, waiter = Event(), Event(), Event(), {}
    authenticator = Authenticator(state["runtime"])

    def pause_change(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO audit_events"):
            reached.set()
            assert release.wait(5)

    def note_waiter(connection, _cursor, statement, _params, _context, _many):
        if reached.is_set() and "FROM users" in statement and "FOR UPDATE" in statement:
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    def access():
        if operation == "select":
            assert (
                authenticator.select_membership(
                    challenge_token=state["pending_token"],
                    browser_binding=state["binding"],
                    membership_id=state["target"],
                )
                is None
            )
        else:
            with (
                Session(state["runtime"]) as session,
                session.begin(),
                pytest.raises(AccessDenied),
            ):
                issue_session(
                    session, user_id=state["foreign"], tenant_id=state["tenant"]
                )

    event.listen(state["runtime"], "before_cursor_execute", pause_change)
    event.listen(state["runtime"], "before_cursor_execute", note_waiter)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(change, state, active=False)
            try:
                assert reached.wait(3)
                other = pool.submit(access)
                assert started.wait(2)
                wait_blocked(state["owner"], waiter["pid"])
            finally:
                release.set()
            assert first.result(timeout=5).changed
            other.result(timeout=5)
    finally:
        release.set()
        event.remove(state["runtime"], "before_cursor_execute", pause_change)
        event.remove(state["runtime"], "before_cursor_execute", note_waiter)
    with Session(state["runtime"]) as session:
        assert session.scalar(select(func.count()).select_from(SessionRecord)) == 4


def test_admin_privilege_is_rechecked_after_waiting_for_user_lock(administration):
    state = administration
    started, waiter = Event(), {}

    def note_waiter(connection, _cursor, statement, _params, _context, _many):
        if "FROM tenants" in statement and "FOR UPDATE" in statement:
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    def attempt():
        with pytest.raises(AccessDenied):
            change(state)

    before = snapshot(state)
    event.listen(state["runtime"], "before_cursor_execute", note_waiter)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(state["runtime"]) as holder, holder.begin():
                _lock_user(holder, state["user"])
                # Controlled setup to prove fresh authorization after a wait.
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
        event.remove(state["runtime"], "before_cursor_execute", note_waiter)
    assert snapshot(state) == before


def test_crossed_actors_lock_in_one_order_and_reject_revoked_waiter(administration):
    state = administration
    with Session(state["runtime"]) as session, session.begin():
        extra = TenantMembership(
            user_id=state["user"], tenant_id=state["other"], role="cashier"
        )
        session.add(extra)
        session.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["foreign_membership"])
            .values(role="admin")
        )
        session.flush()
        extra_id = extra.id
    reached, release, started, waiter = Event(), Event(), Event(), {}

    def observe(connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO audit_events"):
            if not reached.is_set():
                reached.set()
                assert release.wait(5)
        elif (
            reached.is_set()
            and "FROM tenants" in statement
            and "FOR UPDATE" in statement
        ):
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    event.listen(state["runtime"], "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(change, state)
            try:
                assert reached.wait(3)
                other = pool.submit(
                    change, state, target=extra_id, token=state["other_token"]
                )
                assert started.wait(2)
                wait_blocked(state["owner"], waiter["pid"])
            finally:
                release.set()
            assert first.result(timeout=5).changed
            with pytest.raises(AccessDenied):
                other.result(timeout=5)
    finally:
        release.set()
        event.remove(state["runtime"], "before_cursor_execute", observe)
    with Session(state["runtime"]) as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
