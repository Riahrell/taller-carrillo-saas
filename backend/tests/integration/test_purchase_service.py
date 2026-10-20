"""Real transactions for authorized purchase recording, not receipt or payment."""

from decimal import Decimal
from uuid import uuid4

import pytest
from purchase_state import purchase_state
from pydantic import ValidationError
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_inventory_service import competing

from app.modules.access.membership import AccessDenied
from app.modules.access.models import SessionRecord, TenantMembership
from app.modules.access.reauthentication import RecentAuthenticationRequired
from app.modules.access.sessions import issue_session, revoke_session
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockBalance, StockMovement
from app.modules.purchases import service as purchase_module
from app.modules.purchases.inputs import PurchaseCreation, SupplierCreation
from app.modules.purchases.models import Purchase, PurchaseItem, Supplier
from app.modules.purchases.service import (
    PurchaseConflict,
    PurchaseInvalid,
    PurchasesUnavailable,
)

pytestmark = pytest.mark.integration
D = Decimal


@pytest.fixture
def purchases(http_identity, recent_session):
    with purchase_state(http_identity, recent_session) as state:
        yield state


def command(state, *, quantity="2", cost="3", supplier=None, product=None):
    return PurchaseCreation(
        supplier_id=supplier or state["supplier"],
        lines=[
            dict(
                product_id=product or state["product"],
                quantity=quantity,
                unit_cost=cost,
            )
        ],
    )


def record(state, *, key=None, data=None):
    return state["service"].record_purchase(
        state["token"],
        expected_actor=state["membership"],
        operation_key=key or uuid4(),
        data=data if data is not None else command(state),
    )


def totals(state):
    with Session(state["runtime"]) as session:
        return tuple(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == state["tenant"])
            )
            for model in (Purchase, PurchaseItem, StockBalance, StockMovement)
        )


def test_record_snapshots_totals_and_actor_without_touching_stock(purchases):
    state = purchases
    data = command(state).model_copy(
        update={
            "lines": command(state).lines
            + command(
                state,
                product=state["second_product"],
                quantity="0.001",
                cost="4.999999",
            ).lines
        }
    )
    view = record(state, data=data)
    assert view.total == D(6)
    assert view.lines[1].stock_value == D("0.005000")
    assert view.lines[1].amount == 0
    assert totals(state) == (1, 2, 0, 0)
    assert view.supplier_name == "Supplier A"
    assert view.lines[0].product_name == "Counted product"
    assert "actor_membership_id" not in view.model_dump()
    assert "tenant_id" not in view.model_dump()
    assert view.model_dump(mode="json")["total"] == "6.00"
    assert view.model_dump(mode="json")["lines"][0]["unit_cost"] == "3.000000"
    with Session(state["runtime"]) as session:
        saved = session.get(Purchase, view.id)
        assert saved.actor_membership_id == state["membership"]
        assert saved.line_count == 2
        assert saved.created_at.utcoffset().total_seconds() == 0


def test_normalized_replay_survives_product_and_supplier_changes(purchases):
    state = purchases
    key = uuid4()
    first = record(state, key=key)
    with state["owner"].begin() as connection:
        connection.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(name="Renamed", is_active=False, price=D(99))
        )
        connection.execute(
            update(Supplier)
            .where(Supplier.id == state["supplier"])
            .values(name="Renamed supplier", is_active=False)
        )
    assert (
        record(state, key=key, data=command(state, quantity="2.000", cost="3.000000"))
        == first
    )
    assert (
        state["service"].current_purchase(
            state["token"], expected_actor=state["membership"], purchase_id=first.id
        )
        == first
    )
    assert totals(state) == (1, 1, 0, 0)


@pytest.mark.parametrize("change", ["cost", "quantity", "supplier", "order"])
def test_same_key_changed_content_conflicts(purchases, change):
    state = purchases
    key = uuid4()
    first = command(state)
    if change == "order":
        first = first.model_copy(
            update={
                "lines": first.lines
                + command(state, product=state["second_product"]).lines
            }
        )
    record(state, key=key, data=first)
    changes = {
        "cost": dict(cost="4"),
        "quantity": dict(quantity="3"),
        "supplier": dict(supplier=state["foreign_supplier"]),
    }
    changed = (
        command(state, **changes[change])
        if change != "order"
        else first.model_copy(update={"lines": tuple(reversed(first.lines))})
    )
    with pytest.raises(PurchaseConflict):
        record(state, key=key, data=changed)
    assert totals(state) == (1, len(first.lines), 0, 0)


@pytest.mark.parametrize("kind", ["supplier", "product"])
@pytest.mark.parametrize("mode", ["missing", "foreign", "inactive"])
def test_references_require_current_company_and_activity(purchases, kind, mode):
    state = purchases
    target = uuid4() if mode == "missing" else state[f"foreign_{kind}"]
    if mode == "inactive":
        target = state[kind]
        with state["owner"].begin() as connection:
            model = Supplier if kind == "supplier" else Product
            connection.execute(
                update(model).where(model.id == target).values(is_active=False)
            )
    with pytest.raises(AccessDenied, match="^Access denied$"):
        record(state, data=command(state, **{kind: target}))
    assert totals(state) == (0, 0, 0, 0)


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_non_admin_cannot_create_supplier_record_read_or_replay(purchases, role):
    state = purchases
    key = uuid4()
    saved = record(state, key=key)
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    for call in (
        lambda: record(state),
        lambda: record(state, key=key),
        lambda: state["service"].create_supplier(
            state["token"],
            expected_actor=state["membership"],
            data=SupplierCreation(name="Denied"),
        ),
        lambda: state["service"].current_purchase(
            state["token"], expected_actor=state["membership"], purchase_id=saved.id
        ),
    ):
        with pytest.raises(AccessDenied):
            call()
    assert totals(state) == (1, 1, 0, 0)


@pytest.mark.parametrize("mode", ["revoked", "wrong_actor", "stale"])
def test_session_checked_on_new_purchase_and_replay(purchases, mode):
    state = purchases
    key = uuid4()
    record(state, key=key)
    if mode == "revoked":
        with Session(state["runtime"]) as session, session.begin():
            revoke_session(session, state["token"])
    elif mode == "wrong_actor":
        state["membership"] = state["foreign_membership"]
    else:
        with state["owner"].begin() as connection:
            connection.execute(
                update(SessionRecord)
                .where(SessionRecord.membership_id == state["membership"])
                .values(reauthenticated_at=None, reauthentication_fingerprint=None)
            )
    for attempt in (key, uuid4()):
        with pytest.raises((AccessDenied, RecentAuthenticationRequired)):
            record(state, key=attempt)
    assert totals(state) == (1, 1, 0, 0)


def test_read_is_tenant_bound_and_does_not_require_recent_confirmation(purchases):
    state = purchases
    view = record(state)
    with state["owner"].begin() as connection:
        connection.execute(
            update(SessionRecord)
            .where(SessionRecord.membership_id == state["membership"])
            .values(reauthenticated_at=None, reauthentication_fingerprint=None)
        )
    assert (
        state["service"].current_purchase(
            state["token"], expected_actor=state["membership"], purchase_id=view.id
        )
        == view
    )
    with pytest.raises(AccessDenied):
        state["service"].current_purchase(
            state["token"], expected_actor=state["membership"], purchase_id=uuid4()
        )
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["foreign_membership"])
            .values(role="admin")
        )
    with Session(state["runtime"]) as session, session.begin():
        foreign = issue_session(
            session, user_id=state["foreign"], tenant_id=state["other"]
        ).token
    with pytest.raises(AccessDenied):
        state["service"].current_purchase(
            foreign, expected_actor=state["foreign_membership"], purchase_id=view.id
        )


@pytest.mark.parametrize(
    "prefix",
    ["INSERT INTO purchases", "INSERT INTO purchase_items", "INSERT INTO suppliers"],
)
def test_sql_write_failure_rolls_back_all_evidence(purchases, prefix):
    state = purchases

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(prefix):
            return "INSERT INTO purchases (nonexistent) VALUES (1)", {}
        return statement, parameters

    event.listen(state["runtime"], "before_cursor_execute", fail, retval=True)
    try:
        with pytest.raises(PurchasesUnavailable, match="^Purchases unavailable$"):
            if prefix.endswith("suppliers"):
                state["service"].create_supplier(
                    state["token"],
                    expected_actor=state["membership"],
                    data=SupplierCreation(name="Failure"),
                )
            else:
                record(state)
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    assert totals(state) == (0, 0, 0, 0)


@pytest.mark.parametrize("timing", ["before_commit", "after_commit"])
def test_commit_failure_or_lost_acknowledgement(purchases, timing):
    state = purchases
    key = uuid4()

    def fail(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(Session, timing, fail)
    try:
        with pytest.raises(PurchasesUnavailable):
            record(state, key=key)
    finally:
        event.remove(Session, timing, fail)
    assert totals(state) == (
        (0, 0, 0, 0) if timing == "before_commit" else (1, 1, 0, 0)
    )
    result = record(state, key=key)
    assert result.total == 6
    assert totals(state) == (1, 1, 0, 0)


def test_final_authorization_failure_rolls_back(purchases, monkeypatch):
    state = purchases
    original = purchase_module.authorize_administrator
    calls = 0

    def authorize(session, token, expected_actor, **kw):
        nonlocal calls
        calls += 1
        if calls == 2:
            session.execute(
                update(SessionRecord)
                .where(SessionRecord.membership_id == expected_actor)
                .values(reauthenticated_at=None, reauthentication_fingerprint=None)
            )
        return original(session, token, expected_actor, **kw)

    monkeypatch.setattr(purchase_module, "authorize_administrator", authorize)
    data = command(state)
    data = data.model_copy(
        update={
            "lines": data.lines + command(state, product=state["second_product"]).lines
        }
    )
    with pytest.raises(RecentAuthenticationRequired):
        record(state, data=data)
    assert totals(state) == (0, 0, 0, 0)


def test_capacity_and_internal_invalid_copy_cannot_persist(purchases):
    state = purchases
    with pytest.raises(PurchaseInvalid):
        record(state, data=command(state, cost="999999999999999999.99"))
    data = command(state)
    bad = data.model_copy(
        update={"lines": (data.lines[0].model_copy(update={"quantity": D(-1)}),)}
    )
    with pytest.raises(ValidationError):
        record(state, data=bad)
    assert totals(state) == (0, 0, 0, 0)


@pytest.mark.parametrize("conflict", [False, True])
def test_concurrent_key_is_consumed_once(purchases, conflict):
    state = purchases
    key = uuid4()

    def work(index):
        try:
            return record(
                state,
                key=key,
                data=command(state, quantity=str(index + 1) if conflict else "2"),
            )
        except PurchaseConflict:
            return None

    results = competing(state, work)
    assert totals(state) == (1, 1, 0, 0)
    if conflict:
        assert sum(result is not None for result in results) == 1
    else:
        assert results[0] == results[1]


@pytest.mark.parametrize("corruption", ["line_count", "total", "missing_line"])
def test_incomplete_purchase_fails_closed_on_read_and_replay(purchases, corruption):
    state = purchases
    key = uuid4()
    view = record(state, key=key)
    with state["owner"].begin() as connection:
        if corruption == "missing_line":
            connection.execute(
                delete(PurchaseItem).where(PurchaseItem.purchase_id == view.id)
            )
        else:
            connection.execute(
                update(Purchase)
                .where(Purchase.id == view.id)
                .values(**{corruption: 2 if corruption == "line_count" else D(100)})
            )
    with pytest.raises(PurchasesUnavailable):
        state["service"].current_purchase(
            state["token"], expected_actor=state["membership"], purchase_id=view.id
        )
    with pytest.raises(PurchasesUnavailable):
        record(state, key=key)


def test_operation_key_is_scoped_to_session_company(purchases, recent_session):
    state = purchases
    key = uuid4()
    first = record(state, key=key)
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["foreign_membership"])
            .values(role="admin")
        )
    with Session(state["runtime"]) as session, session.begin():
        foreign_token = issue_session(
            session, user_id=state["foreign"], tenant_id=state["other"]
        ).token
    recent_session(foreign_token)
    foreign = state | dict(
        token=foreign_token,
        membership=state["foreign_membership"],
        supplier=state["foreign_supplier"],
        product=state["foreign_product"],
    )
    second = record(foreign, key=key)
    assert first.id != second.id
    assert record(state, key=key) == first
    assert totals(state) == (1, 1, 0, 0)
