"""Grant isolation, exact operational DTOs, revocation and current-session checks."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_inventory_service import inventory_state as inventory_state
from test_inventory_service import record
from test_product_image_http import image_web as image_web
from test_product_image_service import images_state as images_state
from test_product_image_service import replace

from app.modules.access.administration import (
    AdministrationUnavailable,
    MembershipStateChanged,
)
from app.modules.access.inventory_permission_models import InventoryReadGrant
from app.modules.access.inventory_permissions import (
    InventoryPermissions,
    InventoryReadChange,
)
from app.modules.access.membership import AccessDenied
from app.modules.access.models import AuditEvent, SessionRecord, TenantMembership
from app.modules.access.sessions import issue_session
from app.modules.catalog.inputs import ProductSearchQuery
from app.modules.catalog.models import Product
from app.modules.inventory.availability import InventoryAvailability

pytestmark = pytest.mark.integration


def arrange(state):
    state["permissions"] = InventoryPermissions(state["runtime"])
    state["availability"] = InventoryAvailability(state["runtime"])
    with Session(state["runtime"]) as session, session.begin():
        target = TenantMembership(
            tenant_id=state["tenant"],
            user_id=state["foreign"],
            role="read_only",
            is_active=True,
        )
        session.add(target)
        session.flush()
        state["reader"] = target.id
        state["reader_token"] = issue_session(
            session, user_id=state["foreign"], tenant_id=state["tenant"]
        ).token
    return state


def cleanup(state):
    with state["owner"].begin() as connection:
        connection.execute(
            delete(InventoryReadGrant).where(
                InventoryReadGrant.tenant_id == state["tenant"]
            )
        )
        connection.execute(
            delete(SessionRecord).where(SessionRecord.membership_id == state["reader"])
        )
        connection.execute(
            delete(AuditEvent).where(AuditEvent.target_membership_id == state["reader"])
        )
        connection.execute(
            delete(TenantMembership).where(TenantMembership.id == state["reader"])
        )


@pytest.fixture
def readers(inventory_state):
    state = arrange(inventory_state)
    try:
        yield state
    finally:
        cleanup(state)


@pytest.fixture
def reader_images(images_state):
    state = arrange(images_state)
    try:
        yield state
    finally:
        cleanup(state)


def current(state):
    return state["permissions"].current(
        state["token"],
        membership_id=state["reader"],
        expected_actor=state["membership"],
    )


def change(state, granted, etag=None):
    return state["permissions"].change(
        state["token"],
        membership_id=state["reader"],
        expected_actor=state["membership"],
        expected_etag=etag or current(state).etag,
        data=InventoryReadChange(granted=granted),
    )


def consult(state, **query):
    return state["availability"].products(
        state["reader_token"],
        expected_actor=state["reader"],
        query=ProductSearchQuery(**query),
    )


def test_default_deny_grant_no_cost_data_and_immediate_revoke(readers):
    state = readers
    record(state)
    assert current(state).granted is False and current(state).revision == "0"
    with pytest.raises(AccessDenied):
        consult(state)
    granted = change(state, True)
    assert granted.granted and granted.effective and granted.revision == "1"
    data = consult(state).model_dump(mode="json")
    assert len(data["items"]) == 1
    assert set(data["items"][0]) == {
        "id",
        "name",
        "sku",
        "category",
        "price",
        "quantity",
    }
    assert (
        data["items"][0]["quantity"] == "2.000" and data["items"][0]["price"] == "10.00"
    )
    assert state["foreign_product"] not in [row.id for row in consult(state).items]
    change(state, False)
    with pytest.raises(AccessDenied):
        consult(state)
    # A grant never authorizes administrative cost/history queries or writes.
    with pytest.raises(AccessDenied):
        state["service"].current_balance(
            state["reader_token"],
            product_id=state["product"],
            expected_actor=state["reader"],
        )


def test_grant_does_not_change_stock_and_noop_keeps_revision(readers):
    state = readers
    assert change(state, False).revision == "0"
    first = change(state, True)
    assert change(state, True).revision == first.revision
    assert consult(state).items[0].quantity == 0


def test_stale_revision_concurrent_changes_have_one_winner(readers):
    state = readers
    etag = current(state).etag

    def attempt(_):
        try:
            return change(state, True, etag)
        except MembershipStateChanged:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1
    assert current(state).revision == "1"


def test_inactive_or_changed_role_stops_operational_access(readers):
    state = readers
    change(state, True)
    for values in (
        dict(is_active=False),
        dict(is_active=True, role="cashier"),
        dict(role="mechanic"),
    ):
        with state["owner"].begin() as connection:
            connection.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["reader"])
                .values(**values)
            )
        with pytest.raises(AccessDenied):
            consult(state)
    assert not current(state).effective


def test_foreign_target_self_grant_and_expected_actor_denied(readers):
    state = readers
    with pytest.raises(AccessDenied):
        state["permissions"].current(
            state["token"],
            membership_id=state["foreign_membership"],
            expected_actor=state["membership"],
        )
    with pytest.raises(AccessDenied):
        state["permissions"].current(
            state["reader_token"],
            membership_id=state["reader"],
            expected_actor=state["reader"],
        )
    with pytest.raises(AccessDenied):
        state["availability"].products(
            state["reader_token"], expected_actor=uuid4(), query=ProductSearchQuery()
        )


def test_search_literal_inactive_products_and_tenant_filter(readers):
    state = readers
    change(state, True)
    with state["owner"].begin() as connection:
        connection.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(name="Filtro_1", sku="SKU-ONE")
        )
    assert len(consult(state, q="sku-one").items) == 1
    assert not consult(state, q="%").items
    assert not consult(state, category_id=uuid4()).items
    with state["owner"].begin() as connection:
        connection.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(is_active=False)
        )
    assert not consult(state).items


def test_failed_audit_append_never_grants_permission(readers):
    state = readers

    def fail(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO inventory_read_grants"):
            raise SQLAlchemyError("injected")

    event.listen(state["runtime"], "before_cursor_execute", fail)
    try:
        with pytest.raises(AdministrationUnavailable):
            change(state, True)
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    assert current(state).revision == "0"
    with pytest.raises(AccessDenied):
        consult(state)


def test_permission_evidence_is_append_only(readers):
    state = readers
    change(state, True)
    for sql in (
        "UPDATE inventory_read_grants SET granted=false",
        "DELETE FROM inventory_read_grants",
        "TRUNCATE inventory_read_grants",
    ):
        with (
            state["runtime"].begin() as connection,
            pytest.raises(SQLAlchemyError),
            connection.begin_nested(),
        ):
            connection.execute(text(sql))


def test_operational_image_read_checks_grant_again_after_storage(reader_images):
    state = reader_images
    replace(state)
    change(state, True)
    assert state["service"].read(
        state["reader_token"],
        expected_actor=state["reader"],
        product_id=state["product"],
        operational=True,
    )[1]
    with pytest.raises(AccessDenied):
        state["service"].read(
            state["reader_token"],
            expected_actor=state["reader"],
            product_id=state["product"],
        )
    state["storage"].on_read = lambda: change(state, False)
    with pytest.raises(AccessDenied):
        state["service"].read(
            state["reader_token"],
            expected_actor=state["reader"],
            product_id=state["product"],
            operational=True,
        )


@pytest.fixture
def permission_web(image_web):
    from pydantic import SecretStr

    client, state, policy = image_web
    state["token"] = SecretStr(client.cookies[policy.session_cookie])
    state = arrange(state)
    try:
        yield client, state, policy
    finally:
        cleanup(state)


def test_http_permission_projection_and_photo_revocation(permission_web):
    from fastapi.testclient import TestClient

    admin, state, policy = permission_web
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
    path = f"/auth/memberships/{state['reader']}/inventory-read"
    before = admin.get(path, headers=read)
    assert before.status_code == 200 and before.json()["granted"] is False
    assert admin.patch(path, json={"granted": True}, headers=write).status_code == 428
    assert (
        admin.patch(
            path,
            json={"granted": True, "tenant_id": str(state["other"])},
            headers=write | {"If-Match": before.headers["ETag"]},
        ).status_code
        == 422
    )
    assert (
        admin.patch(
            path, json={"granted": True}, headers={"If-Match": before.headers["ETag"]}
        ).status_code
        == 403
    )
    granted = admin.patch(
        path,
        json={"granted": True},
        headers=write | {"If-Match": before.headers["ETag"]},
    )
    assert granted.status_code == 200 and granted.json()["effective"] is True
    assert (
        admin.patch(
            path,
            json={"granted": False},
            headers=write | {"If-Match": before.headers["ETag"]},
        ).status_code
        == 412
    )
    employee = TestClient(
        admin.app,
        base_url=policy.origin,
        client=("127.0.0.2", 12347),
        headers={"X-Real-IP": "127.0.0.1"},
    )
    employee.cookies.set(
        policy.session_cookie, state["reader_token"].get_secret_value()
    )
    employee_read = read | {"X-Expected-Membership": str(state["reader"])}
    try:
        listed = employee.get("/inventory/availability", headers=employee_read)
        assert (
            listed.status_code == 200 and listed.headers["Cache-Control"] == "no-store"
        )
        assert len(listed.json()["items"]) == 1
        assert set(listed.json()["items"][0]) == {
            "id",
            "name",
            "sku",
            "category",
            "price",
            "quantity",
        }
        assert (
            employee.get(
                "/inventory/availability?tenant_id=" + str(state["other"]),
                headers=employee_read,
            ).status_code
            == 422
        )
        assert (
            employee.get(
                "/inventory/availability?limit=1&limit=2", headers=employee_read
            ).status_code
            == 422
        )
        assert (
            employee.get(
                f"/inventory/products/{state['product']}/balance", headers=employee_read
            ).status_code
            == 403
        )
        assert employee.get(path, headers=employee_read).status_code == 403
        replace(state)
        image_path = f"/inventory/availability/products/{state['product']}/image"
        image = employee.get(image_path, headers=employee_read)
        assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
        assert image.headers["X-Access-Membership"] == str(state["reader"])
        assert (
            employee.get(
                f"/inventory/availability/products/{state['foreign_product']}/image",
                headers=employee_read,
            ).status_code
            == 403
        )
        assert employee.get("/auth/csrf", headers=employee_read).status_code == 204
        forbidden_write = employee.post(
            "/inventory/counts",
            json={
                "product_id": str(state["product"]),
                "counted_quantity": "1.000",
                "reason": "Not allowed",
                "declared_unit_cost": "1.000000",
            },
            headers={
                "Origin": policy.origin,
                "X-CSRF-Token": employee.cookies[policy.csrf_cookie],
                "X-Expected-Membership": str(state["reader"]),
                "Idempotency-Key": str(uuid4()),
            },
        )
        assert forbidden_write.status_code == 403
        revoked = admin.patch(
            path,
            json={"granted": False},
            headers=write | {"If-Match": granted.headers["ETag"]},
        )
        assert revoked.status_code == 200
        assert (
            employee.get("/inventory/availability", headers=employee_read).status_code
            == 403
        )
        assert employee.get(image_path, headers=employee_read).status_code == 403
    finally:
        employee.close()


def test_disabling_and_reactivating_membership_does_not_restore_grant(readers):
    from app.modules.access.administration import (
        MembershipAdministrator,
        MembershipChange,
    )

    state = readers
    change(state, True)
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
            change=MembershipChange(role="read_only", is_active=active),
        )
        assert current(state).granted is False
    assert current(state).revision == "2" and current(state).eligible


def test_schema_downgrade_refuses_permission_evidence(readers):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext

    state = readers
    change(state, True)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with state["owner"].begin() as connection:
        config.attributes["connection"] = connection
        with (
            pytest.raises(RuntimeError, match="Inventory permission evidence"),
            connection.begin_nested(),
        ):
            command.downgrade(config, "0044_employee_registration")
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0045_inventory_read_grants"
        )


def test_unconfirmed_administrator_cannot_grant(readers):
    from app.modules.access.reauthentication import RecentAuthenticationRequired

    state = readers
    with Session(state["runtime"]) as session, session.begin():
        unconfirmed = issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        ).token
    with pytest.raises(RecentAuthenticationRequired):
        state["permissions"].change(
            unconfirmed,
            membership_id=state["reader"],
            expected_actor=state["membership"],
            expected_etag=current(state).etag,
            data=InventoryReadChange(granted=True),
        )
    assert current(state).revision == "0"
