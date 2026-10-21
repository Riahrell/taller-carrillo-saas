"""Mailbox proof, pending membership, authorization and transactional approval."""

import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_catalog_service import catalog_state as catalog_state
from test_employee_invitations import invitation_web as invitation_web
from test_employee_invitations import invitations as invitations
from test_inventory_http import inventory_web as inventory_web

from app.modules.access.administration import MembershipListQuery
from app.modules.access.authentication import AuthenticationUnavailable
from app.modules.access.email_outbox import MailProtection, MailPurpose
from app.modules.access.employee_invitations import InvitationInput
from app.modules.access.employee_registration import (
    EmployeeRegistrations,
    RegistrationDenied,
    RegistrationInput,
    RegistrationRate,
)
from app.modules.access.employee_registration_models import (
    EmployeeApproval,
    EmployeeRegistration,
)
from app.modules.access.mail_delivery import TransportOutcome
from app.modules.access.membership import AccessDenied
from app.modules.access.models import AuditEvent, SessionRecord, TenantMembership, User
from app.modules.access.passwords import verify_password

pytestmark = pytest.mark.integration


def protection():
    return MailProtection(
        {"test": SecretBytes(secrets.token_bytes(32))},
        active_key_id="test",
        quota_key=SecretBytes(secrets.token_bytes(32)),
        purpose=MailPurpose.EMPLOYEE_REGISTRATION,
    )


def cleanup(state):
    with state["owner"].begin() as connection:
        users = select(EmployeeRegistration.user_id).where(
            EmployeeRegistration.tenant_id == state["tenant"],
            EmployeeRegistration.user_id.is_not(None),
        )
        identities = list(connection.scalars(users))
        connection.execute(
            delete(EmployeeApproval).where(
                EmployeeApproval.tenant_id == state["tenant"]
            )
        )
        connection.execute(
            delete(EmployeeRegistration).where(
                EmployeeRegistration.tenant_id == state["tenant"]
            )
        )
        if identities:
            connection.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_membership_id.in_(
                        select(TenantMembership.id).where(
                            TenantMembership.user_id.in_(identities)
                        )
                    )
                )
            )
            connection.execute(
                delete(SessionRecord).where(
                    SessionRecord.membership_id.in_(
                        select(TenantMembership.id).where(
                            TenantMembership.user_id.in_(identities)
                        )
                    )
                )
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.user_id.in_(identities))
            )
            connection.execute(delete(User).where(User.id.in_(identities)))


@pytest.fixture
def registering(invitations):
    state = invitations
    state["registration"] = EmployeeRegistrations(state["runtime"], protection())
    state["binding"] = SecretStr(secrets.token_urlsafe(32))
    state["peer"] = ip_address("192.0.2.42")
    state["password"] = SecretStr(secrets.token_urlsafe(24))
    state["email"] = "reg-" + uuid4().hex + "@example.com"
    _, state["invite"], _ = state["invites"].create(
        state["token"],
        data=InvitationInput(email=state["email"]),
        expected_actor=state["membership"],
    )
    try:
        yield state
    finally:
        cleanup(state)


def start(state, **changes):
    data = RegistrationInput(
        **(
            dict(
                email=state["email"],
                invitation=state["invite"],
                password=state["password"],
            )
            | changes
        )
    )
    token, expires = state["registration"].start(data, state["binding"], state["peer"])
    state["registration_token"] = token
    return expires


def delivered_code(state):
    transport = Mock()
    transport.send_message.return_value = TransportOutcome.ACCEPTED
    state["registration"].dispatch_one(transport)
    body = transport.send_message.call_args.args[0].body.get_secret_value()
    return SecretStr(re.search(r"es: ([A-Z0-9]{6})", body)[1])


def verify(state):
    return state["registration"].confirm(
        state["registration_token"],
        state["binding"],
        delivered_code(state),
        state["peer"],
    )


def row(state):
    with Session(state["runtime"]) as session:
        return session.scalar(
            select(EmployeeRegistration).where(
                EmployeeRegistration.tenant_id == state["tenant"]
            )
        )


def test_verify_creates_identity_only_and_approval_grants_read_only_membership(
    registering,
):
    state = registering
    start(state)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(select(User.id).where(User.email == state["email"])) is None
        )
    assert verify(state)
    registered = row(state)
    assert (
        registered.user_id
        and registered.verified_at
        and registered.password_hash is None
    )
    with Session(state["runtime"]) as session:
        user = session.get(User, registered.user_id)
        assert verify_password(state["password"], user.password_hash)
        assert (
            session.scalar(
                select(TenantMembership.id).where(TenantMembership.user_id == user.id)
            )
            is None
        )
    pending, after, actor = state["registration"].pending(
        state["token"], query=MembershipListQuery()
    )
    assert (
        pending[0]["email"] == state["email"]
        and actor == state["membership"]
        and after is None
    )
    membership, _ = state["registration"].approve(
        state["token"],
        registration_id=registered.id,
        expected_actor=state["membership"],
    )
    assert (
        state["registration"].approve(
            state["token"],
            registration_id=registered.id,
            expected_actor=state["membership"],
        )[0]
        == membership
    )
    with Session(state["runtime"]) as session:
        assert session.get(TenantMembership, membership).role == "read_only"
        assert (
            session.scalar(
                select(func.count())
                .select_from(EmployeeApproval)
                .where(EmployeeApproval.registration_id == registered.id)
            )
            == 1
        )
    assert (
        state["registration"].status(state["registration_token"], state["binding"])
        == "approved"
    )


def test_wrong_email_browser_expired_or_revoked_invitation_cannot_register(registering):
    state = registering
    with pytest.raises(RegistrationDenied):
        start(state, email="different@example.com")
    start(state)
    with pytest.raises(RegistrationDenied):
        state["registration"].confirm(
            state["registration_token"],
            SecretStr(secrets.token_urlsafe(32)),
            SecretStr("ABC123"),
            state["peer"],
        )
    registered = row(state)
    with state["owner"].begin() as connection:
        connection.execute(
            update(EmployeeRegistration)
            .where(EmployeeRegistration.id == registered.id)
            .values(
                created_at=registered.created_at - timedelta(minutes=11),
                expires_at=registered.expires_at - timedelta(minutes=11),
            )
        )
    with pytest.raises(RegistrationDenied):
        state["registration"].confirm(
            state["registration_token"],
            state["binding"],
            SecretStr("ABC123"),
            state["peer"],
        )
    state["invites"].revoke(
        state["token"],
        invitation_id=registered.invitation_id,
        expected_actor=state["membership"],
    )
    with pytest.raises(RegistrationDenied):
        start(state)


def test_five_wrong_codes_persist_across_resend_and_block_verification(registering):
    state = registering
    start(state)
    code = delivered_code(state)
    wrong = SecretStr("000000")
    for _ in range(5):
        assert not state["registration"].confirm(
            state["registration_token"], state["binding"], wrong, state["peer"]
        )
    assert row(state).failures == 5 and row(state).code_hash is None
    with pytest.raises(RegistrationDenied):
        state["registration"].confirm(
            state["registration_token"], state["binding"], code, state["peer"]
        )
    with pytest.raises(RegistrationDenied):
        start(state)


def test_resend_cooldown_does_not_rotate_or_consume_existing_code(registering):
    state = registering
    start(state)
    code = delivered_code(state)
    with pytest.raises(RegistrationRate):
        state["registration"].resend(
            state["registration_token"], state["binding"], state["peer"]
        )
    assert state["registration"].confirm(
        state["registration_token"], state["binding"], code, state["peer"]
    )


def test_approval_rejects_unverified_revoked_unknown_and_non_admin(registering):
    state = registering
    start(state)
    registered = row(state)
    with pytest.raises(AccessDenied):
        state["registration"].approve(
            state["token"],
            registration_id=registered.id,
            expected_actor=state["membership"],
        )
    assert verify(state)
    with pytest.raises(AccessDenied):
        state["registration"].approve(
            state["token"], registration_id=uuid4(), expected_actor=state["membership"]
        )
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role="read_only")
        )
    with pytest.raises(AccessDenied):
        state["registration"].approve(
            state["token"],
            registration_id=registered.id,
            expected_actor=state["membership"],
        )
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role="admin")
        )
    state["invites"].revoke(
        state["token"],
        invitation_id=registered.invitation_id,
        expected_actor=state["membership"],
    )
    with pytest.raises(AccessDenied):
        state["registration"].approve(
            state["token"],
            registration_id=registered.id,
            expected_actor=state["membership"],
        )


def test_concurrent_approval_creates_one_membership(registering):
    state = registering
    start(state)
    assert verify(state)
    identity = row(state).id

    def approve(_):
        return state["registration"].approve(
            state["token"], registration_id=identity, expected_actor=state["membership"]
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, range(2)))
    assert results[0] == results[1]


def test_failure_during_approval_audit_rolls_back_membership(registering):
    state = registering
    start(state)
    assert verify(state)

    def fail(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO audit_events"):
            raise SQLAlchemyError("injected")

    event.listen(state["runtime"], "before_cursor_execute", fail)
    try:
        with pytest.raises(AuthenticationUnavailable):
            state["registration"].approve(
                state["token"],
                registration_id=row(state).id,
                expected_actor=state["membership"],
            )
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(TenantMembership.id).where(
                    TenantMembership.user_id == row(state).user_id
                )
            )
            is None
        )


def test_existing_global_email_is_never_attached_or_reset(registering):
    state = registering
    original = state["email"]
    with Session(state["runtime"]) as session:
        own = session.get(User, state["user"])
        existing, original_hash = own.email, own.password_hash
    _, invite, _ = state["invites"].create(
        state["token"],
        data=InvitationInput(email=existing),
        expected_actor=state["membership"],
    )
    state["invite"], state["email"] = invite, existing
    start(state)
    with pytest.raises(RegistrationDenied):
        verify(state)
    with Session(state["runtime"]) as session:
        assert session.get(User, state["user"]).password_hash == original_hash
    state["email"] = original


def test_http_registration_cookie_and_approval_then_normal_login(invitation_web):
    from fastapi.testclient import TestClient

    admin, state, policy = invitation_web
    state = dict(state)
    state["email"] = "http-reg-" + uuid4().hex + "@example.com"
    password = secrets.token_urlsafe(24)
    read = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}
    write = {
        "Origin": policy.origin,
        "X-CSRF-Token": admin.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
    }
    created = admin.post(
        "/auth/employee-invitations", json={"email": state["email"]}, headers=write
    )
    assert created.status_code == 201
    employee = TestClient(
        admin.app,
        base_url=policy.origin,
        client=("127.0.0.2", 12346),
        headers={"X-Real-IP": "127.0.0.1"},
    )
    try:
        assert employee.get("/auth/csrf", headers=read).status_code == 204
        employee_write = {
            "Origin": policy.origin,
            "X-CSRF-Token": employee.cookies[policy.csrf_cookie],
        }
        payload = dict(
            email=state["email"], password=password, invitation=created.json()["code"]
        )
        assert (
            employee.post("/auth/employee-registration", json=payload).status_code
            == 403
        )
        assert (
            employee.post(
                "/auth/employee-registration",
                json=payload | {"role": "admin"},
                headers=employee_write,
            ).status_code
            == 422
        )
        started = employee.post(
            "/auth/employee-registration", json=payload, headers=employee_write
        )
        assert started.status_code == 202 and started.json() == {
            "status": "awaiting_code"
        }
        assert (
            "HttpOnly" in started.headers["set-cookie"]
            and "SameSite=strict" in started.headers["set-cookie"]
        )
        mail = Mock()
        mail.send_message.return_value = TransportOutcome.ACCEPTED
        admin.app.state.identity.employee_registration.dispatch_one(mail)
        code = re.search(
            r"es: ([A-Z0-9]{6})",
            mail.send_message.call_args.args[0].body.get_secret_value(),
        )[1]
        confirmed = employee.post(
            "/auth/employee-registration/confirm",
            json={"code": code},
            headers=employee_write,
        )
        assert confirmed.status_code == 200 and confirmed.json() == {
            "status": "pending_approval"
        }
        assert policy.session_cookie not in employee.cookies
        pending = admin.get("/auth/employee-registrations", headers=read)
        assert (
            pending.status_code == 200
            and pending.json()["items"][0]["email"] == state["email"]
        )
        identity = pending.json()["items"][0]["id"]
        approved = admin.post(
            f"/auth/employee-registrations/{identity}/approve", json={}, headers=write
        )
        assert approved.status_code == 200 and approved.json()["role"] == "read_only"
        assert employee.get(
            "/auth/employee-registration/status", headers=read
        ).json() == {"status": "approved"}
        login = employee.post(
            "/auth/login",
            json={"email": state["email"], "password": password},
            headers=employee_write,
        )
        assert login.status_code == 202 and login.json()["status"] == "email_code"
        assert (
            employee.post(
                "/auth/email-login/request", json={}, headers=employee_write
            ).status_code
            == 204
        )
        mail.reset_mock()
        admin.app.state.identity.email_login.dispatch_one(mail)
        login_code = re.search(
            r"es: ([A-Z0-9]{6})",
            mail.send_message.call_args.args[0].body.get_secret_value(),
        )[1]
        completed = employee.post(
            "/auth/email-login/confirm",
            json={"code": login_code},
            headers=employee_write,
        )
        assert completed.status_code == 200
        assert (
            employee.get(
                f"/inventory/products/{state['product']}/balance", headers=read
            ).status_code
            == 403
        )
    finally:
        employee.close()
        # Remove test-created login evidence first, using guarded owner cleanup.
        with state["owner"].begin() as connection:
            from identity_cleanup import cleanup_identity

            users = select(User.id).where(User.email == state["email"])
            identities = list(connection.scalars(users))
            cleanup_identity(connection, users)
            connection.execute(
                delete(TenantMembership).where(TenantMembership.user_id.in_(identities))
            )
            connection.execute(delete(User).where(User.id.in_(identities)))
        cleanup(state)
