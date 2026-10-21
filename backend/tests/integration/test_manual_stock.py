"""Real transactional manual deltas, idempotency and competing withdrawals."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_inventory_service import inventory_state  # noqa: F401

from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership
from app.modules.inventory.inputs import CountPageQuery, ManualStockInput
from app.modules.inventory.manual import ManualMovementInvalid
from app.modules.inventory.models import ManualStockRecord, StockBalance, StockMovement
from app.modules.inventory.service import InventoryConflict, InventoryUnavailable

pytestmark = pytest.mark.integration
D = Decimal


@pytest.fixture
def manual_state(inventory_state):  # noqa: F811
    state = inventory_state
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


def move(state, direction="IN", quantity="2", cost="3", *, key=None, **extra):
    return state["service"].record_manual(
        state["token"],
        expected_actor=state["membership"],
        operation_key=key or uuid4(),
        data=ManualStockInput.model_validate(
            dict(
                product_id=state["product"],
                direction=direction,
                quantity=quantity,
                unit_cost=cost if direction == "IN" else None,
                reason="Manual stock movement",
            )
            | extra
        ),
    )


def totals(state):
    with Session(state["runtime"]) as session:
        balance = session.get(StockBalance, (state["tenant"], state["product"]))
        ledger = session.execute(
            select(
                func.coalesce(func.sum(StockMovement.quantity_delta), 0),
                func.coalesce(func.sum(StockMovement.value_delta), 0),
            ).where(
                StockMovement.tenant_id == state["tenant"],
                StockMovement.product_id == state["product"],
            )
        ).one()
        if balance:
            assert tuple(ledger) == (balance.quantity, balance.value)
        records = session.scalar(select(func.count()).select_from(ManualStockRecord))
        return tuple(ledger), records


def test_entries_and_exits_use_locked_weighted_value(manual_state):
    state = manual_state
    move(state, quantity="2", cost="3")
    move(state, quantity="2", cost="5")
    out = move(state, "OUT", "1")
    assert (out.before_quantity, out.after_quantity) == (D(4), D(3))
    assert (out.before_value, out.after_value, out.value_delta) == (D(16), D(12), D(-4))
    assert out.declared_unit_cost is None
    assert totals(state) == ((D(3), D(12)), 3)
    emptied = move(state, "OUT", "3")
    assert (emptied.after_quantity, emptied.after_value) == (D(0), D(0))
    assert totals(state) == ((D(0), D(0)), 4)


def test_replay_does_not_add_stock_and_changed_content_conflicts(manual_state):
    state, key = manual_state, uuid4()
    first = move(state, key=key)
    assert move(state, key=key) == first
    assert totals(state) == ((D(2), D(6)), 1)
    with pytest.raises(InventoryConflict):
        move(state, quantity="3", key=key)
    with pytest.raises(InventoryConflict):
        move(state, "OUT", "2", key=key)
    assert totals(state) == ((D(2), D(6)), 1)


def test_insufficient_stock_creates_no_evidence(manual_state):
    state = manual_state
    with pytest.raises(ManualMovementInvalid):
        move(state, "OUT", "1")
    assert totals(state) == ((D(0), D(0)), 0)
    move(state, quantity="1", cost="7")
    with pytest.raises(ManualMovementInvalid):
        move(state, "OUT", "1.001")
    assert totals(state) == ((D(1), D(7)), 1)


def test_competing_exits_cannot_consume_last_unit_twice(manual_state):
    state = manual_state
    move(state, quantity="1", cost="7")
    barrier = Barrier(2)

    def withdraw():
        barrier.wait(timeout=10)
        try:
            move(state, "OUT", "1")
            return "committed"
        except ManualMovementInvalid:
            return "insufficient"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: withdraw(), range(2)))
    assert sorted(results) == ["committed", "insufficient"]
    assert totals(state) == ((D(0), D(0)), 2)


def test_failed_commit_rolls_back_all_and_same_key_can_retry(manual_state):
    state, key = manual_state, uuid4()
    engine = state["service"]._factory.kw["bind"]

    def reject(_connection):
        raise SQLAlchemyError("synthetic-commit-failure")

    event.listen(engine, "commit", reject)
    try:
        with pytest.raises(InventoryUnavailable):
            move(state, key=key)
    finally:
        event.remove(engine, "commit", reject)
    assert totals(state) == ((D(0), D(0)), 0)
    move(state, key=key)
    assert totals(state) == ((D(2), D(6)), 1)


def test_foreign_product_and_replay_after_revocation_are_denied(manual_state):
    state, key = manual_state, uuid4()
    with pytest.raises(AccessDenied):
        move(state, product_id=state["foreign_product"])
    move(state, key=key)
    with Session(state["runtime"]) as session, session.begin():
        session.get(TenantMembership, state["membership"]).role = "read_only"
    with pytest.raises(AccessDenied):
        move(state, key=key)
    assert totals(state) == ((D(2), D(6)), 1)


def test_history_cursor_is_product_scoped_and_evidence_is_immutable(manual_state):
    state = manual_state
    ids = {move(state).id for _ in range(3)}
    first = state["service"].list_manual(
        state["token"],
        product_id=state["product"],
        expected_actor=state["membership"],
        query=CountPageQuery(limit=2),
    )
    assert len(first.items) == 2 and first.next_after == first.items[-1].id
    tail = state["service"].list_manual(
        state["token"],
        product_id=state["product"],
        query=CountPageQuery(limit=2, after=first.next_after),
    )
    assert {r.id for r in first.items + tail.items} == ids
    assert tail.next_after is None
    with pytest.raises(AccessDenied):
        state["service"].list_manual(
            state["token"],
            product_id=state["foreign_product"],
            query=CountPageQuery(after=first.next_after),
        )
    with (
        state["runtime"].connect() as connection,
        pytest.raises(SQLAlchemyError),
        connection.begin(),
    ):
        connection.execute(
            update(ManualStockRecord)
            .where(ManualStockRecord.id == first.items[0].id)
            .values(reason="Tampered")
        )
    with (
        state["owner"].connect() as connection,
        pytest.raises(SQLAlchemyError),
        connection.begin(),
    ):
        connection.execute(
            update(ManualStockRecord)
            .where(ManualStockRecord.id == first.items[0].id)
            .values(declared_unit_cost=D(99))
        )
    assert totals(state) == ((D(6), D(18)), 3)
