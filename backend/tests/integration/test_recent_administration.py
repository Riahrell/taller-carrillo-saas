"""Recent confirmation enforced by real administrative transactions and HTTP."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from threading import Event

import pytest
from fastapi.testclient import TestClient
from identity_http_fixture import arrange_factor, login_with_legacy_factor, next_factor
from pydantic import SecretStr
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access import administration, reauthentication
from app.modules.access.administration import MembershipChange
from app.modules.access.models import (
    AuditEvent,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password
from app.modules.access.reauthentication import RecentAuthenticationRequired

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def web(new_identity_scope, monkeypatch):
    state = new_identity_scope
    with Session(state["runtime"]) as session, session.begin():
        target = TenantMembership(
            tenant_id=state["tenant"], user_id=state["foreign"], role="cashier"
        )
        session.add(target)
        session.flush()
        state["target"] = target.id
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    with TestClient(
        create_app(state["settings"]),
        base_url=state["settings"].public_origin,
        client=("127.0.0.2", 12345),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as client:
        policy = client.app.state.http_security
        arrange_factor(state, client.app, monkeypatch)
        assert client.get("/auth/csrf", headers=READ).status_code == 204
        assert login_with_legacy_factor(client, state, policy).status_code == 200
        state["target_etag"] = client.get(
            f"/auth/memberships/{state['target']}", headers=READ
        ).headers["etag"]
        state["http_now"] += timedelta(minutes=5)
        yield client, state, policy


def headers(web):
    client, state, policy = web
    return {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
    }


def confirm(web):
    client, state, policy = web
    old = client.cookies[policy.session_cookie]
    response = client.post(
        "/auth/mfa/reauthenticate",
        json={
            "password": state["password"].get_secret_value(),
            "code": next_factor(state),
        },
        headers=headers(web),
    )
    assert response.status_code == 204
    assert old != client.cookies[policy.session_cookie]


def write(web, method):
    client, state, _ = web
    if method != "create":
        path = f"/auth/memberships/{state['target']}"
        tag = client.get(path, headers=READ).headers.get("etag", state["target_etag"])
    with Session(state["runtime"]) as session:
        activity = session.scalar(
            select(SessionRecord.last_seen_at).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
    if method == "create":
        response = client.post(
            "/auth/memberships",
            json={
                "email": state["prefix"] + "staff@example.com",
                "password": secrets.token_urlsafe(24),
                "role": "mechanic",
            },
            headers=headers(web),
        )
    else:
        response = client.patch(
            path,
            json={
                "role": "cashier" if method == "noop" else "mechanic",
                "is_active": True,
            },
            headers=headers(web) | {"If-Match": tag},
        )
    if response.status_code == 428:
        with Session(state["runtime"]) as session:
            assert (
                session.scalar(
                    select(SessionRecord.last_seen_at).where(
                        SessionRecord.membership_id == state["membership"]
                    )
                )
                == activity
            )
    return response


def snapshot(state):
    with Session(state["runtime"]) as session:
        return (
            tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (User, TenantMembership, AuditEvent)
            ),
            tuple(
                session.execute(
                    select(
                        TenantMembership.id,
                        TenantMembership.role,
                        TenantMembership.is_active,
                    ).order_by(TenantMembership.id)
                )
            ),
            tuple(
                session.execute(
                    select(
                        SessionRecord.id,
                        SessionRecord.token_hash,
                        SessionRecord.revoked_at,
                        SessionRecord.reauthenticated_at,
                        SessionRecord.reauthentication_fingerprint,
                    ).order_by(SessionRecord.id)
                )
            ),
        )


@pytest.mark.parametrize("method", ["create", "patch", "noop"])
@pytest.mark.parametrize(
    "invalid", ["missing", "expired", "boundary", "future", "password_changed"]
)
def test_stale_confirmation_rejects_writes_without_effects(
    web, method, invalid, monkeypatch
):
    client, state, policy = web
    assert client.get("/auth/memberships", headers=READ).status_code == 200
    if invalid != "missing":
        confirm(web)
        with Session(state["runtime"]) as session, session.begin():
            row = session.scalar(
                select(SessionRecord).where(
                    SessionRecord.membership_id == state["membership"]
                )
            )
            now = reauthentication._database_now(session)
            if invalid in {"expired", "boundary"}:
                row.reauthenticated_at = now - timedelta(minutes=5)
                if invalid == "boundary":
                    monkeypatch.setattr(
                        reauthentication, "_database_now", lambda _: now
                    )
            elif invalid == "future":
                row.reauthenticated_at = now + timedelta(minutes=1)
                row.mfa_reauthenticated_at = row.reauthenticated_at
            else:
                session.get(User, state["user"]).password_hash = hash_password(
                    SecretStr(secrets.token_urlsafe(24))
                )
    before, cookies = snapshot(state), dict(client.cookies.items())
    response = write(web, method)
    expected_status = 401 if invalid == "password_changed" else 428
    expected_detail = (
        "Access denied"
        if invalid == "password_changed"
        else "Recent authentication required"
    )
    assert response.status_code == expected_status and response.json() == {
        "detail": expected_detail
    }
    assert response.headers["cache-control"] == "no-store"
    assert (
        "set-cookie" not in response.headers
        and "x-access-membership" not in response.headers
    )
    assert dict(client.cookies.items()) == cookies and snapshot(state) == before
    assert client.cookies[policy.session_cookie]


@pytest.mark.parametrize("method", ["create", "patch"])
def test_real_confirmation_authorizes_administration(web, method):
    confirm(web)
    client, state, _ = web
    response = write(web, method)
    assert response.status_code == (201 if method == "create" else 200)
    assert response.headers["x-access-membership"] == str(state["membership"])
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.tenant_id == state["tenant"])
            )
            == 1
        )
    assert client.get("/auth/session", headers=READ).status_code == 200


def test_creation_expiring_during_hash_rolls_back_identity_membership_and_audit(
    web, monkeypatch
):
    confirm(web)
    _, state, _ = web
    with Session(state["runtime"]) as session:
        confirmed = session.scalar(
            select(SessionRecord.reauthenticated_at).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
    original = administration.hash_password
    reached = []

    def slow_hash(password):
        encoded = original(password)
        reached.append(True)
        monkeypatch.setattr(
            reauthentication,
            "_database_now",
            lambda _: confirmed + timedelta(minutes=5),
        )
        return encoded

    monkeypatch.setattr(administration, "hash_password", slow_hash)
    before = snapshot(state)
    assert write(web, "create").status_code == 428
    assert reached == [True] and snapshot(state) == before


def test_confirmation_checked_after_actual_tenant_lock_wait(web):
    confirm(web)
    client, state, policy = web
    service = client.app.state.membership_administrator
    engine = service._factory.kw["bind"]
    started, waiter = Event(), {}
    token = SecretStr(client.cookies[policy.session_cookie])

    def observe(connection, _cursor, statement, _params, _context, _many):
        if "FROM tenants" in statement and "FOR UPDATE" in statement:
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    def change():
        with pytest.raises(RecentAuthenticationRequired):
            service.change_membership(
                token,
                membership_id=state["target"],
                change=MembershipChange(role="mechanic", is_active=True),
            )

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(state["runtime"]) as holder, holder.begin():
                holder.scalar(
                    select(Tenant).where(Tenant.id == state["tenant"]).with_for_update()
                )
                future = pool.submit(change)
                assert started.wait(2)
                deadline, blocked = time.monotonic() + 2, False
                while time.monotonic() < deadline:
                    with state["owner"].connect() as connection:
                        blocked = connection.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_locks "
                                "WHERE pid=:pid AND NOT granted)"
                            ),
                            {"pid": waiter["pid"]},
                        )
                    if blocked:
                        break
                    time.sleep(0.01)
                assert blocked, (
                    "Administrative write must actually wait for the tenant lock"
                )
                row = holder.scalar(
                    select(SessionRecord).where(
                        SessionRecord.membership_id == state["membership"]
                    )
                )
                now = reauthentication._database_now(holder)
                row.reauthenticated_at = now - timedelta(minutes=5)
            before = snapshot(state)
            future.result(timeout=5)
            assert snapshot(state) == before
    finally:
        event.remove(engine, "before_cursor_execute", observe)
