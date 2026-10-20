"""Shared synthetic purchase state, only inside guarded PostgreSQL tests."""

from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.modules.access.sessions import issue_session
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockBalance, StockCountRecord, StockMovement
from app.modules.purchases.inputs import SupplierCreation
from app.modules.purchases.models import (
    Purchase,
    PurchaseItem,
    PurchaseReceipt,
    PurchaseReceiptLine,
    Supplier,
)
from app.modules.purchases.service import PurchasesAdministrator

D = Decimal


@contextmanager
def purchase_state(http_identity, recent_session):
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
    state["service"] = PurchasesAdministrator(state["runtime"])
    supplier = state["service"].create_supplier(
        state["token"],
        expected_actor=state["membership"],
        data=SupplierCreation(name=" Supplier A "),
    )
    state["supplier"] = supplier.id
    with Session(state["runtime"]) as session, session.begin():
        other = Supplier(
            tenant_id=state["other"],
            actor_membership_id=state["foreign_membership"],
            name="Foreign",
        )
        product = Product(tenant_id=state["tenant"], name="Second product", price=D(7))
        session.add_all([other, product])
        session.flush()
        state["foreign_supplier"], state["second_product"] = other.id, product.id
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            for model in (
                StockMovement,
                PurchaseReceiptLine,
                PurchaseReceipt,
                StockCountRecord,
                StockBalance,
                PurchaseItem,
                Purchase,
                Supplier,
                Product,
            ):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )
