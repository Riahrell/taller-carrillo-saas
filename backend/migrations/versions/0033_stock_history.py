"""Bounded chronological keyset reads of a product's physical count history."""

from alembic import op

revision = "0033_stock_history"
down_revision = "0032_stock_counts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_stock_counts_history",
        "stock_counts",
        ["tenant_id", "product_id", "created_at", "id"],
    )


def downgrade():
    op.drop_index("ix_stock_counts_history", table_name="stock_counts")
