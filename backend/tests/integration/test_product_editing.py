"""Versioned product changes: atomic evidence, tenant authority and stale edits."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from test_catalog_service import catalog_state as catalog_state
from test_catalog_service import create

from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership
from app.modules.catalog.editing import CatalogStale
from app.modules.catalog.inputs import ProductChange
from app.modules.catalog.models import Category, Product, ProductChangeRecord
from app.modules.catalog.service import CatalogUnavailable
from app.modules.inventory.inputs import PhysicalCount
from app.modules.inventory.models import StockBalance, StockCountRecord, StockMovement
from app.modules.inventory.service import InventoryAdministrator

pytestmark = pytest.mark.integration


@pytest.fixture
def editing_state(catalog_state):
    try:
        yield catalog_state
    finally:
        with catalog_state["owner"].begin() as connection:
            for model in (StockMovement, StockCountRecord, StockBalance):
                connection.execute(
                    delete(model).where(model.tenant_id == catalog_state["tenant"])
                )


def change(state, original, *, version=None, **values):
    return state["service"].update_product(
        state["token"],
        product_id=original.id,
        expected_actor=state["membership"],
        expected_etag=version or original.etag,
        data=ProductChange(
            **{
                field: getattr(original, field)
                for field in ("name", "sku", "category_id", "price", "is_active")
            }
            | values
        ),
    )


def test_edit_and_state_preserve_stock_and_audit_before_after(editing_state):
    state = editing_state
    original = create(state, sku="OLD")
    inventory = InventoryAdministrator(state["runtime"])
    inventory.record_count(
        state["token"],
        expected_actor=state["membership"],
        operation_key=uuid4(),
        data=PhysicalCount(
            product_id=original.id,
            counted_quantity="5",
            declared_unit_cost="3",
            reason="Initial",
        ),
    )
    stock_before = inventory.current_balance(state["token"], product_id=original.id)
    changed = change(
        state, original, name="Nuevo", price="99.99", sku="NEW", is_active=False
    )
    assert changed.revision == 1 and changed.etag != original.etag
    assert not changed.is_active
    assert "revision" not in changed.model_dump(mode="json")
    with Session(state["runtime"]) as session:
        row = session.scalar(
            select(ProductChangeRecord).where(
                ProductChangeRecord.product_id == original.id
            )
        )
        assert (row.before_name, row.after_name) == (original.name, "Nuevo")
        assert (row.before_sku, row.after_sku) == ("OLD", "NEW")
        assert row.actor_membership_id == state["membership"]
        assert row.before_active and not row.after_active
        assert (
            session.scalar(
                select(func.count())
                .select_from(StockBalance)
                .where(StockBalance.product_id == original.id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.product_id == original.id)
            )
            == 1
        )
    assert (
        inventory.current_balance(state["token"], product_id=original.id)
        == stock_before
    )
    active = change(state, changed, is_active=True)
    assert active.is_active and active.revision == 2


def test_noop_does_not_create_evidence_but_old_version_is_rejected(editing_state):
    state = editing_state
    original = create(state)
    assert change(state, original).etag == original.etag
    changed = change(state, original, name="Renamed")
    with pytest.raises(CatalogStale):
        change(state, original, name="Overwrite")
    # A -> B -> A still invalidates the old version.
    restored = change(state, changed, name=original.name)
    assert restored.etag != original.etag
    with pytest.raises(CatalogStale):
        change(state, original)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProductChangeRecord)
                .where(ProductChangeRecord.product_id == original.id)
            )
            == 2
        )


def test_sku_conflict_and_foreign_category_rollback(editing_state):
    from app.modules.catalog.service import CatalogConflict

    state = editing_state
    original = create(state, sku="A")
    create(state, sku="B")
    with pytest.raises(CatalogConflict):
        change(state, original, sku="B", name="Bad")
    with Session(state["runtime"]) as session, session.begin():
        foreign = Category(tenant_id=state["other"], name="Foreign")
        session.add(foreign)
        session.flush()
        foreign_id = foreign.id
    with pytest.raises(AccessDenied):
        change(state, original, category_id=foreign_id)
    assert (
        state["service"].current_product(state["token"], product_id=original.id).etag
        == original.etag
    )


def test_existing_inactive_category_retained_but_cannot_be_newly_assigned(
    editing_state,
):
    state = editing_state
    with Session(state["runtime"]) as session, session.begin():
        category = Category(tenant_id=state["tenant"], name="Old")
        session.add(category)
        session.flush()
        category_id = category.id
    original = create(state, category_id=category_id)
    other = create(state)
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(Category).where(Category.id == category_id).values(is_active=False)
        )
    assert change(state, original, name="Renamed").category_id == category_id
    with pytest.raises(AccessDenied):
        change(state, other, category_id=category_id)


def test_foreign_product_and_changed_role_are_denied(editing_state):
    state = editing_state
    original = create(state)
    with Session(state["runtime"]) as session, session.begin():
        foreign = Product(tenant_id=state["other"], name="Foreign", price="1")
        session.add(foreign)
        session.flush()
        foreign_view = state["service"]._product_view(foreign)
    with pytest.raises(AccessDenied):
        change(state, foreign_view)
    with Session(state["runtime"]) as session, session.begin():
        session.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role="read_only")
        )
    with pytest.raises(AccessDenied):
        change(state, original, name="Denied")


def test_failed_audit_insert_rolls_back_product(editing_state):
    state = editing_state
    original = create(state)

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO product_changes"):
            raise SQLAlchemyError("injected")

    event.listen(state["runtime"], "before_cursor_execute", fail)
    try:
        with pytest.raises(CatalogUnavailable):
            change(state, original, name="Must rollback")
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail)
    assert (
        state["service"].current_product(state["token"], product_id=original.id).etag
        == original.etag
    )


def test_competing_edit_has_one_winner_and_one_stale_response(editing_state):
    state = editing_state
    original = create(state)
    barrier = Barrier(2)

    def run(name):
        barrier.wait(timeout=10)
        try:
            return change(state, original, name=name)
        except CatalogStale:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["A", "B"]))
    assert sum(value is not None for value in results) == 1
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProductChangeRecord)
                .where(ProductChangeRecord.product_id == original.id)
            )
            == 1
        )


def test_evidence_is_append_only_and_revision_constraints_apply(editing_state):
    state = editing_state
    change(state, create(state), name="Changed")
    for operation in (
        "UPDATE product_changes SET after_name='tampered'",
        "DELETE FROM product_changes",
        "TRUNCATE product_changes",
    ):
        with (
            state["runtime"].begin() as connection,
            pytest.raises(SQLAlchemyError),
            connection.begin_nested(),
        ):
            connection.execute(text(operation))
    with (
        state["owner"].begin() as connection,
        pytest.raises(IntegrityError),
        connection.begin_nested(),
    ):
        connection.execute(
            text("UPDATE product_changes SET after_revision=before_revision")
        )
