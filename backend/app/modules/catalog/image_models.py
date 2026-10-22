"""Durable private image reservations, current pointer and append-only evidence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProductImage(Base):
    __tablename__ = "product_images"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "product_id", "id", name="uq_product_images_tenant_product_id"
        ),
        UniqueConstraint(
            "tenant_id", "operation_key", name="uq_product_images_tenant_operation"
        ),
        UniqueConstraint("object_key"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "previous_image_id"],
            [
                "product_images.tenant_id",
                "product_images.product_id",
                "product_images.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('reserved','attached','garbage','deleting','deleted')",
            name="state_allowed",
        ),
        CheckConstraint("size > 0 AND size <= 12582912", name="size_bounds"),
        CheckConstraint(
            "width BETWEEN 1 AND 1600 AND height BETWEEN 1 AND 1600", name="dimensions"
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND input_sha256 ~ '^[0-9a-f]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "object_key = 'product-images/' || tenant_id::text || '/' || "
            "product_id::text || '/' || id::text || "
            "CASE WHEN content_type = 'image/png' THEN '.png' ELSE '.webp' END",
            name="key_source",
        ),
        CheckConstraint(
            "previous_image_id IS NULL OR previous_image_id <> id",
            name="previous_distinct",
        ),
        CheckConstraint(
            "content_type IN ('image/png', 'image/webp')", name="content_type"
        ),
        CheckConstraint(
            "(content_type = 'image/png' AND thumbnail_size IS NULL AND"
            " thumbnail_sha256 IS NULL AND thumbnail_width IS NULL AND "
            "thumbnail_height IS NULL) OR "
            "(content_type = 'image/webp' AND thumbnail_size IS NOT "
            "NULL AND thumbnail_size BETWEEN 30 AND 262144 AND "
            "thumbnail_sha256 IS NOT NULL AND thumbnail_sha256 ~ "
            "'^[0-9a-f]{64}$' AND thumbnail_width IS NOT NULL AND "
            "thumbnail_width BETWEEN 1 AND 300 AND thumbnail_height IS "
            "NOT NULL AND thumbnail_height BETWEEN 1 AND 300)",
            name="thumbnail_metadata",
        ),
        Index("ix_product_images_cleanup", "state", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    operation_key: Mapped[UUID] = mapped_column()
    previous_image_id: Mapped[UUID | None] = mapped_column()
    object_key: Mapped[str] = mapped_column(String(200))
    input_sha256: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(
        String(20), server_default=text("'image/png'")
    )
    thumbnail_size: Mapped[int | None] = mapped_column(Integer)
    thumbnail_sha256: Mapped[str | None] = mapped_column(String(64))
    thumbnail_width: Mapped[int | None] = mapped_column(Integer)
    thumbnail_height: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProductImageHead(Base):
    __tablename__ = "product_image_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "image_id"],
            [
                "product_images.tenant_id",
                "product_images.product_id",
                "product_images.id",
            ],
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    product_id: Mapped[UUID] = mapped_column(primary_key=True)
    image_id: Mapped[UUID | None] = mapped_column()


class ProductImageEvent(Base):
    __tablename__ = "product_image_events"
    __table_args__ = (
        UniqueConstraint("image_id"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "image_id"],
            [
                "product_images.tenant_id",
                "product_images.product_id",
                "product_images.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "previous_image_id"],
            [
                "product_images.tenant_id",
                "product_images.product_id",
                "product_images.id",
            ],
            ondelete="RESTRICT",
            name="fk_product_image_events_previous",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    image_id: Mapped[UUID] = mapped_column()
    previous_image_id: Mapped[UUID | None] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
