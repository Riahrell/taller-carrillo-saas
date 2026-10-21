"""Confirmation HTTP contract; real PostgreSQL and disposable identities only."""

import json
import secrets
from datetime import timedelta
from http.cookies import SimpleCookie
from ipaddress import ip_address
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from identity_http_fixture import arrange_factor, login_with_legacy_factor, next_factor
from pydantic import SecretStr
from sqlalchemy import event, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    LoginChallenge,
    LoginThrottle,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password
from app.modules.access.reauthentication import require_recent_administrator

pytestmark = pytest.mark.integration
PATH = "/auth/mfa/reauthenticate"
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}
CANARY = "carrillo-http-log-canary"


@pytest.fixture
def web(http_identity, monkeypatch, request):
    state = http_identity
    settings = state["settings"]
    if getattr(request, "param", False):
        settings = settings.model_copy(update={"public_origin": "https://localhost"})
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    with TestClient(
        create_app(settings),
        base_url=settings.public_origin,
        client=("127.0.0.2", 12345),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as client:
        policy = client.app.state.http_security
        arrange_factor(state, client.app, monkeypatch)
        assert client.get("/auth/csrf", headers=READ).status_code == 204
        assert login_with_legacy_factor(client, state, policy).status_code == 200
        yield client, state, policy


def headers(client, state, policy, **changes):
    values = {
        "Origin": policy.origin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": client.cookies.get(policy.csrf_cookie, ""),
        "X-Expected-Membership": str(state["membership"]),
        "Content-Type": "application/json",
    } | changes
    return {key: value for key, value in values.items() if value is not None}


def post(web, *, data=None, changes=None, path=PATH, raw=None):
    client, state, policy = web
    body = {"password": state["password"].get_secret_value()} if data is None else data
    code = next_factor(state)
    if isinstance(body, dict):
        body = {"code": code, **body}
    return client.post(
        path,
        content=json.dumps(body).encode() if raw is None else raw,
        headers=headers(client, state, policy, **(changes or {})),
    )


def snapshot(state):
    with Session(state["runtime"]) as session:
        row = session.scalar(
            select(SessionRecord).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
        return (
            row.id,
            row.token_hash,
            row.created_at,
            row.last_seen_at,
            row.expires_at,
            row.revoked_at,
            row.reauthenticated_at,
            row.reauthentication_fingerprint,
        )


def counters(state):
    with Session(state["runtime"]) as session:
        return session.execute(
            select(
                LoginThrottle.scope,
                LoginThrottle.key_hash,
                LoginThrottle.failures,
                LoginThrottle.window_started_at,
            ).order_by(LoginThrottle.scope, LoginThrottle.key_hash)
        ).all()


def denied(response, status, detail):
    assert response.status_code == status and response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"
    assert all(
        name not in response.headers
        for name in ("set-cookie", "x-access-membership", "etag", "location")
    )
    assert CANARY not in response.text


@pytest.mark.parametrize("web", [False, True], indirect=True)
def test_success_rotates_session_and_csrf_after_commit_without_extending_expiry(web):
    client, state, policy = web
    before = snapshot(state)
    old = dict(client.cookies.items())
    response = post(web)
    assert response.status_code == 204 and response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-access-membership"] == str(state["membership"])
    assert "etag" not in response.headers and "location" not in response.headers
    assert client.cookies[policy.session_cookie] != old[policy.session_cookie]
    assert client.cookies[policy.csrf_cookie] != old[policy.csrf_cookie]
    assert (
        policy.preauth_cookie not in client.cookies
        and policy.challenge_cookie not in client.cookies
    )
    cookies = SimpleCookie()
    for line in response.headers.get_list("set-cookie"):
        cookies.load(line)
    cookie = cookies[policy.session_cookie]
    assert bool(cookie["httponly"]) and bool(cookie["secure"]) == policy.secure
    assert (
        cookie["path"] == "/"
        and cookie["samesite"].lower() == "lax"
        and cookie["domain"] == ""
    )
    assert bool(cookies[policy.csrf_cookie]["httponly"]) is False
    assert bool(cookies[policy.csrf_cookie]["secure"]) == policy.secure
    assert policy.session_cookie.startswith("__Host-") == policy.secure
    assert not cookie["expires"] and not cookie["max-age"]
    after = snapshot(state)
    assert after[0] == before[0] and after[2] == before[2] and after[4] == before[4]
    assert after[6] is not None and after[7] is not None
    with Session(state["runtime"]) as session, session.begin():
        actor = require_recent_administrator(
            session,
            SecretStr(client.cookies[policy.session_cookie]),
            expected_actor=state["membership"],
        )
        assert actor.user_id == state["user"] and actor.tenant_id == state["tenant"]
    assert client.get("/auth/session", headers=READ).json()["membership_id"] == str(
        state["membership"]
    )


def test_old_token_replay_and_old_proof_with_new_token_are_rejected(web):
    client, state, policy = web
    old = dict(client.cookies.items())
    assert post(web).status_code == 204
    fresh = dict(client.cookies.items())
    old_cookie = "; ".join(f"{key}={value}" for key, value in old.items())
    denied(
        post(
            web, changes={"Cookie": old_cookie, "X-CSRF-Token": old[policy.csrf_cookie]}
        ),
        401,
        "Access denied",
    )
    mixed = fresh | {policy.csrf_cookie: old[policy.csrf_cookie]}
    denied(
        post(
            web,
            changes={
                "Cookie": "; ".join(f"{key}={value}" for key, value in mixed.items()),
                "X-CSRF-Token": old[policy.csrf_cookie],
            },
        ),
        403,
        "Request rejected",
    )
    assert dict(client.cookies.items()) == fresh
    assert client.get("/auth/session", headers=READ).status_code == 200


def test_current_unicode_password_is_preserved_without_trimming(web):
    client, state, _ = web
    password = " 🔐" + secrets.token_urlsafe(20) + "  "
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(User)
            .where(User.id == state["user"])
            .values(password_hash=hash_password(SecretStr(password)))
        )
    state["password"] = SecretStr(password)
    client.cookies.clear()
    assert client.get("/auth/csrf", headers=READ).status_code == 204
    assert login_with_legacy_factor(client, state, web[2]).status_code == 200
    denied(post(web, data={"password": password.strip()}), 401, "Access denied")
    assert post(web, data={"password": password}).status_code == 204


def test_wrong_password_keeps_cookies_and_activity_but_commits_failures(web):
    client, state, _ = web
    before, cookies = snapshot(state), dict(client.cookies.items())
    denied(post(web, data={"password": CANARY}), 401, "Access denied")
    assert snapshot(state) == before and dict(client.cookies.items()) == cookies
    assert [row.failures for row in counters(state)] == [1, 1]


@pytest.mark.parametrize(
    "change",
    [
        "revoked",
        "idle",
        "absolute",
        "user",
        "tenant",
        "membership",
        "cashier",
        "mechanic",
        "read_only",
        "foreign_actor",
        "unknown_actor",
    ],
)
def test_invalid_session_or_permission_is_generic_without_rotation(web, change):
    client, state, _ = web
    with Session(state["runtime"]) as session, session.begin():
        row = session.scalar(select(SessionRecord))
        if change == "revoked":
            row.revoked_at = row.created_at
        elif change in {"idle", "absolute"}:
            delta = timedelta(minutes=31) if change == "idle" else timedelta(hours=9)
            row.created_at -= delta
            row.last_seen_at -= delta
            row.expires_at -= delta
        elif change in {"user", "tenant", "membership"}:
            model, identifier = {
                "user": (User, state["user"]),
                "tenant": (Tenant, state["tenant"]),
                "membership": (TenantMembership, state["membership"]),
            }[change]
            session.execute(
                update(model).where(model.id == identifier).values(is_active=False)
            )
        elif change in {"cashier", "mechanic", "read_only"}:
            session.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["membership"])
                .values(role=change)
            )
    before, cookies = snapshot(state), dict(client.cookies.items())
    changes = {}
    if change.endswith("actor"):
        changes["X-Expected-Membership"] = str(
            state["foreign_membership"] if change == "foreign_actor" else uuid4()
        )
    if change in {"cashier", "mechanic", "read_only"}:
        assert post(web, changes=changes).status_code == 204
        with (
            Session(state["runtime"]) as session,
            session.begin(),
            pytest.raises(AccessDenied),
        ):
            require_recent_administrator(
                session,
                SecretStr(client.cookies[web[2].session_cookie]),
                expected_actor=state["membership"],
            )
    else:
        denied(post(web, changes=changes), 401, "Access denied")
        assert snapshot(state) == before and dict(client.cookies.items()) == cookies


@pytest.mark.parametrize("pending", [False, True])
def test_preauth_or_pending_identity_cannot_confirm(web, pending):
    client, state, policy = web
    if pending:
        with Session(state["runtime"]) as session, session.begin():
            session.add(
                TenantMembership(
                    user_id=state["user"], tenant_id=state["other"], role="admin"
                )
            )
    client.cookies.clear()
    assert client.get("/auth/csrf", headers=READ).status_code == 204
    if pending:
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
    before, limits = snapshot(state), counters(state)
    denied(post(web), 401, "Access denied")
    assert snapshot(state) == before and counters(state) == limits
    with Session(state["runtime"]) as session:
        if pending:
            assert session.scalar(select(LoginChallenge.finished_at)) is None


@pytest.mark.parametrize(
    "value,status,detail",
    [
        (None, 428, "Precondition required"),
        ("bad", 403, "Request rejected"),
        ("00000000-0000-0000-0000-00000000000A", 403, "Request rejected"),
        ("{00000000-0000-0000-0000-00000000000a}", 403, "Request rejected"),
    ],
)
def test_expected_membership_is_required_and_canonical(web, value, status, detail):
    _, state, _ = web
    before, limits = snapshot(state), counters(state)
    denied(post(web, changes={"X-Expected-Membership": value}), status, detail)
    assert snapshot(state) == before and counters(state) == limits


@pytest.mark.parametrize(
    "changes",
    [
        {"Origin": None},
        {"Origin": "null"},
        {"Origin": "https://other.example"},
        {"Sec-Fetch-Site": "same-site"},
        {"X-CSRF-Token": None},
        {"X-CSRF-Token": CANARY},
    ],
)
def test_origin_and_csrf_reject_before_service(web, changes):
    _, state, _ = web
    before, limits = snapshot(state), counters(state)
    denied(post(web, changes=changes), 403, "Request rejected")
    assert snapshot(state) == before and counters(state) == limits


@pytest.mark.parametrize(
    "name", ["Origin", "X-CSRF-Token", "X-Expected-Membership", "Cookie", "X-Real-IP"]
)
def test_duplicate_headers_are_rejected_before_rotation(web, name):
    client, state, policy = web
    values = list(headers(client, state, policy).items())
    if name == "Cookie":
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        values += [(name, cookie), (name, cookie)]
    elif name == "X-Real-IP":
        values += [(name, "127.0.0.1"), (name, "127.0.0.1")]
    else:
        values.append((name, headers(client, state, policy)[name]))
    before, limits = snapshot(state), counters(state)
    response = client.post(
        PATH,
        json={
            "password": state["password"].get_secret_value(),
            "code": next_factor(state),
        },
        headers=values,
    )
    denied(response, 403, "Request rejected")
    assert snapshot(state) == before and counters(state) == limits


@pytest.mark.parametrize("kind", ["session", "csrf", "preauth", "challenge"])
def test_ambiguous_or_mixed_cookies_cannot_confirm(web, kind):
    client, state, policy = web
    cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
    name = {
        "session": policy.session_cookie,
        "csrf": policy.csrf_cookie,
        "preauth": policy.preauth_cookie,
        "challenge": policy.challenge_cookie,
    }[kind]
    value = client.cookies.get(name, CANARY)
    before, limits = snapshot(state), counters(state)
    denied(
        post(web, changes={"Cookie": cookie + f"; {name}={value}"}),
        403,
        "Request rejected",
    )
    assert snapshot(state) == before and counters(state) == limits


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"password": ""},
        {"password": "x" * 129},
        {"password": "\ud800"},
        {"password": 123},
        {"password": None},
        {"password": {}},
        [],
    ],
)
def test_invalid_payload_is_sanitized_before_service(web, data):
    _, state, _ = web
    before, limits = snapshot(state), counters(state)
    denied(post(web, data=data), 422, "Invalid request")
    assert snapshot(state) == before and counters(state) == limits


@pytest.mark.parametrize(
    "field",
    [
        "email",
        "tenant_id",
        "role",
        "user_id",
        "verified_at",
        "client_ip",
        "token",
        "password_hash",
        "price",
        "stock",
    ],
)
def test_identity_authority_and_metadata_fields_are_forbidden(web, field):
    _, state, _ = web
    before, limits = snapshot(state), counters(state)
    denied(
        post(
            web, data={"password": state["password"].get_secret_value(), field: CANARY}
        ),
        422,
        "Invalid request",
    )
    assert snapshot(state) == before and counters(state) == limits


@pytest.mark.parametrize(
    "path", [PATH + "?probe=" + CANARY, PATH + "?password=" + CANARY, PATH + "?x=1&x=2"]
)
def test_query_is_rejected_without_echo_or_rotation(web, path):
    _, state, _ = web
    before, limits = snapshot(state), counters(state)
    denied(post(web, path=path), 422, "Invalid request")
    assert snapshot(state) == before and counters(state) == limits


@pytest.mark.parametrize(
    "raw,status",
    [
        (b'{"password":', 400),
        (b'{"password":"x","password":"y"}', 400),
        (b'{"extra":{"x":1,"x":2}}', 400),
        (b" " * 8193, 413),
    ],
)
def test_invalid_ambiguous_or_excessive_json_never_reaches_service(web, raw, status):
    _, state, _ = web
    before, limits = snapshot(state), counters(state)
    denied(post(web, raw=raw), status, "Request rejected")
    assert snapshot(state) == before and counters(state) == limits


def test_wrong_content_type_is_rejected_without_side_effects(web):
    _, state, _ = web
    before, limits = snapshot(state), counters(state)
    denied(post(web, changes={"Content-Type": "text/plain"}), 415, "Request rejected")
    assert snapshot(state) == before and counters(state) == limits


def test_direct_peer_cannot_claim_a_trusted_proxy(web, monkeypatch):
    _, state, _ = web
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.3")},
    )
    before, limits = snapshot(state), counters(state)
    denied(post(web), 403, "Request rejected")
    assert snapshot(state) == before and counters(state) == limits


def test_hash_slots_are_shared_and_released_without_queued_work(web):
    client, state, _ = web
    slots = client.app.state.access._hash_slots
    assert slots.acquire(False) and slots.acquire(False)
    before, limits = snapshot(state), counters(state)
    try:
        denied(post(web), 503, "Authentication unavailable")
        assert snapshot(state) == before and counters(state) == limits
    finally:
        slots.release()
        slots.release()
    assert post(web).status_code == 204


def test_failed_commit_publishes_no_cookies_or_actor_and_releases_hash_slot(web):
    client, state, _ = web
    engine = client.app.state.access._factory.kw["bind"]
    before, limits, cookies = (
        snapshot(state),
        counters(state),
        dict(client.cookies.items()),
    )

    def fail(_connection):
        raise SQLAlchemyError(CANARY)

    event.listen(engine, "commit", fail)
    try:
        denied(post(web), 503, "Authentication unavailable")
    finally:
        event.remove(engine, "commit", fail)
    assert snapshot(state) == before and counters(state) == limits
    assert dict(client.cookies.items()) == cookies
    assert post(web).status_code == 204


def test_confirmation_rejections_exhaust_the_login_account_budget(web):
    client, state, policy = web
    before = snapshot(state)
    for _ in range(6):
        denied(post(web, data={"password": CANARY}), 401, "Access denied")
    denied(post(web), 401, "Access denied")
    assert snapshot(state) == before
    client.cookies.clear()
    assert client.get("/auth/csrf", headers=READ).status_code == 204
    denied(
        client.post(
            "/auth/login",
            json={
                "email": state["email"],
                "password": state["password"].get_secret_value(),
            },
            headers=headers(client, state, policy),
        ),
        401,
        "Access denied",
    )
    assert next(row.failures for row in counters(state) if row.scope == "account") == 5
