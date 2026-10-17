"""Access persistence. No credentials, initial users or privileged role defaults."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    false,
    func,
    text,
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
        CheckConstraint("security_revision >= 0", name="security_revision_bounds"),
        CheckConstraint(
            "email = lower(trim(email)) AND length(email) > 0",
            name="email_canonical",
        ),
        CheckConstraint(
            "length(trim(password_hash)) > 0", name="password_hash_not_blank"
        ),
    )

    security_revision: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    mfa_required: Mapped[bool] = mapped_column(Boolean, server_default=false())
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
        CheckConstraint(
            "(reauthenticated_at IS NULL AND reauthentication_fingerprint IS NULL) OR "
            "(reauthenticated_at IS NOT NULL "
            "AND reauthentication_fingerprint IS NOT NULL "
            "AND reauthenticated_at >= created_at AND reauthenticated_at < expires_at "
            "AND reauthentication_fingerprint ~ '^[0-9a-f]{64}$')",
            name="reauthentication_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    membership_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reauthenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reauthentication_fingerprint: Mapped[str | None] = mapped_column(String(64))


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


class MfaPending(Base):
    """Restricted browser-bound state; never a session or a verified MFA proof."""

    __tablename__ = "mfa_pendings"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('login_totp', 'enrollment')", name="purpose_allowed"
        ),
        CheckConstraint("security_revision >= 0", name="revision_bounds"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint("binding_hash ~ '^[0-9a-f]{64}$'", name="binding_hash_sha256"),
        CheckConstraint(
            "password_fingerprint ~ '^[0-9a-f]{64}$'",
            name="password_fingerprint_sha256",
        ),
        CheckConstraint(
            "created_at >= password_verified_at AND created_at < expires_at",
            name="creation_window",
        ),
        CheckConstraint(
            "expires_at = password_verified_at + CASE WHEN purpose = 'login_totp' "
            "THEN interval '5 minutes' ELSE interval '15 minutes' END",
            name="absolute_lifetime",
        ),
        CheckConstraint("finished_at >= created_at", name="finish_window"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))
    security_revision: Mapped[int] = mapped_column(BigInteger)
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    password_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Typed creation/change evidence; no arbitrary payloads or credentials."""

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
        CheckConstraint(
            "action IN ('membership_changed', 'membership_created')",
            name="action_allowed",
        ),
        CheckConstraint(
            "(before_role IS NULL OR before_role IN "
            "('admin', 'cashier', 'mechanic', 'read_only')) AND "
            "after_role IN ('admin', 'cashier', 'mechanic', 'read_only')",
            name="roles_allowed",
        ),
        CheckConstraint(
            "(action = 'membership_changed' AND before_role IS NOT NULL "
            "AND before_active IS NOT NULL AND "
            "(before_role <> after_role OR before_active <> after_active)) OR "
            "(action = 'membership_created' AND before_role IS NULL "
            "AND before_active IS NULL AND after_active IS TRUE)",
            name="state_changed",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    actor_membership_id: Mapped[UUID] = mapped_column()
    target_membership_id: Mapped[UUID] = mapped_column()
    action: Mapped[str] = mapped_column(String(32))
    before_role: Mapped[str | None] = mapped_column(String(16))
    after_role: Mapped[str] = mapped_column(String(16))
    before_active: Mapped[bool | None] = mapped_column(Boolean)
    after_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MfaCredential(Base):
    """Confirmed global credential; staged enrollment will live in typed pendings."""

    __tablename__ = "mfa_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        Index(
            "uq_mfa_credentials_current_user",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint("encryption_version = 1", name="encryption_version"),
        CheckConstraint("key_id ~ '^[A-Za-z0-9_-]{1,64}$'", name="key_id_format"),
        CheckConstraint("octet_length(nonce) = 12", name="nonce_length"),
        CheckConstraint("octet_length(ciphertext) = 36", name="ciphertext_length"),
        CheckConstraint("revoked_at >= created_at", name="revocation_window"),
        CheckConstraint(
            "(last_counter IS NULL AND last_verified_at IS NULL) OR "
            "(last_counter IS NOT NULL AND last_verified_at IS NOT NULL "
            "AND last_counter >= 0 AND last_verified_at >= created_at)",
            name="consumption_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    encryption_version: Mapped[int] = mapped_column(Integer)
    key_id: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_counter: Mapped[int | None] = mapped_column(BigInteger)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaRecoverySet(Base):
    __tablename__ = "mfa_recovery_sets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            ondelete="RESTRICT",
            name="fk_mfa_recovery_sets_credential_owner",
        ),
        Index(
            "uq_mfa_recovery_sets_current_credential",
            "credential_id",
            unique=True,
            postgresql_where=text("invalidated_at IS NULL"),
        ),
        CheckConstraint("invalidated_at >= created_at", name="invalidation_window"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column()
    credential_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (
        UniqueConstraint("set_id", "code_hash"),
        CheckConstraint("position >= 0 AND position < 10", name="position_bounds"),
        CheckConstraint("octet_length(code_hash) = 32", name="hash_length"),
        CheckConstraint(
            "used_at >= '1970-01-01 00:00:00+00'::timestamptz", name="use_window"
        ),
    )

    set_id: Mapped[UUID] = mapped_column(
        ForeignKey("mfa_recovery_sets.id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    """Global typed MFA consumption evidence; no arbitrary payload or tenant actor."""

    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint(
            "(action = 'security_revision_advanced' AND credential_id IS NULL "
            "AND security_revision IS NOT NULL AND security_revision > 0) OR "
            "(action IN ('mfa_totp_consumed', 'mfa_recovery_consumed') "
            "AND credential_id IS NOT NULL AND security_revision IS NULL)",
            name="action_state",
        ),
        ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            ondelete="RESTRICT",
            name="fk_security_events_credential_owner",
        ),
        CheckConstraint(
            "action IN ('mfa_totp_consumed', 'mfa_recovery_consumed', "
            "'security_revision_advanced')",
            name="action_allowed",
        ),
        CheckConstraint(
            "occurred_at >= '1970-01-01 00:00:00+00'::timestamptz",
            name="occurrence_window",
        ),
        Index("ix_security_events_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    credential_id: Mapped[UUID | None] = mapped_column()
    security_revision: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MfaThrottle(Base):
    """Independent shared failure quota; no password-success reset or raw IP."""

    __tablename__ = "mfa_throttles"
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
