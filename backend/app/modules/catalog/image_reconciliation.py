"""Bounded cyclic reconciliation of durable DB jobs, including tombstones."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.access.authentication import _limit_transaction
from app.modules.access.sessions import _database_now
from app.modules.catalog.image_models import ProductImage, ProductImageHead
from app.modules.catalog.image_service import (
    RESERVATION_LIFETIME,
    ProductImageAdministrator,
    ProductImagesConflict,
    ProductImagesUnavailable,
)
from app.modules.catalog.image_storage import ImageStorageUnavailable


@dataclass(frozen=True)
class ReconciliationBatch:
    inspected: int
    deleted: int
    deferred: int
    cycle_complete: bool


class ImageReconciler:
    """Cursor is an optimization only; a restart safely replays durable jobs.

    Capture a finite UUID ceiling each cycle so continuous new reservations cannot
    prevent returning to older tombstones. One worker is recommended; multiple
    workers are safe through per-image advisory locks and idempotent deletes.
    """

    def __init__(self, service: ProductImageAdministrator):
        self._service = service
        self._cursor: UUID | None = None
        self._ceiling: UUID | None = None

    def run_batch(
        self, *, limit: int = 25, stopping=lambda: False
    ) -> ReconciliationBatch:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Batch size must be between 1 and 100")
        try:
            with self._service._factory.begin() as session:
                _limit_transaction(session)
                if self._ceiling is None:
                    self._ceiling = session.scalar(
                        select(ProductImage.id)
                        .order_by(ProductImage.id.desc())
                        .limit(1)
                    )
                if self._ceiling is None:
                    return ReconciliationBatch(0, 0, 0, True)
                filters = [
                    ProductImage.id <= self._ceiling,
                    or_(
                        ProductImageHead.image_id.is_(None),
                        ProductImageHead.image_id != ProductImage.id,
                    ),
                    or_(
                        ProductImage.state != "reserved",
                        ProductImage.created_at
                        <= _database_now(session) - RESERVATION_LIFETIME,
                    ),
                ]
                if self._cursor is not None:
                    filters.append(ProductImage.id > self._cursor)
                identifiers = tuple(
                    session.scalars(
                        select(ProductImage.id)
                        .outerjoin(
                            ProductImageHead,
                            and_(
                                ProductImageHead.tenant_id == ProductImage.tenant_id,
                                ProductImageHead.product_id == ProductImage.product_id,
                            ),
                        )
                        .where(*filters)
                        .order_by(ProductImage.id)
                        .limit(limit)
                    )
                )
        except SQLAlchemyError:
            raise ProductImagesUnavailable("Image reconciliation unavailable") from None
        deleted = deferred = inspected = 0
        for identifier in identifiers:
            if stopping():
                return ReconciliationBatch(inspected, deleted, deferred, False)
            try:
                deleted += bool(self._service.cleanup_one(identifier))
            except (
                ProductImagesConflict,
                ProductImagesUnavailable,
                ImageStorageUnavailable,
            ):
                deferred += 1
            inspected += 1
            self._cursor = identifier
        complete = len(identifiers) < limit
        if complete:
            self._cursor = self._ceiling = None
        return ReconciliationBatch(inspected, deleted, deferred, complete)
