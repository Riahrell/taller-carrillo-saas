"""Pending employee mailbox proof and immutable approval evidence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmployeeRegistration(Base):
    __tablename__ = "employee_registrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "invitation_id"],
            ["employee_invitations.tenant_id", "employee_invitations.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$' AND binding_hash ~ '^[0-9a-f]{64}$'",
            name="opaque_hashes",
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "interval '10 minutes'",
            name="lifetime",
        ),
        CheckConstraint("failures BETWEEN 0 AND 5", name="attempts"),
        CheckConstraint(
            "send_state IN ('none','pending','claimed','sent','failed')",
            name="send_state",
        ),
        CheckConstraint(
            "(key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL) OR "
            "(key_id IS NOT NULL AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL)",
            name="envelope",
        ),
        CheckConstraint(
            "(verified_at IS NULL AND user_id IS NULL AND password_hash IS NOT NULL) "
            "OR (verified_at IS NOT NULL AND verified_at >= created_at AND "
            "verified_at < expires_at AND user_id IS NOT NULL AND "
            "password_hash IS NULL AND code_hash IS NULL AND ciphertext IS NULL)",
            name="verified_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    invitation_id: Mapped[UUID] = mapped_column(unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    code_hash: Mapped[str | None] = mapped_column(String(64))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    send_state: Mapped[str] = mapped_column(String(8), default="none")
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)


class EmployeeApproval(Base):
    __tablename__ = "employee_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "registration_id"],
            ["employee_registrations.tenant_id", "employee_registrations.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_employee_approvals_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_employee_approvals_actor",
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    registration_id: Mapped[UUID] = mapped_column(unique=True)
    membership_id: Mapped[UUID] = mapped_column(unique=True)
    actor_membership_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
