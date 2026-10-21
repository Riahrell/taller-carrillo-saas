"""Authorized, idempotent physical counts in one stock transaction."""

import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer
from sqlalchemy import Engine, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.administrator_guard import (
    AdministrativeDenied,
    authorize_administrator,
)
from app.modules.access.authentication import _limit_transaction
from app.modules.access.membership import AccessDenied
from app.modules.access.sessions import _database_now
from app.modules.catalog.models import Product
from app.modules.inventory.inputs import CountPageQuery, PhysicalCount
from app.modules.inventory.models import StockBalance, StockCountRecord, StockMovement
from app.modules.inventory.valuation import StockValue


class InventoryDenied(AccessDenied):
    """Current session cannot access this inventory operation."""


class InventoryConflict(Exception):
    """An operation key already identifies a different request."""


class InventoryUnavailable(RuntimeError):
    """Safe persistence failure; repeat only with the same operation key."""


class InventoryStale(Exception):
    """A reviewed inventory configuration has changed."""


class InventoryInvalid(Exception):
    """Requested count cannot be valued under the current stock rules."""


class CountView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    product_id: UUID
    operation_key: UUID
    before_quantity: Decimal
    after_quantity: Decimal
    before_value: Decimal
    after_value: Decimal
    quantity_delta: Decimal
    value_delta: Decimal
    reason: str
    declared_unit_cost: Decimal | None
    created_at: datetime
    actor_membership_id: UUID = Field(exclude=True, repr=False)

    @field_serializer(
        "before_quantity", "after_quantity", "quantity_delta", when_used="json"
    )
    def quantity_json(self, value: Decimal) -> str:
        return format(value, ".3f")

    @field_serializer(
        "before_value",
        "after_value",
        "value_delta",
        "declared_unit_cost",
        when_used="json",
    )
    def value_json(self, value: Decimal | None) -> str | None:
        return format(value, ".6f") if value is not None else None


class BalanceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: UUID
    quantity: Decimal
    value: Decimal
    average_unit_cost: Decimal | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)

    @field_serializer("average_unit_cost", when_used="json")
    def cost_json(self, value: Decimal | None) -> str | None:
        return format(value, ".6f") if value is not None else None


class CountPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[CountView, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


def _canonical_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _fingerprint(data: PhysicalCount) -> str:
    content = {
        "kind": "physical_count_v1",
        "product_id": str(data.product_id),
        "counted_quantity": _canonical_decimal(data.counted_quantity),
        "reason": data.reason,
        "declared_unit_cost": _canonical_decimal(data.declared_unit_cost),
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class InventoryAdministrator:
    """Locks tenant -> actor user/session -> product -> balance.

    The tenant lock also serializes first-balance creation and operation keys.
    No network calls, subordinate commits or retry with a new key inside here.
    """

    def __init__(self, engine: Engine):
        self._factory = create_session_factory(engine)

    @staticmethod
    def _authorize(
        session: Session,
        token: SecretStr,
        expected_actor: UUID | None,
        *,
        recent: bool = True,
    ):
        try:
            return authorize_administrator(
                session, token, expected_actor, recent=recent
            )
        except AdministrativeDenied:
            raise InventoryDenied("Access denied") from None

    @staticmethod
    def _view(record: StockCountRecord, actor_id: UUID) -> CountView:
        return CountView(
            **{
                field: getattr(record, field)
                for field in CountView.model_fields
                if field != "actor_membership_id"
            },
            actor_membership_id=actor_id,
        )

    def record_count(
        self,
        token: SecretStr,
        *,
        data: PhysicalCount,
        operation_key: UUID,
        expected_actor: UUID,
    ) -> CountView:
        if type(data) is not PhysicalCount or not isinstance(operation_key, UUID):
            raise AccessDenied("Access denied")
        # Also reject invalid copies constructed by internal callers bypassing
        # Pydantic validation; never trust computed/previous stock as input.
        data = PhysicalCount.model_validate(data.model_dump())
        fingerprint = _fingerprint(data)
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor)
                previous = session.scalar(
                    select(StockCountRecord).where(
                        StockCountRecord.tenant_id == actor.tenant_id,
                        StockCountRecord.operation_key == operation_key,
                    )
                )
                if previous is not None:
                    if previous.request_fingerprint != fingerprint:
                        raise InventoryConflict("Operation key conflict")
                    self._authorize(session, token, expected_actor)
                    result = self._view(previous, actor.membership_id)
                else:
                    product = session.scalar(
                        select(Product)
                        .where(
                            Product.tenant_id == actor.tenant_id,
                            Product.id == data.product_id,
                            Product.is_active.is_(True),
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if product is None:
                        raise InventoryDenied("Access denied")
                    balance = session.scalar(
                        select(StockBalance)
                        .where(
                            StockBalance.tenant_id == actor.tenant_id,
                            StockBalance.product_id == product.id,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    before = (
                        StockValue(balance.quantity, balance.value)
                        if balance
                        else StockValue(Decimal(0), Decimal(0))
                    )
                    try:
                        after = before.recount(
                            data.counted_quantity,
                            declared_unit_cost=data.declared_unit_cost,
                        )
                    except ValueError:
                        raise InventoryInvalid("Invalid physical count") from None
                    with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
                        quantity_delta = after.quantity - before.quantity
                        value_delta = after.value - before.value
                    record = StockCountRecord(
                        tenant_id=actor.tenant_id,
                        product_id=product.id,
                        actor_membership_id=actor.membership_id,
                        operation_key=operation_key,
                        request_fingerprint=fingerprint,
                        reason=data.reason,
                        declared_unit_cost=data.declared_unit_cost,
                        before_quantity=before.quantity,
                        before_value=before.value,
                        after_quantity=after.quantity,
                        after_value=after.value,
                        quantity_delta=quantity_delta,
                        value_delta=value_delta,
                        created_at=_database_now(session),
                    )
                    session.add(record)
                    session.flush()
                    if quantity_delta != 0:
                        session.add(
                            StockMovement(
                                tenant_id=actor.tenant_id,
                                product_id=product.id,
                                count_id=record.id,
                                quantity_delta=quantity_delta,
                                value_delta=value_delta,
                                created_at=record.created_at,
                            )
                        )
                    if balance is None:
                        balance = StockBalance(
                            tenant_id=actor.tenant_id,
                            product_id=product.id,
                            quantity=after.quantity,
                            value=after.value,
                        )
                        session.add(balance)
                    else:
                        balance.quantity, balance.value = after.quantity, after.value
                    session.flush()
                    self._authorize(session, token, expected_actor)
                    result = self._view(record, actor.membership_id)
            return result
        except SQLAlchemyError:
            raise InventoryUnavailable("Inventory unavailable") from None

    def record_manual(self, token, *, data, operation_key, expected_actor):
        from app.modules.inventory.manual import record_manual

        return record_manual(
            self,
            token,
            data=data,
            operation_key=operation_key,
            expected_actor=expected_actor,
        )

    def list_manual(self, token, *, product_id, query, expected_actor=None):
        from app.modules.inventory.manual import list_manual

        return list_manual(
            self,
            token,
            product_id=product_id,
            query=query,
            expected_actor=expected_actor,
        )

    def reverse(self, token, *, data, operation_key, expected_actor):
        from app.modules.inventory.reversals import reverse

        return reverse(
            self,
            token,
            data=data,
            operation_key=operation_key,
            expected_actor=expected_actor,
        )

    def low_stock(self, token, *, query, expected_actor=None):
        from app.modules.inventory.low_stock import low_stock

        return low_stock(self, token, query=query, expected_actor=expected_actor)

    def minimum(
        self, token, *, product_id, expected_actor=None, data=None, expected_etag=None
    ):
        from app.modules.inventory.minimum import minimum

        return minimum(
            self,
            token,
            product_id=product_id,
            expected_actor=expected_actor,
            data=data,
            expected_etag=expected_etag,
        )

    def list_history(self, token, *, product_id, query, expected_actor=None):
        from app.modules.inventory.history import list_history

        return list_history(
            self,
            token,
            product_id=product_id,
            query=query,
            expected_actor=expected_actor,
        )

    @staticmethod
    def _require_product(session: Session, tenant_id: UUID, product_id: UUID):
        if not isinstance(product_id, UUID):
            raise InventoryDenied("Access denied")
        product = session.scalar(
            select(Product.id).where(
                Product.tenant_id == tenant_id,
                Product.id == product_id,
            )
        )
        if product is None:
            raise InventoryDenied("Access denied")

    def current_balance(
        self,
        token: SecretStr,
        *,
        product_id: UUID,
        expected_actor: UUID | None = None,
    ) -> BalanceView:
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor, recent=False)
                self._require_product(session, actor.tenant_id, product_id)
                balance = session.get(StockBalance, (actor.tenant_id, product_id))
                average_unit_cost = None
                if balance is not None and balance.quantity > 0:
                    with localcontext(Context(prec=80, rounding=ROUND_HALF_UP)):
                        average_unit_cost = (balance.value / balance.quantity).quantize(
                            Decimal("0.000001")
                        )
                result = BalanceView(
                    product_id=product_id,
                    quantity=balance.quantity if balance else Decimal("0.000"),
                    value=balance.value if balance else Decimal("0.000000"),
                    average_unit_cost=average_unit_cost,
                    actor_membership_id=actor.membership_id,
                )
            return result
        except SQLAlchemyError:
            raise InventoryUnavailable("Inventory unavailable") from None

    def list_counts(
        self,
        token: SecretStr,
        *,
        product_id: UUID,
        query: CountPageQuery,
        expected_actor: UUID | None = None,
    ) -> CountPage:
        if type(query) is not CountPageQuery:
            raise InventoryDenied("Access denied")
        query = CountPageQuery.model_validate(query.model_dump())
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor, recent=False)
                self._require_product(session, actor.tenant_id, product_id)
                statement = select(StockCountRecord).where(
                    StockCountRecord.tenant_id == actor.tenant_id,
                    StockCountRecord.product_id == product_id,
                )
                if query.after is not None:
                    cursor = session.scalar(
                        statement.where(StockCountRecord.id == query.after)
                    )
                    if cursor is None:
                        raise InventoryDenied("Access denied")
                    statement = statement.where(
                        tuple_(StockCountRecord.created_at, StockCountRecord.id)
                        < (cursor.created_at, cursor.id)
                    )
                rows = list(
                    session.scalars(
                        statement.order_by(
                            StockCountRecord.created_at.desc(),
                            StockCountRecord.id.desc(),
                        ).limit(query.limit + 1)
                    )
                )
                items = tuple(
                    self._view(row, actor.membership_id) for row in rows[: query.limit]
                )
                result = CountPage(
                    items=items,
                    next_after=items[-1].id if len(rows) > query.limit else None,
                    actor_membership_id=actor.membership_id,
                )
            return result
        except SQLAlchemyError:
            raise InventoryUnavailable("Inventory unavailable") from None
