"""Scoped withdrawals retain transactional valuation without exposing it."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_inventory_permissions import arrange, cleanup
from test_inventory_permissions import permission_web as permission_web
from test_inventory_service import inventory_state as inventory_state
from test_inventory_service import record, snapshot
from test_manual_stock import manual_state as manual_state
from test_product_image_http import image_web as image_web
from test_product_image_service import images_state as images_state

from app.modules.access.administration import (
    AdministrationUnavailable,
    MembershipStateChanged,
)
from app.modules.access.inventory_permission_models import InventoryWithdrawalGrant
from app.modules.access.inventory_permissions import (
    InventoryReadChange,
    InventoryWithdrawalPermissions,
)
from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership
from app.modules.catalog.inputs import ProductSearchQuery
from app.modules.inventory.manual import ManualMovementInvalid
from app.modules.inventory.models import (
    ManualStockRecord,
    StockBalance,
    StockCountRecord,
    StockMovement,
)
from app.modules.inventory.service import InventoryConflict
from app.modules.inventory.withdrawals import InventoryWithdrawals, WithdrawalInput

pytestmark = pytest.mark.integration


@pytest.fixture
def operators(manual_state):
    state = arrange(manual_state)
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["reader"])
            .values(role="cashier")
        )
    state["withdraw_permissions"] = InventoryWithdrawalPermissions(state["runtime"])
    state["withdrawals"] = InventoryWithdrawals(state["runtime"])
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(StockMovement).where(
                    StockMovement.manual_record_id.in_(
                        select(ManualStockRecord.id).where(
                            ManualStockRecord.tenant_id == state["tenant"]
                        )
                    )
                )
            )
            connection.execute(
                delete(ManualStockRecord).where(
                    ManualStockRecord.tenant_id == state["tenant"]
                )
            )
            connection.execute(
                delete(InventoryWithdrawalGrant).where(
                    InventoryWithdrawalGrant.tenant_id == state["tenant"]
                )
            )
        cleanup(state)


def permission(state):
    return state["withdraw_permissions"].current(
        state["token"],
        membership_id=state["reader"],
        expected_actor=state["membership"],
    )


def grant(state, value=True, etag=None):
    return state["withdraw_permissions"].change(
        state["token"],
        membership_id=state["reader"],
        expected_actor=state["membership"],
        expected_etag=etag or permission(state).etag,
        data=InventoryReadChange(granted=value),
    )


def withdraw(state, *, quantity="1", key=None, product=None):
    return state["withdrawals"].record(
        state["reader_token"],
        expected_actor=state["reader"],
        operation_key=key or uuid4(),
        data=WithdrawalInput(
            product_id=product or state["product"],
            quantity=quantity,
            reason="Retiro autorizado",
        ),
    )


def test_default_deny_grant_atomic_valuation_replay_and_revoke(operators):
    state = operators
    record(state)
    before = snapshot(state)
    with pytest.raises(AccessDenied):
        withdraw(state)
    assert snapshot(state) == before
    grant(state)
    key = uuid4()
    result = withdraw(state, key=key)
    assert result.after_quantity == 1
    assert set(result.model_dump(mode="json")) == {
        "id",
        "product_id",
        "operation_key",
        "before_quantity",
        "after_quantity",
        "quantity_delta",
        "reason",
        "created_at",
    }
    assert snapshot(state)[1] == (1, 3)
    assert withdraw(state, key=key).id == result.id
    assert snapshot(state)[1] == (1, 3)
    with pytest.raises(InventoryConflict):
        withdraw(state, key=key, quantity="0.5")
    grant(state, False)
    with pytest.raises(AccessDenied):
        withdraw(state, key=key)
    with pytest.raises(AccessDenied):
        state["availability"].products(
            state["reader_token"],
            expected_actor=state["reader"],
            query=ProductSearchQuery(),
        )


def test_foreign_product_cost_routes_and_other_profiles_denied(operators):
    state = operators
    grant(state)
    with pytest.raises(AccessDenied):
        withdraw(state, product=state["foreign_product"])
    with pytest.raises(AccessDenied):
        state["service"].current_balance(
            state["reader_token"],
            product_id=state["product"],
            expected_actor=state["reader"],
        )
    for role in ("read_only", "mechanic", "admin"):
        with state["owner"].begin() as connection:
            connection.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["reader"])
                .values(role=role)
            )
        with pytest.raises(AccessDenied):
            withdraw(state)
        assert not permission(state).eligible


@pytest.mark.parametrize(
    "extra",
    [
        {"unit_cost": "1"},
        {"direction": "IN"},
        {"tenant_id": str(uuid4())},
        {"price": "1"},
        {"quantity": "-1"},
        {"quantity": "0"},
        {"quantity": "1.0001"},
    ],
)
def test_strict_operational_input(extra):
    with pytest.raises(ValidationError):
        WithdrawalInput.model_validate(
            dict(product_id=uuid4(), quantity="1", reason="Motivo") | extra
        )


def test_concurrent_withdrawals_never_oversell(operators):
    state = operators
    record(state, quantity="1")
    grant(state)

    def attempt(_):
        try:
            return withdraw(state)
        except ManualMovementInvalid:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1
    assert snapshot(state)[1] == (0, 0)


def test_failed_write_rolls_back_stock_and_evidence(operators):
    state = operators
    record(state)
    grant(state)
    before = snapshot(state)

    def fail(session, *_):
        if any(isinstance(row, ManualStockRecord) for row in session.new):
            raise SQLAlchemyError("injected")

    event.listen(Session, "before_flush", fail)
    try:
        with pytest.raises(RuntimeError):
            withdraw(state)
    finally:
        event.remove(Session, "before_flush", fail)
    assert snapshot(state) == before


def test_grant_revision_immutable_and_failed_grant_rollback(operators):
    state = operators
    old = permission(state).etag
    grant(state)
    with pytest.raises(MembershipStateChanged):
        grant(state, False, old)
    for command in (
        "UPDATE inventory_withdrawal_grants SET granted=false",
        "DELETE FROM inventory_withdrawal_grants",
        "TRUNCATE inventory_withdrawal_grants",
    ):
        with (
            state["runtime"].begin() as connection,
            pytest.raises(SQLAlchemyError),
            connection.begin_nested(),
        ):
            connection.execute(text(command))

    def fail(session, *_):
        if any(isinstance(row, InventoryWithdrawalGrant) for row in session.new):
            raise SQLAlchemyError("injected")

    event.listen(Session, "before_flush", fail)
    try:
        with pytest.raises(AdministrationUnavailable):
            grant(state, False)
    finally:
        event.remove(Session, "before_flush", fail)
    assert permission(state).granted


def test_disable_reactivate_removes_withdrawal_grant(operators):
    from app.modules.access.administration import (
        MembershipAdministrator,
        MembershipChange,
    )

    state = operators
    grant(state)
    administrator = MembershipAdministrator(state["runtime"])
    for active in (False, True):
        before = administrator.current_membership(
            state["token"], membership_id=state["reader"]
        )
        administrator.change_membership(
            state["token"],
            membership_id=state["reader"],
            expected_actor=state["membership"],
            expected_etag=before.etag,
            change=MembershipChange(role="cashier", is_active=active),
        )
        assert not permission(state).granted
    assert permission(state).eligible and permission(state).revision == "2"


def test_http_scoped_grant_and_withdrawal_deny_forged_financial_input(permission_web):
    from fastapi.testclient import TestClient

    from app.modules.inventory.service import InventoryAdministrator

    admin, state, policy = permission_web
    state["withdraw_permissions"] = InventoryWithdrawalPermissions(state["runtime"])
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["reader"])
            .values(role="cashier")
        )
    record(state | {"service": InventoryAdministrator(state["runtime"])})
    read = {
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "X-Expected-Membership": str(state["membership"]),
    }
    write = {
        "Origin": policy.origin,
        "X-CSRF-Token": admin.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
    }
    path = f"/auth/memberships/{state['reader']}/inventory-withdrawal"
    employee = TestClient(
        admin.app,
        base_url=policy.origin,
        client=("127.0.0.2", 12347),
        headers={"X-Real-IP": "127.0.0.1"},
    )
    employee.cookies.set(
        policy.session_cookie, state["reader_token"].get_secret_value()
    )
    try:
        assert (
            admin.get(path, headers={"X-Requested-With": "XMLHttpRequest"}).status_code
            == 428
        )
        current = admin.get(path, headers=read)
        assert current.status_code == 200
        granted = admin.patch(
            path,
            json={"granted": True},
            headers=write | {"If-Match": current.headers["ETag"]},
        )
        assert granted.status_code == 200
        employee_read = read | {"X-Expected-Membership": str(state["reader"])}
        assert (
            employee.get("/inventory/availability", headers=employee_read).status_code
            == 200
        )
        assert employee.get(path, headers=employee_read).status_code == 403
        assert employee.get("/auth/csrf", headers=employee_read).status_code == 204
        employee_write = {
            "Origin": policy.origin,
            "X-CSRF-Token": employee.cookies[policy.csrf_cookie],
            "X-Expected-Membership": str(state["reader"]),
            "Idempotency-Key": str(uuid4()),
        }
        body = {
            "product_id": str(state["product"]),
            "quantity": "1",
            "reason": "Retiro autorizado",
        }
        endpoint = "/inventory/availability/withdrawals"
        assert employee.post(endpoint, json=body, headers={}).status_code == 403
        assert (
            employee.post(
                endpoint, json=body | {"unit_cost": "0"}, headers=employee_write
            ).status_code
            == 422
        )
        result = employee.post(endpoint, json=body, headers=employee_write)
        assert result.status_code == 200
        assert result.json()["after_quantity"] == "1.000"
        assert not (
            {"value_delta", "before_value", "after_value", "declared_unit_cost"}
            & set(result.json())
        )
        assert (
            employee.post(endpoint, json=body, headers=employee_write).json()
            == result.json()
        )
        assert (
            admin.patch(
                path,
                json={"granted": False},
                headers=write | {"If-Match": granted.headers["ETag"]},
            ).status_code
            == 200
        )
        assert (
            employee.post(endpoint, json=body, headers=employee_write).status_code
            == 403
        )
    finally:
        employee.close()
        with state["owner"].begin() as connection:
            connection.execute(
                delete(StockMovement).where(
                    StockMovement.manual_record_id.in_(
                        select(ManualStockRecord.id).where(
                            ManualStockRecord.tenant_id == state["tenant"]
                        )
                    )
                )
            )
            connection.execute(
                delete(ManualStockRecord).where(
                    ManualStockRecord.tenant_id == state["tenant"]
                )
            )
            for model in (StockMovement, StockCountRecord, StockBalance):
                connection.execute(
                    delete(model).where(model.tenant_id == state["tenant"])
                )
            connection.execute(
                delete(InventoryWithdrawalGrant).where(
                    InventoryWithdrawalGrant.tenant_id == state["tenant"]
                )
            )


def test_cannot_replay_another_actors_operation_key(operators):
    from test_manual_stock import move

    state = operators
    record(state)
    key = uuid4()
    move(state, direction="OUT", quantity="1", key=key, reason="Retiro autorizado")
    grant(state)
    before = snapshot(state)
    with pytest.raises(AccessDenied):
        withdraw(state, key=key)
    assert snapshot(state) == before
