"""Email-bound invitation metadata; no authorization or transport dependencies."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmployeeInvitation(Base):
    __tablename__ = "employee_invitations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "email = lower(trim(email)) AND length(email) BETWEEN 3 AND 254",
            name="email_canonical",
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_shape"),
        CheckConstraint(
            "expires_at = created_at + interval '24 hours'", name="lifetime"
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at", name="revocation_window"
        ),
        Index(
            "uq_employee_invitations_open_email",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    email: Mapped[str] = mapped_column(String(254))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmployeeInvitationEvent(Base):
    __tablename__ = "employee_invitation_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "invitation_id"],
            ["employee_invitations.tenant_id", "employee_invitations.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("invitation_id", "action"),
        CheckConstraint("action IN ('CREATED', 'REVOKED')", name="action_allowed"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    invitation_id: Mapped[UUID] = mapped_column()
    actor_membership_id: Mapped[UUID] = mapped_column()
    action: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
