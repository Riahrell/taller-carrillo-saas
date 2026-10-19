"""Private image reservations, tenant-bound pointer and append-only attachments."""

import sqlalchemy as sa
from alembic import op

revision = "0034_product_images"
down_revision = "0033_stock_history"
branch_labels = None
depends_on = None


def _uuid(name, nullable=False, primary_key=False):
    return sa.Column(name, sa.Uuid(), nullable=nullable, primary_key=primary_key)


def _image_fk(column, name=None):
    return sa.ForeignKeyConstraint(
        ["tenant_id", "product_id", column],
        ["product_images.tenant_id", "product_images.product_id", "product_images.id"],
        ondelete="RESTRICT",
        name=name,
    )


def _actor_fk():
    return sa.ForeignKeyConstraint(
        ["tenant_id", "actor_membership_id"],
        ["tenant_memberships.tenant_id", "tenant_memberships.id"],
        ondelete="RESTRICT",
    )


def _created():
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def upgrade():
    op.create_table(
        "product_images",
        _uuid("id", primary_key=True),
        _uuid("tenant_id"),
        _uuid("product_id"),
        _uuid("actor_membership_id"),
        _uuid("operation_key"),
        _uuid("previous_image_id", nullable=True),
        sa.Column("object_key", sa.String(200), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        _created(),
        sa.UniqueConstraint(
            "tenant_id", "product_id", "id", name="uq_product_images_tenant_product_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "operation_key", name="uq_product_images_tenant_operation"
        ),
        sa.UniqueConstraint("object_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
        ),
        _actor_fk(),
        _image_fk("previous_image_id"),
        sa.CheckConstraint(
            "state IN ('reserved','attached','garbage','deleting','deleted')",
            name=op.f("ck_product_images_state_allowed"),
        ),
        sa.CheckConstraint(
            "size > 0 AND size <= 12582912", name=op.f("ck_product_images_size_bounds")
        ),
        sa.CheckConstraint(
            "width BETWEEN 1 AND 1600 AND height BETWEEN 1 AND 1600",
            name=op.f("ck_product_images_dimensions"),
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND input_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_product_images_hashes"),
        ),
        sa.CheckConstraint(
            "object_key = 'product-images/' || tenant_id::text || '/' || "
            "product_id::text || '/' || id::text || '.png'",
            name=op.f("ck_product_images_key_source"),
        ),
        sa.CheckConstraint(
            "previous_image_id IS NULL OR previous_image_id <> id",
            name=op.f("ck_product_images_previous_distinct"),
        ),
    )
    op.create_index(
        "ix_product_images_cleanup", "product_images", ["state", "created_at", "id"]
    )
    op.create_table(
        "product_image_heads",
        _uuid("tenant_id", primary_key=True),
        _uuid("product_id", primary_key=True),
        _uuid("image_id", nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
        ),
        _image_fk("image_id"),
    )
    op.create_table(
        "product_image_events",
        _uuid("id", primary_key=True),
        _uuid("tenant_id"),
        _uuid("product_id"),
        _uuid("image_id"),
        _uuid("previous_image_id", nullable=True),
        _uuid("actor_membership_id"),
        _created(),
        sa.UniqueConstraint("image_id"),
        _image_fk("image_id"),
        _image_fk("previous_image_id", name="fk_product_image_events_previous"),
        _actor_fk(),
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON product_image_events, product_images, "
        "product_image_heads FROM carrillo_app"
    )
    op.execute("GRANT UPDATE (state) ON product_images TO carrillo_app")
    op.execute("GRANT UPDATE (image_id) ON product_image_heads TO carrillo_app")


def downgrade():
    op.execute(
        "LOCK TABLE product_image_events, product_image_heads, product_images "
        "IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM product_images) OR EXISTS "
            "(SELECT 1 FROM product_image_heads) OR EXISTS "
            "(SELECT 1 FROM product_image_events)"
        )
    ):
        raise RuntimeError("Image evidence exists; refusing destructive downgrade")
    op.drop_table("product_image_events")
    op.drop_table("product_image_heads")
    op.drop_table("product_images")
