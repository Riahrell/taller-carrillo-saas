"""Invitation isolation, secrets, revocation and atomic evidence."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_catalog_service import catalog_state as catalog_state
from test_inventory_http import inventory_web as inventory_web

from app.modules.access.administration import (
    AdministrationUnavailable,
    MembershipListQuery,
)
from app.modules.access.employee_invitation_models import (
    EmployeeInvitation,
    EmployeeInvitationEvent,
)
from app.modules.access.employee_invitations import (
    EmployeeInvitations,
    InvitationConflict,
    InvitationInput,
)
from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership, User
from app.modules.access.sessions import revoke_session

pytestmark = pytest.mark.integration


def cleanup(state):
    with state["owner"].begin() as connection:
        for model in (EmployeeInvitationEvent, EmployeeInvitation):
            connection.execute(
                delete(model).where(
                    model.tenant_id.in_([state["tenant"], state["other"]])
                )
            )


@pytest.fixture
def invitations(catalog_state):
    state = catalog_state
    state["invites"] = EmployeeInvitations(state["runtime"])
    try:
        yield state
    finally:
        cleanup(state)


@pytest.fixture
def invitation_web(inventory_web):
    client, state, policy = inventory_web
    try:
        yield client, state, policy
    finally:
        cleanup(state)


def create(state, email="employee@example.com", actor=None):
    return state["invites"].create(
        state["token"],
        data=InvitationInput(email=email),
        expected_actor=actor or state["membership"],
    )


def test_email_bound_secret_only_once_and_no_identity_or_access_created(invitations):
    state = invitations
    with Session(state["runtime"]) as session:
        identities = session.scalar(select(func.count()).select_from(User))
    view, secret, actor = create(state, " Employee@Example.com ")
    assert view.email == "employee@example.com" and view.status == "pending"
    assert view.expires_at - view.created_at == timedelta(hours=24)
    assert actor == state["membership"]
    assert len(secret.get_secret_value()) == 43
    with Session(state["runtime"]) as session:
        row = session.get(EmployeeInvitation, view.id)
        assert (
            row.token_hash
            == hashlib.sha256(secret.get_secret_value().encode()).hexdigest()
        )
        assert secret.get_secret_value() not in repr(row.__dict__)
        assert session.scalar(select(func.count()).select_from(User)) == identities
        evidence = session.scalar(
            select(EmployeeInvitationEvent).where(
                EmployeeInvitationEvent.invitation_id == view.id
            )
        )
        assert evidence.action == "CREATED" and evidence.actor_membership_id == actor
    items, after, _ = state["invites"].list(state["token"], query=MembershipListQuery())
    assert items == [view] and after is None
    assert (
        "token" not in items[0].model_dump_json()
        and "hash" not in items[0].model_dump_json()
    )


def test_duplicate_and_expired_invitation_require_explicit_revoke(invitations):
    state = invitations
    original, _, _ = create(state)
    with pytest.raises(InvitationConflict):
        create(state)
    with state["owner"].begin() as connection:
        connection.execute(
            update(EmployeeInvitation)
            .where(EmployeeInvitation.id == original.id)
            .values(
                created_at=EmployeeInvitation.created_at - timedelta(days=2),
                expires_at=EmployeeInvitation.expires_at - timedelta(days=2),
            )
        )
    items, _, _ = state["invites"].list(state["token"], query=MembershipListQuery())
    assert items[0].status == "expired"
    with pytest.raises(InvitationConflict):
        create(state)
    state["invites"].revoke(
        state["token"], invitation_id=original.id, expected_actor=state["membership"]
    )
    new, secret, _ = create(state)
    assert new.id != original.id and secret


def test_revoke_idempotent_and_does_not_revoke_memberships(invitations):
    state = invitations
    invitation, _, _ = create(state)
    for _ in range(2):
        result, _ = state["invites"].revoke(
            state["token"],
            invitation_id=invitation.id,
            expected_actor=state["membership"],
        )
        assert result.status == "revoked"
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(EmployeeInvitationEvent)
                .where(EmployeeInvitationEvent.invitation_id == invitation.id)
            )
            == 2
        )
        assert session.get(TenantMembership, state["membership"]).is_active


def test_role_expected_actor_session_and_foreign_invitation_fail_closed(invitations):
    state = invitations
    invitation, _, _ = create(state)
    with pytest.raises(AccessDenied):
        create(state, actor=uuid4())
    foreign_id = uuid4()
    with state["owner"].begin() as connection:
        connection.execute(
            EmployeeInvitation.__table__.insert().values(
                id=foreign_id,
                tenant_id=state["other"],
                actor_membership_id=state["foreign_membership"],
                email="foreign@example.com",
                token_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
                created_at=invitation.created_at,
                expires_at=invitation.expires_at,
            )
        )
    items, _, _ = state["invites"].list(state["token"], query=MembershipListQuery())
    assert [item.id for item in items] == [invitation.id]
    with pytest.raises(AccessDenied):
        state["invites"].revoke(
            state["token"], invitation_id=foreign_id, expected_actor=state["membership"]
        )
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role="read_only")
        )
    with pytest.raises(AccessDenied):
        create(state)
    with pytest.raises(AccessDenied):
        state["invites"].list(state["token"], query=MembershipListQuery())
    with pytest.raises(AccessDenied):
        state["invites"].revoke(
            state["token"],
            invitation_id=invitation.id,
            expected_actor=state["membership"],
        )
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role="admin")
        )
    with Session(state["runtime"]) as session, session.begin():
        revoke_session(session, state["token"])
    with pytest.raises(AccessDenied):
        create(state)


@pytest.mark.parametrize(
    "statement",
    ["INSERT INTO employee_invitation_events", "INSERT INTO employee_invitations"],
)
def test_failure_never_leaves_an_invitation_without_evidence(invitations, statement):
    state = invitations

    def fail(_connection, _cursor, query, _params, _context, _many):
        if query.startswith(statement):
            raise SQLAlchemyError("injected")

    event.listen(state["runtime"], "before_cursor_execute", fail)
    try:
        with pytest.raises(AdministrationUnavailable):
            create(state)
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    with Session(state["runtime"]) as session:
        assert not session.scalar(
            select(EmployeeInvitation.id).where(
                EmployeeInvitation.tenant_id == state["tenant"]
            )
        )


def test_concurrent_duplicate_creation_has_one_winner(invitations):
    state = invitations

    def attempt(_):
        try:
            return create(state)[0]
        except InvitationConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1


def test_pagination_and_evidence_are_private_and_immutable(invitations):
    state = invitations
    created = [
        create(state, f"employee-{index}@example.com")[0].id for index in range(3)
    ]
    page, after, _ = state["invites"].list(
        state["token"], query=MembershipListQuery(limit=2)
    )
    rest, final, _ = state["invites"].list(
        state["token"], query=MembershipListQuery(limit=2, after=after)
    )
    assert [item.id for item in page + rest] == sorted(created) and final is None
    for statement in (
        "UPDATE employee_invitation_events SET action='REVOKED'",
        "DELETE FROM employee_invitation_events",
        "TRUNCATE employee_invitation_events",
    ):
        with (
            state["runtime"].begin() as connection,
            pytest.raises(SQLAlchemyError),
            connection.begin_nested(),
        ):
            connection.execute(text(statement))


def test_http_private_routes_csrf_strict_input_and_no_secret_reads(invitation_web):
    client, state, policy = invitation_web
    read = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}
    write = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
    }
    endpoint = "/auth/employee-invitations"
    assert (
        client.post(endpoint, json={"email": "worker@example.com"}).status_code == 403
    )
    assert (
        client.post(
            endpoint,
            json={"email": "worker@example.com", "role": "admin"},
            headers=write,
        ).status_code
        == 422
    )
    created = client.post(endpoint, json={"email": "worker@example.com"}, headers=write)
    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "no-store"
    assert created.headers["X-Access-Membership"] == str(state["membership"])
    body = created.json()
    assert set(body) == {"invitation", "code"} and len(body["code"]) == 43
    page = client.get(endpoint, headers=read)
    assert (
        page.status_code == 200
        and body["code"] not in page.text
        and "token_hash" not in page.text
    )
    assert client.get(endpoint + "?limit=1&limit=2", headers=read).status_code == 422
    assert (
        client.get(
            endpoint + "?tenant_id=" + str(state["other"]), headers=read
        ).status_code
        == 422
    )
    revoked = client.post(
        endpoint + "/" + body["invitation"]["id"] + "/revoke", json={}, headers=write
    )
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert (
        client.post(
            endpoint + "/" + str(uuid4()) + "/revoke", json={}, headers=write
        ).status_code
        == 403
    )


def test_runtime_cannot_rewrite_invitation_identity_and_downgrade_keeps_evidence(
    invitations,
):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext

    state = invitations
    invitation, _, _ = create(state)
    with (
        state["runtime"].begin() as connection,
        pytest.raises(SQLAlchemyError),
        connection.begin_nested(),
    ):
        connection.execute(
            update(EmployeeInvitation)
            .where(EmployeeInvitation.id == invitation.id)
            .values(email="tampered@example.com")
        )
    for statement in (
        "DELETE FROM employee_invitations",
        "TRUNCATE employee_invitations",
    ):
        with (
            state["runtime"].begin() as connection,
            pytest.raises(SQLAlchemyError),
            connection.begin_nested(),
        ):
            connection.execute(text(statement))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with state["owner"].begin() as connection:
        config.attributes["connection"] = connection
        with (
            pytest.raises(RuntimeError, match="Employee invitation evidence"),
            connection.begin_nested(),
        ):
            command.downgrade(config, "0042_stock_reversals")
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0043_employee_invitations"
        )


def test_revocation_audit_failure_rolls_back_revoked_timestamp(invitations):
    state = invitations
    invitation, _, _ = create(state)

    def fail(_connection, _cursor, query, _params, _context, _many):
        if query.startswith("INSERT INTO employee_invitation_events"):
            raise SQLAlchemyError("injected")

    event.listen(state["runtime"], "before_cursor_execute", fail)
    try:
        with pytest.raises(AdministrationUnavailable):
            state["invites"].revoke(
                state["token"],
                invitation_id=invitation.id,
                expected_actor=state["membership"],
            )
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    with Session(state["runtime"]) as session:
        assert session.get(EmployeeInvitation, invitation.id).revoked_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(EmployeeInvitationEvent)
                .where(EmployeeInvitationEvent.invitation_id == invitation.id)
            )
            == 1
        )


def test_creation_requires_current_identity_confirmation(invitations):
    from app.modules.access.reauthentication import RecentAuthenticationRequired
    from app.modules.access.sessions import issue_session

    state = invitations
    with Session(state["runtime"]) as session, session.begin():
        unconfirmed = issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        ).token
    with pytest.raises(RecentAuthenticationRequired):
        state["invites"].create(
            unconfirmed,
            data=InvitationInput(email="not-approved@example.com"),
            expected_actor=state["membership"],
        )
