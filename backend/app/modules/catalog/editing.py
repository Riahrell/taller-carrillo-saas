"""Audited product changes with optimistic concurrency, separate from stock."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.modules.access.authentication import _limit_transaction
from app.modules.catalog.inputs import ProductChange
from app.modules.catalog.models import Category, Product, ProductChangeRecord
from app.modules.catalog.service import (
    CatalogConflict,
    CatalogDenied,
    CatalogUnavailable,
)


class CatalogStale(Exception):
    """The product was changed since the administrator reviewed it."""


FIELDS = ("name", "sku", "category_id", "price", "is_active")


def update_product(
    service,
    token,
    *,
    product_id: UUID,
    data: ProductChange,
    expected_etag: str,
    expected_actor: UUID,
):
    if (
        type(data) is not ProductChange
        or not isinstance(product_id, UUID)
        or not isinstance(expected_etag, str)
    ):
        raise CatalogDenied("Access denied")
    data = ProductChange.model_validate(data.model_dump())
    try:
        with service._factory.begin() as session:
            _limit_transaction(session)
            actor = service._authorize(session, token, expected_actor)
            product = session.scalar(
                select(Product)
                .where(Product.tenant_id == actor.tenant_id, Product.id == product_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if product is None:
                raise CatalogDenied("Access denied")
            before = service._product_view(product)
            if before.etag != expected_etag:
                raise CatalogStale("Product changed")
            if data.category_id is not None:
                category = session.scalar(
                    select(Category)
                    .where(
                        Category.tenant_id == actor.tenant_id,
                        Category.id == data.category_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                # Retain an existing inactive category; do not newly assign one.
                if category is None or (
                    not category.is_active and category.id != product.category_id
                ):
                    raise CatalogDenied("Access denied")
            if any(getattr(product, field) != getattr(data, field) for field in FIELDS):
                evidence = ProductChangeRecord(
                    tenant_id=actor.tenant_id,
                    product_id=product.id,
                    actor_membership_id=actor.membership_id,
                    before_revision=product.revision,
                    after_revision=product.revision + 1,
                )
                for field in FIELDS:
                    column = "active" if field == "is_active" else field
                    setattr(evidence, "before_" + column, getattr(product, field))
                    setattr(evidence, "after_" + column, getattr(data, field))
                    setattr(product, field, getattr(data, field))
                product.revision += 1
                session.add(evidence)
                session.flush()
            service._authorize(session, token, expected_actor)
            result = service._product_view(product).model_copy(
                update={"actor_membership_id": actor.membership_id}
            )
        return result
    except IntegrityError as error:
        if (
            getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            == "uq_products_tenant_sku"
        ):
            raise CatalogConflict("SKU unavailable") from None
        raise CatalogUnavailable("Catalog unavailable") from None
    except SQLAlchemyError:
        raise CatalogUnavailable("Catalog unavailable") from None
