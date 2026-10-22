"""Access persistence. No credentials, initial users or privileged role defaults."""

from datetime import datetime
from decimal import Decimal
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
    Numeric,
    String,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.database import Base
from app.modules.catalog import models as catalog_models  # noqa: F401 -- audit FK targets
from app.modules.inventory import models as inventory_models  # noqa: F401 -- stock FK targets


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
        CheckConstraint(
            "mfa_pending_id IS NULL OR email_login_id IS NULL",
            name="single_login_method",
        ),
        CheckConstraint(
            (
                "(mfa_reauthenticated_at IS NULL AND "
                "mfa_reauthentication_credential_id IS NULL) OR "
                "(mfa_reauthenticated_at IS NOT NULL AND "
                "mfa_reauthentication_credential_id IS NOT NULL AND "
                "mfa_pending_id IS NOT NULL AND reauthenticated_at IS NOT "
                "NULL AND reauthenticated_at <= mfa_reauthenticated_at AND "
                "mfa_reauthenticated_at < expires_at)"
            ),
            name="mfa_reauthentication_state",
        ),
        CheckConstraint("security_revision >= 0", name="security_revision_bounds"),
        CheckConstraint(
            "mfa_pending_id IS NULL OR security_revision IS NOT NULL",
            name="mfa_revision_present",
        ),
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
    security_revision: Mapped[int | None] = mapped_column(BigInteger)
    mfa_pending_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mfa_pendings.id", ondelete="RESTRICT"), unique=True
    )
    email_login_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("email_login_challenges.id", ondelete="RESTRICT"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_reauthenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    mfa_reauthentication_credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mfa_credentials.id", ondelete="RESTRICT")
    )
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
    """Browser-bound authentication stages, never a tenant session."""

    __tablename__ = "mfa_pendings"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_mfa_pendings_user_id_id"),
        CheckConstraint(
            "purpose IN ('login_totp', 'enrollment', 'login_verified')",
            name="purpose_allowed",
        ),
        ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            ondelete="RESTRICT",
            name="fk_mfa_pendings_credential_owner",
        ),
        CheckConstraint(
            "(purpose = 'login_verified' AND credential_id IS NOT NULL "
            "AND factor_verified_at IS NOT NULL "
            "AND factor_verified_at >= password_verified_at "
            "AND factor_verified_at <= created_at) OR "
            "(purpose IN ('login_totp', 'enrollment') AND credential_id IS NULL "
            "AND factor_verified_at IS NULL)",
            name="factor_state",
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
            "expires_at = password_verified_at + CASE WHEN purpose = 'enrollment' "
            "THEN interval '15 minutes' ELSE interval '5 minutes' END",
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
    credential_id: Mapped[UUID | None] = mapped_column()
    factor_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    password_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailboxChallenge(Base):
    """Fresh proof of exact mailbox, bound to an already authenticated session."""

    __tablename__ = "mailbox_challenges"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_mailbox_challenges_user_id_id"),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$' AND email_fingerprint ~ '^[0-9a-f]{64}$'",
            name="hashes_sha256",
        ),
        CheckConstraint("security_revision >= 0", name="revision_bounds"),
        CheckConstraint(
            (
                "expires_at > created_at AND expires_at <= created_at + "
                "interval '15 minutes'"
            ),
            name="lifetime_bounds",
        ),
        CheckConstraint(
            (
                "(closed_at IS NULL OR closed_at >= created_at) AND "
                "(consumed_at IS NULL OR (closed_at IS NOT NULL AND "
                "consumed_at = closed_at AND consumed_at >= created_at AND "
                "consumed_at < expires_at))"
            ),
            name="closure_state",
        ),
        CheckConstraint(
            (
                "(discarded_at IS NULL AND key_id IS NOT NULL AND key_id ~ "
                "'^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
                "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
                "octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
                "(discarded_at IS NOT NULL AND discarded_at >= created_at "
                "AND key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL)"
            ),
            name="payload_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email_fingerprint: Mapped[str] = mapped_column(String(64))
    security_revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)


class IdentityMailDelivery(Base):
    """Leases for reset/general-verification/notices; message identity is stable."""

    __tablename__ = "identity_mail_deliveries"
    __table_args__ = (
        UniqueConstraint("kind", "source_id", name="uq_identity_mail_source"),
        CheckConstraint("kind IN ('reset','mailbox','notice')", name="kind_allowed"),
        CheckConstraint(
            (
                "state IN ('pending','leased','accepted','uncertain','rejecte"
                "d','cancelled')"
            ),
            name="state_allowed",
        ),
        CheckConstraint("attempts >= 1 AND attempts <= 3", name="attempt_bounds"),
        CheckConstraint(
            (
                "(state = 'leased' AND claim_id IS NOT NULL AND "
                "lease_expires_at IS NOT NULL) OR (state <> 'leased' AND "
                "claim_id IS NULL AND lease_expires_at IS NULL)"
            ),
            name="lease_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(7))
    source_id: Mapped[UUID]
    state: Mapped[str] = mapped_column(String(9))
    attempts: Mapped[int] = mapped_column(Integer)
    claim_id: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecurityNotice(Base):
    """Notification by event reference; no bearer token or recipient copy."""

    __tablename__ = "security_notices"
    __table_args__ = (
        CheckConstraint("address_hash ~ '^[0-9a-f]{64}$'", name="address_hash_sha256"),
        CheckConstraint(
            "expires_at = created_at + interval '24 hours'", name="absolute_lifetime"
        ),
        CheckConstraint(
            "state IN ('pending', 'accepted', 'uncertain', 'rejected', 'expired')",
            name="state_allowed",
        ),
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("security_events.id", ondelete="RESTRICT"), primary_key=True
    )
    address_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(9), server_default=text("'pending'"))


class MfaRotation(Base):
    """Voluntary replacement bound to the current session's recent combined proof."""

    __tablename__ = "mfa_rotations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            name="fk_rotation_current_factor_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$' AND session_hash ~ '^[0-9a-f]{64}$'",
            name="hashes_sha256",
        ),
        CheckConstraint("security_revision >= 0", name="revision_bounds"),
        CheckConstraint(
            (
                "password_verified_at <= factor_verified_at AND "
                "factor_verified_at <= created_at AND created_at < "
                "expires_at AND expires_at <= password_verified_at + "
                "interval '5 minutes'"
            ),
            name="proof_lifetime",
        ),
        CheckConstraint(
            (
                "(closed_at IS NULL OR closed_at >= created_at) AND "
                "(activated_at IS NULL OR (closed_at IS NOT NULL AND "
                "activated_at=closed_at AND activated_at<expires_at))"
            ),
            name="closure_state",
        ),
        CheckConstraint(
            (
                "(closed_at IS NULL AND key_id IS NOT NULL AND key_id ~ "
                "'^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
                "octet_length(nonce)=12 AND ciphertext IS NOT NULL AND "
                "octet_length(ciphertext)=36) OR (closed_at IS NOT NULL AND "
                "key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL)"
            ),
            name="payload_state",
        ),
        Index(
            "uq_mfa_rotations_open_user",
            "user_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT")
    )
    credential_id: Mapped[UUID]
    session_hash: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    security_revision: Mapped[int] = mapped_column(BigInteger)
    password_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    factor_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)


class ResetRequestInbox(Base):
    """Blind encrypted request. No user discovery in the public request path."""

    __tablename__ = "reset_request_inbox"
    __table_args__ = (
        CheckConstraint(
            "expires_at = created_at + interval '15 minutes'", name="absolute_lifetime"
        ),
        CheckConstraint(
            "claimed_at IS NULL OR claimed_at >= created_at", name="claim_window"
        ),
        CheckConstraint(
            (
                "(finished_at IS NULL AND key_id IS NOT NULL AND nonce IS "
                "NOT NULL AND octet_length(nonce)=12 AND ciphertext IS NOT "
                "NULL AND octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
                "(finished_at IS NOT NULL AND finished_at >= created_at AND "
                "key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL)"
            ),
            name="payload_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)


class PasswordResetChallenge(Base):
    """Purpose-specific reset authority and encrypted staging; no sending yet."""

    __tablename__ = "password_reset_challenges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "verification_id"],
            ["verified_mailboxes.user_id", "verified_mailboxes.id"],
            ondelete="RESTRICT",
            name="fk_password_reset_verified_owner",
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint(
            "password_fingerprint ~ '^[0-9a-f]{64}$'",
            name="password_fingerprint_sha256",
        ),
        CheckConstraint("security_revision >= 0", name="revision_bounds"),
        CheckConstraint(
            "expires_at = created_at + interval '15 minutes'", name="absolute_lifetime"
        ),
        CheckConstraint(
            "(closed_at IS NULL OR closed_at >= created_at) AND "
            "(consumed_at IS NULL OR (closed_at IS NOT NULL AND consumed_at = "
            "closed_at AND consumed_at >= created_at AND consumed_at < expires_at)) "
            "AND (closed_at IS NULL OR discarded_at IS NOT NULL)",
            name="closure_state",
        ),
        CheckConstraint(
            "(discarded_at IS NULL AND key_id IS NOT NULL AND "
            "key_id ~ '^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
            "octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
            "(discarded_at IS NOT NULL AND discarded_at >= created_at AND "
            "key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL)",
            name="payload_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(index=True)
    verification_id: Mapped[UUID]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    security_revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetThrottle(Base):
    """All reset requests, including unknown addresses; independent from login."""

    __tablename__ = "password_reset_throttles"
    __table_args__ = (
        CheckConstraint("scope IN ('address', 'ip')", name="scope_allowed"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_sha256"),
        CheckConstraint(
            "requested >= 0 AND requested <= "
            "CASE WHEN scope = 'address' THEN 3 ELSE 20 END",
            name="requested_bounds",
        ),
        CheckConstraint(
            "(requested = 0 AND last_requested_at IS NULL) OR "
            "(requested > 0 AND last_requested_at IS NOT NULL AND "
            "last_requested_at >= window_started_at)",
            name="request_state",
        ),
    )

    scope: Mapped[str] = mapped_column(String(7), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested: Mapped[int] = mapped_column(Integer)
    last_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerifiedMailbox(Base):
    """Durable exact-address evidence, not a reset token or an MFA permission."""

    __tablename__ = "verified_mailboxes"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_verified_mailboxes_user_id_id"),
        ForeignKeyConstraint(
            ["user_id", "general_challenge_id"],
            ["mailbox_challenges.user_id", "mailbox_challenges.id"],
            name="fk_verified_mailbox_general_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "num_nonnulls(challenge_id, general_challenge_id, "
                "email_login_challenge_id) = 1"
            ),
            name="one_source",
        ),
        ForeignKeyConstraint(
            ["user_id", "challenge_id"],
            ["enrollment_email_challenges.user_id", "enrollment_email_challenges.id"],
            name="fk_verified_mailbox_challenge_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("address_hash ~ '^[0-9a-f]{64}$'", name="address_hash_sha256"),
        CheckConstraint(
            "verified_at >= '1970-01-01 00:00:00+00'::timestamptz AND "
            "(invalidated_at IS NULL OR invalidated_at >= verified_at)",
            name="verification_window",
        ),
        Index(
            "uq_verified_mailboxes_current_user",
            "user_id",
            unique=True,
            postgresql_where=text("invalidated_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID]
    challenge_id: Mapped[UUID | None] = mapped_column(unique=True)
    general_challenge_id: Mapped[UUID | None] = mapped_column(unique=True)
    email_login_challenge_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("email_login_challenges.id", ondelete="RESTRICT"), unique=True
    )
    address_hash: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnrollmentEmailChallenge(Base):
    """Initial MFA mailbox proof only, never password reset or an auth session."""

    __tablename__ = "enrollment_email_challenges"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_enrollment_mail_user_id_id"),
        ForeignKeyConstraint(
            ["user_id", "pending_id"],
            ["mfa_pendings.user_id", "mfa_pendings.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_email_challenges_pending_owner",
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint(
            "email_fingerprint ~ '^[0-9a-f]{64}$'", name="email_fingerprint_sha256"
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval "
            "'15 minutes'",
            name="lifetime_bounds",
        ),
        CheckConstraint(
            "closed_at >= created_at AND (consumed_at IS NULL OR closed_at "
            ">= consumed_at)",
            name="closure_window",
        ),
        CheckConstraint(
            "(consumed_at IS NULL AND permit_token_hash IS NULL AND "
            "permit_used_at IS NULL) OR (consumed_at IS NOT NULL AND "
            "consumed_at >= created_at AND consumed_at < expires_at AND "
            "permit_token_hash IS NOT NULL AND permit_token_hash ~ "
            "'^[0-9a-f]{64}$' AND (permit_used_at IS NULL OR (permit_used_at "
            ">= consumed_at AND permit_used_at < expires_at)))",
            name="proof_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(index=True)
    pending_id: Mapped[UUID] = mapped_column(unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permit_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    permit_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaRecoveryPermit(Base):
    """Password + consumed offline code; never a business-session proof."""

    __tablename__ = "mfa_recovery_permits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "pending_id"],
            ["mfa_pendings.user_id", "mfa_pendings.id"],
            ondelete="RESTRICT",
            name="fk_recovery_permit_pending_owner",
        ),
        ForeignKeyConstraint(
            ["user_id", "credential_id"],
            ["mfa_credentials.user_id", "mfa_credentials.id"],
            ondelete="RESTRICT",
            name="fk_recovery_permit_factor_owner",
        ),
        ForeignKeyConstraint(
            ["set_id", "code_position"],
            ["mfa_recovery_codes.set_id", "mfa_recovery_codes.position"],
            ondelete="RESTRICT",
            name="fk_recovery_permit_consumed_code",
        ),
        Index(
            "uq_mfa_recovery_permits_open_user",
            "user_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL AND used_at IS NULL"),
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint("security_revision >= 0", name="revision_bounds"),
        CheckConstraint(
            "expires_at = created_at + interval '15 minutes'", name="absolute_lifetime"
        ),
        CheckConstraint(
            "password_verified_at <= offline_verified_at AND offline_verified_at "
            "<= created_at AND created_at < password_verified_at + "
            "interval '5 minutes'",
            name="fresh_password",
        ),
        CheckConstraint(
            "used_at >= created_at AND used_at < expires_at", name="use_window"
        ),
        CheckConstraint(
            "closed_at >= created_at AND (used_at IS NULL OR closed_at >= used_at)",
            name="closure_window",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(index=True)
    pending_id: Mapped[UUID] = mapped_column(unique=True)
    credential_id: Mapped[UUID] = mapped_column()
    set_id: Mapped[UUID] = mapped_column(
        ForeignKey("mfa_recovery_sets.id", ondelete="RESTRICT")
    )
    code_position: Mapped[int] = mapped_column(Integer)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    security_revision: Mapped[int] = mapped_column(BigInteger)
    password_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    offline_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaReplacement(Base):
    """Encrypted replacement seed, bound to an exchanged offline recovery permit."""

    __tablename__ = "mfa_replacements"
    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "interval '15 minutes'",
            name="lifetime_bounds",
        ),
        CheckConstraint(
            "(closed_at IS NULL AND activated_at IS NULL AND key_id IS NOT NULL "
            "AND key_id ~ '^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
            "octet_length(ciphertext) = 36) OR (closed_at IS NOT NULL AND "
            "closed_at >= created_at AND key_id IS NULL AND nonce IS NULL AND "
            "ciphertext IS NULL AND (activated_at IS NULL OR "
            "(activated_at = closed_at AND activated_at < expires_at)))",
            name="stage_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recovery_permit_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "mfa_recovery_permits.id",
            ondelete="RESTRICT",
            name="fk_mfa_replacement_permit",
        ),
        unique=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaEnrollment(Base):
    """First-factor setup only; closed stages keep evidence without seed payload."""

    __tablename__ = "mfa_enrollments"
    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "interval '15 minutes'",
            name="lifetime_bounds",
        ),
        CheckConstraint(
            "(closed_at IS NULL AND activated_at IS NULL AND key_id IS NOT NULL "
            "AND key_id ~ '^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
            "octet_length(ciphertext) = 36) OR (closed_at IS NOT NULL AND "
            "closed_at >= created_at AND key_id IS NULL AND nonce IS NULL AND "
            "ciphertext IS NULL AND (activated_at IS NULL OR "
            "(activated_at = closed_at AND activated_at < expires_at)))",
            name="stage_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    challenge_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "enrollment_email_challenges.id",
            ondelete="RESTRICT",
            name="fk_mfa_enrollment_mail",
        ),
        unique=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnrollmentMailOutbox(Base):
    """Encrypted initial MFA mail only; no plaintext token/address or SMTP state."""

    __tablename__ = "enrollment_mail_outbox"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "interval '15 minutes'",
            name="lifetime_bounds",
        ),
        CheckConstraint(
            "(discarded_at IS NULL AND key_id IS NOT NULL AND "
            "key_id ~ '^[A-Za-z0-9_-]{1,64}$' AND nonce IS NOT NULL AND "
            "octet_length(nonce) = 12 AND ciphertext IS NOT NULL AND "
            "octet_length(ciphertext) BETWEEN 64 AND 2048) OR "
            "(discarded_at IS NOT NULL AND discarded_at >= created_at AND "
            "key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL)",
            name="payload_state",
        ),
    )

    challenge_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "enrollment_email_challenges.id",
            ondelete="RESTRICT",
            name="fk_mail_outbox_challenge",
        ),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnrollmentMailDelivery(Base):
    """Latest delivery lease/result; provider acceptance is not authentication."""

    __tablename__ = "enrollment_mail_deliveries"
    __table_args__ = (
        CheckConstraint("attempts BETWEEN 1 AND 3", name="attempt_bounds"),
        CheckConstraint(
            "lease_until > started_at AND lease_until <= started_at + "
            "interval '30 seconds'",
            name="lease_bounds",
        ),
        CheckConstraint(
            "(state = 'leased' AND finished_at IS NULL AND next_attempt_at IS NULL) OR "
            "(state = 'retry' AND attempts < 3 AND finished_at IS NOT NULL AND "
            "finished_at >= started_at AND next_attempt_at IS NOT NULL AND "
            "next_attempt_at >= finished_at + interval '60 seconds') OR "
            "(state IN ('accepted', 'uncertain', 'rejected', 'cancelled') AND "
            "finished_at IS NOT NULL AND finished_at >= started_at AND "
            "next_attempt_at IS NULL)",
            name="delivery_state",
        ),
    )

    challenge_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "enrollment_mail_outbox.challenge_id",
            ondelete="RESTRICT",
            name="fk_mail_delivery_outbox",
        ),
        primary_key=True,
    )
    state: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(Integer)
    claim_id: Mapped[UUID] = mapped_column(unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnrollmentMailThrottle(Base):
    """Mail quotas independent of password/MFA verification failures."""

    __tablename__ = "enrollment_mail_throttles"
    __table_args__ = (
        CheckConstraint("scope IN ('account', 'ip')", name="scope_allowed"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_sha256"),
        CheckConstraint(
            "issued >= 0 AND issued <= CASE WHEN scope = 'account' THEN 3 ELSE 20 END",
            name="issued_bounds",
        ),
        CheckConstraint(
            "(issued = 0 AND last_issued_at IS NULL) OR "
            "(issued > 0 AND last_issued_at IS NOT NULL AND "
            "last_issued_at >= window_started_at)",
            name="issuance_state",
        ),
    )

    scope: Mapped[str] = mapped_column(String(7), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued: Mapped[int] = mapped_column(Integer)
    last_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
            "action IN ('membership_changed', 'membership_created', "
            "'catalog_category_created', 'catalog_product_created')",
            name="action_allowed",
        ),
        CheckConstraint(
            "(action IN ('membership_changed', 'membership_created') AND "
            "after_role IS NOT NULL AND "
            "(before_role IS NULL OR before_role IN "
            "('admin', 'cashier', 'mechanic', 'read_only')) AND "
            "after_role IN ('admin', 'cashier', 'mechanic', 'read_only')) OR "
            "(action IN ('catalog_category_created', 'catalog_product_created') "
            "AND before_role IS NULL AND after_role IS NULL)",
            name="roles_allowed",
        ),
        CheckConstraint(
            "(action = 'membership_changed' AND before_role IS NOT NULL "
            "AND before_active IS NOT NULL AND "
            "(before_role <> after_role OR before_active <> after_active)) OR "
            "(action = 'membership_created' AND before_role IS NULL "
            "AND before_active IS NULL AND after_active IS TRUE) OR "
            "(action IN ('catalog_category_created', 'catalog_product_created') "
            "AND before_active IS NULL AND after_active IS TRUE)",
            name="state_changed",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_category_id"],
            ["categories.tenant_id", "categories.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_catalog_category",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_catalog_product",
        ),
        CheckConstraint(
            "(action IN ('membership_changed', 'membership_created') AND "
            "target_membership_id IS NOT NULL AND catalog_category_id IS NULL "
            "AND catalog_product_id IS NULL AND catalog_name IS NULL "
            "AND catalog_sku IS NULL AND catalog_price IS NULL) OR "
            "(action = 'catalog_category_created' AND target_membership_id IS NULL "
            "AND catalog_category_id IS NOT NULL AND catalog_product_id IS NULL "
            "AND catalog_name IS NOT NULL AND length(trim(catalog_name)) > 0 "
            "AND catalog_sku IS NULL AND catalog_price IS NULL) OR "
            "(action = 'catalog_product_created' AND target_membership_id IS NULL "
            "AND catalog_product_id IS NOT NULL AND catalog_name IS NOT NULL "
            "AND length(trim(catalog_name)) > 0 AND catalog_price IS NOT NULL "
            "AND catalog_price >= 0 AND catalog_price < 'Infinity'::numeric "
            "AND (catalog_sku IS NULL OR (catalog_sku = trim(catalog_sku) "
            "AND length(catalog_sku) > 0)))",
            name="target_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    actor_membership_id: Mapped[UUID] = mapped_column()
    target_membership_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(32))
    before_role: Mapped[str | None] = mapped_column(String(16))
    after_role: Mapped[str | None] = mapped_column(String(16))
    before_active: Mapped[bool | None] = mapped_column(Boolean)
    after_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    catalog_category_id: Mapped[UUID | None] = mapped_column()
    catalog_product_id: Mapped[UUID | None] = mapped_column()
    catalog_name: Mapped[str | None] = mapped_column(String(200))
    catalog_sku: Mapped[str | None] = mapped_column(String(100))
    catalog_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))


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
            "(action = 'mfa_enrollment_email_confirmed' AND credential_id IS NULL "
            "AND security_revision IS NOT NULL AND security_revision >= 0) OR "
            "(action = 'mfa_enrolled' AND credential_id IS NOT NULL AND "
            "security_revision IS NOT NULL AND security_revision > 0) OR "
            "(action = 'mfa_recovery_started' AND credential_id IS NOT NULL AND "
            "security_revision IS NOT NULL AND security_revision >= 0) OR "
            "(action IN ('login_succeeded', 'login_failed', "
            "'identity_reauthenticated') AND credential_id IS NULL AND "
            "security_revision IS NOT NULL AND security_revision >= 0) OR "
            "(action = 'mailbox_verified' AND credential_id IS NULL AND "
            "security_revision IS NOT NULL AND security_revision >= 0) OR "
            "(action = 'password_reset_completed' AND credential_id IS NULL AND "
            "security_revision IS NOT NULL AND security_revision > 0) OR "
            "(action = 'mfa_seed_rewrapped' AND credential_id IS NOT "
            "NULL AND security_revision IS NOT NULL AND "
            "security_revision >= 0) OR "
            "(action IN ('mfa_replaced', 'mfa_codes_regenerated', 'mfa_rotated') AND "
            "credential_id IS NOT NULL AND "
            "security_revision IS NOT NULL AND security_revision > 0) OR "
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
            "'security_revision_advanced', 'mfa_enrollment_email_confirmed', "
            "'mfa_enrolled', 'mfa_recovery_started', 'mfa_replaced', "
            "'mfa_codes_regenerated', 'password_reset_completed', "
            "'mailbox_verified', 'login_succeeded', 'login_failed', "
            "'identity_reauthenticated', 'mfa_rotated', "
            "'mfa_seed_rewrapped')",
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


# Register independent browser-verification tables for session foreign keys.
from app.modules.access import email_login_models as _email_login_models  # noqa: E402, F401

# Register invitation tables with the same metadata used by migrations.
from app.modules.access import employee_invitation_models as employee_invitation_models  # noqa: E402, F401
from app.modules.access import (  # noqa: E402
    employee_registration_models as _employee_registration_models,  # noqa: F401
)
from app.modules.access import (  # noqa: E402
    inventory_permission_models as _inventory_permission_models,  # noqa: F401
)
