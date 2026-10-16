"""Actual ASGI guard with test-only routes; no access endpoints in the app."""

import secrets
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from unittest.mock import MagicMock

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.datastructures import Headers

from app.core.config import ConfigurationError, Settings
from app.main import create_app
from app.modules.access import http_security as http
from app.modules.access.authentication import Credentials
from app.modules.access.sessions import IssuedSession


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr("app.main.build_engine", lambda _: MagicMock())
    app = create_app(Settings(app_env="test"))
    policy = app.state.http_security
    effects = []

    @app.get("/_test/preauth")
    def preauth(response: Response):
        policy.start_preauth(response)
        return {"ready": True}

    @app.get("/_test/session")
    def session_cookie(response: Response):
        issued = IssuedSession(
            SecretStr(secrets.token_urlsafe(32)), http._now() + timedelta(hours=8)
        )
        policy.set_session(response, issued)
        return {"ready": True}

    @app.post("/_test/write")
    def write(data: Credentials):
        effects.append(True)
        return {"accepted": True}

    with TestClient(app, base_url="https://testserver") as client:
        yield client, policy, effects


def valid_headers(client, policy):
    return {"Origin": policy.origin, "X-CSRF-Token": client.cookies[policy.csrf_cookie]}


def payload():
    return {"email": "admin@example.com", "password": secrets.token_urlsafe(24)}


@pytest.mark.parametrize("bootstrap", ["preauth", "session"])
def test_valid_proof_allows_json_and_keeps_session_token_out_of_response(
    web, bootstrap
):
    client, policy, effects = web
    minted = client.get("/_test/" + bootstrap)
    assert minted.json() == {"ready": True}
    assert minted.headers["cache-control"] == "no-store"
    result = client.post(
        "/_test/write", json=payload(), headers=valid_headers(client, policy)
    )
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert effects == [True]
    if bootstrap == "session":
        token = client.cookies[policy.session_cookie]
        assert (
            token not in minted.text and token not in client.cookies[policy.csrf_cookie]
        )


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://untrusted.example",
        "https://testserver:443",
        "https://testserver/",
        "http://testserver",
    ],
)
def test_wrong_missing_or_noncanonical_origin_never_reaches_handler(web, origin):
    client, policy, effects = web
    client.get("/_test/preauth")
    headers = valid_headers(client, policy)
    if origin is None:
        del headers["Origin"]
    else:
        headers["Origin"] = origin
    response = client.post("/_test/write", json=payload(), headers=headers)
    assert response.status_code == 403 and not effects
    assert response.json() == {"detail": "Request rejected"}


@pytest.mark.parametrize("site", ["cross-site", "same-site", "none"])
def test_fetch_site_must_be_same_origin_when_present(web, site):
    client, policy, effects = web
    client.get("/_test/preauth")
    headers = valid_headers(client, policy) | {"Sec-Fetch-Site": site}
    assert (
        client.post("/_test/write", json=payload(), headers=headers).status_code == 403
    )
    assert not effects


@pytest.mark.parametrize(
    "change",
    [
        "missing_header",
        "missing_cookie",
        "tampered",
        "other_nonce",
        "bad_session",
        "duplicate_origin",
        "duplicate_header",
        "duplicate_cookie",
    ],
)
def test_ambiguous_missing_tampered_or_unbound_proofs_fail_closed(web, change):
    client, policy, effects = web
    client.get("/_test/preauth")
    headers = valid_headers(client, policy)
    if change == "missing_header":
        del headers["X-CSRF-Token"]
    elif change == "missing_cookie":
        client.cookies.delete(policy.csrf_cookie)
    elif change == "tampered":
        headers["X-CSRF-Token"] = "v1.1." + "a" * 43 + "." + "a" * 64
    elif change == "other_nonce":
        old = client.cookies[policy.csrf_cookie]
        client.get("/_test/preauth")
        headers["X-CSRF-Token"] = old
        # Equality alone must not authorize an old proof with a new nonce binding.
        client.cookies.set(policy.csrf_cookie, old, domain="testserver.local", path="/")
    elif change == "bad_session":
        client.cookies.set(
            policy.session_cookie, "invalid", domain="testserver.local", path="/"
        )
    elif change in {"duplicate_origin", "duplicate_header"}:
        repeated = "Origin" if change == "duplicate_origin" else "X-CSRF-Token"
        headers = list(headers.items()) + [(repeated, headers[repeated])]
    else:
        raw = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
        headers["Cookie"] = (
            raw + f"; {policy.csrf_cookie}=" + client.cookies[policy.csrf_cookie]
        )
    response = client.post("/_test/write", json=payload(), headers=headers)
    assert response.status_code == 403 and not effects


def test_session_rotation_invalidates_old_proof_even_if_cookie_and_header_match(web):
    client, policy, effects = web
    client.get("/_test/session")
    old = client.cookies[policy.csrf_cookie]
    client.get("/_test/session")
    raw = (
        f"{policy.session_cookie}={client.cookies[policy.session_cookie]}; "
        f"{policy.csrf_cookie}={old}"
    )
    headers = {"Origin": policy.origin, "Cookie": raw, "X-CSRF-Token": old}
    assert (
        client.post("/_test/write", json=payload(), headers=headers).status_code == 403
    )
    assert not effects


def test_exact_expiry_and_different_worker_key_reject(web, monkeypatch):
    client, policy, effects = web
    now = datetime.now(UTC)
    monkeypatch.setattr(http, "_now", lambda: now)
    client.get("/_test/preauth")
    headers = valid_headers(client, policy)
    raw_cookie = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    other = http.HttpSecurity(
        Settings(app_env="test", csrf_signing_key=SecretStr(secrets.token_hex(32)))
    )
    with pytest.raises(http.RequestRejected):
        other.verify_request(Headers(headers | {"Cookie": raw_cookie}))
    expiry = int(client.cookies[policy.csrf_cookie].split(".")[1])
    monkeypatch.setattr(http, "_now", lambda: datetime.fromtimestamp(expiry, UTC))
    assert (
        client.post("/_test/write", json=payload(), headers=headers).status_code == 403
    )
    assert not effects


@pytest.mark.parametrize(
    "body,content_type,expected",
    [
        ("password=secret", "application/x-www-form-urlencoded", 415),
        ("{invalid", "application/json", 400),
        ('{"value":NaN}', "application/json", 400),
        (" " * 8193, "application/json", 413),
    ],
)
def test_content_and_size_are_checked_before_handler(web, body, content_type, expected):
    client, policy, effects = web
    client.get("/_test/preauth")
    response = client.post(
        "/_test/write",
        content=body,
        headers=valid_headers(client, policy) | {"Content-Type": content_type},
    )
    assert response.status_code == expected and not effects
    assert response.json() == {"detail": "Request rejected"}


def test_pydantic_validation_never_echoes_password(web):
    client, policy, effects = web
    client.get("/_test/preauth")
    canary = secrets.token_urlsafe(128)
    response = client.post(
        "/_test/write",
        json={"email": "admin@example.com", "password": canary},
        headers=valid_headers(client, policy),
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert canary not in response.text and not effects


@pytest.mark.parametrize(
    "environment,origin,secure",
    [
        ("production", "https://taller.example", True),
        ("development", "http://localhost:8080", False),
        ("test", "https://testserver", True),
    ],
)
def test_cookie_scope_flags_and_deletion_match(environment, origin, secure):
    policy = http.HttpSecurity(Settings(app_env=environment, public_origin=origin))
    response = Response()
    policy.set_session(
        response,
        IssuedSession(
            SecretStr(secrets.token_urlsafe(32)), datetime.now(UTC) + timedelta(hours=8)
        ),
    )
    cookies = SimpleCookie()
    for value in response.headers.getlist("set-cookie"):
        cookies.load(value)
    session, csrf = cookies[policy.session_cookie], cookies[policy.csrf_cookie]
    assert bool(session["secure"]) is secure
    assert bool(session["httponly"]) and not csrf["httponly"]
    assert session["path"] == csrf["path"] == "/"
    assert session["samesite"] == csrf["samesite"] == "lax"
    assert not session["domain"] and not csrf["domain"]
    assert policy.session_cookie.startswith("__Host-") is secure
    assert 0 < int(session["max-age"]) <= 28800
    assert 0 < int(csrf["max-age"]) <= 900
    deletion = Response()
    policy.clear_cookies(deletion)
    deleted = SimpleCookie()
    for value in deletion.headers.getlist("set-cookie"):
        deleted.load(value)
    for name in (policy.session_cookie, policy.preauth_cookie, policy.csrf_cookie):
        assert deleted[name]["max-age"] == "0"
        assert deleted[name]["path"] == "/" and not deleted[name]["domain"]
        assert bool(deleted[name]["secure"]) is secure


@pytest.mark.parametrize(
    "origin",
    [
        "http://taller.example",
        "https://user:secret@taller.example",
        "https://taller.example/",
        "https://taller.example?x=1",
        "https://taller.example#x",
        "https://TALLER.example",
        "https://taller.example:443",
        "https://a..example",
        "https://taller.example:99999",
    ],
)
def test_bad_origin_configuration_is_rejected_without_echo(origin):
    with pytest.raises(ConfigurationError) as caught:
        http.HttpSecurity(Settings(app_env="production", public_origin=origin))
    assert origin not in str(caught.value)


def test_missing_http_configuration_never_starts_app(monkeypatch):
    monkeypatch.delenv("PUBLIC_ORIGIN")
    monkeypatch.delenv("CSRF_SIGNING_KEY")
    with pytest.raises(ConfigurationError, match="HTTP requires"):
        create_app(Settings(app_env="test"))


def test_oversized_chunked_body_is_stopped_before_handler(web):
    import asyncio

    client, policy, effects = web
    client.get("/_test/preauth")
    raw = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    headers = valid_headers(client, policy) | {
        "Cookie": raw,
        "Content-Type": "application/json",
    }
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/_test/write",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in headers.items()
        ],
    }
    messages = [
        {"type": "http.request", "body": b"a" * 5000, "more_body": True},
        {"type": "http.request", "body": b"b" * 5000, "more_body": False},
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    async def must_not_run(_scope, _receive, _send):
        pytest.fail("Oversized request reached the handler")

    from app.modules.access.http_middleware import HttpSecurityMiddleware

    asyncio.run(
        HttpSecurityMiddleware(must_not_run, policy=policy)(scope, receive, send)
    )
    assert sent[0]["status"] == 413 and not effects


def test_non_ascii_cookie_proof_is_rejected_safely(web):
    client, policy, _ = web
    client.get("/_test/preauth")
    headers = valid_headers(client, policy)
    raw = (
        f"{policy.csrf_cookie}=\u00ff; "
        f"{policy.preauth_cookie}={client.cookies[policy.preauth_cookie]}"
    )
    parsed = Headers(
        raw=[(key.lower().encode(), value.encode()) for key, value in headers.items()]
        + [(b"cookie", raw.encode("latin-1"))]
    )
    with pytest.raises(http.RequestRejected):
        policy.verify_request(parsed)
