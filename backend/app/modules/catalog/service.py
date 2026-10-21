"""Session-authorized catalog creation, committed with typed audit evidence."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Engine, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.administrator_guard import (
    AdministrativeDenied,
    authorize_administrator,
)
from app.modules.access.authentication import _limit_transaction
from app.modules.access.membership import AccessDenied, MembershipContext
from app.modules.access.models import AuditEvent
from app.modules.access.sessions import _database_now
from app.modules.catalog.inputs import (
    CatalogPageQuery,
    CategoryCreation,
    ProductCreation,
    ProductSearchQuery,
)
from app.modules.catalog.models import Category, Product


class CatalogConflict(Exception):
    """SKU already used in this tenant, including inactive products."""


class CatalogUnavailable(RuntimeError):
    """Safe storage failure; a lost response must not trigger automatic replay."""


class CatalogDenied(AccessDenied):
    """Current session without permission or missing/foreign catalog target."""


class CategoryView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str
    is_active: bool
    actor_membership_id: UUID | None = Field(default=None, exclude=True, repr=False)


class ProductView(CategoryView):
    category_id: UUID | None
    sku: str | None
    price: Decimal


class CategoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[CategoryView, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


class ProductPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[ProductView, ...]
    next_after: UUID | None
    actor_membership_id: UUID = Field(exclude=True, repr=False)


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
        session: Session,
        token: SecretStr,
        expected_actor: UUID | None,
        *,
        recent: bool = True,
    ) -> MembershipContext:
        try:
            return authorize_administrator(
                session, token, expected_actor, recent=recent
            )
        except AdministrativeDenied:
            raise CatalogDenied("Access denied") from None

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
                    id=category.id,
                    name=category.name,
                    is_active=category.is_active,
                    actor_membership_id=actor.membership_id,
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
                        raise CatalogDenied("Access denied")
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
                    actor_membership_id=actor.membership_id,
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

    def list_products(
        self,
        token: SecretStr,
        *,
        query: CatalogPageQuery,
        expected_actor: UUID | None = None,
    ) -> ProductPage:
        if type(query) not in (CatalogPageQuery, ProductSearchQuery):
            raise AccessDenied("Access denied")
        query = ProductSearchQuery.model_validate(query.model_dump())
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor, recent=False)
                statement = select(Product).where(Product.tenant_id == actor.tenant_id)
                if query.category_id is not None:
                    statement = statement.where(
                        Product.category_id == query.category_id
                    )
                if query.q is not None:
                    # Bind a literal substring, never accept SQL LIKE wildcards
                    # as a way to widen the tenant-bound query.
                    literal = (
                        query.q.replace("\\", "\\\\")
                        .replace("%", "\\%")
                        .replace("_", "\\_")
                    )
                    pattern = "%" + literal + "%"
                    statement = statement.where(
                        or_(
                            Product.name.ilike(pattern, escape="\\"),
                            Product.sku.ilike(pattern, escape="\\"),
                        )
                    )
                if query.after is not None:
                    statement = statement.where(Product.id > query.after)
                rows = list(
                    session.scalars(
                        statement.order_by(Product.id).limit(query.limit + 1)
                    )
                )
                items = tuple(self._product_view(row) for row in rows[: query.limit])
                result = ProductPage(
                    items=items,
                    actor_membership_id=actor.membership_id,
                    next_after=items[-1].id if len(rows) > query.limit else None,
                )
            return result
        except SQLAlchemyError:
            raise CatalogUnavailable("Catalog unavailable") from None

    def list_categories(
        self,
        token: SecretStr,
        *,
        query: CatalogPageQuery,
        expected_actor: UUID | None = None,
    ) -> CategoryPage:
        if type(query) is not CatalogPageQuery:
            raise AccessDenied("Access denied")
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor, recent=False)
                statement = select(Category).where(
                    Category.tenant_id == actor.tenant_id
                )
                if query.after is not None:
                    statement = statement.where(Category.id > query.after)
                rows = list(
                    session.scalars(
                        statement.order_by(Category.id).limit(query.limit + 1)
                    )
                )
                items = tuple(
                    CategoryView(id=row.id, name=row.name, is_active=row.is_active)
                    for row in rows[: query.limit]
                )
                result = CategoryPage(
                    items=items,
                    actor_membership_id=actor.membership_id,
                    next_after=items[-1].id if len(rows) > query.limit else None,
                )
            return result
        except SQLAlchemyError:
            raise CatalogUnavailable("Catalog unavailable") from None

    def current_product(
        self,
        token: SecretStr,
        *,
        product_id: UUID,
        expected_actor: UUID | None = None,
    ) -> ProductView:
        if not isinstance(product_id, UUID):
            raise AccessDenied("Access denied")
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = self._authorize(session, token, expected_actor, recent=False)
                product = session.scalar(
                    select(Product).where(
                        Product.tenant_id == actor.tenant_id, Product.id == product_id
                    )
                )
                if product is None:
                    raise CatalogDenied("Access denied")
                result = self._product_view(product).model_copy(
                    update={"actor_membership_id": actor.membership_id}
                )
            return result
        except SQLAlchemyError:
            raise CatalogUnavailable("Catalog unavailable") from None

    @staticmethod
    def _product_view(product: Product) -> ProductView:
        return ProductView(
            id=product.id,
            name=product.name,
            is_active=product.is_active,
            category_id=product.category_id,
            sku=product.sku,
            price=product.price,
        )
