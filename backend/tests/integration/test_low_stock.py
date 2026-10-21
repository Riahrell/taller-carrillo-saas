"""Latest explicit minimum, tenant isolation and bounded pagination."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from test_inventory_service import inventory_state as inventory_state
from test_inventory_service import record
from test_stock_minimum import minimum_state as minimum_state
from test_stock_minimum import read, save

from app.modules.access.membership import AccessDenied
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import CountPageQuery
from app.modules.inventory.models import StockMinimumRecord

pytestmark = pytest.mark.integration


@pytest.fixture
def low_state(minimum_state):
    try:
        yield minimum_state
    finally:
        with minimum_state["owner"].begin() as connection:
            connection.execute(
                delete(StockMinimumRecord).where(
                    StockMinimumRecord.tenant_id.in_(
                        [minimum_state["tenant"], minimum_state["other"]]
                    )
                )
            )


def listing(state, **query):
    return state["service"].low_stock(
        state["token"],
        query=CountPageQuery(**query),
        expected_actor=state["membership"],
    )


def test_missing_balance_zero_latest_minimum_and_inactive(low_state):
    state = low_state
    assert listing(state).items == ()
    first = save(state, "0", read(state).etag)
    assert listing(state).items[0].quantity == Decimal(0)
    record(state, quantity="2", cost="3")
    assert listing(state).items == ()
    second = save(state, "2", first.etag)
    assert listing(state).items[0].minimum == Decimal(2)
    save(state, None, second.etag)
    assert listing(state).items == ()
    save(state, "3", read(state).etag)
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(Product)
            .where(Product.id == state["product"])
            .values(is_active=False)
        )
    assert listing(state).items == ()


def test_pages_exclude_foreign_and_allow_cursor_no_longer_low(low_state):
    state = low_state
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            StockMinimumRecord(
                tenant_id=state["other"],
                product_id=state["foreign_product"],
                actor_membership_id=state["foreign_membership"],
                revision=1,
                minimum="0",
            )
        )
        for index in range(27):
            product = Product(tenant_id=state["tenant"], name=f"Low {index}", price="1")
            session.add(product)
            session.flush()
            session.add(
                StockMinimumRecord(
                    tenant_id=state["tenant"],
                    product_id=product.id,
                    actor_membership_id=state["membership"],
                    revision=1,
                    minimum="0",
                )
            )
    page = listing(state, limit=25)
    assert len(page.items) == 25 and page.next_after == page.items[-1].id
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(Product).where(Product.id == page.next_after).values(is_active=False)
        )
    last = listing(state, after=page.next_after)
    assert len(last.items) == 2 and last.next_after is None
    assert len({row.id for row in page.items + last.items}) == 27
    assert all(row.id != state["foreign_product"] for row in page.items + last.items)
    for cursor in (state["foreign_product"], uuid4()):
        with pytest.raises(AccessDenied):
            listing(state, after=cursor)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(select(Product.id).where(Product.id == state["product"]))
            is not None
        )
