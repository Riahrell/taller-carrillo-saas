"""Complete purchase receipt, its inventory ledger and balances in one commit."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.administrator_guard import authorize_administrator
from app.modules.access.authentication import _limit_transaction
from app.modules.access.membership import AccessDenied
from app.modules.access.sessions import _database_now
from app.modules.catalog.models import Product
from app.modules.inventory.models import StockMovement
from app.modules.inventory.receiving import receive_purchase_item
from app.modules.purchases.models import (
    Purchase,
    PurchaseItem,
    PurchaseReceipt,
    PurchaseReceiptLine,
)
from app.modules.purchases.service import (
    PurchaseConflict,
    PurchaseInvalid,
    PurchasesAdministrator,
    PurchasesUnavailable,
)


class ReceiptLineView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: UUID
    before_quantity: Decimal
    after_quantity: Decimal
    before_value: Decimal
    after_value: Decimal
    quantity_delta: Decimal
    value_delta: Decimal

    @field_serializer(
        "before_quantity",
        "after_quantity",
        "quantity_delta",
        "before_value",
        "after_value",
        "value_delta",
        when_used="json",
    )
    def decimal_json(self, value: Decimal, info) -> str:
        places = 3 if "quantity" in info.field_name else 6
        return format(value, f".{places}f")


class ReceiptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    purchase_id: UUID
    operation_key: UUID
    created_at: datetime
    lines: tuple[ReceiptLineView, ...]
    actor_membership_id: UUID = Field(exclude=True, repr=False)


class PurchaseReceiver:
    def __init__(self, engine: Engine):
        self._factory = create_session_factory(engine)

    def receive(
        self,
        token: SecretStr,
        *,
        purchase_id: UUID,
        operation_key: UUID,
        expected_actor: UUID,
    ) -> ReceiptView:
        if not isinstance(purchase_id, UUID) or not isinstance(operation_key, UUID):
            raise AccessDenied("Access denied")
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_administrator(session, token, expected_actor)
                # Tenant lock serializes new keys and first-balance creation;
                # immutable purchase rows need no UPDATE privilege for locking.
                receipt = session.scalar(
                    select(PurchaseReceipt).where(
                        PurchaseReceipt.tenant_id == actor.tenant_id,
                        PurchaseReceipt.operation_key == operation_key,
                    )
                )
                if receipt is not None and receipt.purchase_id != purchase_id:
                    raise PurchaseConflict("Operation key conflict")
                purchase = session.scalar(
                    select(Purchase).where(
                        Purchase.tenant_id == actor.tenant_id,
                        Purchase.id == purchase_id,
                    )
                )
                if purchase is None:
                    raise AccessDenied("Access denied")
                PurchasesAdministrator._view(session, purchase, actor.membership_id)
                items = tuple(
                    session.scalars(
                        select(PurchaseItem)
                        .where(
                            PurchaseItem.tenant_id == actor.tenant_id,
                            PurchaseItem.purchase_id == purchase.id,
                        )
                        .order_by(PurchaseItem.product_id)
                    )
                )
                if receipt is None:
                    received = session.scalar(
                        select(PurchaseReceipt.id).where(
                            PurchaseReceipt.tenant_id == actor.tenant_id,
                            PurchaseReceipt.purchase_id == purchase.id,
                        )
                    )
                    if received is not None:
                        raise PurchaseConflict("Purchase already received")
                    products = tuple(
                        session.scalars(
                            select(Product.id)
                            .where(
                                Product.tenant_id == actor.tenant_id,
                                Product.id.in_([item.product_id for item in items]),
                                Product.is_active.is_(True),
                            )
                            .order_by(Product.id)
                            .with_for_update()
                        )
                    )
                    if len(products) != len(items):
                        raise AccessDenied("Access denied")
                    receipt = PurchaseReceipt(
                        tenant_id=actor.tenant_id,
                        purchase_id=purchase.id,
                        operation_key=operation_key,
                        actor_membership_id=actor.membership_id,
                        created_at=_database_now(session),
                    )
                    session.add(receipt)
                    session.flush()
                    try:
                        for item in items:
                            receive_purchase_item(session, receipt, item)
                    except ValueError:
                        raise PurchaseInvalid("Invalid purchase receipt") from None
                rows = tuple(
                    session.scalars(
                        select(PurchaseReceiptLine)
                        .where(
                            PurchaseReceiptLine.tenant_id == actor.tenant_id,
                            PurchaseReceiptLine.receipt_id == receipt.id,
                        )
                        .order_by(PurchaseReceiptLine.product_id)
                        .limit(101)
                    )
                )
                movement_count = session.scalar(
                    select(func.count())
                    .select_from(StockMovement)
                    .where(
                        StockMovement.tenant_id == actor.tenant_id,
                        StockMovement.receipt_line_id.in_([row.id for row in rows]),
                    )
                )
                if (
                    len(rows) != len(items)
                    or movement_count != len(items)
                    or any(
                        row.purchase_item_id != item.id
                        for row, item in zip(rows, items, strict=True)
                    )
                ):
                    raise PurchasesUnavailable("Purchases unavailable")
                authorize_administrator(session, token, expected_actor)
                result = ReceiptView(
                    id=receipt.id,
                    purchase_id=receipt.purchase_id,
                    operation_key=receipt.operation_key,
                    created_at=receipt.created_at,
                    actor_membership_id=actor.membership_id,
                    lines=tuple(
                        ReceiptLineView(
                            **{
                                field: getattr(row, field)
                                for field in ReceiptLineView.model_fields
                            }
                        )
                        for row in rows
                    ),
                )
            return result
        except SQLAlchemyError:
            raise PurchasesUnavailable("Purchases unavailable") from None
