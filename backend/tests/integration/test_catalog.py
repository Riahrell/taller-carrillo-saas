"""Catalog constraints exercised with PostgreSQL and the restricted runtime role."""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.access.models import Tenant
from app.modules.catalog.models import Category, Product

pytestmark = pytest.mark.integration


@pytest.fixture
def catalog(db_session):
    first, second = Tenant(name="Catalog A"), Tenant(name="Catalog B")
    db_session.add_all([first, second])
    db_session.flush()
    category = Category(tenant_id=first.id, name="Repuestos")
    db_session.add(category)
    db_session.flush()
    return first, second, category


def product(tenant, **changes):
    return Product(
        tenant_id=tenant.id, name="Repuesto", price=Decimal("12.34"), **changes
    )


def test_catalog_roundtrip_and_inactive_history(db_session, catalog):
    tenant, _, category = catalog
    item = product(tenant, category_id=category.id, sku="SKU-1")
    db_session.add(item)
    db_session.flush()
    assert item.is_active is True
    assert item.created_at.utcoffset().total_seconds() == 0
    db_session.execute(
        update(Product).where(Product.id == item.id).values(is_active=False)
    )
    db_session.flush()
    db_session.expire_all()
    saved = db_session.get(Product, item.id)
    assert saved.is_active is False
    assert saved.price == Decimal("12.34")
    assert saved.category_id == category.id


def test_sku_unique_per_tenant_including_inactive_products(db_session, catalog):
    first, second, _ = catalog
    db_session.add_all(
        [product(first, sku="SAME", is_active=False), product(second, sku="SAME")]
    )
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(product(first, sku="SAME"))
        db_session.flush()


def test_multiple_products_without_sku_and_category_are_allowed(db_session, catalog):
    tenant, _, _ = catalog
    db_session.add_all([product(tenant), product(tenant)])
    db_session.flush()


def test_cross_tenant_category_is_rejected_even_by_sql(db_session, catalog):
    _, second, category = catalog
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(product(second, category_id=category.id))
        db_session.flush()


@pytest.mark.parametrize(
    "price", ["-0.01", "NaN", "Infinity", "-Infinity", "1000000000000000000"]
)
def test_invalid_price_cannot_enter_database(db_session, catalog, price):
    tenant, _, _ = catalog
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO products (id, tenant_id, name, price) "
                "VALUES (:id, :tenant, 'Synthetic', CAST(:price AS numeric))"
            ),
            {"id": uuid4(), "tenant": tenant.id, "price": price},
        )


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("products", "name", "   "),
        ("categories", "name", "   "),
        ("products", "sku", ""),
        ("products", "sku", " spaced "),
    ],
)
def test_blank_names_and_noncanonical_sku_fail(
    db_session, catalog, table, field, value
):
    tenant, _, category = catalog
    item = product(tenant)
    db_session.add(item)
    db_session.flush()
    # Names are from the fixed parameter list, never request-controlled SQL.
    target = item.id if table == "products" else category.id
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            text(f"UPDATE {table} SET {field} = :value WHERE id = :id"),
            {"value": value, "id": target},
        )


@pytest.mark.parametrize("model", [Product, Category])
def test_runtime_cannot_delete_catalog(db_session, catalog, model):
    tenant, _, _ = catalog
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(delete(model).where(model.tenant_id == tenant.id))


def test_tenant_delete_preserves_catalog_references(db_session, catalog):
    tenant, _, _ = catalog
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(delete(Tenant).where(Tenant.id == tenant.id))


def test_downgrade_refuses_to_erase_catalog(databases):
    _, owner, _ = databases
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    tenant_id, category_id = uuid4(), uuid4()
    with owner.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Guard')"),
            {"id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO categories (id, tenant_id, name) "
                "VALUES (:id, :tenant, 'Guard')"
            ),
            {"id": category_id, "tenant": tenant_id},
        )
    try:
        with (
            pytest.raises(RuntimeError, match="refusing destructive downgrade"),
            owner.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0029_mfa_rotation")
        with owner.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == (
                "0044_employee_registration"
            )
            assert (
                connection.execute(
                    select(Category.id).where(Category.id == category_id)
                ).scalar_one()
                == category_id
            )
    finally:
        with owner.begin() as connection:
            connection.execute(delete(Category).where(Category.id == category_id))
            connection.execute(delete(Tenant).where(Tenant.id == tenant_id))
