"""Atomic, session-authorized counts, including real competing transactions."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, local
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access.membership import AccessDenied
from app.modules.access.models import SessionRecord, TenantMembership
from app.modules.access.reauthentication import RecentAuthenticationRequired
from app.modules.access.sessions import issue_session, revoke_session
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import PhysicalCount
from app.modules.inventory.models import StockBalance, StockCountRecord, StockMovement
from app.modules.inventory.service import (
    InventoryAdministrator,
    InventoryConflict,
    InventoryUnavailable,
)

pytestmark = pytest.mark.integration
D = Decimal


@pytest.fixture
def inventory_state(http_identity, recent_session):
    state = dict(http_identity)
    with Session(state["runtime"]) as session, session.begin():
        state["token"] = issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        ).token
        product = Product(
            tenant_id=state["tenant"], name="Counted product", price=D(10)
        )
        foreign = Product(tenant_id=state["other"], name="Foreign product", price=D(10))
        session.add_all([product, foreign])
        session.flush()
        state["product"], state["foreign_product"] = product.id, foreign.id
    recent_session(state["token"])
    state["service"] = InventoryAdministrator(state["runtime"])
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            for model in (StockMovement, StockCountRecord, StockBalance, Product):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )


def record(state, quantity="2", *, key=None, cost="3", **changes):
    data = PhysicalCount.model_validate(
        dict(
            product_id=state["product"],
            counted_quantity=quantity,
            reason="Physical count",
            declared_unit_cost=cost,
        )
        | changes
    )
    return state["service"].record_count(
        state["token"],
        expected_actor=state["membership"],
        operation_key=key if key is not None else uuid4(),
        data=data,
    )


def snapshot(state):
    with Session(state["runtime"]) as session:
        evidence = tuple(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == state["tenant"])
            )
            for model in (StockCountRecord, StockMovement, StockBalance)
        )
        balance = session.get(StockBalance, (state["tenant"], state["product"]))
        return evidence, (balance.quantity, balance.value) if balance else None


def reconcile(state):
    with Session(state["runtime"]) as session:
        totals = session.execute(
            select(
                func.coalesce(func.sum(StockMovement.quantity_delta), 0),
                func.coalesce(func.sum(StockMovement.value_delta), 0),
            ).where(
                StockMovement.tenant_id == state["tenant"],
                StockMovement.product_id == state["product"],
            )
        ).one()
        balance = session.get(StockBalance, (state["tenant"], state["product"]))
        assert tuple(totals) == (balance.quantity, balance.value)


def test_count_evidence_movement_and_balance_commit_together(inventory_state):
    state = inventory_state
    result = record(state)
    assert (result.before_quantity, result.after_quantity) == (D(0), D(2))
    assert (result.quantity_delta, result.value_delta) == (D(2), D(6))
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))
    assert "tenant_id" not in result.model_dump()
    assert "actor_membership_id" not in result.model_dump()
    with Session(state["runtime"]) as session:
        saved = session.get(StockCountRecord, result.id)
        assert saved.actor_membership_id == state["membership"]
        assert saved.reason == "Physical count"
        assert session.get(Product, state["product"]).price == D(10)
    reconcile(state)


def test_repeated_normalized_request_returns_original_evidence(inventory_state):
    state = inventory_state
    key = uuid4()
    first = record(state, key=key)
    repeated = record(
        state, quantity="2.000", key=key, cost="3.000000", reason=" Physical count "
    )
    assert first == repeated
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))


@pytest.mark.parametrize(
    "changes", [{"quantity": "3"}, {"reason": "Other count"}, {"cost": "4"}]
)
def test_same_key_different_content_conflicts(inventory_state, changes):
    state = inventory_state
    key = uuid4()
    record(state, key=key)
    with pytest.raises(InventoryConflict):
        record(state, key=key, **changes)
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))


def test_count_uses_current_locked_balance_and_retains_noop_evidence(inventory_state):
    state = inventory_state
    record(state)
    unchanged = record(state, cost=None)
    assert unchanged.quantity_delta == 0
    assert snapshot(state) == ((2, 1, 1), (D(2), D(6)))
    decrease = record(state, "1", cost=None)
    assert (decrease.before_quantity, decrease.before_value) == (D(2), D(6))
    assert (decrease.quantity_delta, decrease.value_delta) == (D(-1), D(-3))
    record(state, "0", cost=None)
    assert snapshot(state) == ((4, 3, 1), (D(0), D(0)))
    reconcile(state)


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_current_role_authorizes_neither_new_count_nor_replay(inventory_state, role):
    state = inventory_state
    key = uuid4()
    record(state, key=key)
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    for attempt_key in (key, uuid4()):
        with pytest.raises(AccessDenied):
            record(state, key=attempt_key)
    assert snapshot(state) == before


@pytest.mark.parametrize("mode", ["revoked", "wrong_actor", "stale", "invalid"])
def test_session_authority_rechecked_even_on_replay(inventory_state, mode):
    state = inventory_state
    key = uuid4()
    record(state, key=key)
    before = snapshot(state)
    if mode == "revoked":
        with Session(state["runtime"]) as session, session.begin():
            revoke_session(session, state["token"])
    elif mode == "wrong_actor":
        state["membership"] = state["foreign_membership"]
    elif mode == "invalid":
        state["token"] = SecretStr("invalid")
    else:
        with state["owner"].begin() as connection:
            connection.execute(
                update(SessionRecord)
                .where(SessionRecord.membership_id == state["membership"])
                .values(reauthenticated_at=None, reauthentication_fingerprint=None)
            )
    with pytest.raises((AccessDenied, RecentAuthenticationRequired)):
        record(state, key=key)
    assert snapshot(state) == before


@pytest.mark.parametrize("mode", ["missing", "foreign", "inactive"])
def test_product_reference_cannot_select_other_company(inventory_state, mode):
    state = inventory_state
    product_id = uuid4() if mode == "missing" else state["foreign_product"]
    if mode == "inactive":
        product_id = state["product"]
        with state["owner"].begin() as connection:
            connection.execute(
                update(Product).where(Product.id == product_id).values(is_active=False)
            )
    with pytest.raises(AccessDenied, match="^Access denied$"):
        record(state, product_id=product_id)
    assert snapshot(state) == ((0, 0, 0), None)


def test_replay_preserves_evidence_after_product_deactivation(inventory_state):
    state = inventory_state
    key = uuid4()
    first = record(state, key=key)
    with state["owner"].begin() as connection:
        connection.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(is_active=False)
        )
    assert record(state, key=key) == first
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))


@pytest.mark.parametrize(
    "statement_prefix",
    [
        "INSERT INTO stock_counts",
        "INSERT INTO stock_movements",
        "INSERT INTO stock_balances",
        "UPDATE stock_balances",
    ],
)
def test_real_write_failure_rolls_back_everything(inventory_state, statement_prefix):
    state = inventory_state
    if statement_prefix.startswith("UPDATE"):
        record(state)
    before = snapshot(state)
    hits = 0

    def fail(connection, cursor, statement, parameters, context, executemany):
        nonlocal hits
        if statement.startswith(statement_prefix):
            hits += 1
            return "INSERT INTO stock_counts (nonexistent_column) VALUES (1)", {}
        return statement, parameters

    event.listen(state["runtime"], "before_cursor_execute", fail, retval=True)
    try:
        with pytest.raises(InventoryUnavailable, match="^Inventory unavailable$"):
            record(
                state, "1", cost=None if statement_prefix.startswith("UPDATE") else "3"
            )
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    assert hits == 1
    assert snapshot(state) == before


def test_commit_failure_returns_no_result_and_rolls_back(inventory_state):
    state = inventory_state

    def fail(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(InventoryUnavailable):
            record(state)
    finally:
        event.remove(Session, "before_commit", fail)
    assert snapshot(state) == ((0, 0, 0), None)


def test_lost_acknowledgement_after_commit_replays_without_second_movement(
    inventory_state,
):
    state = inventory_state
    key = uuid4()

    def lose_acknowledgement(session):
        # The database has committed; the application cannot report success.
        raise SQLAlchemyError("Synthetic loss after commit")

    event.listen(Session, "after_commit", lose_acknowledgement)
    try:
        with pytest.raises(InventoryUnavailable):
            record(state, key=key)
    finally:
        event.remove(Session, "after_commit", lose_acknowledgement)
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))
    recovered = record(state, key=key)
    assert recovered.after_quantity == 2
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))
    reconcile(state)


def test_operation_key_is_scoped_to_authorized_company(inventory_state, recent_session):
    state = inventory_state
    key = uuid4()
    first = record(state, key=key)
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["foreign_membership"])
            .values(role="admin")
        )
    with Session(state["runtime"]) as session, session.begin():
        token = issue_session(
            session, user_id=state["foreign"], tenant_id=state["other"]
        ).token
    recent_session(token)
    other_state = state | dict(
        token=token,
        membership=state["foreign_membership"],
        product=state["foreign_product"],
    )
    second = record(other_state, "4", key=key)
    assert first.id != second.id
    assert (first.after_quantity, second.after_quantity) == (D(2), D(4))
    assert record(state, key=key) == first
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))


def test_final_authorization_failure_rolls_back_count_and_stock(
    inventory_state, monkeypatch
):
    state = inventory_state
    original = state["service"]._authorize
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

    monkeypatch.setattr(state["service"], "_authorize", authorize)
    with pytest.raises(RecentAuthenticationRequired):
        record(state)
    assert calls == 2
    assert snapshot(state) == ((0, 0, 0), None)


def test_bypassed_input_validation_cannot_write(inventory_state):
    state = inventory_state
    invalid = PhysicalCount(
        product_id=state["product"], counted_quantity="0", reason="Count"
    ).model_copy(update={"counted_quantity": D(-1)})
    with pytest.raises(ValidationError):
        state["service"].record_count(
            state["token"],
            expected_actor=state["membership"],
            operation_key=uuid4(),
            data=invalid,
        )
    assert snapshot(state) == ((0, 0, 0), None)


def competing(state, work):
    barrier = Barrier(2, timeout=10)
    first_lock = local()

    def synchronize(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.startswith("SELECT tenants.")
            and "FOR UPDATE" in statement
            and not getattr(first_lock, "seen", False)
        ):
            first_lock.seen = True
            # Both transactions have begun and resolved the token hint before
            # either can acquire the common tenant lock. Final authorization
            # runs in the same transaction and must not wait at this barrier.
            barrier.wait()

    def attempt(index):
        return work(index)

    event.listen(state["runtime"], "before_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(attempt, range(2)))
    finally:
        event.remove(state["runtime"], "before_cursor_execute", synchronize)


def test_concurrent_same_key_creates_one_count_and_one_first_balance(inventory_state):
    state = inventory_state
    key = uuid4()
    results = competing(state, lambda _: record(state, key=key))
    assert results[0] == results[1]
    assert snapshot(state) == ((1, 1, 1), (D(2), D(6)))
    reconcile(state)


def test_concurrent_conflicting_key_has_one_winner(inventory_state):
    state = inventory_state
    key = uuid4()

    def work(index):
        try:
            return record(state, str(index + 1), key=key)
        except InventoryConflict:
            return None

    results = competing(state, work)
    assert sum(result is not None for result in results) == 1
    assert snapshot(state)[0] == (1, 1, 1)
    reconcile(state)


def test_concurrent_distinct_first_counts_keep_one_balance(inventory_state):
    state = inventory_state
    results = competing(state, lambda _: record(state, "0", cost=None))
    assert results[0].id != results[1].id
    assert snapshot(state) == ((2, 0, 1), (D(0), D(0)))
    reconcile(state)


def test_concurrent_distinct_counts_use_last_committed_balance(inventory_state):
    state = inventory_state
    record(state)
    results = competing(state, lambda index: record(state, str(index + 3), cost=None))
    assert sorted(result.after_quantity for result in results) == [D(3), D(4)]
    first = next(result for result in results if result.before_quantity == 2)
    second = next(result for result in results if result.id != first.id)
    assert second.before_quantity == first.after_quantity
    assert second.before_value == first.after_value
    assert snapshot(state) == ((3, 3, 1), (second.after_quantity, second.after_value))
    reconcile(state)
