"""Private stock API with real MFA login and disposable PostgreSQL."""

from datetime import timedelta
from ipaddress import ip_address
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from identity_http_fixture import arrange_factor, login_with_legacy_factor
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.access.models import TenantMembership
from app.modules.access.sessions import revoke_user_sessions
from app.modules.catalog.models import Product
from app.modules.inventory.models import (
    ManualStockRecord,
    StockBalance,
    StockCountRecord,
    StockMinimumRecord,
    StockMovement,
)
from app.modules.inventory.service import InventoryUnavailable

pytestmark = pytest.mark.integration
READ = {"X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def inventory_web(http_identity, monkeypatch):
    state = dict(http_identity)
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    with Session(state["runtime"]) as session, session.begin():
        own = Product(tenant_id=state["tenant"], name="Stock", price="1.00")
        foreign = Product(tenant_id=state["other"], name="Hidden", price="1.00")
        session.add_all([own, foreign])
        session.flush()
        state["product"], state["foreign_product"] = own.id, foreign.id
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
            for model in (
                StockMovement,
                ManualStockRecord,
                StockCountRecord,
                StockBalance,
                StockMinimumRecord,
                Product,
            ):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )


def write(client, state, policy, *, key=None, headers=None, **changes):
    return client.post(
        "/inventory/counts",
        json=dict(
            product_id=str(state["product"]),
            counted_quantity="2.000",
            reason="Physical count",
            declared_unit_cost="3.000000",
        )
        | changes,
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies[policy.csrf_cookie],
            "X-Expected-Membership": str(state["membership"]),
            "Idempotency-Key": str(key if key is not None else uuid4()),
            **(headers or {}),
        },
    )


def path(state, kind="balance"):
    return f"/inventory/products/{state['product']}/{kind}"


def counts(state):
    with Session(state["runtime"]) as session:
        return tuple(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == state["tenant"])
            )
            for model in (StockCountRecord, StockMovement, StockBalance)
        )


def test_balance_count_replay_and_history_allowlists(inventory_web):
    client, state, policy = inventory_web
    assert client.get(path(state), headers=READ).json() == {
        "product_id": str(state["product"]),
        "quantity": "0.000",
        "value": "0.000000",
        "average_unit_cost": None,
    }
    assert counts(state) == (0, 0, 0)
    key = uuid4()
    response = write(client, state, policy, key=key)
    assert response.status_code == 200
    item = response.json()
    assert set(item) == {
        "id",
        "product_id",
        "operation_key",
        "before_quantity",
        "after_quantity",
        "before_value",
        "after_value",
        "quantity_delta",
        "value_delta",
        "reason",
        "declared_unit_cost",
        "created_at",
    }
    assert item["after_quantity"] == "2.000" and item["after_value"] == "6.000000"
    assert write(client, state, policy, key=key).json() == item
    assert client.get(path(state, "counts"), headers=READ).json() == {
        "items": [item],
        "next_after": None,
    }
    assert client.get(path(state), headers=READ).json()["value"] == "6.000000"
    assert (
        client.get(path(state), headers=READ).json()["average_unit_cost"] == "3.000000"
    )
    for result in (
        response,
        client.get(path(state), headers=READ),
        client.get(path(state, "counts"), headers=READ),
    ):
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["x-access-membership"] == str(state["membership"])
        assert "set-cookie" not in result.headers
    assert counts(state) == (1, 1, 1)


def test_history_keyset_with_timestamp_ties_and_isolated_cursor(inventory_web):
    client, state, policy = inventory_web
    ids = []
    for _ in range(3):
        response = write(
            client, state, policy, counted_quantity="0", declared_unit_cost=None
        )
        assert response.status_code == 200
        ids.append(response.json()["id"])
    chronological = client.get(path(state, "counts"), headers=READ).json()
    assert [row["id"] for row in chronological["items"]] == list(reversed(ids))
    # Immutable in runtime. Owner-only synthetic setup deliberately creates ties.
    with state["owner"].begin() as connection:
        connection.execute(
            update(StockCountRecord)
            .where(StockCountRecord.tenant_id == state["tenant"])
            .values(created_at=state["http_now"])
        )
    first = client.get(path(state, "counts") + "?limit=2", headers=READ).json()
    assert [row["id"] for row in first["items"]] == sorted(ids, reverse=True)[:2]
    second = client.get(
        path(state, "counts") + "?limit=2&after=" + first["next_after"], headers=READ
    ).json()
    assert [row["id"] for row in second["items"]] == sorted(ids, reverse=True)[2:]
    assert second["next_after"] is None
    other = Product(tenant_id=state["tenant"], name="Other product", price="1.00")
    with Session(state["runtime"]) as session, session.begin():
        session.add(other)
        session.flush()
        other_id = other.id
    for url in (
        path(state, "counts") + "?after=" + str(uuid4()),
        f"/inventory/products/{other_id}/counts?after={ids[0]}",
    ):
        assert client.get(url, headers=READ).status_code == 403

    with Session(state["runtime"]) as session, session.begin():
        foreign_count = StockCountRecord(
            tenant_id=state["other"],
            product_id=state["foreign_product"],
            actor_membership_id=state["foreign_membership"],
            operation_key=uuid4(),
            request_fingerprint="a" * 64,
            reason="Hidden count",
            before_quantity=0,
            after_quantity=0,
            before_value=0,
            after_value=0,
            quantity_delta=0,
            value_delta=0,
            declared_unit_cost=None,
        )
        session.add(foreign_count)
        session.flush()
        foreign_cursor = foreign_count.id
    denied = client.get(
        path(state, "counts") + f"?after={foreign_cursor}", headers=READ
    )
    assert denied.status_code == 403 and "Hidden count" not in denied.text


def test_inactive_product_retains_readable_stock_history(inventory_web):
    client, state, policy = inventory_web
    assert write(client, state, policy).status_code == 200
    with state["owner"].begin() as connection:
        connection.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(is_active=False)
        )
    assert client.get(path(state), headers=READ).json()["quantity"] == "2.000"
    assert len(client.get(path(state, "counts"), headers=READ).json()["items"]) == 1
    assert write(client, state, policy).status_code == 403
    assert counts(state) == (1, 1, 1)


@pytest.mark.parametrize("cost,average", [("0", "0.000000"), ("0.000500", "0.000667")])
def test_server_average_distinguishes_zero_cost_from_no_stock(
    inventory_web, cost, average
):
    client, state, policy = inventory_web
    assert (
        write(
            client, state, policy, counted_quantity="0.003", declared_unit_cost=cost
        ).status_code
        == 200
    )
    assert client.get(path(state), headers=READ).json()["average_unit_cost"] == average
    assert (
        write(
            client, state, policy, counted_quantity="0", declared_unit_cost=None
        ).status_code
        == 200
    )
    empty = client.get(path(state), headers=READ).json()
    assert empty["average_unit_cost"] is None and empty["value"] == "0.000000"


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_admin_permission_required_for_all_stock_routes(inventory_web, role):
    client, state, policy = inventory_web
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    for kind in ("balance", "counts"):
        assert client.get(path(state, kind), headers=READ).status_code == 403
    assert write(client, state, policy).status_code == 403
    assert counts(state) == (0, 0, 0)


def test_foreign_and_missing_products_have_identical_denial(inventory_web):
    client, state, policy = inventory_web
    for identity in (state["foreign_product"], uuid4()):
        for kind in ("balance", "counts"):
            result = client.get(f"/inventory/products/{identity}/{kind}", headers=READ)
            assert result.status_code == 403 and result.json() == {
                "detail": "Access denied"
            }
        assert write(client, state, policy, product_id=str(identity)).status_code == 403
    assert counts(state) == (0, 0, 0)


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=01",
        "limit=1&limit=2",
        "after=bad",
        "tenant_id=bad",
    ],
)
def test_history_query_is_bounded_and_unambiguous(inventory_web, query):
    client, state, _ = inventory_web
    assert (
        client.get(path(state, "counts") + "?" + query, headers=READ).status_code == 422
    )
    assert client.get(path(state) + "?" + query, headers=READ).status_code == 422


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "role", "price", "previous_stock", "value_delta", "operation_key"],
)
def test_stock_authority_fields_are_forbidden_in_json(inventory_web, field):
    client, state, policy = inventory_web
    assert write(client, state, policy, **{field: "untrusted"}).status_code == 422
    assert counts(state) == (0, 0, 0)


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://foreign.invalid"},
        {"X-CSRF-Token": "invalid"},
        {"X-Expected-Membership": str(uuid4())},
    ],
)
def test_browser_proof_and_context_are_required(inventory_web, headers):
    client, state, policy = inventory_web
    assert write(client, state, policy, headers=headers).status_code == 403
    assert counts(state) == (0, 0, 0)


def test_operation_key_missing_malformed_and_duplicate(inventory_web):
    client, state, policy = inventory_web
    body = dict(product_id=str(state["product"]), counted_quantity="0", reason="Count")
    headers = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
    }
    assert (
        client.post("/inventory/counts", json=body, headers=headers).status_code == 428
    )
    assert (
        client.post(
            "/inventory/counts",
            json=body,
            headers=headers | {"Idempotency-Key": "invalid"},
        ).status_code
        == 422
    )
    duplicate = list(headers.items()) + [
        ("Idempotency-Key", str(uuid4())),
        ("Idempotency-Key", str(uuid4())),
    ]
    assert (
        client.post("/inventory/counts", json=body, headers=duplicate).status_code
        == 422
    )
    assert counts(state) == (0, 0, 0)


def test_expired_confirmation_blocks_write_but_not_reads(inventory_web):
    client, state, policy = inventory_web
    state["http_now"] += timedelta(minutes=5)
    assert write(client, state, policy).status_code == 428
    assert client.get(path(state), headers=READ).status_code == 200
    assert client.get(path(state, "counts"), headers=READ).status_code == 200


def test_missing_context_blocks_write(inventory_web):
    client, state, policy = inventory_web
    response = client.post(
        "/inventory/counts",
        json={
            "product_id": str(state["product"]),
            "counted_quantity": "0",
            "reason": "Count",
        },
        headers={
            "Origin": policy.origin,
            "X-CSRF-Token": client.cookies[policy.csrf_cookie],
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert response.status_code == 428 and counts(state) == (0, 0, 0)


def test_invalid_cost_and_key_conflict_are_safe_and_atomic(inventory_web):
    client, state, policy = inventory_web
    invalid = write(client, state, policy, declared_unit_cost=None)
    assert invalid.status_code == 422 and invalid.json() == {
        "detail": "Invalid physical count"
    }
    key = uuid4()
    assert write(client, state, policy, key=key).status_code == 200
    changes = {"counted_quantity": "3"}
    conflict = write(client, state, policy, key=key, **changes)
    assert conflict.status_code == 409 and conflict.json() == {
        "detail": "Operation key conflict"
    }
    assert counts(state) == (1, 1, 1)


def test_reads_reject_missing_intent_revocation_and_direct_peer(
    inventory_web, monkeypatch
):
    client, state, _ = inventory_web
    assert client.get(path(state)).status_code == 403
    with Session(state["runtime"]) as session, session.begin():
        revoke_user_sessions(session, state["user"])
    assert client.get(path(state), headers=READ).status_code == 401
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.3")},
    )
    assert client.get(path(state), headers=READ).status_code == 403


def test_mixed_pending_cookie_cannot_read_stock(inventory_web):
    client, state, policy = inventory_web
    client.cookies.set(policy.preauth_cookie, "pending")
    assert client.get(path(state), headers=READ).status_code == 403


def test_storage_failure_is_sanitized(inventory_web, monkeypatch):
    client, state, policy = inventory_web

    def unavailable(*args, **kwargs):
        raise InventoryUnavailable("Sensitive database details")

    for method in ("current_balance", "list_counts", "record_count"):
        monkeypatch.setattr(client.app.state.inventory, method, unavailable)
    for response in (
        client.get(path(state), headers=READ),
        client.get(path(state, "counts"), headers=READ),
        write(client, state, policy),
    ):
        assert response.status_code == 503 and response.json() == {
            "detail": "Inventory unavailable"
        }
        assert response.headers["cache-control"] == "no-store"
        assert "x-access-membership" not in response.headers


def test_manual_entry_exit_replay_and_private_history(inventory_web):
    client, state, policy = inventory_web
    key = str(uuid4())
    headers = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
        "Idempotency-Key": key,
    }
    data = {
        "product_id": str(state["product"]),
        "direction": "IN",
        "quantity": "3",
        "unit_cost": "4",
        "reason": "Ingreso de bodega",
    }
    result = client.post("/inventory/movements", json=data, headers=headers)
    assert result.status_code == 200
    body = result.json()
    assert body["direction"] == "IN" and body["quantity_delta"] == "3.000"
    assert body["value_delta"] == "12.000000"
    assert "tenant_id" not in body and "request_fingerprint" not in body
    assert (
        client.post("/inventory/movements", json=data, headers=headers).json() == body
    )
    headers["Idempotency-Key"] = str(uuid4())
    data.update(direction="OUT", quantity="1", unit_cost=None)
    out = client.post("/inventory/movements", json=data, headers=headers)
    assert out.status_code == 200 and out.json()["value_delta"] == "-4.000000"
    history = client.get(path(state, "manual-movements"), headers=READ)
    assert history.status_code == 200
    assert len(history.json()["items"]) == 2
    assert history.headers["Cache-Control"] == "no-store"
    assert (
        client.get(
            f"/inventory/products/{state['foreign_product']}/manual-movements",
            headers=READ,
        ).status_code
        == 403
    )
    assert client.get(path(state, "manual-movements")).status_code == 403
    headers["Idempotency-Key"] = str(uuid4())
    data["quantity"] = "3"
    assert (
        client.post("/inventory/movements", json=data, headers=headers).status_code
        == 422
    )
    assert client.get(path(state), headers=READ).json()["quantity"] == "2.000"


@pytest.mark.parametrize(
    "extra",
    [
        {"tenant_id": str(uuid4())},
        {"role": "admin"},
        {"quantity": "0"},
        {"quantity": "-1"},
        {"quantity": 1.5},
        {"direction": "SALE"},
        {"unit_cost": None},
        {"reason": " "},
    ],
)
def test_manual_write_rejects_client_authority_and_invalid_deltas(inventory_web, extra):
    client, state, policy = inventory_web
    data = {
        "product_id": str(state["product"]),
        "direction": "IN",
        "quantity": "3",
        "unit_cost": "4",
        "reason": "Ingreso de bodega",
    } | extra
    headers = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
        "Idempotency-Key": str(uuid4()),
    }
    assert (
        client.post("/inventory/movements", json=data, headers=headers).status_code
        == 422
    )
    assert client.get(path(state), headers=READ).json()["quantity"] == "0.000"
    assert client.post("/inventory/movements", json=data).status_code == 403


def test_unified_history_http_contract(inventory_web):
    client, state, policy = inventory_web
    response = write(client, state, policy)
    assert response.status_code == 200
    evidence = response.json()
    response = client.get(path(state, "history"), headers=READ)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Access-Membership"] == str(state["membership"])
    row = response.json()["items"][0]
    assert row["source"] == "COUNT" and row["id"] == evidence["id"]
    assert row["source_document_id"] == evidence["id"]
    assert "@" in row["recorded_by"]
    assert set(row) == {
        "id",
        "product_id",
        "source",
        "direction",
        "source_document_id",
        "recorded_by",
        "reason",
        "before_quantity",
        "after_quantity",
        "before_value",
        "after_value",
        "quantity_delta",
        "value_delta",
        "created_at",
    }
    assert client.get(path(state, "history")).status_code == 403
    assert (
        client.get(
            path(state, "history"),
            headers=READ | {"X-Expected-Membership": str(state["foreign_membership"])},
        ).status_code
        == 403
    )
    for query in (
        "limit=0",
        "limit=101",
        "after=invalid",
        "after=COUNT:" + str(uuid4()).upper(),
        "tenant_id=" + str(state["other"]),
        "limit=1&limit=2",
    ):
        assert (
            client.get(path(state, "history") + "?" + query, headers=READ).status_code
            == 422
        )
    assert (
        client.get(
            f"/inventory/products/{state['foreign_product']}/history", headers=READ
        ).status_code
        == 403
    )


def test_minimum_http_version_boundaries_and_strict_input(inventory_web):
    client, state, policy = inventory_web
    url = path(state, "minimum")
    initial = client.get(url, headers=READ)
    assert initial.status_code == 200
    assert initial.json() == {
        "product_id": str(state["product"]),
        "quantity": "0.000",
        "minimum": None,
        "is_active": True,
        "is_low": False,
    }
    headers = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
        "If-Match": initial.headers["ETag"],
    }
    configured = client.patch(url, json={"minimum": "0"}, headers=headers)
    assert configured.status_code == 200 and configured.json()["is_low"]
    assert client.patch(url, json={"minimum": "2"}, headers=headers).status_code == 412
    for invalid in (
        {"minimum": "-1"},
        {"minimum": 1.5},
        {"minimum": "2", "quantity": "9"},
        {"minimum": None, "tenant_id": str(state["other"])},
    ):
        assert client.patch(url, json=invalid, headers=headers).status_code == 422
    assert client.patch(url, json={"minimum": None}).status_code == 403
    assert client.get(url).status_code == 403
