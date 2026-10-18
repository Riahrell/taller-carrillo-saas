"""Internal reset request issuance only; no consumption, HTTP, URL or transport.

Normal outcomes always return None. No address, eligibility, quota decision or
secret is returned. Timing/HTTP enumeration defenses still require integration.
"""

import secrets
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import mfa_pending
from app.modules.access.email_outbox import (
    MailProtection,
    MailProtectionError,
    MailPurpose,
)
from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.mailbox_verification import lock_eligible_mailbox
from app.modules.access.mfa_verification import _timeouts
from app.modules.access.models import (
    PasswordResetChallenge,
    PasswordResetThrottle,
    User,
)
from app.modules.access.sessions import _database_now

WINDOW = timedelta(minutes=15)
COOLDOWN = timedelta(seconds=60)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    email: EmailStr = Field(max_length=254, repr=False, exclude=True)

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return value.lower()


class PasswordResetUnavailable(RuntimeError):
    """Infrastructure failure without SQL, email, token or eligibility details."""


def _clock(session: Session, previous: datetime) -> datetime:
    now = _database_now(session)
    if now < previous:
        raise PasswordResetUnavailable("Password reset unavailable")
    return now


def _reserve_request(
    session: Session, scopes: list[tuple[str, str]]
) -> datetime | None:
    """IP -> address BEFORE owner locks. IP counts address-cooldown denials too."""
    now = _database_now(session)
    for scope, key_hash in scopes:
        session.execute(
            insert(PasswordResetThrottle)
            .values(scope=scope, key_hash=key_hash, window_started_at=now, requested=0)
            .on_conflict_do_nothing()
        )
        row = session.scalar(
            select(PasswordResetThrottle)
            .where(
                PasswordResetThrottle.scope == scope,
                PasswordResetThrottle.key_hash == key_hash,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = _clock(session, now)
        if now < row.window_started_at or (
            row.last_requested_at is not None and now < row.last_requested_at
        ):
            raise PasswordResetUnavailable("Password reset unavailable")
        if now >= row.window_started_at + WINDOW:
            row.window_started_at, row.requested, row.last_requested_at = now, 0, None
        if row.requested >= (20 if scope == "ip" else 3):
            return None
        if (
            scope == "address"
            and row.last_requested_at is not None
            and now < row.last_requested_at + COOLDOWN
        ):
            return None
        if row.requested == 0:
            row.window_started_at = now
        row.requested += 1
        row.last_requested_at = now
        session.flush()
    return now


class PasswordResetIssuer:
    """Own transaction; stage one independent encrypted message if eligible.

    Keys/validated peer IP come from trusted configuration, not a request body.
    New requests never revoke sessions, passwords, MFA or previous reset links.
    """

    def __init__(self, engine: Engine, *, protection: MailProtection):
        if (
            not isinstance(protection, MailProtection)
            or protection.purpose != MailPurpose.PASSWORD_RESET
        ):
            raise PasswordResetUnavailable("Password reset unavailable")
        self._factory = create_session_factory(engine)
        self._protection = protection

    def issue(
        self, request: PasswordResetRequest, *, client_ip: IPv4Address | IPv6Address
    ) -> None:
        if not isinstance(request, PasswordResetRequest):
            raise ValueError("Invalid internal reset input")
        try:
            scopes = self._protection.request_scopes(
                SecretStr(str(request.email)), client_ip
            )
            with self._factory.begin() as session:
                _timeouts(session)
                checked_at = _reserve_request(session, scopes)
                if checked_at is None:
                    return
                user_id = session.scalar(
                    select(User.id).where(User.email == str(request.email))
                )
                if user_id is None:
                    return
                verification = lock_eligible_mailbox(session, user_id=user_id)
                if verification is None:
                    return
                user = session.get(User, user_id)
                # Discovery granted no authority: recheck after the owner lock.
                if user.email != str(request.email):
                    return
                now = _clock(session, max(checked_at, verification.verified_at))
                identity, token = uuid4(), SecretStr(secrets.token_urlsafe(32))
                expires_at = now + WINDOW
                envelope = self._protection.encrypt(
                    recipient=SecretStr(user.email),
                    token=token,
                    user_id=user.id,
                    challenge_id=identity,
                    expires_at=expires_at,
                )
                session.add(
                    PasswordResetChallenge(
                        id=identity,
                        user_id=user.id,
                        verification_id=verification.id,
                        token_hash=mfa_pending._hash(token, "password-reset-token-v1:"),
                        password_fingerprint=_password_fingerprint(user),
                        security_revision=user.security_revision,
                        created_at=now,
                        expires_at=expires_at,
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                    )
                )
                session.flush()
                if _clock(session, now) >= expires_at:
                    # Rollback rather than commit a payload already expired at creation.
                    raise PasswordResetUnavailable("Password reset unavailable")
        except (SQLAlchemyError, MailProtectionError):
            raise PasswordResetUnavailable("Password reset unavailable") from None


def purge_expired_resets(engine: Engine, *, limit: int = 100) -> int:
    """Explicit bounded maintenance; child locks only, no refunds or scheduling."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid reset maintenance limit")
    try:
        with create_session_factory(engine).begin() as session:
            _timeouts(session)
            now = _database_now(session)
            rows = session.scalars(
                select(PasswordResetChallenge)
                .where(
                    PasswordResetChallenge.expires_at <= now,
                    PasswordResetChallenge.closed_at.is_(None),
                )
                .order_by(PasswordResetChallenge.expires_at, PasswordResetChallenge.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                row.closed_at = row.discarded_at = max(now, row.created_at)
                row.key_id = row.nonce = row.ciphertext = None
            session.flush()
            count = len(rows)
        return count
    except SQLAlchemyError:
        raise PasswordResetUnavailable("Password reset unavailable") from None
