"""Password reset issuance and single-use consumption under owner locks.

Public submissions are blinded through reset_inbox; proof consumption never
changes MFA and revokes previous sessions atomically with the password change.
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
                self._stage(session, request, checked_at)

        except (SQLAlchemyError, MailProtectionError):
            raise PasswordResetUnavailable("Password reset unavailable") from None

    def _stage(
        self, session, request, checked_at, *, supplied_token=None, original_start=None
    ):
        """Worker composition only: bearer secret minted by our encrypted inbox."""
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
        identity, token = (
            uuid4(),
            supplied_token or SecretStr(secrets.token_urlsafe(32)),
        )
        created_at = original_start or now
        expires_at = created_at + WINDOW
        if now >= expires_at:
            return
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
                created_at=created_at,
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


class PasswordResetConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    token: SecretStr = Field(min_length=43, max_length=43, repr=False, exclude=True)
    password: SecretStr = Field(min_length=15, max_length=128, repr=False, exclude=True)

    @field_validator("password")
    @classmethod
    def unicode_password(cls, value: SecretStr) -> SecretStr:
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise ValueError("Invalid password") from None
        from app.modules.access.password_policy import validate_new_password

        return validate_new_password(value)


class PasswordResetConsumer:
    """Token is purpose-specific; no auto-login or MFA changes after reset."""

    def __init__(self, engine: Engine):
        self._factory = create_session_factory(engine)

    def consume(self, data: PasswordResetConfirmation) -> bool:
        import hmac

        from argon2.exceptions import HashingError

        from app.modules.access.mailbox_verification import _address_hash
        from app.modules.access.membership import AccessDenied
        from app.modules.access.models import SecurityEvent, SecurityNotice
        from app.modules.access.passwords import hash_password
        from app.modules.access.security_state import advance_security_revision

        if not isinstance(data, PasswordResetConfirmation):
            raise ValueError("Invalid internal reset input")
        try:
            digest = mfa_pending._hash(data.token, "password-reset-token-v1:")
        except AccessDenied:
            return False
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                hint = session.scalar(
                    select(PasswordResetChallenge.user_id).where(
                        PasswordResetChallenge.token_hash == digest
                    )
                )
                if hint is None:
                    return False
                verification = lock_eligible_mailbox(session, user_id=hint)
                if verification is None:
                    return False
                rows = session.scalars(
                    select(PasswordResetChallenge)
                    .where(PasswordResetChallenge.user_id == hint)
                    .order_by(PasswordResetChallenge.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).all()
                row = next(
                    (
                        item
                        for item in rows
                        if hmac.compare_digest(item.token_hash, digest)
                    ),
                    None,
                )
                user = session.get(User, hint)
                now = _database_now(session)
                if (
                    row is None
                    or row.closed_at is not None
                    or row.consumed_at is not None
                    or row.verification_id != verification.id
                    or row.security_revision != user.security_revision
                    or not hmac.compare_digest(
                        row.password_fingerprint, _password_fingerprint(user)
                    )
                    or not row.created_at <= now < row.expires_at
                ):
                    return False
                encoded = hash_password(data.password)
                finished_at = _clock(session, now)
                if finished_at >= row.expires_at:
                    raise _ResetExpired
                user.password_hash = encoded
                state = advance_security_revision(
                    session, user_id=user.id, expected_revision=user.security_revision
                )
                row.consumed_at = row.closed_at = row.discarded_at = finished_at
                row.key_id = row.nonce = row.ciphertext = None
                event = SecurityEvent(
                    user_id=user.id,
                    credential_id=None,
                    action="password_reset_completed",
                    security_revision=state.revision,
                    occurred_at=finished_at,
                )
                session.add(event)
                session.flush()
                session.add(
                    SecurityNotice(
                        event_id=event.id,
                        address_hash=_address_hash(user),
                        created_at=finished_at,
                        expires_at=finished_at + timedelta(hours=24),
                    )
                )
                session.flush()
                if _clock(session, finished_at) >= row.expires_at:
                    raise _ResetExpired
            return True
        except _ResetExpired:
            return False
        except (SQLAlchemyError, HashingError):
            raise PasswordResetUnavailable("Password reset unavailable") from None


class _ResetExpired(Exception):
    """Internal rollback after hashing or revocation exceeded the original lease."""
