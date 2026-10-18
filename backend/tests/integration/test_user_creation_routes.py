"""Administrative creation through HTTP; only guarded disposable identities."""

import json
import secrets
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from urllib.parse import urljoin
from uuid import UUID, uuid4

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient
from identity_http_fixture import arrange_factor, login_with_factor
from pydantic import SecretStr
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access.models import (
    AuditEvent,
    LoginChallenge,
    MembershipRole,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import verify_password
from app.modules.access.sessions import IssuedSession

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}
PATH = "/auth/memberships"
CANARY = "carrillo-http-log-canary"


@pytest.fixture
def creation_web(new_identity_scope, monkeypatch):
    state = new_identity_scope
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
        assert login_with_factor(client, state, policy).status_code == 200
        yield client, state, policy


def payload(state, **changes):
    return {
        "email": state["prefix"] + "staff@example.com",
        "password": secrets.token_urlsafe(24),
        "role": "mechanic",
    } | changes


def headers(client, state, policy):
    return {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies.get(policy.csrf_cookie, ""),
        "X-Expected-Membership": str(state["membership"]),
    }


def post(client, state, policy, body=None, *, changes=None, path=PATH):
    # Escaped lone surrogates can reach an HTTP server as legal ASCII JSON.
    return client.post(
        path,
        content=json.dumps(payload(state) if body is None else body).encode(),
        headers=headers(client, state, policy)
        | {"Content-Type": "application/json"}
        | (changes or {}),
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


def denied(response, status, detail):
    assert response.status_code == status and response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"
    assert not any(
        name in response.headers
        for name in ("etag", "location", "x-access-membership", "set-cookie")
    )
    assert CANARY not in response.text


@pytest.mark.parametrize("role", list(MembershipRole))
def test_create_returns_committed_membership_and_keeps_actor_session(
    creation_web, role
):
    client, state, policy = creation_web
    value = payload(
        state,
        email=" " + (state["prefix"] + "staff@example.com").upper() + " ",
        password=" 🔧" + secrets.token_urlsafe(24) + " ",
        role=role,
    )
    before, cookies = counts(state), dict(client.cookies.items())
    # Renew the session-bound proof as required by the future client.
    assert client.get("/auth/csrf", headers=READ).status_code == 204
    cookies = dict(client.cookies.items())
    response = post(client, state, policy, value)
    assert response.status_code == 201
    result = response.json()
    assert set(result) == {"membership_id", "role", "is_active"}
    assert result["role"] == role and result["is_active"] is True
    assert response.headers["x-access-membership"] == str(state["membership"])
    assert (
        response.headers["cache-control"] == "no-store"
        and "set-cookie" not in response.headers
    )
    assert dict(client.cookies.items()) == cookies
    assert counts(state) == (
        before[0] + 1,
        before[1] + 1,
        before[2] + 1,
        before[3],
        before[4],
    )
    detail_url = urljoin(str(response.request.url), response.headers["location"])
    detail = client.get(detail_url, headers=READ)
    assert detail.status_code == 200 and detail.json() == result
    assert detail.headers["etag"] == response.headers["etag"]
    assert client.get("/auth/session", headers=READ).json()["membership_id"] == str(
        state["membership"]
    )
    with Session(state["runtime"]) as session:
        member = session.get(TenantMembership, UUID(result["membership_id"]))
        user = session.get(User, member.user_id)
        assert member.tenant_id == state["tenant"] and user.is_active
        assert user.email == value["email"].strip().lower()
        assert verify_password(SecretStr(value["password"]), user.password_hash)
        audit = session.scalar(select(AuditEvent))
        assert (
            audit.action == "membership_created"
            and audit.target_membership_id == member.id
        )
        assert (
            audit.actor_membership_id == state["membership"]
            and audit.tenant_id == state["tenant"]
        )


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_non_admin_cannot_create_or_escalate_via_http(creation_web, role):
    client, state, policy = creation_web
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    before = counts(state)
    denied(
        post(client, state, policy, payload(state, role="admin")), 403, "Access denied"
    )
    assert counts(state) == before


@pytest.mark.parametrize(
    "invalid",
    ["absent", "unknown", "revoked", "expired", "idle", "user", "membership", "tenant"],
)
def test_session_required_even_with_signed_csrf(creation_web, invalid):
    client, state, policy = creation_web
    with Session(state["runtime"]) as session, session.begin():
        record = session.scalar(
            select(SessionRecord).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
        if invalid == "revoked":
            record.revoked_at = record.created_at
        elif invalid == "expired":
            record.reauthenticated_at = record.reauthentication_fingerprint = None
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
    elif invalid == "unknown":
        client.cookies.clear()
        response = Response()
        policy.set_session(
            response,
            IssuedSession(
                SecretStr(secrets.token_urlsafe(32)),
                datetime.now(UTC) + timedelta(hours=1),
            ),
        )
        for cookie in response.headers.getlist("set-cookie"):
            name, value = cookie.split(";", 1)[0].split("=", 1)
            if value:
                client.cookies.set(name, value)
    before = counts(state)
    denied(post(client, state, policy), 401, "Access denied")
    assert counts(state) == before


@pytest.mark.parametrize(
    "invalid", ["missing", "malformed", "duplicate", "foreign", "uppercase"]
)
def test_actor_context_cannot_be_selected_or_omitted(creation_web, invalid):
    client, state, policy = creation_web
    values = headers(client, state, policy)
    if invalid == "missing":
        del values["X-Expected-Membership"]
    elif invalid == "malformed":
        values["X-Expected-Membership"] = CANARY
    elif invalid == "foreign":
        values["X-Expected-Membership"] = str(state["foreign_membership"])
    elif invalid == "uppercase":
        # A fixed format sample guarantees letters rather than relying on random UUID.
        values["X-Expected-Membership"] = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
    pairs = list(values.items())
    if invalid == "duplicate":
        pairs.append(("X-Expected-Membership", str(state["membership"])))
    before = counts(state)
    response = client.post(PATH, json=payload(state), headers=pairs)
    denied(
        response,
        428 if invalid == "missing" else 403,
        "Precondition required"
        if invalid == "missing"
        else ("Access denied" if invalid == "foreign" else "Request rejected"),
    )
    assert counts(state) == before


@pytest.mark.parametrize(
    "bad",
    [
        "origin",
        "null_origin",
        "missing_origin",
        "csrf",
        "missing_csrf",
        "cross_site",
        "duplicate_origin",
        "duplicate_cookie",
    ],
)
def test_origin_csrf_and_cookie_ambiguity_rejected(creation_web, bad):
    client, state, policy = creation_web
    values = headers(client, state, policy)
    if bad == "origin":
        values["Origin"] = "https://foreign.example"
    elif bad == "null_origin":
        values["Origin"] = "null"
    elif bad == "missing_origin":
        del values["Origin"]
    elif bad == "csrf":
        values["X-CSRF-Token"] = CANARY
    elif bad == "missing_csrf":
        del values["X-CSRF-Token"]
    elif bad == "cross_site":
        values["Sec-Fetch-Site"] = "cross-site"
    pairs = list(values.items())
    if bad == "duplicate_origin":
        pairs.append(("Origin", policy.origin))
    elif bad == "duplicate_cookie":
        token = client.cookies[policy.session_cookie]
        pairs.append(
            (
                "Cookie",
                f"{policy.session_cookie}={token}; {policy.session_cookie}={token}",
            )
        )
    before = counts(state)
    denied(
        client.post(PATH, json=payload(state), headers=pairs), 403, "Request rejected"
    )
    assert counts(state) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": CANARY},
        {"user_id": CANARY},
        {"actor_role": "admin"},
        {"password_hash": CANARY},
        {"is_active": True},
        {"user_is_active": True},
        {"role": "root"},
        {"email": CANARY},
        {"password": "x" * 14},
        {"password": "x" * 129},
        {"password": "\ud800" * 15},
    ],
)
def test_invalid_body_has_no_echo_or_side_effects(creation_web, changes):
    client, state, policy = creation_web
    before = counts(state)
    body = payload(state, password=CANARY) | changes
    denied(post(client, state, policy, body), 422, "Invalid request")
    assert counts(state) == before


@pytest.mark.parametrize("field", ["email", "password", "role"])
def test_required_fields_have_no_defaults(creation_web, field):
    client, state, policy = creation_web
    body = payload(state)
    del body[field]
    before = counts(state)
    denied(post(client, state, policy, body), 422, "Invalid request")
    assert counts(state) == before


@pytest.mark.parametrize(
    "query",
    [
        "?tenant_id=" + CANARY,
        "?limit=25",
        "?after=" + str(uuid4()),
        "?role=admin&role=mechanic",
    ],
)
def test_creation_rejects_all_query_parameters(creation_web, query):
    client, state, policy = creation_web
    before = counts(state)
    denied(post(client, state, policy, path=PATH + query), 422, "Invalid request")
    assert counts(state) == before


@pytest.mark.parametrize("kind,status", [("media", 415), ("json", 400), ("size", 413)])
def test_content_limits_apply_before_creation(creation_web, kind, status):
    client, state, policy = creation_web
    values = headers(client, state, policy)
    values["Content-Type"] = "text/plain" if kind == "media" else "application/json"
    body = (
        b'{"incomplete":'
        if kind == "json"
        else (b"x" * 8193 if kind == "size" else b"{}")
    )
    before = counts(state)
    denied(client.post(PATH, content=body, headers=values), status, "Request rejected")
    assert counts(state) == before


@pytest.mark.parametrize("identity", ["own", "foreign", "inactive", "repeat"])
def test_duplicate_is_generic_and_never_resets_or_attaches(creation_web, identity):
    client, state, policy = creation_web
    value = payload(state)
    with Session(state["runtime"]) as session:
        value["email"] = (
            state["email"]
            if identity == "own"
            else session.get(User, state["foreign"]).email
        )
    if identity == "inactive":
        with state["owner"].begin() as connection:
            connection.execute(
                update(User).where(User.id == state["foreign"]).values(is_active=False)
            )
    elif identity == "repeat":
        value = payload(state)
        assert post(client, state, policy, value).status_code == 201
    before = counts(state)
    denied(
        post(client, state, policy, value | {"password": CANARY}),
        409,
        "User creation unavailable",
    )
    assert counts(state) == before
    if identity == "repeat":
        with Session(state["runtime"]) as session:
            user = session.scalar(select(User).where(User.email == value["email"]))
            assert verify_password(SecretStr(value["password"]), user.password_hash)


def test_pending_selection_is_not_a_session_for_creation(creation_web):
    client, state, policy = creation_web
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            TenantMembership(
                user_id=state["user"], tenant_id=state["other"], role="mechanic"
            )
        )
    assert (
        client.post(
            "/auth/logout", json={}, headers=headers(client, state, policy)
        ).status_code
        == 204
    )
    assert client.get("/auth/csrf", headers=READ).status_code == 204
    assert (
        client.post(
            "/auth/login",
            json={
                "email": state["email"],
                "password": state["password"].get_secret_value(),
            },
            headers=headers(client, state, policy),
        ).status_code
        == 202
    )
    before = counts(state)
    denied(post(client, state, policy), 401, "Access denied")
    assert counts(state) == before


def test_shared_hash_slots_reject_without_work_then_release(creation_web):
    client, state, policy = creation_web
    before = counts(state)
    access = client.app.state.access
    with access.password_slot(), access.password_slot():
        denied(post(client, state, policy), 503, "Authentication unavailable")
    assert counts(state) == before
    assert post(client, state, policy).status_code == 201


def test_commit_failure_never_returns_created_or_changes_cookies(creation_web):
    client, state, policy = creation_web
    before, cookies = counts(state), dict(client.cookies.items())
    engine = client.app.state.membership_administrator._factory.kw["bind"]

    def fail(_connection):
        raise SQLAlchemyError(CANARY)

    event.listen(engine, "commit", fail)
    try:
        denied(post(client, state, policy), 503, "Authentication unavailable")
    finally:
        event.remove(engine, "commit", fail)
    assert counts(state) == before and dict(client.cookies.items()) == cookies
