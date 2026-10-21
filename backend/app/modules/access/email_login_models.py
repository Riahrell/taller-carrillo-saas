"""Bound email challenges and revocable browser verification, never credentials."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VerifiedBrowser(Base):
    __tablename__ = "verified_browsers"
    __table_args__ = (
        CheckConstraint(
            "expires_at = created_at + interval '15 days'", name="fixed_lifetime"
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    security_revision: Mapped[int] = mapped_column(BigInteger)
    email_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailLoginChallenge(Base):
    __tablename__ = "email_login_challenges"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "interval '10 minutes'",
            name="lifetime",
        ),
        CheckConstraint("failures BETWEEN 0 AND 5", name="attempts"),
        CheckConstraint(
            "send_state IN ('none', 'pending', 'claimed', 'sent', 'failed')",
            name="send_state",
        ),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$' AND binding_hash ~ '^[0-9a-f]{64}$'",
            name="opaque_hashes",
        ),
        CheckConstraint(
            "(key_id IS NULL AND nonce IS NULL AND ciphertext IS NULL) OR "
            "(key_id IS NOT NULL AND octet_length(nonce) = 12 AND "
            "ciphertext IS NOT NULL)",
            name="encrypted_payload",
        ),
        CheckConstraint(
            "verified_at IS NULL OR (browser_id IS NOT NULL AND verified_at"
            " >= created_at AND verified_at < expires_at)",
            name="verified_state",
        ),
        CheckConstraint(
            "finished_at IS NULL OR (verified_at IS NOT NULL AND "
            "finished_at >= verified_at AND finished_at < expires_at)",
            name="finished_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    binding_hash: Mapped[str] = mapped_column(String(64), index=True)
    security_revision: Mapped[int] = mapped_column(BigInteger)
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    email_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    code_hash: Mapped[str | None] = mapped_column(String(64))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    browser_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("verified_browsers.id", ondelete="RESTRICT")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_state: Mapped[str] = mapped_column(String(8), default="none")
    key_id: Mapped[str | None] = mapped_column(String(64))
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
