"""Access persistence. No credentials, initial users or privileged role defaults."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
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


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sessions_tenant_membership",
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint(
            "expires_at = created_at + interval '8 hours'", name="absolute_lifetime"
        ),
        CheckConstraint(
            "last_seen_at >= created_at AND last_seen_at < expires_at",
            name="activity_window",
        ),
        CheckConstraint("revoked_at >= created_at", name="revocation_window"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    membership_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    __table_args__ = (
        CheckConstraint("scope IN ('account', 'ip')", name="scope_allowed"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_sha256"),
        CheckConstraint(
            "failures >= 0 AND failures <= "
            "CASE WHEN scope = 'account' THEN 5 ELSE 30 END",
            name="failure_bounds",
        ),
    )

    scope: Mapped[str] = mapped_column(String(7), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    failures: Mapped[int] = mapped_column(Integer)


class LoginChallenge(Base):
    """Short-lived verified identity; never an authenticated tenant session."""

    __tablename__ = "login_challenges"
    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint("binding_hash ~ '^[0-9a-f]{64}$'", name="binding_hash_sha256"),
        CheckConstraint(
            "password_fingerprint ~ '^[0-9a-f]{64}$'",
            name="password_fingerprint_sha256",
        ),
        CheckConstraint(
            "expires_at = created_at + interval '5 minutes'", name="absolute_lifetime"
        ),
        CheckConstraint("finished_at >= created_at", name="finish_window"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Typed membership-change evidence; no arbitrary payloads or credentials."""

    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_actor_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_target_membership",
        ),
        CheckConstraint("action = 'membership_changed'", name="action_allowed"),
        CheckConstraint(
            "before_role IN ('admin', 'cashier', 'mechanic', 'read_only') AND "
            "after_role IN ('admin', 'cashier', 'mechanic', 'read_only')",
            name="roles_allowed",
        ),
        CheckConstraint(
            "before_role <> after_role OR before_active <> after_active",
            name="state_changed",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    actor_membership_id: Mapped[UUID] = mapped_column()
    target_membership_id: Mapped[UUID] = mapped_column()
    action: Mapped[str] = mapped_column(String(32))
    before_role: Mapped[str] = mapped_column(String(16))
    after_role: Mapped[str] = mapped_column(String(16))
    before_active: Mapped[bool] = mapped_column(Boolean)
    after_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
