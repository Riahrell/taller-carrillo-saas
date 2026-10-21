"""Unified evidence with tenant-bound typed cursors; no stock writes on reads."""

from decimal import Decimal
from uuid import uuid4

import pytest
from purchase_state import purchase_state
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership
from app.modules.access.sessions import revoke_session
from app.modules.inventory.inputs import (
    HistoryPageQuery,
    ManualStockInput,
    PhysicalCount,
)
from app.modules.inventory.models import (
    ManualStockRecord,
    StockCountRecord,
    StockMovement,
)
from app.modules.inventory.service import InventoryAdministrator
from app.modules.purchases.inputs import PurchaseCreation
from app.modules.purchases.receipts import PurchaseReceiver

pytestmark = pytest.mark.integration


@pytest.fixture
def history_state(http_identity, recent_session):
    with purchase_state(http_identity, recent_session) as state:
        state["inventory"] = InventoryAdministrator(state["runtime"])
        try:
            yield state
        finally:
            with state["owner"].begin() as connection:
                for model in (StockMovement, ManualStockRecord):
                    connection.execute(
                        delete(model).where(model.tenant_id == state["tenant"])
                    )


def count(state, quantity, cost=None, product=None):
    return state["inventory"].record_count(
        state["token"],
        expected_actor=state["membership"],
        operation_key=uuid4(),
        data=PhysicalCount(
            product_id=product or state["product"],
            counted_quantity=quantity,
            declared_unit_cost=cost,
            reason="Count",
        ),
    )


def manual(state, direction):
    return state["inventory"].record_manual(
        state["token"],
        expected_actor=state["membership"],
        operation_key=uuid4(),
        data=ManualStockInput(
            product_id=state["product"],
            direction=direction,
            quantity="1",
            unit_cost="4" if direction == "IN" else None,
            reason="Manual",
        ),
    )


def history(state, **kwargs):
    return state["inventory"].list_history(
        state["token"],
        expected_actor=state["membership"],
        product_id=state["product"],
        query=HistoryPageQuery(**kwargs),
    )


def test_mixed_sources_noop_and_receipt_are_listed_once_without_changing_balance(
    history_state,
):
    state = history_state
    first = count(state, "2", "3")
    noop = count(state, "2")
    incoming, outgoing = manual(state, "IN"), manual(state, "OUT")
    purchase = state["service"].record_purchase(
        state["token"],
        expected_actor=state["membership"],
        operation_key=uuid4(),
        data=PurchaseCreation(
            supplier_id=state["supplier"],
            lines=[dict(product_id=state["product"], quantity="3", unit_cost="2")],
        ),
    )
    PurchaseReceiver(state["runtime"]).receive(
        state["token"],
        expected_actor=state["membership"],
        purchase_id=purchase.id,
        operation_key=uuid4(),
    )
    before = state["inventory"].current_balance(
        state["token"], product_id=state["product"]
    )
    result = history(state)
    assert [row.source for row in result.items] == [
        "RECEIPT",
        "MANUAL",
        "MANUAL",
        "COUNT",
        "COUNT",
    ]
    assert [row.id for row in result.items[1:]] == [
        outgoing.id,
        incoming.id,
        noop.id,
        first.id,
    ]
    receipt = result.items[0]
    assert receipt.source_document_id == purchase.id
    assert receipt.reason is None and receipt.direction is None
    assert all(row.recorded_by and "@" in row.recorded_by for row in result.items)
    assert result.items[-2].quantity_delta == Decimal(0)
    assert result.next_after is None
    assert (
        state["inventory"].current_balance(state["token"], product_id=state["product"])
        == before
    )
    payload = result.model_dump(mode="json")
    assert "actor_membership_id" not in payload
    assert payload["items"][0]["quantity_delta"] == "3.000"
    assert "operation_key" not in payload["items"][0]


def test_same_time_typed_pagination_keeps_noops_and_advances_with_new_rows(
    history_state,
):
    state = history_state
    count(state, "1", "2")
    a, b = count(state, "1"), count(state, "1")
    # Tie timestamps using owner credentials only in the disposable test DB.
    with state["owner"].begin() as connection:
        stamp = connection.scalar(
            select(StockCountRecord.created_at).where(StockCountRecord.id == a.id)
        )
        connection.execute(
            update(StockCountRecord)
            .where(StockCountRecord.tenant_id == state["tenant"])
            .values(created_at=stamp)
        )
    page = history(state, limit=1)
    cursor = page.next_after
    assert cursor and cursor.startswith("COUNT:")
    added = count(state, "1")
    seen = [page.items[0].id]
    while cursor:
        page = history(state, limit=1, after=cursor)
        seen.extend(row.id for row in page.items)
        cursor = page.next_after
    assert len(seen) == len(set(seen)) == 3
    assert {a.id, b.id}.issubset(seen) and added.id not in seen


def test_cursors_cannot_cross_product_or_source_and_foreign_products_are_denied(
    history_state,
):
    state = history_state
    other = count(state, "0", product=state["second_product"])
    for cursor in (f"COUNT:{other.id}", f"COUNT:{uuid4()}", f"MANUAL:{other.id}"):
        with pytest.raises(AccessDenied):
            history(state, after=cursor)
    with pytest.raises(AccessDenied):
        state["inventory"].list_history(
            state["token"],
            product_id=state["foreign_product"],
            query=HistoryPageQuery(),
        )


@pytest.mark.parametrize("change", ["role", "revoked", "actor"])
def test_history_rechecks_current_authority(history_state, change):
    state = history_state
    count(state, "0")
    expected = state["membership"]
    with Session(state["runtime"]) as session, session.begin():
        if change == "role":
            session.execute(
                update(TenantMembership)
                .where(TenantMembership.id == expected)
                .values(role="read_only")
            )
        elif change == "revoked":
            revoke_session(session, state["token"])
        else:
            expected = state["foreign_membership"]
    with pytest.raises(AccessDenied):
        state["inventory"].list_history(
            state["token"],
            expected_actor=expected,
            product_id=state["product"],
            query=HistoryPageQuery(),
        )
