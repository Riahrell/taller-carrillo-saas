"""Receipt consistency, real concurrency and failure recovery in isolated PG."""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from purchase_state import purchase_state
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from test_inventory_service import competing

from app.modules.access.membership import AccessDenied
from app.modules.access.models import SessionRecord, TenantMembership
from app.modules.access.reauthentication import RecentAuthenticationRequired
from app.modules.access.sessions import issue_session, revoke_session
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import PhysicalCount
from app.modules.inventory.models import StockBalance, StockMovement
from app.modules.inventory.quantities import MAX_QUANTITY
from app.modules.inventory.service import InventoryAdministrator
from app.modules.purchases import receipts as receipt_module
from app.modules.purchases.inputs import PurchaseCreation
from app.modules.purchases.models import PurchaseReceipt, PurchaseReceiptLine, Supplier
from app.modules.purchases.receipts import PurchaseReceiver
from app.modules.purchases.service import (
    PurchaseConflict,
    PurchaseInvalid,
    PurchasesUnavailable,
)

pytestmark = pytest.mark.integration
D = Decimal


@pytest.fixture
def receiving(http_identity, recent_session):
    with purchase_state(http_identity, recent_session) as state:
        state["receiver"] = PurchaseReceiver(state["runtime"])
        state["purchase"] = new_purchase(state).id
        yield state


def new_purchase(state, cost="3"):
    return state["service"].record_purchase(
        state["token"],
        expected_actor=state["membership"],
        operation_key=uuid4(),
        data=PurchaseCreation(
            supplier_id=state["supplier"],
            lines=[
                dict(product_id=state["product"], quantity="2", unit_cost=cost),
                dict(product_id=state["second_product"], quantity="0.5", unit_cost="4"),
            ],
        ),
    )


def receive(state, *, key=None, purchase=None):
    return state["receiver"].receive(
        state["token"],
        expected_actor=state["membership"],
        purchase_id=purchase or state["purchase"],
        operation_key=key or uuid4(),
    )


def snapshot(state):
    with Session(state["runtime"]) as session:
        counts = tuple(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == state["tenant"])
            )
            for model in (PurchaseReceipt, PurchaseReceiptLine, StockMovement)
        )
        balances = tuple(
            session.execute(
                select(
                    StockBalance.product_id, StockBalance.quantity, StockBalance.value
                )
                .where(StockBalance.tenant_id == state["tenant"])
                .order_by(StockBalance.product_id)
            )
        )
        return counts, balances


def reconcile(state):
    with Session(state["runtime"]) as session:
        for balance in session.scalars(
            select(StockBalance).where(StockBalance.tenant_id == state["tenant"])
        ):
            totals = session.execute(
                select(
                    func.sum(StockMovement.quantity_delta),
                    func.sum(StockMovement.value_delta),
                ).where(
                    StockMovement.tenant_id == state["tenant"],
                    StockMovement.product_id == balance.product_id,
                )
            ).one()
            assert tuple(totals) == (balance.quantity, balance.value)


def count(state, product, quantity, cost=None):
    return InventoryAdministrator(state["runtime"]).record_count(
        state["token"],
        expected_actor=state["membership"],
        operation_key=uuid4(),
        data=PhysicalCount(
            product_id=product,
            counted_quantity=quantity,
            declared_unit_cost=cost,
            reason="Synthetic count",
        ),
    )


def test_receives_complete_purchase_with_typed_ledger_and_no_price_change(receiving):
    state = receiving
    result = receive(state)
    assert snapshot(state)[0] == (1, 2, 2)
    assert {
        (line.product_id, line.after_quantity, line.after_value)
        for line in result.lines
    } == {(state["product"], D(2), D(6)), (state["second_product"], D("0.5"), D(2))}
    assert all(line.before_quantity == line.before_value == 0 for line in result.lines)
    assert "actor_membership_id" not in result.model_dump()
    assert "tenant_id" not in result.model_dump()
    with Session(state["runtime"]) as session:
        assert session.get(Product, state["product"]).price == 10
        record = session.get(PurchaseReceipt, result.id)
        assert record.actor_membership_id == state["membership"]
        assert record.created_at.utcoffset().total_seconds() == 0
        assert all(
            m.count_id is None and m.receipt_line_id is not None
            for m in session.scalars(
                select(StockMovement).where(StockMovement.tenant_id == state["tenant"])
            )
        )
    reconcile(state)


def test_replay_is_historical_and_different_key_cannot_receive_twice(receiving):
    state = receiving
    key = uuid4()
    original = receive(state, key=key)
    count(state, state["product"], "0")
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(is_active=False)
        )
    assert receive(state, key=key) == original
    with pytest.raises(PurchaseConflict):
        receive(state)
    assert snapshot(state) == before
    reconcile(state)


def test_second_purchase_uses_locked_stock_and_changes_weighted_cost(receiving):
    state = receiving
    key = uuid4()
    receive(state, key=key)
    second = new_purchase(state, cost="6")
    with pytest.raises(PurchaseConflict):
        receive(state, key=key, purchase=second.id)
    receive(state, purchase=second.id)
    with Session(state["runtime"]) as session:
        balance = session.get(StockBalance, (state["tenant"], state["product"]))
        assert (balance.quantity, balance.value) == (D(4), D(18))
    assert snapshot(state)[0] == (2, 4, 4)
    reconcile(state)


@pytest.mark.parametrize("mode", ["missing", "foreign", "inactive_product"])
def test_receipt_requires_authorized_purchase_and_active_products(
    receiving, mode, recent_session
):
    state = receiving
    target = state["purchase"]
    if mode == "missing":
        target = uuid4()
    elif mode == "inactive_product":
        with state["owner"].begin() as connection:
            connection.execute(
                update(Product)
                .where(Product.id == state["product"])
                .values(is_active=False)
            )
    else:
        with state["owner"].begin() as connection:
            connection.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["foreign_membership"])
                .values(role="admin")
            )
        with Session(state["runtime"]) as session, session.begin():
            state = state | dict(
                token=issue_session(
                    session, user_id=state["foreign"], tenant_id=state["other"]
                ).token,
                membership=state["foreign_membership"],
            )
        recent_session(state["token"])
    with pytest.raises(AccessDenied):
        receive(state, purchase=target)
    assert snapshot(state)[0] == (0, 0, 0)


def test_existing_purchase_can_be_received_after_supplier_deactivation(receiving):
    state = receiving
    with state["owner"].begin() as connection:
        connection.execute(
            update(Supplier)
            .where(Supplier.id == state["supplier"])
            .values(is_active=False)
        )
    receive(state)
    reconcile(state)


@pytest.mark.parametrize(
    "mode", ["cashier", "mechanic", "read_only", "revoked", "stale", "wrong_actor"]
)
def test_authorization_is_required_for_new_receipts_and_replays(receiving, mode):
    state = receiving
    key = uuid4()
    receive(state, key=key)
    before = snapshot(state)
    if mode in {"cashier", "mechanic", "read_only"}:
        with state["owner"].begin() as connection:
            connection.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["membership"])
                .values(role=mode)
            )
    elif mode == "revoked":
        with Session(state["runtime"]) as session, session.begin():
            revoke_session(session, state["token"])
    elif mode == "stale":
        with state["owner"].begin() as connection:
            connection.execute(
                update(SessionRecord)
                .where(SessionRecord.membership_id == state["membership"])
                .values(reauthenticated_at=None, reauthentication_fingerprint=None)
            )
    else:
        state = state | dict(membership=state["foreign_membership"])
    for attempt in (key, uuid4()):
        with pytest.raises((AccessDenied, RecentAuthenticationRequired)):
            receive(state, key=attempt)
    assert snapshot(state) == before


@pytest.mark.parametrize("capacity", ["quantity", "value"])
def test_capacity_failure_on_later_product_rolls_back_earlier_entry(
    receiving, capacity
):
    state = receiving
    last = max(state["product"], state["second_product"])
    count(
        state,
        last,
        str(MAX_QUANTITY) if capacity == "quantity" else "1",
        "0" if capacity == "quantity" else "9999999999999999999999.999999",
    )
    before = snapshot(state)
    with pytest.raises(PurchaseInvalid):
        receive(state)
    assert snapshot(state) == before
    reconcile(state)


@pytest.mark.parametrize(
    "prefix,occurrence",
    [
        ("INSERT INTO purchase_receipts", 1),
        ("INSERT INTO purchase_receipt_lines", 2),
        ("INSERT INTO stock_movements", 2),
        ("INSERT INTO stock_balances", 2),
        ("UPDATE stock_balances", 1),
    ],
)
def test_sql_failure_at_any_step_reverts_all_products(receiving, prefix, occurrence):
    state = receiving
    if prefix.startswith("UPDATE"):
        count(state, state["product"], "1", "1")
    before = snapshot(state)
    hits = 0

    def fail(connection, cursor, statement, parameters, context, executemany):
        nonlocal hits
        if statement.startswith(prefix):
            hits += 1
            if hits == occurrence:
                return "INSERT INTO stock_balances (nonexistent) VALUES (1)", {}
        return statement, parameters

    event.listen(state["runtime"], "before_cursor_execute", fail, retval=True)
    try:
        with pytest.raises(PurchasesUnavailable):
            receive(state)
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    assert hits == occurrence
    assert snapshot(state) == before
    reconcile(state)


@pytest.mark.parametrize("timing", ["before_commit", "after_commit"])
def test_commit_failure_and_lost_acknowledgement_are_recoverable(receiving, timing):
    state = receiving
    key = uuid4()

    def fail(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(Session, timing, fail)
    try:
        with pytest.raises(PurchasesUnavailable):
            receive(state, key=key)
    finally:
        event.remove(Session, timing, fail)
    assert snapshot(state)[0] == ((0, 0, 0) if timing == "before_commit" else (1, 2, 2))
    receive(state, key=key)
    assert snapshot(state)[0] == (1, 2, 2)
    reconcile(state)


def test_final_authorization_failure_rolls_back_all_products(receiving, monkeypatch):
    state = receiving
    original = receipt_module.authorize_administrator
    calls = 0

    def authorize(session, token, expected_actor):
        nonlocal calls
        calls += 1
        if calls == 2:
            session.execute(
                update(SessionRecord)
                .where(SessionRecord.membership_id == expected_actor)
                .values(reauthenticated_at=None, reauthentication_fingerprint=None)
            )
        return original(session, token, expected_actor)

    monkeypatch.setattr(receipt_module, "authorize_administrator", authorize)
    with pytest.raises(RecentAuthenticationRequired):
        receive(state)
    assert snapshot(state) == ((0, 0, 0), ())


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_receipts_create_one_entry_per_purchase_line(receiving, same_key):
    state = receiving
    keys = [uuid4(), uuid4()]

    def work(index):
        try:
            return receive(state, key=keys[0] if same_key else keys[index])
        except PurchaseConflict:
            return None

    results = competing(state, work)
    if same_key:
        assert results[0] == results[1]
    else:
        assert sum(result is not None for result in results) == 1
    assert snapshot(state)[0] == (1, 2, 2)
    reconcile(state)


def test_receipt_and_physical_count_share_locking_and_ledger(receiving):
    state = receiving

    def work(index):
        return receive(state) if index == 0 else count(state, state["product"], "0")

    competing(state, work)
    assert snapshot(state)[0][:2] == (1, 2)
    reconcile(state)


@pytest.mark.parametrize(
    "change", ["wrong_delta", "wrong_tenant", "no_source", "two_sources"]
)
def test_ledger_source_cannot_be_forged_even_by_owner_sql(receiving, change):
    state = receiving
    receive(state)
    with Session(state["runtime"]) as session:
        movement = session.scalar(
            select(StockMovement).where(StockMovement.tenant_id == state["tenant"])
        )
        identifier = movement.id
        changes = {
            "wrong_delta": dict(quantity_delta=movement.quantity_delta + 1),
            "wrong_tenant": dict(tenant_id=state["other"]),
            "no_source": dict(receipt_line_id=None),
            "two_sources": dict(count_id=uuid4()),
        }
    with pytest.raises(IntegrityError), state["owner"].begin() as connection:
        connection.execute(
            update(StockMovement)
            .where(StockMovement.id == identifier)
            .values(**changes[change])
        )
    reconcile(state)


@pytest.mark.parametrize("model", [PurchaseReceipt, PurchaseReceiptLine])
def test_runtime_cannot_rewrite_or_delete_receipt_evidence(receiving, model):
    state = receiving
    receive(state)
    for statement in (delete(model), update(model).values(tenant_id=uuid4())):
        with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
            connection.execute(statement)


def test_downgrade_refuses_to_erase_receipts(receiving):
    state = receiving
    receive(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(RuntimeError, match="refusing destructive downgrade"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0035_purchases")
    with state["owner"].connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0047_product_image_variants"
        )
    assert snapshot(state)[0] == (1, 2, 2)


def test_zero_cost_receipt_and_missing_movement_replay(receiving):
    state = receiving
    purchase = new_purchase(state, cost="0")
    key = uuid4()
    result = receive(state, key=key, purchase=purchase.id)
    line = next(line for line in result.lines if line.product_id == state["product"])
    assert line.after_quantity == 2 and line.after_value == 0
    reconcile(state)
    with state["owner"].begin() as connection:
        connection.execute(
            delete(StockMovement).where(StockMovement.tenant_id == state["tenant"])
        )
    before = snapshot(state)
    with pytest.raises(PurchasesUnavailable):
        receive(state, key=key, purchase=purchase.id)
    assert snapshot(state) == before
