"""Purchase references and immutable evidence under the real restricted DB role."""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.access.models import Tenant, TenantMembership, User
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockBalance, StockMovement
from app.modules.purchases.calculation import calculate_purchase
from app.modules.purchases.inputs import PurchaseCreation
from app.modules.purchases.models import Purchase, PurchaseItem, Supplier

pytestmark = pytest.mark.integration
D = Decimal


@pytest.fixture
def purchasing(db_session):
    tenants = [Tenant(name="Purchase A"), Tenant(name="Purchase B")]
    user = User(email=f"purchase-{uuid4().hex}@example.com", password_hash="synthetic")
    db_session.add_all([*tenants, user])
    db_session.flush()
    members = [
        TenantMembership(tenant_id=t.id, user_id=user.id, role="admin") for t in tenants
    ]
    products = [
        Product(tenant_id=t.id, name="Purchase product", price=D(5)) for t in tenants
    ]
    db_session.add_all([*members, *products])
    db_session.flush()
    suppliers = [
        Supplier(tenant_id=t.id, actor_membership_id=m.id, name="Supplier")
        for t, m in zip(tenants, members, strict=True)
    ]
    db_session.add_all(suppliers)
    db_session.flush()
    return tenants, members, products, suppliers


def purchase_values(data, **changes):
    tenants, members, _, suppliers = data
    return (
        dict(
            id=uuid4(),
            tenant_id=tenants[0].id,
            supplier_id=suppliers[0].id,
            supplier_name=suppliers[0].name,
            actor_membership_id=members[0].id,
            operation_key=uuid4(),
            request_fingerprint="a" * 64,
            line_count=1,
            total=D("0.00"),
        )
        | changes
    )


def item_values(data, parent, **changes):
    product = data[2][0]
    return (
        dict(
            id=uuid4(),
            tenant_id=parent.tenant_id,
            purchase_id=parent.id,
            product_id=product.id,
            position=1,
            product_name=product.name,
            quantity=D("0.001"),
            unit_cost=D("4.999999"),
            stock_value=D("0.005000"),
            amount=D("0.00"),
        )
        | changes
    )


def save_purchase(db_session, data):
    parent = Purchase(**purchase_values(data))
    db_session.add(parent)
    db_session.flush()
    return parent


def test_roundtrip_matches_calculation_and_does_not_create_stock(
    db_session, purchasing
):
    parent = save_purchase(db_session, purchasing)
    item = PurchaseItem(**item_values(purchasing, parent))
    db_session.add(item)
    db_session.flush()
    db_session.expire_all()
    result = calculate_purchase(
        PurchaseCreation(
            supplier_id=parent.supplier_id,
            lines=[
                dict(
                    product_id=item.product_id,
                    quantity=item.quantity,
                    unit_cost=item.unit_cost,
                )
            ],
        )
    )
    assert result.total == parent.total == item.amount
    assert result.lines[0].stock_value == item.stock_value
    assert parent.created_at.utcoffset().total_seconds() == 0
    assert purchasing[3][0].is_active
    assert (
        db_session.scalar(
            select(StockBalance).where(StockBalance.product_id == item.product_id)
        )
        is None
    )
    assert (
        db_session.scalar(
            select(StockMovement).where(StockMovement.product_id == item.product_id)
        )
        is None
    )


@pytest.mark.parametrize(
    "kind",
    [
        "supplier",
        "purchase_supplier",
        "purchase_actor",
        "item_purchase",
        "item_product",
    ],
)
def test_cross_company_references_fail_even_in_sql(db_session, purchasing, kind):
    tenants, members, products, suppliers = purchasing
    parent = save_purchase(db_session, purchasing)
    if kind == "supplier":
        record = Supplier(
            tenant_id=tenants[0].id, actor_membership_id=members[1].id, name="Invalid"
        )
    elif kind == "purchase_supplier":
        record = Purchase(**purchase_values(purchasing, supplier_id=suppliers[1].id))
    elif kind == "purchase_actor":
        record = Purchase(
            **purchase_values(purchasing, actor_membership_id=members[1].id)
        )
    elif kind == "item_purchase":
        record = PurchaseItem(
            **item_values(
                purchasing, parent, tenant_id=tenants[1].id, product_id=products[1].id
            )
        )
    else:
        record = PurchaseItem(
            **item_values(purchasing, parent, product_id=products[1].id)
        )
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(record)
        db_session.flush()


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", "NaN"),
        ("quantity", "Infinity"),
        ("unit_cost", "-1"),
        ("unit_cost", "NaN"),
        ("unit_cost", "Infinity"),
        ("stock_value", "0.004999"),
        ("stock_value", "NaN"),
        ("amount", "0.01"),
        ("amount", "Infinity"),
        ("position", 0),
        ("position", 101),
        ("product_name", " "),
    ],
)
def test_invalid_line_evidence_is_rejected(db_session, purchasing, field, value):
    parent = save_purchase(db_session, purchasing)
    changes = {field: value if field in {"position", "product_name"} else D(value)}
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(PurchaseItem(**item_values(purchasing, parent, **changes)))
        db_session.flush()


@pytest.mark.parametrize(
    "changes",
    [
        dict(total=D(-1)),
        dict(total=D("NaN")),
        dict(total=D("Infinity")),
        dict(total=D("1000000000000000000")),
        dict(line_count=0),
        dict(line_count=101),
        dict(request_fingerprint="invalid"),
        dict(supplier_name=" "),
    ],
)
def test_invalid_header_evidence_is_rejected(db_session, purchasing, changes):
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(Purchase(**purchase_values(purchasing, **changes)))
        db_session.flush()


def test_operation_is_unique_per_company(db_session, purchasing):
    parent = save_purchase(db_session, purchasing)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            Purchase(**purchase_values(purchasing, operation_key=parent.operation_key))
        )
        db_session.flush()
    tenants, members, _, suppliers = purchasing
    db_session.add(
        Purchase(
            **purchase_values(
                purchasing,
                tenant_id=tenants[1].id,
                supplier_id=suppliers[1].id,
                actor_membership_id=members[1].id,
                operation_key=parent.operation_key,
            )
        )
    )
    db_session.flush()


def test_duplicate_product_or_position_is_rejected(db_session, purchasing):
    parent = save_purchase(db_session, purchasing)
    db_session.add(PurchaseItem(**item_values(purchasing, parent)))
    other = Product(tenant_id=parent.tenant_id, name="Another product", price=D(2))
    db_session.add(other)
    db_session.flush()
    for changes in (dict(position=2), dict(product_id=other.id)):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(PurchaseItem(**item_values(purchasing, parent, **changes)))
            db_session.flush()


@pytest.mark.parametrize("model", [Supplier, Purchase, PurchaseItem])
@pytest.mark.parametrize("action", ["update", "delete", "truncate"])
def test_runtime_cannot_rewrite_or_delete_evidence(db_session, model, action):
    assert not db_session.scalar(
        text("SELECT has_table_privilege(current_user, :table, :action)"),
        dict(table=model.__tablename__, action=action.upper()),
    )
    if action == "update":
        assert not db_session.scalar(
            text("SELECT has_any_column_privilege(current_user, :table, 'UPDATE')"),
            dict(table=model.__tablename__),
        )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        if action == "delete":
            db_session.execute(delete(model))
        elif action == "update":
            db_session.execute(update(model).values(tenant_id=uuid4()))
        else:
            # Model comes from the fixed parameter list, never client input.
            db_session.execute(text(f"TRUNCATE TABLE {model.__tablename__}"))


def test_downgrade_refuses_even_supplier_without_purchase(databases):
    _, owner, _ = databases
    tenant, user, member, supplier = (uuid4() for _ in range(4))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with owner.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants (id,name) VALUES (:id,'Guard')"), dict(id=tenant)
        )
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash) "
                "VALUES (:id,:email,'synthetic')"
            ),
            dict(id=user, email=f"guard-{user}@example.com"),
        )
        connection.execute(
            text(
                "INSERT INTO tenant_memberships (id,tenant_id,user_id,role) "
                "VALUES (:id,:tenant,:user,'admin')"
            ),
            dict(id=member, tenant=tenant, user=user),
        )
        connection.execute(
            text(
                "INSERT INTO suppliers (id,tenant_id,actor_membership_id,name) "
                "VALUES (:id,:tenant,:member,'Guard')"
            ),
            dict(id=supplier, tenant=tenant, member=member),
        )
    try:
        with (
            pytest.raises(RuntimeError, match="refusing destructive downgrade"),
            owner.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0034_product_images")
        with owner.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0046_inventory_withdrawal_grants"
            )
            assert (
                connection.scalar(select(Supplier.id).where(Supplier.id == supplier))
                == supplier
            )
    finally:
        with owner.begin() as connection:
            for model, identifier in (
                (Supplier, supplier),
                (TenantMembership, member),
                (User, user),
                (Tenant, tenant),
            ):
                connection.execute(delete(model).where(model.id == identifier))
