"""Cookie HTTP flows with real PostgreSQL; transport peer simulated explicitly."""

import secrets
from datetime import timedelta
from ipaddress import ip_address
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from identity_http_fixture import arrange_factor, next_factor
from pydantic import SecretStr
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access import mfa_pending
from app.modules.access.models import (
    LoginThrottle,
    MfaPending,
    SessionRecord,
    TenantMembership,
    User,
)

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def web(http_identity, monkeypatch):
    state = http_identity
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    app = create_app(state["settings"])
    with TestClient(
        app,
        base_url=state["settings"].public_origin,
        client=("127.0.0.2", 12345),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as client:
        arrange_factor(state, app, monkeypatch)
        yield client, state, app.state.http_security


def bootstrap(client):
    response = client.get("/auth/csrf", headers=READ)
    assert response.status_code == 204 and response.content == b""
    assert response.headers["cache-control"] == "no-store"
    return response


def write(client, policy, path, body, **kwargs):
    return client.post(
        path,
        json=body,
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies.get(policy.csrf_cookie, ""),
            "Sec-Fetch-Site": "same-origin",
        },
        **kwargs,
    )


def login(client, state, policy, **extra):
    code = next_factor(state)
    response = write(
        client,
        policy,
        "/auth/login",
        {
            "email": state["email"],
            "password": state["password"].get_secret_value(),
            **extra,
        },
    )
    if response.status_code == 202 and response.json().get("status") == "mfa_totp":
        return write(client, policy, "/auth/mfa/totp", {"code": code})
    return response


def multi(state):
    with Session(state["runtime"]) as session, session.begin():
        membership = TenantMembership(
            tenant_id=state["other"], user_id=state["user"], role="mechanic"
        )
        session.add(membership)
        session.flush()
        return membership.id


def count(state, model):
    with Session(state["runtime"]) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_login_session_csrf_refresh_logout_and_replay(web):
    client, state, policy = web
    bootstrap(client)
    assert client.get("/auth/session", headers=READ).status_code == 401
    response = login(client, state, policy)
    assert response.status_code == 200 and response.json() == {
        "status": "authenticated"
    }
    assert state["password"].get_secret_value() not in response.text
    token = client.cookies[policy.session_cookie]
    assert token not in response.text and policy.preauth_cookie not in client.cookies
    assert "HttpOnly" in response.headers.get_list("set-cookie")[0]
    current = client.get("/auth/session", headers=READ)
    assert current.status_code == 200 and current.headers["cache-control"] == "no-store"
    assert current.json() == {
        "user_id": str(state["user"]),
        "tenant_id": str(state["tenant"]),
        "membership_id": str(state["membership"]),
        "tenant_name": "HTTP company",
        "role": "admin",
    }
    with Session(state["runtime"]) as session:
        expiry = session.scalar(select(SessionRecord.expires_at))
    renewed = bootstrap(client)
    assert client.cookies[policy.session_cookie] == token
    assert all(
        not cookie.startswith(policy.session_cookie + "=")
        for cookie in renewed.headers.get_list("set-cookie")
    )
    with Session(state["runtime"]) as session:
        assert session.scalar(select(SessionRecord.expires_at)) == expiry
    old_cookies = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
    old_csrf = client.cookies[policy.csrf_cookie]
    assert write(client, policy, "/auth/logout", {}).status_code == 204
    assert not list(client.cookies.items())
    assert (
        client.get("/auth/session", headers=READ | {"Cookie": old_cookies}).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/logout",
            json={},
            headers={
                "Cookie": old_cookies,
                "Origin": policy.origin,
                "X-CSRF-Token": old_csrf,
            },
        ).status_code
        == 204
    )
    with Session(state["runtime"]) as session:
        assert session.scalar(select(SessionRecord.revoked_at)) is not None


def test_pending_selection_preserves_binding_during_csrf_refresh(web):
    client, state, policy = web
    selected = multi(state)
    bootstrap(client)
    nonce = client.cookies[policy.preauth_cookie]
    response = login(client, state, policy)
    assert (
        response.status_code == 202 and response.json()["status"] == "select_membership"
    )
    assert {choice["membership_id"] for choice in response.json()["choices"]} == {
        str(state["membership"]),
        str(selected),
    }
    assert (
        policy.session_cookie not in client.cookies and count(state, SessionRecord) == 0
    )
    pending = client.cookies[policy.challenge_cookie]
    assert pending not in response.text and "token" not in response.json()
    bootstrap(client)
    assert (
        client.cookies[policy.preauth_cookie] == nonce
        and client.cookies[policy.challenge_cookie] == pending
    )
    result = write(
        client, policy, "/auth/select-membership", {"membership_id": str(selected)}
    )
    assert result.status_code == 200
    assert (
        policy.challenge_cookie not in client.cookies
        and policy.preauth_cookie not in client.cookies
    )
    current = client.get("/auth/session", headers=READ).json()
    assert current["tenant_id"] == str(state["other"]) and current["role"] == "mechanic"
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(MfaPending.finished_at).where(
                    MfaPending.purpose == "login_verified"
                )
            )
            is not None
        )


def test_pending_logout_cancels_identity_before_deleting_cookies(web):
    client, state, policy = web
    multi(state)
    bootstrap(client)
    assert login(client, state, policy).status_code == 202
    cookies = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
    proof = client.cookies[policy.csrf_cookie]
    assert write(client, policy, "/auth/logout", {}).status_code == 204
    assert not list(client.cookies.items())
    response = client.post(
        "/auth/select-membership",
        json={"membership_id": str(state["membership"])},
        headers={"Cookie": cookies, "Origin": policy.origin, "X-CSRF-Token": proof},
    )
    assert response.status_code == 401 and count(state, SessionRecord) == 0
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(MfaPending.finished_at).where(
                    MfaPending.purpose == "login_verified"
                )
            )
            is not None
        )


@pytest.mark.parametrize("candidate", ["foreign", "unknown", "tenant"])
def test_foreign_selection_is_generic_and_one_use(web, candidate):
    client, state, policy = web
    multi(state)
    bootstrap(client)
    assert login(client, state, policy).status_code == 202
    choice = {
        "foreign": state["foreign_membership"],
        "unknown": uuid4(),
        "tenant": state["tenant"],
    }[candidate]
    response = write(
        client, policy, "/auth/select-membership", {"membership_id": str(choice)}
    )
    assert response.status_code == 401 and response.json() == {
        "detail": "Access denied"
    }
    assert (
        policy.session_cookie not in client.cookies
        and policy.challenge_cookie not in client.cookies
    )
    assert count(state, SessionRecord) == 0


@pytest.mark.parametrize(
    "field", ["tenant_id", "role", "price", "stock", "user_id", "client_ip"]
)
def test_client_authority_is_rejected_without_echo_or_counters(web, field):
    client, state, policy = web
    bootstrap(client)
    response = login(client, state, policy, **{field: "canary-rejected-value"})
    assert response.status_code == 422 and response.json() == {
        "detail": "Invalid request"
    }
    assert state["password"].get_secret_value() not in response.text
    assert "canary-rejected-value" not in response.text
    assert count(state, LoginThrottle) == count(state, SessionRecord) == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Requested-With": "XMLHttpRequest", "Origin": "null"},
        {"X-Requested-With": "XMLHttpRequest", "Origin": "https://foreign.example"},
        {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-site"},
    ],
)
@pytest.mark.parametrize("path", ["/auth/csrf", "/auth/session"])
def test_cookie_reads_cannot_be_bootstrapped_cross_origin(web, path, headers):
    client, state, _ = web
    response = client.get(path, headers=headers)
    assert response.status_code == 403 and "set-cookie" not in response.headers
    assert (
        response.headers["cache-control"] == "no-store"
        and count(state, SessionRecord) == 0
    )


def test_preflight_does_not_allow_cross_origin_custom_header(web):
    client, _, _ = web
    response = client.options(
        "/auth/csrf",
        headers={
            "Origin": "https://foreign.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Requested-With",
        },
    )
    assert (
        response.status_code == 405
        and "access-control-allow-origin" not in response.headers
    )
    assert "set-cookie" not in response.headers


def test_http_rejections_share_message_and_commit_rate_counters(web):
    client, state, policy = web
    bootstrap(client)
    for _ in range(6):
        response = login(client, state, policy, password=secrets.token_urlsafe(32))
        assert response.status_code == 401 and response.json() == {
            "detail": "Access denied"
        }
        assert (
            "retry-after" not in response.headers
            and "set-cookie" not in response.headers
        )
    assert login(client, state, policy).status_code == 401
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(LoginThrottle.failures).where(LoginThrottle.scope == "account")
            )
            == 5
        )
    assert count(state, SessionRecord) == 0


def test_guard_stops_login_without_csrf_before_creating_counters(web):
    client, state, _ = web
    response = client.post(
        "/auth/login",
        json={
            "email": state["email"],
            "password": state["password"].get_secret_value(),
        },
    )
    assert response.status_code == 403 and "set-cookie" not in response.headers
    assert count(state, LoginThrottle) == 0


@pytest.mark.parametrize("change", ["revoked", "expired", "membership", "user"])
def test_session_rechecks_db_and_csrf_can_recover_stale_cookie(web, change):
    client, state, policy = web
    bootstrap(client)
    assert login(client, state, policy).status_code == 200
    with Session(state["runtime"]) as session, session.begin():
        row = session.scalar(select(SessionRecord))
        if change == "revoked":
            row.revoked_at = row.created_at
        elif change == "expired":
            row.created_at -= timedelta(hours=9)
            row.last_seen_at -= timedelta(hours=9)
            row.expires_at -= timedelta(hours=9)
        else:
            model, identity = (
                (User, state["user"])
                if change == "user"
                else (TenantMembership, state["membership"])
            )
            session.execute(
                update(model).where(model.id == identity).values(is_active=False)
            )
    response = bootstrap(client)
    assert (
        policy.session_cookie not in client.cookies
        and policy.preauth_cookie in client.cookies
    )
    assert client.get("/auth/session", headers=READ).status_code == 401
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("operation", ["login", "select", "logout", "session", "csrf"])
def test_failed_commit_does_not_publish_or_clear_authentication_cookies(web, operation):
    client, state, policy = web
    bootstrap(client)
    if operation == "select":
        multi(state)
        assert login(client, state, policy).status_code == 202
    elif operation != "login":
        assert login(client, state, policy).status_code == 200
    before = dict(client.cookies.items())
    engine = client.app.state.access._factory.kw["bind"]
    canary = secrets.token_urlsafe(24)

    def fail_commit(_connection):
        raise SQLAlchemyError(canary)

    event.listen(engine, "commit", fail_commit)
    try:
        if operation == "login":
            response = login(client, state, policy)
        elif operation == "select":
            response = write(
                client,
                policy,
                "/auth/select-membership",
                {"membership_id": str(state["membership"])},
            )
        elif operation == "logout":
            response = write(client, policy, "/auth/logout", {})
        else:
            response = client.get(f"/auth/{operation}", headers=READ)
        assert response.status_code == 503 and response.json() == {
            "detail": "Authentication unavailable"
        }
        assert canary not in response.text and "set-cookie" not in response.headers
        assert dict(client.cookies.items()) == before
    finally:
        event.remove(engine, "commit", fail_commit)
    if operation == "login":
        assert (
            count(state, SessionRecord) == 0
            and login(client, state, policy).status_code == 200
        )
    elif operation == "select":
        with Session(state["runtime"]) as session:
            assert (
                session.scalar(
                    select(MfaPending.finished_at).where(
                        MfaPending.purpose == "login_verified"
                    )
                )
                is None
            )
        assert (
            write(
                client,
                policy,
                "/auth/select-membership",
                {"membership_id": str(state["membership"])},
            ).status_code
            == 200
        )
    else:
        assert client.get("/auth/session", headers=READ).status_code == 200


def test_hash_work_is_bounded_and_capacity_is_released(web):
    client, state, policy = web
    bootstrap(client)
    slots = client.app.state.access._hash_slots
    assert slots.acquire(False) and slots.acquire(False)
    try:
        assert login(client, state, policy).status_code == 503
        assert count(state, LoginThrottle) == 0
    finally:
        slots.release()
        slots.release()
    assert login(client, state, policy).status_code == 200


def test_direct_peer_cannot_claim_proxy_headers(web, monkeypatch):
    client, state, policy = web
    bootstrap(client)
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.3")},
    )
    assert login(client, state, policy).status_code == 403
    assert count(state, LoginThrottle) == 0


def test_authenticated_browser_must_logout_before_new_login(web):
    client, state, policy = web
    bootstrap(client)
    assert login(client, state, policy).status_code == 200
    assert login(client, state, policy).status_code == 403
    assert count(state, SessionRecord) == 1


def test_other_browser_cannot_use_pending_identity_or_consume_it(web):
    client, state, policy = web
    multi(state)
    bootstrap(client)
    assert login(client, state, policy).status_code == 202
    pending = client.cookies[policy.challenge_cookie]
    with TestClient(
        create_app(state["settings"]),
        base_url=policy.origin,
        client=("127.0.0.2", 12346),
    ) as other:
        bootstrap(other)
        other.cookies.set(policy.challenge_cookie, pending)
        assert (
            write(
                other,
                policy,
                "/auth/select-membership",
                {"membership_id": str(state["membership"])},
            ).status_code
            == 401
        )
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(MfaPending.finished_at).where(
                    MfaPending.purpose == "login_verified"
                )
            )
            is None
        )
    assert (
        write(
            client,
            policy,
            "/auth/select-membership",
            {"membership_id": str(state["membership"])},
        ).status_code
        == 200
    )


@pytest.mark.parametrize("fail_commit", [False, True])
def test_mixed_browser_cookies_logout_revokes_session_and_pending_atomically(
    web, fail_commit
):
    client, state, policy = web
    bootstrap(client)
    assert login(client, state, policy).status_code == 200
    multi(state)
    with TestClient(
        create_app(state["settings"]),
        base_url=policy.origin,
        client=("127.0.0.2", 12347),
        headers={"X-Real-IP": "127.0.0.1"},
    ) as other:
        bootstrap(other)
        assert login(other, state, policy).status_code == 202
        pending_hash = mfa_pending._hash(
            SecretStr(other.cookies[policy.challenge_cookie]), "mfa-pending-token-v1:"
        )
        # Simulate overlapping browser responses: retain the original session CSRF.
        for name in (policy.preauth_cookie, policy.challenge_cookie):
            client.cookies.set(name, other.cookies[name])
        engine = client.app.state.access._factory.kw["bind"]

        def reject(_connection):
            raise SQLAlchemyError("synthetic-commit-failure")

        if fail_commit:
            event.listen(engine, "commit", reject)
        try:
            response = write(client, policy, "/auth/logout", {})
            assert response.status_code == (503 if fail_commit else 204)
            if fail_commit:
                assert "set-cookie" not in response.headers
        finally:
            if fail_commit:
                event.remove(engine, "commit", reject)
        with Session(state["runtime"]) as session:
            assert (
                session.scalar(
                    select(MfaPending.finished_at).where(
                        MfaPending.purpose == "login_verified",
                        MfaPending.token_hash == pending_hash,
                    )
                )
                is not None
            ) is not fail_commit
            assert (
                session.scalar(select(SessionRecord.revoked_at)) is not None
            ) is not fail_commit
        if fail_commit:
            assert write(client, policy, "/auth/logout", {}).status_code == 204
