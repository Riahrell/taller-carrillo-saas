"""Explicit CI-only tests through real Nginx/Uvicorn, outside auto collection."""

import hashlib
import json
import os
import socket
import time
from copy import copy
from datetime import timedelta
from http.client import HTTPResponse
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

import pyotp
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.access.identity_http import IdentityAccess
from app.modules.access.mfa_crypto import generate_totp_seed
from app.modules.access.models import (
    AuditEvent,
    LoginThrottle,
    MfaCredential,
    MfaPending,
    SessionRecord,
    TenantMembership,
)
from app.modules.access.reauthentication import require_recent_administrator
from app.modules.catalog.models import Category, Product
from app.modules.inventory.models import StockBalance, StockCountRecord, StockMovement

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
    _next_request = 0.0

    def __init__(self, state, base="http://proxy:8080/api"):
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))
        self.origin, self.base = state["settings"].public_origin, base
        self.state = state
        if "proxy_seed" not in state:
            identity = uuid4()
            seed = generate_totp_seed()
            keyring = (
                IdentityAccess(state["runtime"], state["settings"].identity_keys_file)
                .require()
                .keyring
            )
            envelope = keyring.encrypt(
                seed, user_id=state["user"], credential_id=identity
            )
            with Session(state["runtime"]) as session, session.begin():
                session.add(
                    MfaCredential(
                        id=identity,
                        user_id=state["user"],
                        encryption_version=envelope.version,
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                        created_at=session.scalar(select(func.clock_timestamp())),
                    )
                )
            state["proxy_seed"] = seed

    def request(self, path, *, body=None, headers=None, method=None):
        if path.startswith("/auth/mfa/reauthenticate") and isinstance(body, dict):
            body = {
                "code": pyotp.TOTP(
                    self.state["proxy_seed"].base32().get_secret_value()
                ).at(time.time(), counter_offset=1),
                **body,
            }
        # Ordinary flows stay below Nginx's shared 10r/s budget across browsers.
        # The final raw-socket test deliberately bypasses this pacing to test limits.
        delay = Browser._next_request - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        Browser._next_request = time.monotonic() + 0.12
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
            result = response.status, json.loads(raw) if raw else None, response.headers
        if (
            path == "/auth/login"
            and result[0] == 202
            and result[1] == {"status": "mfa_totp"}
        ):
            assert not any(cookie.name == "carrillo_session" for cookie in self.cookies)
            return self.request(
                "/auth/mfa/totp",
                body={
                    "code": pyotp.TOTP(
                        self.state["proxy_seed"].base32().get_secret_value()
                    ).now()
                },
            )
        return result


def test_private_catalog_creation_and_tenant_scope_through_real_proxy(http_identity):
    from sqlalchemy import delete

    state = http_identity
    browser = Browser(state)
    try:
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
        expected = {"X-Expected-Membership": str(state["membership"])}
        status, category, _ = browser.request(
            "/catalog/categories", body={"name": "Repuestos"}, headers=expected
        )
        assert status == 201
        status, product, headers = browser.request(
            "/catalog/products",
            body={
                "name": "Filtro",
                "price": "12.34",
                "sku": "PROXY-1",
                "category_id": category["id"],
            },
            headers=expected,
        )
        assert status == 201 and headers["Cache-Control"] == "no-store"
        assert headers["X-Access-Membership"] == str(state["membership"])
        assert browser.request("/catalog/products", headers=READ)[1] == {
            "items": [product],
            "next_after": None,
        }
        assert (
            browser.request("/catalog/products/" + product["id"], headers=READ)[1]
            == product
        )
        assert (
            browser.request(
                "/catalog/products?tenant_id=" + str(state["other"]), headers=READ
            )[0]
            == 422
        )
        with Session(state["runtime"]) as session, session.begin():
            foreign = Product(tenant_id=state["other"], name="Private", price="1.00")
            session.add(foreign)
            session.flush()
            foreign_id = str(foreign.id)
        assert (
            browser.request("/catalog/products/" + foreign_id, headers=READ)[0] == 403
        )
        assert (
            browser.request(
                "/catalog/products",
                body={"name": CANARY * 1000, "price": "1.00"},
                headers=expected,
            )[0]
            == 413
        )
    finally:
        with state["owner"].begin() as connection:
            for model in (AuditEvent, Product, Category):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )


def test_inventory_count_replay_and_balance_through_real_proxy(http_identity):
    from sqlalchemy import delete

    state = http_identity
    browser = Browser(state)
    try:
        with Session(state["runtime"]) as session, session.begin():
            product = Product(
                tenant_id=state["tenant"], name="Proxy stock", price="1.00"
            )
            foreign = Product(
                tenant_id=state["other"], name="Hidden stock", price="1.00"
            )
            session.add_all([product, foreign])
            session.flush()
            product_id, foreign_id = str(product.id), str(foreign.id)
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
        expected = {
            "X-Expected-Membership": str(state["membership"]),
            "Idempotency-Key": str(uuid4()),
        }
        body = {
            "product_id": product_id,
            "counted_quantity": "2",
            "declared_unit_cost": "3",
            "reason": "Proxy count",
        }
        status, evidence, headers = browser.request(
            "/inventory/counts", body=body, headers=expected
        )
        assert status == 200 and evidence["after_value"] == "6.000000"
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Access-Membership"] == str(state["membership"])
        assert (
            browser.request("/inventory/counts", body=body, headers=expected)[1]
            == evidence
        )
        assert browser.request(
            f"/inventory/products/{product_id}/balance", headers=READ
        )[1] == {
            "product_id": product_id,
            "quantity": "2.000",
            "value": "6.000000",
            "average_unit_cost": "3.000000",
        }
        assert browser.request(
            f"/inventory/products/{product_id}/counts", headers=READ
        )[1] == {
            "items": [evidence],
            "next_after": None,
        }
        assert (
            browser.request(f"/inventory/products/{foreign_id}/balance", headers=READ)[
                0
            ]
            == 403
        )
        assert (
            browser.request(
                "/inventory/counts",
                body=body | {"reason": CANARY * 1000},
                headers=expected,
            )[0]
            == 413
        )
    finally:
        with state["owner"].begin() as connection:
            for model in (StockMovement, StockCountRecord, StockBalance, Product):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )


def test_purchase_and_receipt_replay_through_real_proxy(http_identity):
    from sqlalchemy import delete

    from app.modules.purchases.models import (
        Purchase,
        PurchaseItem,
        PurchaseReceipt,
        PurchaseReceiptLine,
        Supplier,
    )

    state = http_identity
    browser = Browser(state)
    try:
        with Session(state["runtime"]) as session, session.begin():
            product = Product(
                tenant_id=state["tenant"], name="Proxy purchase product", price="10.00"
            )
            session.add(product)
            session.flush()
            product_id = str(product.id)
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
        context = {"X-Expected-Membership": str(state["membership"])}
        status, supplier, _ = browser.request(
            "/purchases/suppliers", body={"name": "Proxy supplier"}, headers=context
        )
        assert status == 201
        expected = context | {"Idempotency-Key": str(uuid4())}
        payload = {
            "supplier_id": supplier["id"],
            "lines": [{"product_id": product_id, "quantity": "2", "unit_cost": "3"}],
        }
        status, purchase, result_headers = browser.request(
            "/purchases", body=payload, headers=expected
        )
        assert status == 200 and purchase["total"] == "6.00"
        assert result_headers["Cache-Control"] == "no-store"
        assert (
            browser.request("/purchases", body=payload, headers=expected)[1] == purchase
        )
        url = f"/purchases/{purchase['id']}/receipt"
        assert browser.request(url, headers=READ | context)[0] == 204
        expected = context | {"Idempotency-Key": str(uuid4())}
        status, receipt, result_headers = browser.request(
            url, body={}, headers=expected
        )
        assert status == 200 and receipt["lines"][0]["after_value"] == "6.000000"
        assert result_headers["X-Access-Membership"] == str(state["membership"])
        assert browser.request(url, body={}, headers=expected)[1] == receipt
        assert browser.request(url, headers=READ | context)[1] == receipt
        assert (
            browser.request("/purchases", headers=READ | context)[1]["items"][0][
                "receipt_id"
            ]
            == receipt["id"]
        )
        assert (
            browser.request(f"/inventory/products/{product_id}/balance", headers=READ)[
                1
            ]["quantity"]
            == "2.000"
        )
    finally:
        with state["owner"].begin() as connection:
            for model in (
                StockMovement,
                PurchaseReceiptLine,
                PurchaseReceipt,
                StockBalance,
                PurchaseItem,
                Purchase,
                Supplier,
                Product,
            ):
                connection.execute(
                    delete(model).where(model.tenant_id == state["tenant"])
                )


def stale_password_proof(state):
    # Model an entire successful login six minutes ago. Moving only the password
    # timestamp would describe an impossible login beyond its five-minute limit.
    with Session(state["runtime"]) as session, session.begin():
        record = session.scalar(
            select(SessionRecord).where(
                SessionRecord.membership_id == state["membership"]
            )
        )
        proof = session.get(MfaPending, record.mfa_pending_id)
        elapsed = timedelta(minutes=6)
        for name in (
            "password_verified_at",
            "created_at",
            "expires_at",
            "finished_at",
            "factor_verified_at",
        ):
            setattr(proof, name, getattr(proof, name) - elapsed)
        for name in ("created_at", "last_seen_at", "expires_at"):
            setattr(record, name, getattr(record, name) - elapsed)


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
    status, page, headers = browser.request("/auth/memberships?limit=1", headers=READ)
    assert status == 200 and len(page["items"]) == 1
    assert headers["X-Access-Membership"] == str(state["membership"])
    assert headers["Cache-Control"] == "no-store" and headers.get("ETag") is None
    status, tail, _ = browser.request(
        "/auth/memberships?limit=1&after=" + page["next_after"], headers=READ
    )
    assert status == 200 and tail["next_after"] is None
    assert {row["membership_id"] for row in page["items"] + tail["items"]} == {
        str(membership_id),
        str(state["membership"]),
    }
    status, data, headers = browser.request(target_path, headers=READ)
    assert status == 200 and data == {
        "membership_id": str(membership_id),
        "role": "cashier",
        "is_active": True,
    }
    old_tag = headers["ETag"]
    stale_password_proof(state)
    assert (
        browser.request(
            target_path,
            method="PATCH",
            body={"role": "mechanic", "is_active": True},
            headers={"If-Match": old_tag},
        )[0]
        == 428
    )
    assert (
        browser.request(
            "/auth/mfa/reauthenticate",
            body={"password": state["password"].get_secret_value()},
            headers={"X-Expected-Membership": str(state["membership"])},
        )[0]
        == 204
    )
    status, data, headers = browser.request(
        target_path,
        method="PATCH",
        body={"role": "mechanic", "is_active": True},
        headers={
            "If-Match": old_tag,
            "X-Expected-Membership": str(state["membership"]),
        },
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


def test_administrative_creation_and_new_user_login_through_real_proxy(
    new_identity_scope,
):
    state = new_identity_scope
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
    assert browser.request("/auth/csrf", headers=READ)[0] == 204
    value = {
        "email": state["prefix"] + CANARY + "@example.com",
        "password": CANARY,
        "role": "mechanic",
    }
    context = {"X-Expected-Membership": str(state["membership"])}
    stale_password_proof(state)
    assert browser.request("/auth/memberships", body=value, headers=context)[0] == 428
    assert (
        browser.request(
            "/auth/mfa/reauthenticate",
            body={"password": state["password"].get_secret_value()},
            headers=context,
        )[0]
        == 204
    )
    before = list(browser.cookies)
    status, data, headers = browser.request(
        "/auth/memberships", body=value, headers=context
    )
    assert status == 201 and data["role"] == "mechanic" and data["is_active"] is True
    assert set(data) == {"membership_id", "role", "is_active"}
    assert headers["X-Access-Membership"] == str(state["membership"])
    assert headers["Cache-Control"] == "no-store" and headers.get("Set-Cookie") is None
    assert list(browser.cookies) == before
    resolved = urljoin(browser.base + "/auth/memberships", headers["Location"])
    assert resolved == browser.base + "/auth/memberships/" + data["membership_id"]
    status, detail, detail_headers = browser.request(
        "/auth/memberships/" + data["membership_id"], headers=READ
    )
    assert (
        status == 200 and detail == data and detail_headers["ETag"] == headers["ETag"]
    )
    status, conflict, error_headers = browser.request(
        "/auth/memberships", body=value, headers=context
    )
    assert status == 409 and conflict == {"detail": "User creation unavailable"}
    assert (
        error_headers["Cache-Control"] == "no-store"
        and error_headers.get("Location") is None
    )
    status, invalid, _ = browser.request(
        "/auth/memberships?probe=" + CANARY, body=value, headers=context
    )
    assert status == 422 and CANARY not in json.dumps(invalid)
    assert browser.request("/auth/logout", body={})[0] == 204
    staff = Browser(state)
    assert staff.request("/auth/csrf", headers=READ)[0] == 204
    assert (
        staff.request(
            "/auth/login", body={"email": value["email"], "password": value["password"]}
        )[0]
        == 200
    )
    status, current, _ = staff.request("/auth/session", headers=READ)
    assert status == 200 and current["membership_id"] == data["membership_id"]
    assert (
        current["tenant_id"] == str(state["tenant"]) and current["role"] == "mechanic"
    )
    assert staff.request("/auth/memberships", headers=READ)[0] == 403
    assert staff.request("/auth/logout", body={})[0] == 204
    with Session(state["runtime"]) as session:
        records = list(session.scalars(select(AuditEvent)))
        assert len(records) == 1 and records[0].action == "membership_created"


def test_confirmation_rotates_cookies_and_rejects_replay_through_real_proxy(
    http_identity,
):
    state = http_identity
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
    context = {"X-Expected-Membership": str(state["membership"])}
    original = [copy(cookie) for cookie in browser.cookies]
    status, data, headers = browser.request(
        "/auth/mfa/reauthenticate",
        body={"password": CANARY},
        headers=context,
    )
    assert status == 401 and data == {"detail": "Access denied"}
    assert (
        headers.get("Set-Cookie") is None and headers.get("X-Access-Membership") is None
    )
    assert headers["Cache-Control"] == "no-store"
    assert [(cookie.name, cookie.value) for cookie in browser.cookies] == [
        (cookie.name, cookie.value) for cookie in original
    ]
    # Direct access to the backend cannot claim the trusted proxy, even with
    # real session/proof cookies from the isolated authenticated browser.
    direct = Browser(state, base="http://backend:8000")
    for cookie in original:
        direct.cookies.set_cookie(copy(cookie))
    status, data, headers = direct.request(
        "/auth/mfa/reauthenticate",
        body={"password": state["password"].get_secret_value()},
        headers=context
        | {
            "Cookie": "; ".join(f"{cookie.name}={cookie.value}" for cookie in original),
            "X-Real-IP": "127.0.0.1",
        },
    )
    assert status == 403 and data == {"detail": "Request rejected"}
    assert headers.get("Set-Cookie") is None
    with Session(state["runtime"]) as session:
        before = session.scalar(select(SessionRecord))
        expiry, record_id = before.expires_at, before.id
    status, data, headers = browser.request(
        "/auth/mfa/reauthenticate",
        body={"password": state["password"].get_secret_value()},
        headers=context | {"X-Real-IP": "127.0.9.1", "X-Forwarded-For": "127.0.9.1"},
    )
    assert status == 204 and data is None
    assert headers["Cache-Control"] == "no-store" and headers[
        "X-Access-Membership"
    ] == str(state["membership"])
    fresh = {cookie.name: cookie.value for cookie in browser.cookies}
    old = {cookie.name: cookie.value for cookie in original}
    assert fresh["carrillo_session"] != old["carrillo_session"]
    assert fresh["carrillo_csrf"] != old["carrillo_csrf"]
    with Session(state["runtime"]) as session, session.begin():
        record = session.scalar(select(SessionRecord))
        assert record.id == record_id and record.expires_at == expiry
        assert record.reauthenticated_at is not None
        actor = require_recent_administrator(
            session,
            SecretStr(fresh["carrillo_session"]),
            expected_actor=state["membership"],
        )
        assert actor.tenant_id == state["tenant"]
    replay = Browser(state)
    for cookie in original:
        replay.cookies.set_cookie(copy(cookie))
    status, data, headers = replay.request(
        "/auth/mfa/reauthenticate",
        body={"password": state["password"].get_secret_value()},
        headers=context,
    )
    assert status == 401 and data == {"detail": "Access denied"}
    assert (
        headers.get("Set-Cookie") is None and headers.get("X-Access-Membership") is None
    )
    stale_proof = Browser(state)
    for cookie in browser.cookies:
        stale_proof.cookies.set_cookie(copy(cookie))
    for cookie in original:
        if cookie.name == "carrillo_csrf":
            stale_proof.cookies.set_cookie(copy(cookie))
    assert (
        stale_proof.request(
            "/auth/mfa/reauthenticate",
            body={"password": CANARY},
            headers=context,
        )[0]
        == 403
    )
    status, data, headers = browser.request(
        "/auth/mfa/reauthenticate?probe=" + CANARY,
        body={"password": CANARY},
        headers=context,
    )
    assert status == 422 and CANARY not in json.dumps(data)
    assert headers.get("Set-Cookie") is None
    assert browser.request("/auth/session", headers=READ)[0] == 200
    assert browser.request("/auth/logout", body={})[0] == 204
    assert not list(browser.cookies)


def test_image_location_keeps_json_limits_and_bounds_binary(http_identity):
    target = str(uuid4())
    for path, size, expected in (
        (f"/api/catalog/products/{target}/image", 8193, 403),
        (f"/api/catalog/products/{target}/image", 8388609, 413),
        ("/api/catalog/products", 8193, 413),
        ("/api/purchases", 8193, 403),
        ("/api/purchases", 32769, 413),
        ("/api/purchases/suppliers", 8193, 413),
    ):
        with socket.create_connection(("proxy", 8080), timeout=10) as connection:
            payload = b"x" * size if size < 10000 else b""
            connection.sendall(
                (
                    f"POST {path} HTTP/1.1\r\nHost: proxy\r\n"
                    "Content-Type: application/octet-stream\r\n"
                    f"Content-Length: {size}\r\nConnection: close\r\n\r\n"
                ).encode()
                + payload
            )
            with HTTPResponse(connection) as response:
                response.begin()
                assert response.status == expected
                assert response.getheader("Cache-Control") == "no-store"
                response.read(8192)


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
