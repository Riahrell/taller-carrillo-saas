"""Authorized image lifecycle; S3 and decoding never hold tenant transactions."""

import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.administrator_guard import authorize_administrator
from app.modules.access.authentication import _limit_transaction
from app.modules.access.membership import AccessDenied
from app.modules.access.models import Tenant
from app.modules.access.sessions import _database_now
from app.modules.catalog.image_models import (
    ProductImage,
    ProductImageEvent,
    ProductImageHead,
)
from app.modules.catalog.image_processing import process_product_image
from app.modules.catalog.image_storage import ImageStorageConflict, StoredProductImage
from app.modules.catalog.images import (
    MAX_INPUT_BYTES,
    InvalidProductImage,
    PreparedProductImage,
)
from app.modules.catalog.models import Product

RESERVATION_LIFETIME = timedelta(hours=1)
MAX_PENDING_PER_PRODUCT = 3
MAX_TENANT_BYTES = 1024**3


class ProductImagesConflict(RuntimeError):
    """Operation differs, image changed, expired reservation or concurrent work."""


class ProductImagesUnavailable(RuntimeError):
    """Database failure; an uncertain commit requires replay with the same key."""


class ImageTransport(Protocol):
    def allocate(
        self, tenant_id: UUID, product_id: UUID, image: PreparedProductImage
    ) -> StoredProductImage: ...
    def put(
        self, reference: StoredProductImage, image: PreparedProductImage
    ) -> None: ...
    def read(self, reference: StoredProductImage) -> bytes: ...
    def delete_unreferenced(self, key: str) -> None: ...


class ProductImageView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    product_id: UUID
    width: int
    height: int
    size: int
    created_at: datetime
    is_current: bool
    actor_membership_id: UUID = Field(exclude=True, repr=False)


def _reference(image: ProductImage) -> StoredProductImage:
    return StoredProductImage(image.object_key, image.size, image.sha256)


def _view(image: ProductImage, current: UUID | None, actor: UUID) -> ProductImageView:
    return ProductImageView(
        id=image.id,
        product_id=image.product_id,
        width=image.width,
        height=image.height,
        size=image.size,
        created_at=image.created_at,
        is_current=current == image.id,
        actor_membership_id=actor,
    )


class ProductImageAdministrator:
    def __init__(self, engine: Engine, storage: ImageTransport):
        self._engine = engine
        self._factory = create_session_factory(engine)
        self._storage = storage

    @contextmanager
    def _authorized(self, token, expected_actor, product_id, *, recent=True):
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor = authorize_administrator(
                    session, token, expected_actor, recent=recent
                )
                product = session.scalar(
                    select(Product)
                    .where(
                        Product.id == product_id, Product.tenant_id == actor.tenant_id
                    )
                    .with_for_update()
                )
                if product is None or recent and not product.is_active:
                    raise AccessDenied("Access denied")
                yield session, actor
                authorize_administrator(session, token, expected_actor, recent=recent)
        except SQLAlchemyError:
            raise ProductImagesUnavailable("Images unavailable") from None

    @contextmanager
    def _exclusive(self, image_id: UUID):
        # Session advisory lock spans network work without an open transaction or
        # stock/tenant row locks. Never return a locked connection to the pool.
        key = int.from_bytes(hashlib.sha256(image_id.bytes).digest()[:8], signed=True)
        try:
            with self._engine.connect() as connection:
                try:
                    acquired = connection.scalar(
                        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                    )
                    connection.commit()
                except SQLAlchemyError:
                    connection.invalidate()
                    raise
                if not acquired:
                    raise ProductImagesConflict("Image operation in progress")
                try:
                    yield
                finally:
                    try:
                        connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                        )
                        connection.commit()
                    except SQLAlchemyError:
                        connection.invalidate()
                        raise
        except SQLAlchemyError:
            raise ProductImagesUnavailable("Images unavailable") from None

    @staticmethod
    def _head(session, tenant, product):
        return session.get(ProductImageHead, (tenant, product))

    @staticmethod
    def _check_replay(image, product_id, expected_actor, fingerprint):
        if image.product_id != product_id or image.input_sha256 != fingerprint:
            raise ProductImagesConflict("Operation content differs")
        if image.actor_membership_id != expected_actor:
            raise AccessDenied("Access denied")
        if image.state in ("garbage", "deleting", "deleted"):
            # Completed attachment remains replayable as evidence after cleanup.
            return

    def replace(
        self,
        token: SecretStr,
        *,
        expected_actor: UUID,
        product_id: UUID,
        operation_key: UUID,
        content: bytes,
    ) -> ProductImageView:
        if not all(
            isinstance(value, UUID)
            for value in (expected_actor, product_id, operation_key)
        ):
            raise AccessDenied("Access denied")
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_INPUT_BYTES:
            raise InvalidProductImage("Tamaño de archivo no permitido.")
        fingerprint = hashlib.sha256(content).hexdigest()
        with self._authorized(token, expected_actor, product_id) as (session, actor):
            existing = session.scalar(
                select(ProductImage).where(
                    ProductImage.tenant_id == actor.tenant_id,
                    ProductImage.operation_key == operation_key,
                )
            )
            if existing:
                self._check_replay(existing, product_id, expected_actor, fingerprint)
                if session.scalar(
                    select(ProductImageEvent.id).where(
                        ProductImageEvent.image_id == existing.id
                    )
                ):
                    head = self._head(session, actor.tenant_id, product_id)
                    return _view(
                        existing, head.image_id if head else None, expected_actor
                    )
        prepared = process_product_image(content)
        with self._authorized(token, expected_actor, product_id) as (session, actor):
            image = session.scalar(
                select(ProductImage).where(
                    ProductImage.tenant_id == actor.tenant_id,
                    ProductImage.operation_key == operation_key,
                )
            )
            head = self._head(session, actor.tenant_id, product_id)
            if image:
                self._check_replay(image, product_id, expected_actor, fingerprint)
            else:
                pending = session.scalar(
                    select(func.count())
                    .select_from(ProductImage)
                    .where(
                        ProductImage.tenant_id == actor.tenant_id,
                        ProductImage.product_id == product_id,
                        ProductImage.state == "reserved",
                    )
                )
                used = session.scalar(
                    select(func.coalesce(func.sum(ProductImage.size), 0)).where(
                        ProductImage.tenant_id == actor.tenant_id,
                        ProductImage.state != "deleted",
                    )
                )
                if (
                    pending >= MAX_PENDING_PER_PRODUCT
                    or used + len(prepared.content) > MAX_TENANT_BYTES
                ):
                    raise ProductImagesConflict("Image capacity exhausted")
                reference = self._storage.allocate(
                    actor.tenant_id, product_id, prepared
                )
                image = ProductImage(
                    id=UUID(reference.key.rsplit("/", 1)[1][:-4]),
                    tenant_id=actor.tenant_id,
                    product_id=product_id,
                    actor_membership_id=expected_actor,
                    operation_key=operation_key,
                    previous_image_id=head.image_id if head else None,
                    object_key=reference.key,
                    input_sha256=fingerprint,
                    sha256=prepared.sha256,
                    size=len(prepared.content),
                    width=prepared.width,
                    height=prepared.height,
                    state="reserved",
                    created_at=_database_now(session),
                )
                session.add(image)
                session.flush()
                if head is None:
                    session.add(
                        ProductImageHead(
                            tenant_id=actor.tenant_id, product_id=product_id
                        )
                    )
            image_id = image.id
        with self._exclusive(image_id):
            with self._authorized(token, expected_actor, product_id) as (
                session,
                actor,
            ):
                image = session.get(ProductImage, image_id)
                event = session.scalar(
                    select(ProductImageEvent.id).where(
                        ProductImageEvent.image_id == image_id
                    )
                )
                head = self._head(session, actor.tenant_id, product_id)
                if event:
                    return _view(image, head.image_id, expected_actor)
                if (
                    image.state != "reserved"
                    or image.created_at + RESERVATION_LIFETIME <= _database_now(session)
                ):
                    raise ProductImagesConflict("Image reservation unavailable")
                reference = _reference(image)
            # An ambiguous PUT leaves the durable reference for explicit replay.
            try:
                self._storage.put(reference, prepared)
            except ImageStorageConflict:
                self._storage.read(reference)
            conflict = False
            with self._authorized(token, expected_actor, product_id) as (
                session,
                actor,
            ):
                image = session.get(ProductImage, image_id)
                head = self._head(session, actor.tenant_id, product_id)
                if (
                    image.state != "reserved"
                    or head.image_id != image.previous_image_id
                    or image.created_at + RESERVATION_LIFETIME <= _database_now(session)
                ):
                    image.state = "garbage"
                    conflict = True
                else:
                    head.image_id = image.id
                    image.state = "attached"
                    session.add(
                        ProductImageEvent(
                            tenant_id=actor.tenant_id,
                            product_id=product_id,
                            image_id=image.id,
                            previous_image_id=image.previous_image_id,
                            actor_membership_id=expected_actor,
                            created_at=_database_now(session),
                        )
                    )
                    session.flush()
                    result = _view(image, head.image_id, expected_actor)
            if conflict:
                raise ProductImagesConflict(
                    "Product image changed; review before replacing"
                )
            return result

    def read(
        self, token: SecretStr, *, expected_actor: UUID, product_id: UUID
    ) -> tuple[ProductImageView, bytes] | None:
        if not isinstance(expected_actor, UUID) or not isinstance(product_id, UUID):
            raise AccessDenied("Access denied")
        with self._authorized(token, expected_actor, product_id, recent=False) as (
            session,
            actor,
        ):
            head = self._head(session, actor.tenant_id, product_id)
            if head is None or head.image_id is None:
                return None
            image_id = head.image_id
            image = session.get(ProductImage, image_id)
            reference = _reference(image)
        content = self._storage.read(reference)
        with self._authorized(token, expected_actor, product_id, recent=False) as (
            session,
            actor,
        ):
            head = self._head(session, actor.tenant_id, product_id)
            if head.image_id != image_id:
                raise ProductImagesConflict("Product image changed")
            return _view(
                session.get(ProductImage, image_id), head.image_id, expected_actor
            ), content

    def cleanup_one(self, image_id: UUID) -> bool:
        """Trusted worker only. Revisit tombstones to catch late uncertain PUTs."""
        try:
            with self._exclusive(image_id):
                with self._factory.begin() as session:
                    _limit_transaction(session)
                    image = session.get(ProductImage, image_id)
                    if image is None:
                        return False
                    session.scalar(
                        select(Tenant)
                        .where(Tenant.id == image.tenant_id)
                        .with_for_update()
                    )
                    session.refresh(image)
                    head = self._head(session, image.tenant_id, image.product_id)
                    if head and head.image_id == image.id:
                        return False
                    if (
                        image.state == "reserved"
                        and image.created_at + RESERVATION_LIFETIME
                        > _database_now(session)
                    ):
                        return False
                    image.state = "deleting"
                    reference = _reference(image)
                self._storage.delete_unreferenced(reference.key)
                with self._factory.begin() as session:
                    _limit_transaction(session)
                    image = session.get(ProductImage, image_id)
                    image.state = "deleted"
                return True
        except SQLAlchemyError:
            raise ProductImagesUnavailable("Images unavailable") from None
