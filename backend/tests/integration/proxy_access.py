"""Explicit CI-only tests through real Nginx/Uvicorn, outside auto collection."""

import hashlib
import json
import os
import socket
from http.client import HTTPResponse
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.models import AuditEvent, LoginThrottle, TenantMembership

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_PROXY_INTEGRATION") != "1",
        reason="Explicit isolated CI proxy run required",
    ),
]
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}
CANARY = "carrillo-http-log-canary"


class Browser:
    def __init__(self, state, base="http://proxy:8080/api"):
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))
        self.origin, self.base = state["settings"].public_origin, base

    def request(self, path, *, body=None, headers=None, method=None):
        request = Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers or {},
            method=method or ("POST" if body is not None else "GET"),
        )
        if body is not None:
            request.add_header("Content-Type", "application/json")
            request.add_header("Origin", self.origin)
            proof = next(
                (
                    cookie.value
                    for cookie in self.cookies
                    if cookie.name == "carrillo_csrf"
                ),
                "",
            )
            request.add_header("X-CSRF-Token", proof)
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(8192)
            return response.status, json.loads(raw) if raw else None, response.headers


def test_real_proxy_overwrites_forged_ips_and_logout_revokes(http_identity):
    state = http_identity
    browser = Browser(state)
    assert browser.request("/auth/csrf", headers=READ)[0] == 204
    # The local address on the shared API network is what Nginx must observe.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("proxy", 8080))
        expected_ip = route.getsockname()[0]
    for fake in ("127.0.9.1", "127.0.9.2"):
        status, data, headers = browser.request(
            "/auth/login?probe=" + CANARY,
            body={"email": state["email"], "password": CANARY},
            headers={
                "X-Real-IP": fake,
                "X-Forwarded-For": fake,
                "Forwarded": "for=" + fake,
                "X-Forwarded-Host": CANARY,
                "X-Log-Probe": CANARY,
            },
        )
        assert status == 401 and data == {"detail": "Access denied"}
        assert headers["Cache-Control"] == "no-store"
    with Session(state["runtime"]) as session:
        buckets = session.scalars(
            select(LoginThrottle).where(LoginThrottle.scope == "ip")
        ).all()
        assert len(buckets) == 1 and buckets[0].failures == 2
        assert buckets[0].key_hash == hashlib.sha256(expected_ip.encode()).hexdigest()
    status, data, _ = browser.request(
        "/auth/login",
        body={
            "email": state["email"],
            "password": state["password"].get_secret_value(),
        },
    )
    assert status == 200 and data == {"status": "authenticated"}
    status, data, _ = browser.request("/auth/session", headers=READ)
    assert (
        status == 200
        and data["tenant_id"] == str(state["tenant"])
        and data["role"] == "admin"
    )
    saved = "; ".join(f"{cookie.name}={cookie.value}" for cookie in browser.cookies)
    assert browser.request("/auth/logout", body={})[0] == 204
    assert not list(browser.cookies)
    assert browser.request("/auth/session", headers=READ | {"Cookie": saved})[0] == 401


def test_real_backend_rejects_direct_peer_with_claimed_proxy_headers(http_identity):
    state = http_identity
    direct = Browser(state, base="http://backend:8000")
    assert direct.request("/auth/csrf", headers=READ)[0] == 204
    status, data, _ = direct.request(
        "/auth/login",
        body={
            "email": state["email"],
            "password": state["password"].get_secret_value(),
        },
        headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-For": "proxy"},
    )
    assert status == 403 and data == {"detail": "Request rejected"}
    with Session(state["runtime"]) as session:
        assert session.scalar(select(LoginThrottle)) is None


def test_multi_company_selection_through_real_proxy(http_identity):
    state = http_identity
    with Session(state["runtime"]) as session, session.begin():
        choice = TenantMembership(
            tenant_id=state["other"], user_id=state["user"], role="mechanic"
        )
        session.add(choice)
        session.flush()
        membership_id = str(choice.id)
    browser = Browser(state)
    assert browser.request("/auth/csrf", headers=READ)[0] == 204
    status, data, _ = browser.request(
        "/auth/login",
        body={
            "email": state["email"],
            "password": state["password"].get_secret_value(),
        },
    )
    assert status == 202 and data["status"] == "select_membership"
    assert membership_id in {choice["membership_id"] for choice in data["choices"]}
    assert not any(cookie.name == "carrillo_session" for cookie in browser.cookies)
    assert browser.request("/auth/csrf", headers=READ)[0] == 204
    assert (
        browser.request(
            "/auth/select-membership", body={"membership_id": membership_id}
        )[0]
        == 200
    )
    status, data, _ = browser.request("/auth/session", headers=READ)
    assert (
        status == 200
        and data["tenant_id"] == str(state["other"])
        and data["role"] == "mechanic"
    )
    assert browser.request("/auth/logout", body={})[0] == 204


def test_membership_read_patch_stale_and_foreign_rejection_through_real_proxy(
    http_identity,
):
    state = http_identity
    with Session(state["runtime"]) as session, session.begin():
        target = TenantMembership(
            tenant_id=state["tenant"], user_id=state["foreign"], role="cashier"
        )
        session.add(target)
        session.flush()
        membership_id = target.id
    browser = Browser(state)
    assert browser.request("/auth/csrf", headers=READ)[0] == 204
    assert (
        browser.request(
            "/auth/login",
            body={
                "email": state["email"],
                "password": state["password"].get_secret_value(),
            },
        )[0]
        == 200
    )
    target_path = f"/auth/memberships/{membership_id}"
    status, data, headers = browser.request(target_path, headers=READ)
    assert status == 200 and data == {
        "membership_id": str(membership_id),
        "role": "cashier",
        "is_active": True,
    }
    old_tag = headers["ETag"]
    status, data, headers = browser.request(
        target_path,
        method="PATCH",
        body={"role": "mechanic", "is_active": True},
        headers={"If-Match": old_tag},
    )
    assert status == 200 and data["role"] == "mechanic"
    assert headers["Cache-Control"] == "no-store" and headers["ETag"] != old_tag
    assert (
        browser.request(
            target_path,
            method="PATCH",
            body={"role": "admin", "is_active": True},
            headers={"If-Match": old_tag},
        )[0]
        == 412
    )
    assert (
        browser.request(
            f"/auth/memberships/{state['foreign_membership']}", headers=READ
        )[0]
        == 403
    )
    # Existing proxy log canary inspection also covers administration/query/header.
    status, data, headers = browser.request(
        target_path + "?probe=" + CANARY, headers=READ | {"X-Log-Probe": CANARY}
    )
    assert status == 422 and CANARY not in json.dumps(data)
    assert headers["Cache-Control"] == "no-store"
    with Session(state["runtime"]) as session:
        assert len(list(session.scalars(select(AuditEvent)))) == 1


def test_proxy_rejects_excess_body_uploads_without_reaching_authentication(
    http_identity,
):
    sockets = []
    try:
        # At most ten active requests from this peer; retain incomplete bounded
        # JSON uploads so the last request must be rejected without timing a burst.
        for _ in range(11):
            connection = socket.create_connection(("proxy", 8080), timeout=5)
            sockets.append(connection)
            connection.sendall(
                b"POST /api/auth/login?probe=carrillo-http-log-canary HTTP/1.1\r\n"
                b"Host: proxy\r\n"
                b"Content-Type: application/json\r\nContent-Length: 8192\r\n"
                b"Connection: close\r\n\r\n"
            )
        with HTTPResponse(sockets[-1]) as response:
            response.begin()
            assert response.status == 429
            assert response.getheader("Cache-Control") == "no-store"
            assert json.loads(response.read(8192)) == {"detail": "Request rejected"}
        with Session(http_identity["runtime"]) as session:
            assert session.scalar(select(LoginThrottle)) is None
    finally:
        for connection in sockets:
            connection.close()
