"""Real PostgreSQL constraints and runtime privileges for the stock ledger."""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.access.models import Tenant, TenantMembership, User
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockBalance, StockCountRecord, StockMovement

pytestmark = pytest.mark.integration
D = Decimal


@pytest.fixture
def stock(db_session):
    first, second = Tenant(name="Stock A"), Tenant(name="Stock B")
    user = User(email=f"stock-{uuid4().hex}@example.com", password_hash="synthetic")
    db_session.add_all([first, second, user])
    db_session.flush()
    member = TenantMembership(tenant_id=first.id, user_id=user.id, role="admin")
    product = Product(tenant_id=first.id, name="Stock product", price=D(1))
    db_session.add_all([member, product])
    db_session.flush()
    return first, second, member, product


def count(stock, **changes):
    first, _, member, product = stock
    return StockCountRecord(
        **(
            dict(
                tenant_id=first.id,
                product_id=product.id,
                actor_membership_id=member.id,
                operation_key=uuid4(),
                request_fingerprint="a" * 64,
                reason="Physical count",
                before_quantity=D(0),
                after_quantity=D(2),
                before_value=D(0),
                after_value=D(6),
                quantity_delta=D(2),
                value_delta=D(6),
                declared_unit_cost=D(3),
            )
            | changes
        )
    )


def test_count_movement_balance_roundtrip(db_session, stock):
    record = count(stock)
    db_session.add(record)
    db_session.flush()
    movement = StockMovement(
        tenant_id=record.tenant_id,
        product_id=record.product_id,
        count_id=record.id,
        quantity_delta=record.quantity_delta,
        value_delta=record.value_delta,
    )
    balance = StockBalance(
        tenant_id=record.tenant_id,
        product_id=record.product_id,
        quantity=D(2),
        value=D(6),
    )
    db_session.add_all([movement, balance])
    db_session.flush()
    assert record.created_at.utcoffset().total_seconds() == 0
    assert movement.created_at.utcoffset().total_seconds() == 0
    assert db_session.get(
        StockBalance, (record.tenant_id, record.product_id)
    ).value == D(6)


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity_delta": D(1)},
        {"value_delta": D(5)},
        {"declared_unit_cost": None},
        {"declared_unit_cost": D(-1)},
        {"reason": " "},
        {"request_fingerprint": "invalid"},
        {"before_quantity": D(-1)},
        {"before_value": D(-1)},
        {"after_quantity": D("NaN")},
        {"after_value": D("Infinity")},
        {"before_value": D(1)},
    ],
)
def test_invalid_count_evidence_rejected(db_session, stock, changes):
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(count(stock, **changes))
        db_session.flush()


def test_cross_tenant_actor_and_product_rejected(db_session, stock):
    for changes in (
        {"tenant_id": stock[1].id},
        {"actor_membership_id": uuid4()},
        {"product_id": uuid4()},
    ):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(count(stock, **changes))
            db_session.flush()


def test_operation_key_unique_in_company(db_session, stock):
    first = count(stock)
    db_session.add(first)
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(count(stock, operation_key=first.operation_key))
        db_session.flush()


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity_delta": D(1)},
        {"value_delta": D(5)},
        {"tenant_id": uuid4()},
        {"product_id": uuid4()},
        {"count_id": uuid4()},
    ],
)
def test_ledger_must_match_its_real_count(db_session, stock, changes):
    record = count(stock)
    db_session.add(record)
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            StockMovement(
                **(
                    dict(
                        tenant_id=record.tenant_id,
                        product_id=record.product_id,
                        count_id=record.id,
                        quantity_delta=D(2),
                        value_delta=D(6),
                    )
                    | changes
                )
            )
        )
        db_session.flush()


def test_no_difference_count_cannot_generate_zero_movement(db_session, stock):
    record = count(
        stock,
        after_quantity=D(0),
        after_value=D(0),
        quantity_delta=D(0),
        value_delta=D(0),
        declared_unit_cost=None,
    )
    db_session.add(record)
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            StockMovement(
                tenant_id=record.tenant_id,
                product_id=record.product_id,
                count_id=record.id,
                quantity_delta=D(0),
                value_delta=D(0),
            )
        )
        db_session.flush()


@pytest.mark.parametrize(
    "quantity,value",
    [
        (D(-1), D(0)),
        (D(0), D(1)),
        (D(1), D(-1)),
        (D("NaN"), D(0)),
        (D(1), D("Infinity")),
    ],
)
def test_invalid_balance_rejected(db_session, stock, quantity, value):
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            StockBalance(
                tenant_id=stock[0].id,
                product_id=stock[3].id,
                quantity=quantity,
                value=value,
            )
        )
        db_session.flush()


@pytest.mark.parametrize("model", [StockCountRecord, StockMovement])
def test_runtime_cannot_rewrite_evidence(db_session, model):
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            update(model).where(model.id == uuid4()).values(tenant_id=uuid4())
        )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(delete(model).where(model.id == uuid4()))


def test_runtime_cannot_delete_balance(db_session):
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            delete(StockBalance).where(StockBalance.tenant_id == uuid4())
        )


def test_only_one_movement_per_count(db_session, stock):
    record = count(stock)
    db_session.add(record)
    db_session.flush()
    values = dict(
        tenant_id=record.tenant_id,
        product_id=record.product_id,
        count_id=record.id,
        quantity_delta=record.quantity_delta,
        value_delta=record.value_delta,
    )
    db_session.add(StockMovement(**values))
    db_session.flush()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(StockMovement(**values))
        db_session.flush()


def test_balance_unique_and_tenant_bound(db_session, stock):
    values = dict(
        tenant_id=stock[0].id, product_id=stock[3].id, quantity=D(0), value=D(0)
    )
    db_session.add(StockBalance(**values))
    db_session.flush()
    # Core INSERT avoids SQLAlchemy's identity-map collision warning, while
    # exercising the actual database uniqueness under the runtime role.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(StockBalance.__table__.insert().values(**values))
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(StockBalance(**(values | {"tenant_id": stock[1].id})))
        db_session.flush()


def test_empty_schema_downgrade_and_upgrade(databases):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with databases[1].begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0031_catalog_audit")
        command.upgrade(config, "head")
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0043_employee_invitations"
        )


def test_downgrade_refuses_stock_data(databases):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    tenant_id, product_id = uuid4(), uuid4()
    owner = databases[1]
    with owner.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants (id,name) VALUES (:id,'Stock guard')"),
            {"id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO products (id,tenant_id,name,price) "
                "VALUES (:id,:tenant,'Stock guard',1)"
            ),
            {"id": product_id, "tenant": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO stock_balances (tenant_id,product_id,quantity,value) "
                "VALUES (:tenant,:product,0,0)"
            ),
            {"tenant": tenant_id, "product": product_id},
        )
    try:
        with (
            pytest.raises(RuntimeError, match="refusing destructive downgrade"),
            owner.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0031_catalog_audit")
        with owner.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0043_employee_invitations"
            )
    finally:
        with owner.begin() as connection:
            connection.execute(
                delete(StockBalance).where(StockBalance.tenant_id == tenant_id)
            )
            connection.execute(delete(Product).where(Product.id == product_id))
            connection.execute(delete(Tenant).where(Tenant.id == tenant_id))
