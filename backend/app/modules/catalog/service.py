"""Session-authorized catalog creation, committed with typed audit evidence."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.authentication import _limit_transaction
from app.modules.access.membership import AccessDenied, MembershipContext
from app.modules.access.models import AuditEvent, SessionRecord, Tenant
from app.modules.access.reauthentication import require_recent_administrator
from app.modules.access.sessions import _database_now, _token_hash
from app.modules.catalog.inputs import CategoryCreation, ProductCreation
from app.modules.catalog.models import Category, Product


class CatalogConflict(Exception):
    """SKU already used in this tenant, including inactive products."""


class CatalogUnavailable(RuntimeError):
    """Safe storage failure; a lost response must not trigger automatic replay."""


class CategoryView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str
    is_active: bool


class ProductView(CategoryView):
    category_id: UUID | None
    sku: str | None
    price: Decimal


class CatalogAdministrator:
    """Own a transaction; never accept tenant, role or actor authority as data.

    Locks: tenant -> actor user -> session -> category. Membership administration
    uses the same parent order. No external work or subordinate commits.
    An HTTP adapter must add origin/CSRF and browser-context checks separately.
    """

    def __init__(self, engine: Engine):
        self._factory = create_session_factory(engine)

    @staticmethod
    def _authorize(
        session: Session, token: SecretStr, expected_actor: UUID
    ) -> MembershipContext:
        if not isinstance(expected_actor, UUID):
            raise AccessDenied("Access denied")
        # Unlocked hint locates the parent only; never grants authority.
        tenant_id = session.scalar(
            select(SessionRecord.tenant_id).where(
                SessionRecord.token_hash == _token_hash(token)
            )
        )
        if tenant_id is None:
            raise AccessDenied("Access denied")
        tenant = session.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if tenant is None or not tenant.is_active:
            raise AccessDenied("Access denied")
        actor = require_recent_administrator(
            session, token, expected_actor=expected_actor
        )
        if actor.tenant_id != tenant.id:
            raise AccessDenied("Access denied")
        return actor

    def create_category(
        self, token: SecretStr, *, data: CategoryCreation, expected_actor: UUID
    ) -> CategoryView:
        if type(data) is not CategoryCreation:
            raise AccessDenied("Access denied")
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor)
                category = Category(tenant_id=actor.tenant_id, name=data.name)
                session.add(category)
                session.flush()
                session.add(
                    AuditEvent(
                        tenant_id=actor.tenant_id,
                        actor_membership_id=actor.membership_id,
                        action="catalog_category_created",
                        catalog_category_id=category.id,
                        catalog_name=category.name,
                        after_active=True,
                        created_at=_database_now(session),
                    )
                )
                session.flush()
                self._authorize(session, token, expected_actor)
                result = CategoryView(
                    id=category.id, name=category.name, is_active=category.is_active
                )
            return result
        except SQLAlchemyError:
            raise CatalogUnavailable("Catalog unavailable") from None

    def create_product(
        self, token: SecretStr, *, data: ProductCreation, expected_actor: UUID
    ) -> ProductView:
        if type(data) is not ProductCreation:
            raise AccessDenied("Access denied")
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor)
                if data.category_id is not None:
                    category = session.scalar(
                        select(Category)
                        .where(
                            Category.id == data.category_id,
                            Category.tenant_id == actor.tenant_id,
                            Category.is_active.is_(True),
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if category is None:
                        raise AccessDenied("Access denied")
                product = Product(
                    tenant_id=actor.tenant_id,
                    name=data.name,
                    category_id=data.category_id,
                    sku=data.sku,
                    price=data.price,
                )
                session.add(product)
                session.flush()
                session.add(
                    AuditEvent(
                        tenant_id=actor.tenant_id,
                        actor_membership_id=actor.membership_id,
                        action="catalog_product_created",
                        catalog_product_id=product.id,
                        catalog_category_id=product.category_id,
                        catalog_name=product.name,
                        catalog_sku=product.sku,
                        catalog_price=product.price,
                        after_active=True,
                        created_at=_database_now(session),
                    )
                )
                session.flush()
                self._authorize(session, token, expected_actor)
                result = ProductView(
                    id=product.id,
                    name=product.name,
                    is_active=product.is_active,
                    category_id=product.category_id,
                    sku=product.sku,
                    price=product.price,
                )
            return result
        except IntegrityError as error:
            if getattr(getattr(error.orig, "diag", None), "constraint_name", None) == (
                "uq_products_tenant_sku"
            ):
                raise CatalogConflict("SKU unavailable") from None
            raise CatalogUnavailable("Catalog unavailable") from None
        except SQLAlchemyError:
            raise CatalogUnavailable("Catalog unavailable") from None
