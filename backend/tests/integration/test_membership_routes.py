"""Direct HTTP requests must pass session, tenant, role, CSRF and reviewed state."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access.administration import MembershipChange, MembershipStateChanged
from app.modules.access.models import (
    AuditEvent,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.sessions import issue_session

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def membership_web(http_identity, monkeypatch):
    state = dict(http_identity)
    with Session(state["runtime"]) as session, session.begin():
        target = TenantMembership(
            tenant_id=state["tenant"], user_id=state["foreign"], role="cashier"
        )
        session.add(target)
        session.flush()
        state["target"] = target.id
        state["target_token"] = issue_session(
            session, user_id=state["foreign"], tenant_id=state["tenant"]
        ).token
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
        assert client.get("/auth/csrf", headers=READ).status_code == 204
        assert (
            client.post(
                "/auth/login",
                json={
                    "email": state["email"],
                    "password": state["password"].get_secret_value(),
                },
                headers={
                    "Origin": policy.origin,
                    "X-CSRF-Token": client.cookies[policy.csrf_cookie],
                },
            ).status_code
            == 200
        )
        yield client, state, policy


def path(state, key="target"):
    return f"/auth/memberships/{state[key]}"


def patch(client, state, policy, *, etag=None, body=None, target=None, headers=None):
    return client.patch(
        target or path(state),
        json=body if body is not None else {"role": "mechanic", "is_active": True},
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies.get(policy.csrf_cookie, ""),
            **({"If-Match": etag} if etag is not None else {}),
            **(headers or {}),
        },
    )


def stored(state):
    with Session(state["runtime"]) as session:
        member = session.get(TenantMembership, state["target"])
        return (
            member.role,
            member.is_active,
            session.scalar(select(func.count()).select_from(AuditEvent)),
            tuple(
                session.scalars(
                    select(SessionRecord.revoked_at).order_by(SessionRecord.id)
                )
            ),
        )


def test_get_patch_noop_and_target_session_replay(membership_web):
    client, state, policy = membership_web
    view = client.get(path(state), headers=READ)
    assert view.status_code == 200
    assert view.json() == {
        "membership_id": str(state["target"]),
        "role": "cashier",
        "is_active": True,
    }
    assert view.headers["cache-control"] == "no-store"
    assert "set-cookie" not in view.headers
    response = patch(client, state, policy, etag=view.headers["etag"])
    assert response.status_code == 200
    assert response.json() == {
        "membership_id": str(state["target"]),
        "role": "mechanic",
        "is_active": True,
    }
    assert response.headers["etag"] != view.headers["etag"]
    assert (
        response.headers["cache-control"] == "no-store"
        and "set-cookie" not in response.headers
    )
    assert client.get(path(state), headers=READ).json() == response.json()
    before = stored(state)
    assert (
        patch(client, state, policy, etag=response.headers["etag"]).status_code == 200
    )
    assert stored(state) == before
    assert (
        client.get(
            "/auth/session",
            headers=READ
            | {
                "Cookie": (
                    f"{policy.session_cookie}="
                    f"{state['target_token'].get_secret_value()}"
                )
            },
        ).status_code
        == 401
    )


@pytest.mark.parametrize("method", ["get", "patch"])
@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_non_admin_cannot_read_or_write_or_escalate(membership_web, role, method):
    client, state, policy = membership_web
    tag = client.get(path(state, "membership"), headers=READ).headers["etag"]
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    before = stored(state)
    response = (
        client.get(path(state, "membership"), headers=READ)
        if method == "get"
        else patch(
            client,
            state,
            policy,
            target=path(state, "membership"),
            etag=tag,
            body={"role": "admin", "is_active": True},
        )
    )
    assert response.status_code == 403 and response.json() == {
        "detail": "Access denied"
    }
    assert (
        "etag" not in response.headers
        and response.headers["cache-control"] == "no-store"
    )
    assert stored(state) == before


@pytest.mark.parametrize("key", ["foreign_membership", "missing"])
@pytest.mark.parametrize("method", ["get", "patch"])
def test_foreign_and_missing_targets_share_denial(membership_web, key, method):
    client, state, policy = membership_web
    state["missing"] = uuid4()
    tag = client.get(path(state), headers=READ).headers["etag"]
    before = stored(state)
    response = (
        client.get(path(state, key), headers=READ)
        if method == "get"
        else patch(client, state, policy, target=path(state, key), etag=tag)
    )
    assert response.status_code == 403 and response.json() == {
        "detail": "Access denied"
    }
    assert stored(state) == before


@pytest.mark.parametrize(
    "invalid",
    [
        "absent",
        "malformed",
        "unknown",
        "revoked",
        "expired",
        "idle",
        "user",
        "membership",
        "tenant",
    ],
)
@pytest.mark.parametrize("method", ["get", "patch"])
def test_invalid_session_cannot_administer(membership_web, invalid, method):
    client, state, policy = membership_web
    tag = client.get(path(state), headers=READ).headers["etag"]
    with Session(state["runtime"]) as session, session.begin():
        record = session.scalar(
            select(SessionRecord).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
        if invalid == "revoked":
            record.revoked_at = record.created_at
        elif invalid == "expired":
            record.created_at -= timedelta(hours=9)
            record.last_seen_at -= timedelta(hours=9)
            record.expires_at -= timedelta(hours=9)
        elif invalid == "idle":
            record.created_at -= timedelta(hours=1)
            record.expires_at -= timedelta(hours=1)
            record.last_seen_at -= timedelta(minutes=31)
        elif invalid in {"user", "membership", "tenant"}:
            model, key = {
                "user": (User, "user"),
                "membership": (TenantMembership, "membership"),
                "tenant": (Tenant, "tenant"),
            }[invalid]
            session.execute(
                update(model).where(model.id == state[key]).values(is_active=False)
            )
    if invalid == "absent":
        client.cookies.clear()
        assert client.get("/auth/csrf", headers=READ).status_code == 204
    elif invalid in {"malformed", "unknown"}:
        client.cookies.clear()
        # New proof bound to a syntactically valid unknown session for PATCH.
        # Malformed session fails CSRF before the service, as intended.
        from datetime import UTC, datetime

        from fastapi.responses import Response

        from app.modules.access.sessions import IssuedSession

        token = SecretStr(secrets.token_urlsafe(32))
        response = Response()
        policy.set_session(
            response, IssuedSession(token, datetime.now(UTC) + timedelta(hours=1))
        )
        for value in response.headers.getlist("set-cookie"):
            name, content = value.split(";", 1)[0].split("=", 1)
            if content:
                client.cookies.set(name, content)
        if invalid == "malformed":
            client.cookies.set(policy.session_cookie, "malformed")
    before = stored(state)
    response = (
        client.get(path(state), headers=READ)
        if method == "get"
        else patch(client, state, policy, etag=tag)
    )
    expected = 403 if invalid == "malformed" and method == "patch" else 401
    assert response.status_code == expected
    assert (
        response.headers["cache-control"] == "no-store"
        and "etag" not in response.headers
    )
    assert stored(state) == before


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Requested-With": "XMLHttpRequest", "Origin": "null"},
        READ | {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_admin_read_requires_browser_intent(membership_web, headers):
    client, state, _ = membership_web
    before = stored(state)
    response = client.get(path(state), headers=headers)
    assert response.status_code == 403 and "etag" not in response.headers
    assert stored(state) == before


@pytest.mark.parametrize(
    "bad",
    [
        {"Origin": "https://foreign.example"},
        {"Origin": "null"},
        {"X-CSRF-Token": "invalid"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_admin_write_rejects_invalid_origin_or_csrf(membership_web, bad):
    client, state, policy = membership_web
    tag = client.get(path(state), headers=READ).headers["etag"]
    before = stored(state)
    response = patch(client, state, policy, etag=tag, headers=bad)
    assert response.status_code == 403 and response.json() == {
        "detail": "Request rejected"
    }
    assert stored(state) == before


@pytest.mark.parametrize(
    "tag,status",
    [(None, 428), ("*", 400), ('W/"weak"', 400), ('"invalid"', 400), ('"a", "b"', 400)],
)
def test_missing_or_malformed_precondition_is_rejected(membership_web, tag, status):
    client, state, policy = membership_web
    before = stored(state)
    response = patch(client, state, policy, etag=tag)
    assert response.status_code == status
    assert (
        "etag" not in response.headers
        and response.headers["cache-control"] == "no-store"
    )
    assert stored(state) == before


def test_stale_tag_never_overwrites_a_later_change(membership_web):
    client, state, policy = membership_web
    tag = client.get(path(state), headers=READ).headers["etag"]
    changed = patch(client, state, policy, etag=tag)
    assert changed.status_code == 200
    before = stored(state)
    stale = patch(
        client, state, policy, etag=tag, body={"role": "admin", "is_active": True}
    )
    assert stale.status_code == 412 and stale.json() == {
        "detail": "Membership state changed"
    }
    assert "etag" not in stale.headers and stored(state) == before
    assert (
        client.get(path(state), headers=READ).headers["etag"] == changed.headers["etag"]
    )


@pytest.mark.parametrize(
    "body",
    [
        {"role": "mechanic"},
        {"role": "root", "is_active": True},
        {"role": "admin", "is_active": "true"},
        {"role": "admin", "is_active": 1},
        {"role": "admin", "is_active": True, "tenant_id": "synthetic-canary"},
        {"role": "admin", "is_active": True, "actor_role": "synthetic-canary"},
    ],
)
def test_invalid_payload_is_not_echoed_or_written(membership_web, body):
    client, state, policy = membership_web
    tag = client.get(path(state), headers=READ).headers["etag"]
    before = stored(state)
    response = patch(client, state, policy, etag=tag, body=body)
    assert response.status_code == 422 and response.json() == {
        "detail": "Invalid request"
    }
    assert "synthetic-canary" not in response.text and stored(state) == before


@pytest.mark.parametrize("method", ["get", "patch"])
def test_no_query_authority_or_invalid_identifier(membership_web, method):
    client, state, policy = membership_web
    tag = client.get(path(state), headers=READ).headers["etag"]
    for target in (
        path(state) + "?tenant_id=synthetic-canary",
        "/auth/memberships/invalid",
    ):
        response = (
            client.get(target, headers=READ)
            if method == "get"
            else patch(client, state, policy, target=target, etag=tag)
        )
        assert response.status_code == 422 and response.json() == {
            "detail": "Invalid request"
        }


def test_last_admin_conflict_preserves_access_and_cookies(membership_web):
    client, state, policy = membership_web
    tag = client.get(path(state, "membership"), headers=READ).headers["etag"]
    before, cookies = stored(state), dict(client.cookies.items())
    response = patch(
        client,
        state,
        policy,
        target=path(state, "membership"),
        etag=tag,
        body={"role": "read_only", "is_active": True},
    )
    assert response.status_code == 409 and response.json() == {
        "detail": "An active administrator is required"
    }
    assert stored(state) == before and dict(client.cookies.items()) == cookies


def test_self_change_clears_cookies_only_after_commit_and_replay_is_denied(
    membership_web,
):
    client, state, policy = membership_web
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["target"])
            .values(role="admin")
        )
    tag = client.get(path(state, "membership"), headers=READ).headers["etag"]
    old = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
    response = patch(
        client,
        state,
        policy,
        target=path(state, "membership"),
        etag=tag,
        body={"role": "read_only", "is_active": True},
    )
    assert (
        response.status_code == 200
        and len(response.headers.get_list("set-cookie")) == 4
    )
    assert not list(client.cookies.items())
    assert client.get(path(state), headers=READ | {"Cookie": old}).status_code == 401


@pytest.mark.parametrize("method", ["get", "patch", "self_patch"])
def test_failed_commit_does_not_publish_view_etag_or_cookie_deletion(
    membership_web, method
):
    client, state, policy = membership_web
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["target"])
            .values(role="admin")
        )
    target = path(state, "membership") if method == "self_patch" else path(state)
    tag = client.get(target, headers=READ).headers["etag"]
    before, cookies = stored(state), dict(client.cookies.items())
    engine = client.app.state.membership_administrator._factory.kw["bind"]
    canary = secrets.token_urlsafe(24)

    def fail_commit(_connection):
        raise SQLAlchemyError(canary)

    event.listen(engine, "commit", fail_commit)
    try:
        response = (
            client.get(target, headers=READ)
            if method == "get"
            else patch(client, state, policy, target=target, etag=tag)
        )
        assert response.status_code == 503 and response.json() == {
            "detail": "Authentication unavailable"
        }
        assert (
            canary not in response.text
            and "etag" not in response.headers
            and "set-cookie" not in response.headers
        )
    finally:
        event.remove(engine, "commit", fail_commit)
    assert stored(state) == before and dict(client.cookies.items()) == cookies


def test_duplicate_precondition_is_rejected(membership_web):
    client, state, policy = membership_web
    tag = client.get(path(state), headers=READ).headers["etag"]
    before = stored(state)
    response = client.patch(
        path(state),
        json={"role": "mechanic", "is_active": True},
        headers=[
            ("Origin", policy.origin),
            ("X-CSRF-Token", client.cookies[policy.csrf_cookie]),
            ("If-Match", tag),
            ("If-Match", tag),
        ],
    )
    assert response.status_code == 400 and stored(state) == before


def test_self_noop_preserves_cookies_and_session(membership_web):
    client, state, policy = membership_web
    tag = client.get(path(state, "membership"), headers=READ).headers["etag"]
    before, cookies = stored(state), dict(client.cookies.items())
    response = patch(
        client,
        state,
        policy,
        target=path(state, "membership"),
        etag=tag,
        body={"role": "admin", "is_active": True},
    )
    assert response.status_code == 200 and response.headers["etag"] == tag
    assert (
        "set-cookie" not in response.headers and dict(client.cookies.items()) == cookies
    )
    assert stored(state) == before


def test_pending_identity_is_not_an_administrator_session(membership_web):
    client, state, policy = membership_web
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            TenantMembership(
                user_id=state["user"], tenant_id=state["other"], role="mechanic"
            )
        )
    with TestClient(
        create_app(state["settings"]),
        base_url=policy.origin,
        client=("127.0.0.2", 12346),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as pending:
        assert pending.get("/auth/csrf", headers=READ).status_code == 204
        assert (
            pending.post(
                "/auth/login",
                json={
                    "email": state["email"],
                    "password": state["password"].get_secret_value(),
                },
                headers={
                    "Origin": policy.origin,
                    "X-CSRF-Token": pending.cookies[policy.csrf_cookie],
                },
            ).status_code
            == 202
        )
        assert pending.get(path(state), headers=READ).status_code == 401
        assert pending.get("/auth/memberships", headers=READ).status_code == 401
        tag = client.get(path(state), headers=READ).headers["etag"]
        before = stored(state)
        assert patch(pending, state, policy, etag=tag).status_code == 401
        assert stored(state) == before


def test_delete_and_cross_origin_preflight_cannot_administer(membership_web):
    client, state, policy = membership_web
    response = client.request(
        "DELETE",
        path(state),
        json={},
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        },
    )
    assert response.status_code == 405
    preflight = client.options(
        path(state),
        headers={
            "Origin": "https://foreign.example",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "X-CSRF-Token, If-Match",
        },
    )
    assert (
        preflight.status_code == 405
        and "access-control-allow-origin" not in preflight.headers
    )


def test_two_different_changes_with_same_reviewed_tag_commit_only_one(membership_web):
    client, state, policy = membership_web
    service = client.app.state.membership_administrator
    engine = service._factory.kw["bind"]
    tag = client.get(path(state), headers=READ).headers["etag"]
    token = SecretStr(client.cookies[policy.session_cookie])
    reached, release, started, waiter = Event(), Event(), Event(), {}

    def observe(connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO audit_events"):
            reached.set()
            assert release.wait(5)
        elif (
            reached.is_set()
            and "FROM tenants" in statement
            and "FOR UPDATE" in statement
        ):
            waiter["pid"] = connection.connection.driver_connection.info.backend_pid
            started.set()

    def apply(role):
        return service.change_membership(
            token,
            membership_id=state["target"],
            change=MembershipChange(role=role, is_active=True),
            expected_etag=tag,
        )

    def second():
        with pytest.raises(MembershipStateChanged):
            apply("admin")

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(apply, "mechanic")
            try:
                assert reached.wait(3)
                other = pool.submit(second)
                assert started.wait(2)
                deadline, blocked = time.monotonic() + 2, False
                while time.monotonic() < deadline:
                    with state["owner"].connect() as connection:
                        blocked = connection.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_locks "
                                "WHERE pid = :pid AND NOT granted)"
                            ),
                            {"pid": waiter["pid"]},
                        )
                    if blocked:
                        break
                    time.sleep(0.01)
                assert blocked, "Second change must actually wait for the tenant lock"
            finally:
                release.set()
            assert first.result(timeout=5).changed
            other.result(timeout=5)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe)
    result = stored(state)
    assert result[:3] == ("mechanic", True, 1)


@pytest.fixture
def membership_pages(membership_web):
    client, state, policy = membership_web
    user_ids, member_ids = [], []
    try:
        with Session(state["runtime"]) as session, session.begin():
            for index in range(103):
                user = User(
                    email=f"page-{uuid4().hex}@example.com",
                    password_hash="synthetic",
                    is_active=index % 3 != 0,
                )
                session.add(user)
                session.flush()
                member = TenantMembership(
                    user_id=user.id,
                    tenant_id=state["tenant"],
                    role="read_only",
                    is_active=index % 2 != 0,
                )
                session.add(member)
                session.flush()
                user_ids.append(user.id)
                member_ids.append(member.id)
        yield client, state, sorted(member_ids + [state["membership"], state["target"]])
    finally:
        # Restricted to these fixture IDs in the ephemeral test DB.
        with state["owner"].begin() as connection:
            connection.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_membership_id.in_(member_ids)
                )
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id.in_(member_ids))
            )
            connection.execute(delete(User).where(User.id.in_(user_ids)))


@pytest.mark.parametrize("limit", [1, 25, 100])
def test_list_pagination_order_boundaries_and_privacy(membership_pages, limit):
    client, state, expected_ids = membership_pages
    seen, cursor = [], None
    for _ in range(106):
        query = {"limit": limit, **({"after": cursor} if cursor else {})}
        response = client.get("/auth/memberships", params=query, headers=READ)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "etag" not in response.headers and "set-cookie" not in response.headers
        page = response.json()
        assert set(page) == {"items", "next_after"}
        assert len(page["items"]) <= limit
        for row in page["items"]:
            assert set(row) == {
                "membership_id",
                "email",
                "role",
                "is_active",
                "user_is_active",
            }
            assert row["membership_id"] != str(state["foreign_membership"])
        ids = [row["membership_id"] for row in page["items"]]
        seen.extend(ids)
        cursor = page["next_after"]
        if cursor is None:
            break
        assert cursor == ids[-1]
    else:
        pytest.fail("Pagination must terminate")
    assert seen == [str(value) for value in expected_ids]
    assert len(seen) == len(set(seen)) == 105
    assert (
        client.get("/auth/memberships", headers=READ).json()["items"]
        == client.get("/auth/memberships?limit=25", headers=READ).json()["items"]
    )


def test_list_includes_inactive_membership_and_account_but_no_other_company(
    membership_web,
):
    client, state, _ = membership_web
    with state["owner"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["foreign"]).values(is_active=False)
        )
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["target"])
            .values(is_active=False)
        )
    before = stored(state)
    response = client.get("/auth/memberships", headers=READ)
    assert response.status_code == 200
    items = {row["membership_id"]: row for row in response.json()["items"]}
    assert set(items) == {str(state["target"]), str(state["membership"])}
    target = items[str(state["target"])]
    assert target["is_active"] is False and target["user_is_active"] is False
    with Session(state["runtime"]) as session:
        assert target["email"] == session.get(User, state["foreign"]).email
    assert stored(state) == before


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_list_requires_current_admin_role(membership_web, role):
    client, state, _ = membership_web
    assert client.get("/auth/memberships", headers=READ).status_code == 200
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    before = stored(state)
    response = client.get("/auth/memberships", headers=READ)
    assert response.status_code == 403 and response.json() == {
        "detail": "Access denied"
    }
    assert response.headers["cache-control"] == "no-store" and stored(state) == before


@pytest.mark.parametrize(
    "invalid", ["absent", "revoked", "user", "membership", "tenant", "expired"]
)
def test_list_requires_live_identity(membership_web, invalid):
    client, state, _ = membership_web
    if invalid == "absent":
        client.cookies.clear()
    else:
        with Session(state["runtime"]) as session, session.begin():
            if invalid in {"revoked", "expired"}:
                record = session.scalar(
                    select(SessionRecord).where(
                        SessionRecord.membership_id == state["membership"]
                    )
                )
                if invalid == "revoked":
                    record.revoked_at = record.created_at
                else:
                    record.created_at -= timedelta(hours=9)
                    record.last_seen_at -= timedelta(hours=9)
                    record.expires_at -= timedelta(hours=9)
            else:
                model, key = {
                    "user": (User, "user"),
                    "membership": (TenantMembership, "membership"),
                    "tenant": (Tenant, "tenant"),
                }[invalid]
                session.execute(
                    update(model).where(model.id == state[key]).values(is_active=False)
                )
    response = client.get("/auth/memberships", headers=READ)
    assert response.status_code == 401 and response.json() == {
        "detail": "Access denied"
    }
    assert "items" not in response.json() and "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=1.0",
        "limit=01",
        "limit=-1",
        "limit=true",
        "limit=",
        "after=invalid",
        "tenant_id=canary",
        "role=admin",
        "offset=0",
        "limit=1&limit=2",
        "after=00000000-0000-4000-8000-000000000000&after=00000000-0000-4000-8000-000000000000",
    ],
)
def test_list_rejects_invalid_extra_or_ambiguous_query(membership_web, query):
    client, state, _ = membership_web
    before = stored(state)
    response = client.get("/auth/memberships?" + query, headers=READ)
    assert response.status_code == 422 and response.json() == {
        "detail": "Invalid request"
    }
    assert response.headers["cache-control"] == "no-store" and stored(state) == before


def test_list_unknown_or_foreign_cursor_only_changes_boundary(membership_web):
    client, state, _ = membership_web
    for cursor in (state["foreign_membership"], uuid4()):
        response = client.get(
            "/auth/memberships", params={"after": str(cursor)}, headers=READ
        )
        assert response.status_code == 200
        assert [row["membership_id"] for row in response.json()["items"]] == [
            str(value)
            for value in sorted([state["membership"], state["target"]])
            if value > cursor
        ]
    empty = client.get(
        "/auth/memberships?after=ffffffff-ffff-ffff-ffff-ffffffffffff", headers=READ
    )
    assert empty.json() == {"items": [], "next_after": None}


@pytest.mark.parametrize(
    "headers", [{}, READ | {"Origin": "null"}, READ | {"Sec-Fetch-Site": "cross-site"}]
)
def test_list_requires_browser_read_intent(membership_web, headers):
    client, _, _ = membership_web
    response = client.get("/auth/memberships", headers=headers)
    assert response.status_code == 403 and "items" not in response.json()


def test_list_commit_failure_does_not_publish_personal_data(membership_web):
    client, state, _ = membership_web
    engine = client.app.state.membership_administrator._factory.kw["bind"]
    before = stored(state)

    def fail_commit(_connection):
        raise SQLAlchemyError("synthetic-list-commit-canary")

    event.listen(engine, "commit", fail_commit)
    try:
        response = client.get("/auth/memberships", headers=READ)
        assert response.status_code == 503 and response.json() == {
            "detail": "Authentication unavailable"
        }
        assert state["email"] not in response.text and "canary" not in response.text
        assert "set-cookie" not in response.headers and "etag" not in response.headers
    finally:
        event.remove(engine, "commit", fail_commit)
    assert stored(state) == before


def test_list_next_page_reads_current_state_after_a_change(membership_pages):
    client, state, expected_ids = membership_pages
    policy = client.app.state.http_security
    first = client.get("/auth/memberships?limit=1", headers=READ).json()
    target = next(value for value in expected_ids[1:] if value != state["membership"])
    detail_path = f"/auth/memberships/{target}"
    detail = client.get(detail_path, headers=READ)
    assert (
        patch(
            client,
            state,
            policy,
            target=detail_path,
            etag=detail.headers["etag"],
            body={"role": "mechanic", "is_active": False},
        ).status_code
        == 200
    )
    following = client.get(
        "/auth/memberships",
        params={"limit": 100, "after": first["next_after"]},
        headers=READ,
    ).json()
    updated = next(
        row for row in following["items"] if row["membership_id"] == str(target)
    )
    assert updated["role"] == "mechanic" and updated["is_active"] is False
