"""Explicit thresholds never mutate stock; concurrent configuration uses versions."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from test_inventory_service import inventory_state as inventory_state
from test_inventory_service import record

from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import StockMinimumChange
from app.modules.inventory.models import StockMinimumRecord
from app.modules.inventory.service import InventoryStale

pytestmark = pytest.mark.integration


@pytest.fixture
def minimum_state(inventory_state):
    try:
        yield inventory_state
    finally:
        with inventory_state["owner"].begin() as connection:
            connection.execute(
                delete(StockMinimumRecord).where(
                    StockMinimumRecord.tenant_id == inventory_state["tenant"]
                )
            )


def read(state, product=None):
    return state["service"].minimum(
        state["token"],
        product_id=product or state["product"],
        expected_actor=state["membership"],
    )


def save(state, minimum, etag):
    return state["service"].minimum(
        state["token"],
        product_id=state["product"],
        expected_actor=state["membership"],
        data=StockMinimumChange(minimum=minimum),
        expected_etag=etag,
    )


def test_unconfigured_zero_boundary_and_clear_preserve_stock(minimum_state):
    state = minimum_state
    initial = read(state)
    assert initial.minimum is None and not initial.is_low and initial.revision == 0
    zero = save(state, "0", initial.etag)
    assert zero.is_low and zero.minimum == Decimal(0)
    record(state, quantity="2", cost="3")
    balance = state["service"].current_balance(
        state["token"], product_id=state["product"]
    )
    assert not read(state).is_low
    equal = save(state, "2", zero.etag)
    assert equal.is_low
    cleared = save(state, None, equal.etag)
    assert not cleared.is_low and cleared.minimum is None
    assert (
        state["service"].current_balance(state["token"], product_id=state["product"])
        == balance
    )
    with Session(state["runtime"]) as session:
        rows = session.scalars(
            select(StockMinimumRecord)
            .where(StockMinimumRecord.product_id == state["product"])
            .order_by(StockMinimumRecord.revision)
        ).all()
        assert [row.revision for row in rows] == [1, 2, 3]
        assert rows[-1].before_minimum == Decimal(2) and rows[-1].minimum is None


def test_noop_and_stale_config_do_not_append_evidence(minimum_state):
    state = minimum_state
    initial = read(state)
    assert save(state, None, initial.etag).revision == 0
    configured = save(state, "3.001", initial.etag)
    assert save(state, "3.001", configured.etag).etag == configured.etag
    with pytest.raises(InventoryStale):
        save(state, "5", initial.etag)
    assert read(state).minimum == Decimal("3.001")


def test_inactive_keeps_minimum_and_suppresses_notice(minimum_state):
    state = minimum_state
    configured = save(state, "4", read(state).etag)
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(is_active=False)
        )
    inactive = read(state)
    assert (
        inactive.minimum == configured.minimum
        and not inactive.is_active
        and not inactive.is_low
    )


def test_foreign_context_and_changed_role_are_denied(minimum_state):
    state = minimum_state
    for target in (state["foreign_product"], uuid4()):
        with pytest.raises(AccessDenied):
            read(state, target)
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role="read_only")
        )
    with pytest.raises(AccessDenied):
        read(state)


@pytest.mark.parametrize("minimum", ["-1", "0.0001", "NaN", "Infinity", True, 0.5])
def test_invalid_threshold_is_rejected(minimum):
    with pytest.raises(ValidationError):
        StockMinimumChange(minimum=minimum)


def test_competing_configuration_has_one_winner(minimum_state):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    state = minimum_state
    etag = read(state).etag
    barrier = Barrier(2)

    def configure(value):
        barrier.wait(timeout=10)
        try:
            return save(state, value, etag)
        except InventoryStale:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(configure, ["1", "2"]))
    assert sum(result is not None for result in results) == 1
    assert read(state).revision == 1


def test_failed_evidence_write_leaves_no_configuration(minimum_state):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.inventory.service import InventoryUnavailable

    state = minimum_state
    initial = read(state)

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO stock_minimum_records"):
            raise SQLAlchemyError("injected")

    event.listen(state["runtime"], "before_cursor_execute", fail)
    try:
        with pytest.raises(InventoryUnavailable):
            save(state, "3", initial.etag)
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    assert read(state).etag == initial.etag


def test_configuration_evidence_cannot_be_erased_or_downgraded(minimum_state):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    state = minimum_state
    save(state, "1", read(state).etag)
    for statement in (
        "UPDATE stock_minimum_records SET minimum=0",
        "DELETE FROM stock_minimum_records",
        "TRUNCATE stock_minimum_records",
    ):
        with (
            state["runtime"].begin() as connection,
            pytest.raises(SQLAlchemyError),
            connection.begin_nested(),
        ):
            connection.execute(text(statement))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with state["owner"].begin() as connection:
        config.attributes["connection"] = connection
        with (
            pytest.raises(RuntimeError, match="Stock minimum evidence"),
            connection.begin_nested(),
        ):
            command.downgrade(config, "0040_product_changes")
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0045_inventory_read_grants"
        )
