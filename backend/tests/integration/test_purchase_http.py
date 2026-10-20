"""Private purchase API with real MFA, CSRF and ephemeral PostgreSQL."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from test_inventory_http import READ
from test_inventory_http import inventory_web as inventory_web

from app.modules.access.models import TenantMembership
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockBalance, StockCountRecord, StockMovement
from app.modules.purchases.models import (
    Purchase,
    PurchaseItem,
    PurchaseReceipt,
    PurchaseReceiptLine,
    Supplier,
)
from app.modules.purchases.service import PurchasesUnavailable

pytestmark = pytest.mark.integration


@pytest.fixture
def purchase_web(inventory_web):
    client, state, policy = inventory_web
    try:
        supplier = post(
            client, state, policy, "/purchases/suppliers", dict(name="Supplier")
        )
        assert supplier.status_code == 201, supplier.text
        state["supplier"] = supplier.json()["id"]
        with Session(state["runtime"]) as session, session.begin():
            foreign = Supplier(
                tenant_id=state["other"],
                actor_membership_id=state["foreign_membership"],
                name="Hidden supplier",
            )
            session.add(foreign)
            session.flush()
            purchase = Purchase(
                tenant_id=state["other"],
                supplier_id=foreign.id,
                supplier_name=foreign.name,
                actor_membership_id=state["foreign_membership"],
                operation_key=uuid4(),
                request_fingerprint="a" * 64,
                line_count=1,
                total=0,
            )
            session.add(purchase)
            session.flush()
            state["foreign_supplier"], state["foreign_purchase"] = (
                str(foreign.id),
                str(purchase.id),
            )
        yield client, state, policy
    finally:
        with state["owner"].begin() as connection:
            for model in (
                StockMovement,
                PurchaseReceiptLine,
                PurchaseReceipt,
                StockCountRecord,
                StockBalance,
                PurchaseItem,
                Purchase,
                Supplier,
            ):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )


def read(state):
    return READ | {"X-Expected-Membership": str(state["membership"])}


def headers(client, state, policy):
    return {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
        "Idempotency-Key": str(uuid4()),
    }


def post(client, state, policy, path, body, *, extra=None):
    return client.post(
        path, json=body, headers=headers(client, state, policy) | (extra or {})
    )


def body(state, **changes):
    return (
        dict(
            supplier_id=state["supplier"],
            lines=[dict(product_id=str(state["product"]), quantity="2", unit_cost="3")],
        )
        | changes
    )


def saved(client, state, policy):
    response = post(client, state, policy, "/purchases", body(state))
    assert response.status_code == 200, response.text
    return response.json()


def counts(state):
    with Session(state["runtime"]) as session:
        return tuple(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == state["tenant"])
            )
            for model in (
                Purchase,
                PurchaseItem,
                PurchaseReceipt,
                StockMovement,
                StockBalance,
            )
        )


def test_create_read_receive_replay_and_allowlists(purchase_web):
    client, state, policy = purchase_web
    key = str(uuid4())
    first = post(
        client, state, policy, "/purchases", body(state), extra={"Idempotency-Key": key}
    )
    assert first.status_code == 200
    purchase = first.json()
    assert set(purchase) == {
        "id",
        "supplier_id",
        "supplier_name",
        "operation_key",
        "lines",
        "total",
        "created_at",
    }
    assert purchase["total"] == "6.00"
    assert set(purchase["lines"][0]) == {
        "product_id",
        "product_name",
        "quantity",
        "unit_cost",
        "stock_value",
        "amount",
    }
    assert (
        post(
            client,
            state,
            policy,
            "/purchases",
            body(state),
            extra={"Idempotency-Key": key},
        ).json()
        == purchase
    )
    assert counts(state) == (1, 1, 0, 0, 0)
    path = f"/purchases/{purchase['id']}"
    assert client.get(path, headers=read(state)).json() == purchase
    pending = client.get(path + "/receipt", headers=read(state))
    assert pending.status_code == 204 and pending.content == b""
    key = str(uuid4())
    receipt = post(
        client, state, policy, path + "/receipt", {}, extra={"Idempotency-Key": key}
    )
    assert receipt.status_code == 200, receipt.text
    assert set(receipt.json()) == {
        "id",
        "purchase_id",
        "operation_key",
        "created_at",
        "lines",
    }
    assert receipt.json()["lines"][0]["after_quantity"] == "2.000"
    assert (
        post(
            client, state, policy, path + "/receipt", {}, extra={"Idempotency-Key": key}
        ).json()
        == receipt.json()
    )
    assert client.get(path + "/receipt", headers=read(state)).json() == receipt.json()
    assert post(client, state, policy, path + "/receipt", {}).status_code == 409
    listing = client.get("/purchases", headers=read(state))
    assert set(listing.json()["items"][0]) == {
        "id",
        "supplier_id",
        "supplier_name",
        "total",
        "created_at",
        "receipt_id",
    }
    assert listing.json()["items"][0]["receipt_id"] == receipt.json()["id"]
    assert counts(state) == (1, 1, 1, 1, 1)
    for response in (first, pending, receipt, listing):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-access-membership"] == str(state["membership"])
        assert "set-cookie" not in response.headers


def test_supplier_and_purchase_pagination_ties_and_cursor_scope(purchase_web):
    client, state, policy = purchase_web
    for i in range(2):
        assert (
            post(
                client,
                state,
                policy,
                "/purchases/suppliers",
                dict(name=f"Supplier {i}"),
            ).status_code
            == 201
        )
    first = client.get("/purchases/suppliers?limit=2", headers=read(state)).json()
    second = client.get(
        "/purchases/suppliers?limit=2&after=" + first["next_after"], headers=read(state)
    ).json()
    assert len(first["items"]) + len(second["items"]) == 3
    assert second["next_after"] is None
    assert all(set(row) == {"id", "name", "is_active"} for row in first["items"])
    ids = [saved(client, state, policy)["id"] for _ in range(3)]
    with state["owner"].begin() as connection:
        connection.execute(
            update(Purchase)
            .where(Purchase.tenant_id == state["tenant"])
            .values(created_at=state["http_now"])
        )
    first = client.get("/purchases?limit=2", headers=read(state)).json()
    second = client.get(
        "/purchases?limit=2&after=" + first["next_after"], headers=read(state)
    ).json()
    assert [row["id"] for row in first["items"] + second["items"]] == sorted(
        ids, reverse=True
    )
    assert second["next_after"] is None
    for path in (
        "/purchases?after=" + state["foreign_purchase"],
        "/purchases/suppliers?after=" + state["foreign_supplier"],
    ):
        assert client.get(path, headers=read(state)).status_code == 401


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=01",
        "limit=2&limit=3",
        "tenant_id=forged",
        "after=invalid",
    ],
)
def test_invalid_query_is_rejected(purchase_web, query):
    client, state, _ = purchase_web
    for path in ("/purchases", "/purchases/suppliers"):
        response = client.get(path + "?" + query, headers=read(state))
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("field", ["tenant_id", "role", "stock", "total", "price"])
def test_authority_fields_are_rejected_in_purchase_and_receipt(purchase_web, field):
    client, state, policy = purchase_web
    response = post(
        client, state, policy, "/purchases", body(state, **{field: "forged"})
    )
    assert response.status_code == 422
    purchase = saved(client, state, policy)
    assert (
        post(
            client,
            state,
            policy,
            f"/purchases/{purchase['id']}/receipt",
            {field: "forged"},
        ).status_code
        == 422
    )
    assert counts(state) == (1, 1, 0, 0, 0)


def test_csrf_origin_context_key_and_json_guards(purchase_web):
    client, state, policy = purchase_web
    normal = headers(client, state, policy)
    cases = [
        (normal | {"Origin": "https://attacker.example"}, 403),
        (normal | {"X-CSRF-Token": "invalid"}, 403),
        ({k: v for k, v in normal.items() if k != "X-Expected-Membership"}, 428),
        (normal | {"X-Expected-Membership": str(state["foreign_membership"])}, 401),
        ({k: v for k, v in normal.items() if k != "Idempotency-Key"}, 428),
        (normal | {"Idempotency-Key": "invalid"}, 422),
    ]
    for h, status in cases:
        assert (
            client.post("/purchases", json=body(state), headers=h).status_code == status
        )
    duplicate = (
        '{"supplier_id":"'
        + state["supplier"]
        + '","supplier_id":"'
        + state["supplier"]
        + '","lines":[]}'
    )
    assert (
        client.post(
            "/purchases",
            content=duplicate,
            headers=normal | {"Content-Type": "application/json"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/purchases", content="{}", headers=normal | {"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert counts(state) == (0, 0, 0, 0, 0)


def test_hundred_line_body_is_bounded_without_raising_other_limits(purchase_web):
    client, state, policy = purchase_web
    with Session(state["runtime"]) as session, session.begin():
        products = [
            Product(tenant_id=state["tenant"], name=f"Bulk {i}", price="1.00")
            for i in range(100)
        ]
        session.add_all(products)
        session.flush()
        payload = body(
            state,
            lines=[
                dict(product_id=str(p.id), quantity="1.000", unit_cost="1.000000")
                for p in products
            ],
        )
    assert 8192 < len(json.dumps(payload).encode()) < 32768
    result = post(client, state, policy, "/purchases", payload)
    assert result.status_code == 200, result.text
    assert len(result.json()["lines"]) == 100
    normal = headers(client, state, policy) | {"Content-Type": "application/json"}
    assert (
        client.post("/purchases", content=b" " * 32769, headers=normal).status_code
        == 413
    )
    assert (
        client.post(
            "/purchases/suppliers", content=b" " * 8193, headers=normal
        ).status_code
        == 413
    )

    def chunks():
        yield b" " * 20000
        yield b" " * 20000

    assert (
        client.post("/purchases", content=chunks(), headers=normal).status_code == 413
    )


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_non_admin_cannot_list_read_create_or_receive(purchase_web, role):
    client, state, policy = purchase_web
    purchase = saved(client, state, policy)
    path = f"/purchases/{purchase['id']}"
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    for url in ("/purchases", "/purchases/suppliers", path, path + "/receipt"):
        assert client.get(url, headers=read(state)).status_code == 401
    assert post(client, state, policy, "/purchases", body(state)).status_code == 401
    assert (
        post(
            client, state, policy, "/purchases/suppliers", dict(name="Denied")
        ).status_code
        == 401
    )
    assert post(client, state, policy, path + "/receipt", {}).status_code == 401
    assert counts(state) == (1, 1, 0, 0, 0)


def test_read_requires_context_and_browser_intent_and_denies_foreign_ids(purchase_web):
    client, state, policy = purchase_web
    for path in ("/purchases", "/purchases/suppliers"):
        assert client.get(path, headers=READ).status_code == 428
        assert (
            client.get(
                path, headers={"X-Expected-Membership": str(state["membership"])}
            ).status_code
            == 403
        )
        assert (
            client.get(
                path, headers=read(state) | {"Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
    foreign = f"/purchases/{state['foreign_purchase']}"
    assert client.get(foreign, headers=read(state)).status_code == 401
    assert client.get(foreign + "/receipt", headers=read(state)).status_code == 401
    assert post(client, state, policy, foreign + "/receipt", {}).status_code == 401
    assert (
        post(
            client,
            state,
            policy,
            "/purchases",
            body(state, supplier_id=state["foreign_supplier"]),
        ).status_code
        == 401
    )


def test_expired_confirmation_still_allows_reads_not_receipt(purchase_web):
    client, state, policy = purchase_web
    purchase = saved(client, state, policy)
    state["http_now"] += timedelta(minutes=5)
    path = f"/purchases/{purchase['id']}"
    assert client.get(path + "/receipt", headers=read(state)).status_code == 204
    assert client.get("/purchases", headers=read(state)).status_code == 200
    assert post(client, state, policy, path + "/receipt", {}).status_code == 428


def test_unavailable_error_is_generic_and_not_cached(purchase_web, monkeypatch):
    client, state, _ = purchase_web

    def fail(*args, **kwargs):
        raise PurchasesUnavailable("Synthetic internal detail")

    monkeypatch.setattr(client.app.state.purchases, "list_purchases", fail)
    response = client.get("/purchases", headers=read(state))
    assert response.status_code == 503
    assert response.json() == {"detail": "Purchases unavailable"}
    assert response.headers["cache-control"] == "no-store"
