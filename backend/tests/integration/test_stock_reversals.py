"""Exact inverses, current authority, atomic ledger/balance and replay races."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_inventory_service import inventory_state as inventory_state
from test_inventory_service import record

from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import ManualStockInput, StockReversalInput
from app.modules.inventory.models import (
    ManualStockRecord,
    StockBalance,
    StockMovement,
    StockReversalRecord,
)
from app.modules.inventory.service import (
    InventoryConflict,
    InventoryInvalid,
    InventoryUnavailable,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def reversing(inventory_state):
    try:
        yield inventory_state
    finally:
        with inventory_state["owner"].begin() as connection:
            tenant = inventory_state["tenant"]
            connection.execute(
                delete(StockMovement).where(
                    StockMovement.tenant_id == tenant,
                    StockMovement.reversal_id.is_not(None),
                )
            )
            connection.execute(
                delete(StockReversalRecord).where(
                    StockReversalRecord.tenant_id == tenant
                )
            )
            connection.execute(
                delete(StockMovement).where(StockMovement.tenant_id == tenant)
            )
            connection.execute(
                delete(ManualStockRecord).where(ManualStockRecord.tenant_id == tenant)
            )


def manual(state, direction, quantity="1", cost=None):
    return state["service"].record_manual(
        state["token"],
        expected_actor=state["membership"],
        operation_key=uuid4(),
        data=ManualStockInput(
            product_id=state["product"],
            direction=direction,
            quantity=quantity,
            unit_cost=cost,
            reason="Manual",
        ),
    )


def reverse(
    state, original, *, source="COUNT", key=None, reason="Correct mistaken record"
):
    return state["service"].reverse(
        state["token"],
        expected_actor=state["membership"],
        operation_key=key or uuid4(),
        data=StockReversalInput(
            product_id=state["product"],
            source=source,
            original_id=original.id,
            reason=reason,
        ),
    )


def test_count_reversal_restores_original_balance_and_keeps_evidence(reversing):
    state = reversing
    original = record(state, quantity="2", cost="3")
    reversed = reverse(state, original)
    assert (reversed.before_quantity, reversed.after_quantity) == (
        Decimal(2),
        Decimal(0),
    )
    assert (
        reversed.quantity_delta == -original.quantity_delta
        and reversed.value_delta == -original.value_delta
    )
    balance = state["service"].current_balance(
        state["token"], product_id=state["product"]
    )
    assert balance.quantity == 0 and balance.value == 0
    with Session(state["runtime"]) as session:
        movements = session.scalars(
            select(StockMovement).where(StockMovement.product_id == state["product"])
        ).all()
        assert len(movements) == 2
        assert sum(row.quantity_delta for row in movements) == balance.quantity
        assert sum(row.value_delta for row in movements) == balance.value


@pytest.mark.parametrize("direction", ["IN", "OUT"])
def test_manual_reversal_uses_original_value_not_a_new_cost(reversing, direction):
    state = reversing
    record(state, quantity="3", cost="1.123456")
    original = manual(
        state,
        direction,
        quantity="1.001",
        cost="2.765432" if direction == "IN" else None,
    )
    reversed = reverse(state, original, source="MANUAL")
    assert reversed.after_quantity == original.before_quantity
    assert reversed.after_value == original.before_value
    assert reversed.value_delta == -original.value_delta


def test_same_key_replays_even_after_deactivation_but_different_reason_conflicts(
    reversing,
):
    state = reversing
    original = record(state)
    key = uuid4()
    result = reverse(state, original, key=key)
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(is_active=False)
        )
    assert reverse(state, original, key=key) == result
    with pytest.raises(InventoryConflict):
        reverse(state, original, key=key, reason="Different request")


def test_later_movements_noop_and_already_reversed_fail_without_changes(reversing):
    state = reversing
    original = record(state)
    later = manual(state, "IN", cost="4")
    with pytest.raises(InventoryInvalid):
        reverse(state, original)
    reverse(state, later, source="MANUAL")
    with pytest.raises(InventoryInvalid):
        reverse(state, later, source="MANUAL")
    with pytest.raises(InventoryInvalid):
        reverse(state, original)
    noop = record(state, quantity="2", cost=None)
    with pytest.raises(InventoryInvalid):
        reverse(state, noop)


def test_current_role_and_foreign_origin_cannot_reverse(reversing):
    state = reversing
    original = record(state)
    with pytest.raises(AccessDenied):
        reverse(state, type("Origin", (), {"id": uuid4()})())
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role="read_only")
        )
    with pytest.raises(AccessDenied):
        reverse(state, original)


@pytest.mark.parametrize(
    "fail_at",
    [
        "INSERT INTO stock_reversals",
        "INSERT INTO stock_movements",
        "UPDATE stock_balances",
    ],
)
def test_failures_roll_back_all_effects(reversing, fail_at):
    state = reversing
    original = record(state)

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith(fail_at):
            raise SQLAlchemyError("injected")

    event.listen(state["runtime"], "before_cursor_execute", fail)
    try:
        with pytest.raises(InventoryUnavailable):
            reverse(state, original)
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StockReversalRecord)
                .where(StockReversalRecord.product_id == state["product"])
            )
            == 0
        )
        assert session.get(
            StockBalance, (state["tenant"], state["product"])
        ).quantity == Decimal(2)


@pytest.mark.parametrize("same_key", [True, False])
def test_racing_reversals_never_double_compensate(reversing, same_key):
    state = reversing
    original = record(state)
    barrier = Barrier(2)
    first = uuid4()

    def run(index):
        barrier.wait(timeout=10)
        try:
            return reverse(
                state, original, key=first if same_key or index == 0 else uuid4()
            )
        except InventoryInvalid:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [0, 1]))
    if same_key:
        assert results[0].id == results[1].id
    else:
        assert sum(row is not None for row in results) == 1
    assert (
        state["service"]
        .current_balance(state["token"], product_id=state["product"])
        .quantity
        == 0
    )


def test_reversal_evidence_and_original_are_retained(reversing):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    state = reversing
    reverse(state, record(state))
    for statement in (
        "UPDATE stock_reversals SET reason='tampered'",
        "DELETE FROM stock_reversals",
        "TRUNCATE stock_reversals",
    ):
        with (
            state["runtime"].begin() as connection,
            pytest.raises(SQLAlchemyError),
            connection.begin_nested(),
        ):
            connection.execute(text(statement))
    with (
        state["owner"].begin() as connection,
        pytest.raises(IntegrityError),
        connection.begin_nested(),
    ):
        connection.execute(
            text("UPDATE stock_reversals SET quantity_delta=quantity_delta+1")
        )
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with state["owner"].begin() as connection:
        config.attributes["connection"] = connection
        with (
            pytest.raises(RuntimeError, match="Stock reversal evidence"),
            connection.begin_nested(),
        ):
            command.downgrade(config, "0041_stock_minimum")
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0044_employee_registration"
        )
