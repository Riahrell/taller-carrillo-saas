"""Synthetic inventory graph used only by the guarded isolated restore drill."""

from decimal import Decimal
from uuid import uuid5

from sqlalchemy import select

from app.modules.catalog.models import Product, ProductChangeRecord
from app.modules.inventory.models import (
    ManualStockRecord,
    StockBalance,
    StockCountRecord,
    StockMinimumRecord,
    StockMovement,
    StockReversalRecord,
)

D = Decimal


def _id(user_id, label):
    return uuid5(user_id, "carrillo-restore-" + label)


def seed(session, member, now):
    user_id = member.user_id
    product_id = _id(user_id, "product")
    product = Product(
        id=product_id,
        tenant_id=member.tenant_id,
        name="Restore part",
        price=D("12.34"),
        revision=1,
    )
    session.add(product)
    session.flush()
    session.add(
        ProductChangeRecord(
            tenant_id=member.tenant_id,
            product_id=product_id,
            actor_membership_id=member.id,
            before_revision=0,
            after_revision=1,
            before_name="Initial restore part",
            after_name=product.name,
            before_price=D("10.00"),
            after_price=product.price,
            before_active=True,
            after_active=True,
        )
    )
    count = StockCountRecord(
        id=_id(user_id, "count"),
        tenant_id=member.tenant_id,
        product_id=product_id,
        actor_membership_id=member.id,
        operation_key=_id(user_id, "count-key"),
        request_fingerprint="a" * 64,
        reason="Synthetic restore count",
        before_quantity=D(0),
        after_quantity=D(3),
        before_value=D(0),
        after_value=D(9),
        quantity_delta=D(3),
        value_delta=D(9),
        declared_unit_cost=D(3),
        created_at=now,
    )
    session.add(count)
    session.flush()
    original = ManualStockRecord(
        id=_id(user_id, "manual"),
        tenant_id=member.tenant_id,
        product_id=product_id,
        actor_membership_id=member.id,
        operation_key=_id(user_id, "manual-key"),
        request_fingerprint="b" * 64,
        direction="IN",
        reason="Synthetic restore entry",
        before_quantity=D(3),
        after_quantity=D(5),
        before_value=D(9),
        after_value=D(17),
        quantity_delta=D(2),
        value_delta=D(8),
        declared_unit_cost=D(4),
        created_at=now,
    )
    session.add(original)
    session.flush()
    source = StockMovement(
        id=_id(user_id, "manual-movement"),
        tenant_id=member.tenant_id,
        product_id=product_id,
        manual_record_id=original.id,
        quantity_delta=D(2),
        value_delta=D(8),
        created_at=now,
    )
    session.add_all(
        [
            source,
            StockMovement(
                tenant_id=member.tenant_id,
                product_id=product_id,
                count_id=count.id,
                quantity_delta=D(3),
                value_delta=D(9),
                created_at=now,
            ),
        ]
    )
    session.flush()
    reversal = StockReversalRecord(
        id=_id(user_id, "reversal"),
        tenant_id=member.tenant_id,
        product_id=product_id,
        actor_membership_id=member.id,
        operation_key=_id(user_id, "reversal-key"),
        request_fingerprint="c" * 64,
        original_movement_id=source.id,
        original_source="MANUAL",
        original_id=original.id,
        original_quantity_delta=D(2),
        original_value_delta=D(8),
        reason="Synthetic restore compensation",
        before_quantity=D(5),
        after_quantity=D(3),
        before_value=D(17),
        after_value=D(9),
        quantity_delta=D(-2),
        value_delta=D(-8),
        created_at=now,
    )
    session.add(reversal)
    session.flush()
    session.add_all(
        [
            StockMovement(
                tenant_id=member.tenant_id,
                product_id=product_id,
                reversal_id=reversal.id,
                quantity_delta=D(-2),
                value_delta=D(-8),
                created_at=now,
            ),
            StockBalance(
                tenant_id=member.tenant_id,
                product_id=product_id,
                quantity=D(3),
                value=D(9),
            ),
            StockMinimumRecord(
                tenant_id=member.tenant_id,
                product_id=product_id,
                actor_membership_id=member.id,
                revision=1,
                minimum=D(2),
                created_at=now,
            ),
        ]
    )
    session.flush()


def mutate(session, member):
    # Deliberate synthetic corruption after the snapshot; no operational DB allowed.
    product = session.get(Product, _id(member.user_id, "product"))
    product.name = "Post-snapshot mutation"
    product.revision = 2
    balance = session.get(StockBalance, (member.tenant_id, product.id))
    balance.quantity, balance.value = D(99), D(297)
    session.add(
        StockMinimumRecord(
            tenant_id=member.tenant_id,
            product_id=product.id,
            actor_membership_id=member.id,
            revision=2,
            before_minimum=D(2),
            minimum=D(100),
        )
    )


def check(session, member):
    product_id = _id(member.user_id, "product")
    product = session.get(Product, product_id)
    balance = session.get(StockBalance, (member.tenant_id, product_id))
    rows = session.scalars(
        select(StockMovement).where(
            StockMovement.tenant_id == member.tenant_id,
            StockMovement.product_id == product_id,
        )
    ).all()
    minimum = session.scalar(
        select(StockMinimumRecord)
        .where(
            StockMinimumRecord.tenant_id == member.tenant_id,
            StockMinimumRecord.product_id == product_id,
        )
        .order_by(StockMinimumRecord.revision.desc())
        .limit(1)
    )
    reversal = session.get(StockReversalRecord, _id(member.user_id, "reversal"))
    change = session.scalar(
        select(ProductChangeRecord).where(
            ProductChangeRecord.tenant_id == member.tenant_id,
            ProductChangeRecord.product_id == product_id,
        )
    )
    if (
        product is None
        or product.name != "Restore part"
        or product.revision != 1
        or product.price != D("12.34")
        or balance is None
        or balance.quantity != D(3)
        or balance.value != D(9)
        or len(rows) != 3
        or sum(row.quantity_delta for row in rows) != balance.quantity
        or sum(row.value_delta for row in rows) != balance.value
        or minimum is None
        or minimum.revision != 1
        or minimum.minimum != D(2)
        or reversal is None
        or reversal.original_id != _id(member.user_id, "manual")
        or reversal.original_movement_id != _id(member.user_id, "manual-movement")
        or change is None
        or change.after_revision != 1
    ):
        raise ValueError("Inventory snapshot mismatch")


def check_mutated(session, member):
    product = session.get(Product, _id(member.user_id, "product"))
    balance = session.get(StockBalance, (member.tenant_id, product.id))
    minimum = session.scalar(
        select(StockMinimumRecord)
        .where(
            StockMinimumRecord.tenant_id == member.tenant_id,
            StockMinimumRecord.product_id == product.id,
        )
        .order_by(StockMinimumRecord.revision.desc())
        .limit(1)
    )
    if (
        product.name != "Post-snapshot mutation"
        or product.revision != 2
        or balance.quantity != D(99)
        or balance.value != D(297)
        or minimum.revision != 2
        or minimum.minimum != D(100)
    ):
        raise ValueError("Synthetic mutation missing")
