"""Catalog HTTP with real MFA login, PostgreSQL, browser intent and tenant scope."""

from datetime import timedelta
from ipaddress import ip_address
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from identity_http_fixture import arrange_factor, login_with_legacy_factor
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access.models import AuditEvent, TenantMembership
from app.modules.access.sessions import revoke_user_sessions
from app.modules.catalog.models import Category, Product

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def catalog_web(http_identity, monkeypatch):
    state = dict(http_identity)
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    try:
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
            yield client, state, policy
    finally:
        with state["owner"].begin() as connection:
            for model in (AuditEvent, Product, Category):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )


def write(client, state, policy, *, path="products", body=None, headers=None):
    return client.post(
        "/catalog/" + path,
        json=body if body is not None else {"name": "Filtro", "price": "12.34"},
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies.get(policy.csrf_cookie, ""),
            "X-Expected-Membership": str(state["membership"]),
            **(headers or {}),
        },
    )


def test_create_list_detail_and_response_allowlist(catalog_web):
    client, state, policy = catalog_web
    category = write(
        client, state, policy, path="categories", body={"name": "Repuestos"}
    )
    assert category.status_code == 201
    category_id = category.json()["id"]
    created = write(
        client,
        state,
        policy,
        body={
            "name": "Filtro",
            "price": "12.34",
            "category_id": category_id,
            "sku": "F-1",
        },
    )
    assert created.status_code == 201
    item = created.json()
    assert set(item) == {"id", "name", "price", "sku", "category_id", "is_active"}
    assert item["price"] == "12.34"
    assert created.headers["x-access-membership"] == str(state["membership"])
    for path in ("products", "categories", "products/" + item["id"]):
        response = client.get("/catalog/" + path, headers=READ)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-access-membership"] == str(state["membership"])
        assert "set-cookie" not in response.headers
    assert client.get("/catalog/products", headers=READ).json() == {
        "items": [item],
        "next_after": None,
    }
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.tenant_id == state["tenant"])
            )
            == 2
        )


@pytest.mark.parametrize(
    "collection,model", [("products", Product), ("categories", Category)]
)
def test_pagination_never_exposes_another_company(catalog_web, collection, model):
    client, state, _ = catalog_web
    ids = sorted(uuid4() for _ in range(3))
    with Session(state["runtime"]) as session, session.begin():
        for identity in ids:
            fields = {"price": "1.00"} if model is Product else {}
            session.add(
                model(id=identity, tenant_id=state["tenant"], name="Own", **fields)
            )
        fields = {"price": "2.00"} if model is Product else {}
        foreign = model(tenant_id=state["other"], name="Private foreign", **fields)
        session.add(foreign)
        session.flush()
        foreign_id = foreign.id
    first = client.get(f"/catalog/{collection}?limit=2", headers=READ).json()
    assert [UUID(row["id"]) for row in first["items"]] == ids[:2]
    assert first["next_after"] == str(ids[1])
    second = client.get(
        f"/catalog/{collection}?limit=2&after={first['next_after']}", headers=READ
    ).json()
    assert [UUID(row["id"]) for row in second["items"]] == ids[2:]
    assert second["next_after"] is None
    foreign_cursor = client.get(
        f"/catalog/{collection}?after={foreign_id}", headers=READ
    )
    assert foreign_cursor.status_code == 200
    assert all(UUID(item["id"]) in ids for item in foreign_cursor.json()["items"])
    assert "Private foreign" not in foreign_cursor.text


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_non_admin_has_no_administrative_catalog_access(catalog_web, role):
    client, state, policy = catalog_web
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    assert client.get("/catalog/products", headers=READ).status_code == 403
    assert client.get("/catalog/categories", headers=READ).status_code == 403
    assert write(client, state, policy).status_code == 403


def test_missing_and_foreign_products_share_denial(catalog_web):
    client, state, _ = catalog_web
    with Session(state["runtime"]) as session, session.begin():
        product = Product(tenant_id=state["other"], name="Hidden", price="1.00")
        session.add(product)
        session.flush()
        identity = product.id
    foreign = client.get(f"/catalog/products/{identity}", headers=READ)
    missing = client.get(f"/catalog/products/{uuid4()}", headers=READ)
    assert foreign.status_code == missing.status_code == 403
    assert foreign.json() == missing.json() == {"detail": "Access denied"}


@pytest.mark.parametrize(
    "query",
    [
        "limit=101",
        "limit=0",
        "limit=01",
        "limit=1&limit=2",
        "tenant_id=forbidden",
        "after=invalid",
        "after=&after=invalid",
    ],
)
def test_invalid_pagination_is_rejected(catalog_web, query):
    client, _, _ = catalog_web
    assert client.get("/catalog/products?" + query, headers=READ).status_code == 422


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://foreign.invalid"},
        {"X-CSRF-Token": "invalid"},
        {"X-Expected-Membership": str(uuid4())},
    ],
)
def test_write_checks_csrf_origin_and_expected_context(catalog_web, headers):
    client, state, policy = catalog_web
    response = write(client, state, policy, headers=headers)
    assert response.status_code == 403
    with Session(state["runtime"]) as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 0


def test_required_context_and_recent_proof_without_restricting_reads(catalog_web):
    client, state, policy = catalog_web
    response = client.post(
        "/catalog/categories",
        json={"name": "Repuestos"},
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        },
    )
    assert response.status_code == 428
    state["http_now"] += timedelta(minutes=5)
    assert write(client, state, policy).status_code == 428
    assert client.get("/catalog/products", headers=READ).status_code == 200


@pytest.mark.parametrize("field", ["tenant_id", "role", "stock", "cost", "is_active"])
def test_write_forbids_authority_and_stock_payload(catalog_web, field):
    client, state, policy = catalog_web
    assert (
        write(
            client,
            state,
            policy,
            body={"name": "Filtro", "price": "1.00", field: "forbidden"},
        ).status_code
        == 422
    )


def test_duplicate_sku_conflicts_without_second_event(catalog_web):
    client, state, policy = catalog_web
    body = {"name": "Filtro", "price": "1.00", "sku": "ONE"}
    assert write(client, state, policy, body=body).status_code == 201
    duplicate = write(client, state, policy, body=body)
    assert duplicate.status_code == 409 and duplicate.json() == {
        "detail": "SKU unavailable"
    }
    with Session(state["runtime"]) as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_revoked_session_and_read_without_browser_intent_are_denied(catalog_web):
    client, state, _ = catalog_web
    assert client.get("/catalog/products").status_code == 403
    with Session(state["runtime"]) as session, session.begin():
        revoke_user_sessions(session, state["user"])
    assert client.get("/catalog/products", headers=READ).status_code == 401


def test_direct_peer_cannot_use_forged_proxy_headers(catalog_web, monkeypatch):
    client, _, _ = catalog_web
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.3")},
    )
    assert client.get("/catalog/products", headers=READ).status_code == 403
