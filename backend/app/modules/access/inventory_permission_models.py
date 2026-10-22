"""Append-only explicit inventory consultation grants for read-only memberships."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InventoryReadGrant(Base):
    __tablename__ = "inventory_read_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "membership_id", "revision"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_inventory_read_grants_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_inventory_read_grants_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint("before_granted <> granted", name="actual_change"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    membership_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(BigInteger)
    before_granted: Mapped[bool] = mapped_column(Boolean)
    granted: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
