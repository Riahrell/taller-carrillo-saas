"""Authorized purchase recording with immutable creation evidence, no receipts."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer
from sqlalchemy import Engine, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.administrator_guard import authorize_administrator
from app.modules.access.authentication import _limit_transaction
from app.modules.access.membership import AccessDenied
from app.modules.access.sessions import _database_now
from app.modules.catalog.models import Product
from app.modules.purchases.calculation import calculate_purchase
from app.modules.purchases.inputs import (
    PurchaseCreation,
    PurchasePageQuery,
    SupplierCreation,
)
from app.modules.purchases.models import (
    Purchase,
    PurchaseItem,
    PurchaseReceipt,
    Supplier,
)


class PurchaseConflict(Exception):
    """Operation key already identifies different purchase contents."""


class PurchaseInvalid(Exception):
    """Purchase exceeds supported valuation or monetary capacity."""


class PurchasesUnavailable(RuntimeError):
    """Safe storage failure; never automatically repeat with a new key."""


class SupplierView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str
    is_active: bool
    actor_membership_id: UUID = Field(exclude=True, repr=False)


class PurchaseItemView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: UUID
    product_name: str
    quantity: Decimal
    unit_cost: Decimal
    stock_value: Decimal
    amount: Decimal

    @field_serializer(
        "quantity", "unit_cost", "stock_value", "amount", when_used="json"
    )
    def decimal_json(self, value: Decimal, info) -> str:
        places = {"quantity": 3, "unit_cost": 6, "stock_value": 6, "amount": 2}
        return format(value, f".{places[info.field_name]}f")


class PurchaseView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    supplier_id: UUID
    supplier_name: str
    operation_key: UUID
    lines: tuple[PurchaseItemView, ...]
    total: Decimal
    created_at: datetime
    actor_membership_id: UUID = Field(exclude=True, repr=False)

    @field_serializer("total", when_used="json")
    def money_json(self, value: Decimal) -> str:
        return format(value, ".2f")


class PurchaseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    supplier_id: UUID
    supplier_name: str
    total: Decimal
    created_at: datetime
    receipt_id: UUID | None

    @field_serializer("total", when_used="json")
    def money_json(self, value: Decimal) -> str:
        return format(value, ".2f")


class PurchasePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[PurchaseSummary, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


class SupplierPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[SupplierView, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


def _decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _fingerprint(data: PurchaseCreation) -> str:
    payload = {
        "kind": "purchase_creation_v1",
        "supplier_id": str(data.supplier_id),
        "lines": [
            dict(
                product_id=str(line.product_id),
                quantity=_decimal(line.quantity),
                unit_cost=_decimal(line.unit_cost),
            )
            for line in data.lines
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PurchasesAdministrator:
    """Own one transaction, locks tenant -> user/session -> products.

    Products lock in UUID order, independently of submitted display order. Tenant
    lock serializes operation keys as in counts. No network, stock or payment work.
    """

    def __init__(self, engine: Engine):
        self._factory = create_session_factory(engine)

    def list_suppliers(
        self, token: SecretStr, *, query: PurchasePageQuery, expected_actor: UUID
    ) -> SupplierPage:
        if type(query) is not PurchasePageQuery:
            raise AccessDenied("Access denied")
        query = PurchasePageQuery.model_validate(query.model_dump())
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_administrator(
                    session, token, expected_actor, recent=False
                )
                statement = select(Supplier).where(
                    Supplier.tenant_id == actor.tenant_id
                )
                if query.after is not None:
                    if (
                        session.scalar(statement.where(Supplier.id == query.after))
                        is None
                    ):
                        raise AccessDenied("Access denied")
                    statement = statement.where(Supplier.id > query.after)
                rows = tuple(
                    session.scalars(
                        statement.order_by(Supplier.id).limit(query.limit + 1)
                    )
                )
                items = tuple(
                    SupplierView(
                        id=row.id,
                        name=row.name,
                        is_active=row.is_active,
                        actor_membership_id=actor.membership_id,
                    )
                    for row in rows[: query.limit]
                )
                authorize_administrator(session, token, expected_actor, recent=False)
                result = SupplierPage(
                    items=items,
                    next_after=items[-1].id if len(rows) > query.limit else None,
                    actor_membership_id=actor.membership_id,
                )
            return result
        except SQLAlchemyError:
            raise PurchasesUnavailable("Purchases unavailable") from None

    def list_purchases(
        self, token: SecretStr, *, query: PurchasePageQuery, expected_actor: UUID
    ) -> PurchasePage:
        if type(query) is not PurchasePageQuery:
            raise AccessDenied("Access denied")
        query = PurchasePageQuery.model_validate(query.model_dump())
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_administrator(
                    session, token, expected_actor, recent=False
                )
                statement = select(Purchase).where(
                    Purchase.tenant_id == actor.tenant_id
                )
                if query.after is not None:
                    cursor = session.scalar(statement.where(Purchase.id == query.after))
                    if cursor is None:
                        raise AccessDenied("Access denied")
                    statement = statement.where(
                        tuple_(Purchase.created_at, Purchase.id)
                        < (cursor.created_at, cursor.id)
                    )
                rows = tuple(
                    session.scalars(
                        statement.order_by(
                            Purchase.created_at.desc(), Purchase.id.desc()
                        ).limit(query.limit + 1)
                    )
                )
                receipt_ids = dict(
                    session.execute(
                        select(PurchaseReceipt.purchase_id, PurchaseReceipt.id).where(
                            PurchaseReceipt.tenant_id == actor.tenant_id,
                            PurchaseReceipt.purchase_id.in_(
                                [row.id for row in rows[: query.limit]]
                            ),
                        )
                    ).all()
                )
                items = tuple(
                    PurchaseSummary(
                        id=row.id,
                        supplier_id=row.supplier_id,
                        supplier_name=row.supplier_name,
                        total=row.total,
                        created_at=row.created_at,
                        receipt_id=receipt_ids.get(row.id),
                    )
                    for row in rows[: query.limit]
                )
                authorize_administrator(session, token, expected_actor, recent=False)
                result = PurchasePage(
                    items=items,
                    next_after=items[-1].id if len(rows) > query.limit else None,
                    actor_membership_id=actor.membership_id,
                )
            return result
        except SQLAlchemyError:
            raise PurchasesUnavailable("Purchases unavailable") from None

    def create_supplier(
        self, token: SecretStr, *, data: SupplierCreation, expected_actor: UUID
    ) -> SupplierView:
        if type(data) is not SupplierCreation:
            raise AccessDenied("Access denied")
        data = SupplierCreation.model_validate(data.model_dump())
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_administrator(session, token, expected_actor)
                record = Supplier(
                    tenant_id=actor.tenant_id,
                    actor_membership_id=actor.membership_id,
                    name=data.name,
                    created_at=_database_now(session),
                )
                session.add(record)
                session.flush()
                authorize_administrator(session, token, expected_actor)
                result = SupplierView(
                    id=record.id,
                    name=record.name,
                    is_active=record.is_active,
                    actor_membership_id=actor.membership_id,
                )
            return result
        except SQLAlchemyError:
            raise PurchasesUnavailable("Purchases unavailable") from None

    def record_purchase(
        self,
        token: SecretStr,
        *,
        data: PurchaseCreation,
        operation_key: UUID,
        expected_actor: UUID,
    ) -> PurchaseView:
        if type(data) is not PurchaseCreation or not isinstance(operation_key, UUID):
            raise AccessDenied("Access denied")
        data = PurchaseCreation.model_validate(data.model_dump())
        fingerprint = _fingerprint(data)
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_administrator(session, token, expected_actor)
                previous = session.scalar(
                    select(Purchase).where(
                        Purchase.tenant_id == actor.tenant_id,
                        Purchase.operation_key == operation_key,
                    )
                )
                if previous is not None:
                    if previous.request_fingerprint != fingerprint:
                        raise PurchaseConflict("Operation key conflict")
                    result = self._view(session, previous, actor.membership_id)
                else:
                    # Supplier is immutable to runtime. Parent tenant lock is
                    # the serialization point for authorized business changes;
                    # FOR UPDATE would require granting supplier UPDATE rights.
                    supplier = session.scalar(
                        select(Supplier)
                        .where(
                            Supplier.tenant_id == actor.tenant_id,
                            Supplier.id == data.supplier_id,
                            Supplier.is_active.is_(True),
                        )
                        .execution_options(populate_existing=True)
                    )
                    if supplier is None:
                        raise AccessDenied("Access denied")
                    products = {
                        row.id: row
                        for row in session.scalars(
                            select(Product)
                            .where(
                                Product.tenant_id == actor.tenant_id,
                                Product.id.in_(
                                    [line.product_id for line in data.lines]
                                ),
                                Product.is_active.is_(True),
                            )
                            .order_by(Product.id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                    }
                    if len(products) != len(data.lines):
                        raise AccessDenied("Access denied")
                    try:
                        amounts = calculate_purchase(data)
                    except ValueError:
                        raise PurchaseInvalid("Invalid purchase") from None
                    record = Purchase(
                        tenant_id=actor.tenant_id,
                        supplier_id=supplier.id,
                        supplier_name=supplier.name,
                        actor_membership_id=actor.membership_id,
                        operation_key=operation_key,
                        request_fingerprint=fingerprint,
                        line_count=len(amounts.lines),
                        total=amounts.total,
                        created_at=_database_now(session),
                    )
                    session.add(record)
                    session.flush()
                    for position, line in enumerate(amounts.lines, 1):
                        session.add(
                            PurchaseItem(
                                tenant_id=actor.tenant_id,
                                purchase_id=record.id,
                                product_id=line.product_id,
                                product_name=products[line.product_id].name,
                                position=position,
                                quantity=line.quantity,
                                unit_cost=line.unit_cost,
                                stock_value=line.stock_value,
                                amount=line.amount,
                            )
                        )
                    session.flush()
                    result = self._view(session, record, actor.membership_id)
                authorize_administrator(session, token, expected_actor)
            return result
        except SQLAlchemyError:
            raise PurchasesUnavailable("Purchases unavailable") from None

    def current_purchase(
        self, token: SecretStr, *, purchase_id: UUID, expected_actor: UUID
    ) -> PurchaseView:
        if not isinstance(purchase_id, UUID):
            raise AccessDenied("Access denied")
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_administrator(
                    session, token, expected_actor, recent=False
                )
                record = session.scalar(
                    select(Purchase).where(
                        Purchase.tenant_id == actor.tenant_id,
                        Purchase.id == purchase_id,
                    )
                )
                if record is None:
                    raise AccessDenied("Access denied")
                result = self._view(session, record, actor.membership_id)
                authorize_administrator(session, token, expected_actor, recent=False)
            return result
        except SQLAlchemyError:
            raise PurchasesUnavailable("Purchases unavailable") from None

    @staticmethod
    def _view(session: Session, record: Purchase, actor: UUID) -> PurchaseView:
        rows = tuple(
            session.scalars(
                select(PurchaseItem)
                .where(
                    PurchaseItem.tenant_id == record.tenant_id,
                    PurchaseItem.purchase_id == record.id,
                )
                .order_by(PurchaseItem.position)
                .limit(101)
            )
        )
        # A header alone is not a valid purchase. Fail closed if incomplete or
        # inconsistent data was inserted outside this service.
        try:
            command = PurchaseCreation(
                supplier_id=record.supplier_id,
                lines=[
                    dict(
                        product_id=row.product_id,
                        quantity=row.quantity,
                        unit_cost=row.unit_cost,
                    )
                    for row in rows
                ],
            )
            calculated = calculate_purchase(command)
            valid = len(rows) == record.line_count and calculated.total == record.total
            valid = valid and all(
                row.position == position
                and row.stock_value == line.stock_value
                and row.amount == line.amount
                for position, (row, line) in enumerate(
                    zip(rows, calculated.lines, strict=True), 1
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise PurchasesUnavailable("Purchases unavailable")
        return PurchaseView(
            id=record.id,
            supplier_id=record.supplier_id,
            supplier_name=record.supplier_name,
            operation_key=record.operation_key,
            total=record.total,
            created_at=record.created_at,
            lines=tuple(
                PurchaseItemView(
                    **{
                        field: getattr(row, field)
                        for field in PurchaseItemView.model_fields
                    }
                )
                for row in rows
            ),
            actor_membership_id=actor,
        )
