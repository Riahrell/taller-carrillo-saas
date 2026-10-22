"""Immutable private WebP detail and thumbnail, retaining legacy PNG references."""

import sqlalchemy as sa
from alembic import op

revision = "0047_product_image_variants"
down_revision = "0046_inventory_withdrawal_grants"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "product_images",
        sa.Column(
            "content_type",
            sa.String(20),
            server_default=sa.text("'image/png'"),
            nullable=False,
        ),
    )
    for name, kind in (
        ("thumbnail_size", sa.Integer()),
        ("thumbnail_sha256", sa.String(64)),
        ("thumbnail_width", sa.Integer()),
        ("thumbnail_height", sa.Integer()),
    ):
        op.add_column("product_images", sa.Column(name, kind, nullable=True))
    op.drop_constraint(
        op.f("ck_product_images_key_source"), "product_images", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_product_images_key_source"),
        "product_images",
        "object_key = 'product-images/' || tenant_id::text || '/' || "
        "product_id::text || '/' || id::text || CASE WHEN content_type "
        "= 'image/png' THEN '.png' ELSE '.webp' END",
    )
    op.create_check_constraint(
        op.f("ck_product_images_content_type"),
        "product_images",
        "content_type IN ('image/png', 'image/webp')",
    )
    op.create_check_constraint(
        op.f("ck_product_images_thumbnail_metadata"),
        "product_images",
        "(content_type = 'image/png' AND thumbnail_size IS NULL AND "
        "thumbnail_sha256 IS NULL AND thumbnail_width IS NULL AND "
        "thumbnail_height IS NULL) OR (content_type = 'image/webp' AND "
        "thumbnail_size IS NOT NULL AND thumbnail_size BETWEEN 30 AND "
        "262144 AND thumbnail_sha256 IS NOT NULL AND thumbnail_sha256 ~"
        " '^[0-9a-f]{64}$' AND thumbnail_width IS NOT NULL AND "
        "thumbnail_width BETWEEN 1 AND 300 AND thumbnail_height IS NOT "
        "NULL AND thumbnail_height BETWEEN 1 AND 300)",
    )


def downgrade():
    op.execute("LOCK TABLE product_images IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM product_images WHERE "
            "content_type <> 'image/png')"
        )
    ):
        raise RuntimeError("WebP evidence exists; refusing destructive downgrade")
    for name in ("thumbnail_metadata", "content_type", "key_source"):
        op.drop_constraint(
            op.f("ck_product_images_" + name), "product_images", type_="check"
        )
    op.create_check_constraint(
        op.f("ck_product_images_key_source"),
        "product_images",
        "object_key = 'product-images/' || tenant_id::text || '/' || "
        "product_id::text || '/' || id::text || '.png'",
    )
    for name in (
        "thumbnail_height",
        "thumbnail_width",
        "thumbnail_sha256",
        "thumbnail_size",
        "content_type",
    ):
        op.drop_column("product_images", name)
