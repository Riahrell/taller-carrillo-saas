"""Access persistence. No credentials, initial users or privileged role defaults."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.database import Base


class MembershipRole(StrEnum):
    ADMIN = "admin"
    CASHIER = "cashier"
    MECHANIC = "mechanic"
    READ_ONLY = "read_only"


class AccessRecord:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Tenant(AccessRecord, Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    name: Mapped[str] = mapped_column(String(200))


class User(AccessRecord, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "email = lower(trim(email)) AND length(email) > 0",
            name="email_canonical",
        ),
        CheckConstraint(
            "length(trim(password_hash)) > 0", name="password_hash_not_blank"
        ),
    )

    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))

    @validates("email")
    def normalize_email(self, _key: str, value: str) -> str:
        # The HTTP input contract and password hashing are a separate delivery.
        return value.strip().lower()


class TenantMembership(AccessRecord, Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_memberships_tenant_id_id"),
        CheckConstraint(
            "role IN ('admin', 'cashier', 'mechanic', 'read_only')",
            name="role_allowed",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
