"""Internal encrypted mail staging. No provider, worker, HTTP or delivery claim.

Keys are injected from outside the database; no key generation/loading defaults.
Consumers must revalidate identity, purpose, time and revocation before sending.
"""

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from types import MappingProxyType
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, SecretBytes, SecretStr
from sqlalchemy import Engine, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.models import EnrollmentMailOutbox, EnrollmentMailThrottle
from app.modules.access.sessions import _database_now

WINDOW = timedelta(minutes=15)
COOLDOWN = timedelta(seconds=60)


class MailPurpose(StrEnum):
    ENROLLMENT = "enrollment"
    PASSWORD_RESET = "password_reset"


class MailProtectionError(ValueError):
    """No key identifiers, payload or encryption internals in errors."""


class MailPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    recipient: SecretStr = Field(repr=False, exclude=True)
    token: SecretStr = Field(repr=False, exclude=True)


class MailEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    nonce: bytes = Field(min_length=12, max_length=12, repr=False)
    ciphertext: bytes = Field(min_length=64, max_length=2048, repr=False)


def _aad(
    user_id: UUID,
    challenge_id: UUID,
    expires_at: datetime,
    key_id: str,
    purpose: MailPurpose,
):
    if (
        not isinstance(user_id, UUID)
        or not isinstance(challenge_id, UUID)
        or not isinstance(expires_at, datetime)
        or expires_at.utcoffset() is None
    ):
        raise MailProtectionError("Mail protection unavailable")
    return (
        {
            MailPurpose.ENROLLMENT: b"carrillo:enrollment-mail:v1\x00",
            MailPurpose.PASSWORD_RESET: b"carrillo:password-reset-mail:v1\x00",
        }[purpose]
        + user_id.bytes
        + challenge_id.bytes
        + expires_at.astimezone(UTC).isoformat(timespec="microseconds").encode("ascii")
        + b"\x00"
        + key_id.encode("ascii")
    )


def _payload(recipient: SecretStr, token: SecretStr) -> MailPayload:
    if (
        not isinstance(recipient, SecretStr)
        or not isinstance(token, SecretStr)
        or not 3 <= len(recipient.get_secret_value()) <= 254
        or any(c in recipient.get_secret_value() for c in "\r\n\x00")
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token.get_secret_value())
    ):
        raise MailProtectionError("Mail protection unavailable")
    return MailPayload(recipient=recipient, token=token)


class MailProtection:
    """Distinct mail encryption and quota keys; old encryption keys read only.

    Quota key is stable across encryption rotation. Rotating it requires a
    controlled quota transition; do not regenerate it on process startup.
    """

    def __init__(
        self,
        keys: Mapping[str, SecretBytes],
        *,
        active_key_id: str,
        quota_key: SecretBytes,
        purpose: MailPurpose = MailPurpose.ENROLLMENT,
    ):
        if (
            not isinstance(purpose, MailPurpose)
            or not keys
            or active_key_id not in keys
            or not isinstance(quota_key, SecretBytes)
            or len(quota_key.get_secret_value()) != 32
            or any(
                not isinstance(key_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id) is None
                or not isinstance(key, SecretBytes)
                or len(key.get_secret_value()) != 32
                or hmac.compare_digest(
                    key.get_secret_value(), quota_key.get_secret_value()
                )
                for key_id, key in keys.items()
            )
        ):
            raise MailProtectionError("Mail protection unavailable")
        self._keys = MappingProxyType(dict(keys))
        self._active = active_key_id
        self._quota_key = quota_key
        self._purpose = purpose

    @property
    def purpose(self) -> MailPurpose:
        return self._purpose

    def request_scopes(self, email: SecretStr, client_ip: IPv4Address | IPv6Address):
        """Reset request keys include unknown addresses; never persist them in clear."""
        if (
            self.purpose != MailPurpose.PASSWORD_RESET
            or not isinstance(email, SecretStr)
            or not isinstance(client_ip, (IPv4Address, IPv6Address))
        ):
            raise MailProtectionError("Mail protection unavailable")
        if isinstance(client_ip, IPv6Address) and client_ip.ipv4_mapped is not None:
            client_ip = client_ip.ipv4_mapped
        return [
            (
                scope,
                hmac.new(
                    self._quota_key.get_secret_value(),
                    b"carrillo:password-reset-request-quota:v1\x00"
                    + scope.encode("ascii")
                    + b"\x00"
                    + identity,
                    hashlib.sha256,
                ).hexdigest(),
            )
            for scope, identity in (
                ("ip", client_ip.packed),
                ("address", email.get_secret_value().encode("utf-8")),
            )
        ]

    def encrypt(
        self,
        *,
        recipient: SecretStr,
        token: SecretStr,
        user_id: UUID,
        challenge_id: UUID,
        expires_at: datetime,
    ) -> MailEnvelope:
        payload = _payload(recipient, token)
        raw = json.dumps(
            {
                "recipient": payload.recipient.get_secret_value(),
                "token": payload.token.get_secret_value(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._keys[self._active].get_secret_value()).encrypt(
            nonce,
            raw,
            _aad(user_id, challenge_id, expires_at, self._active, self.purpose),
        )
        return MailEnvelope(key_id=self._active, nonce=nonce, ciphertext=ciphertext)

    def decrypt(
        self,
        envelope: MailEnvelope,
        *,
        user_id: UUID,
        challenge_id: UUID,
        expires_at: datetime,
    ) -> MailPayload:
        """Decoding alone never authorizes sending or authenticates a user."""
        try:
            envelope = MailEnvelope.model_validate(envelope.model_dump())
            raw = AESGCM(self._keys[envelope.key_id].get_secret_value()).decrypt(
                envelope.nonce,
                envelope.ciphertext,
                _aad(user_id, challenge_id, expires_at, envelope.key_id, self.purpose),
            )
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"recipient", "token"}:
                raise ValueError
            return _payload(SecretStr(data["recipient"]), SecretStr(data["token"]))
        except (InvalidTag, KeyError, ValueError, TypeError, AttributeError):
            raise MailProtectionError("Mail protection unavailable") from None

    def scopes(self, user_id: UUID, client_ip: IPv4Address | IPv6Address):
        if self.purpose != MailPurpose.ENROLLMENT or not isinstance(
            client_ip, (IPv4Address, IPv6Address)
        ):
            raise MailProtectionError("Mail protection unavailable")
        if isinstance(client_ip, IPv6Address) and client_ip.ipv4_mapped is not None:
            client_ip = client_ip.ipv4_mapped
        return [
            (
                scope,
                hmac.new(
                    self._quota_key.get_secret_value(),
                    b"carrillo:enrollment-mail-quota:v1\x00"
                    + scope.encode("ascii")
                    + b"\x00"
                    + identity,
                    hashlib.sha256,
                ).hexdigest(),
            )
            for scope, identity in (
                ("account", user_id.bytes),
                ("ip", client_ip.packed),
            )
        ]


def reserve_mail(
    session: Session,
    protection: MailProtection,
    *,
    user_id: UUID,
    client_ip: IPv4Address | IPv6Address,
    checked_at: datetime,
    expires_at: datetime,
) -> datetime | None:
    """Caller holds user/pending; no commit. Count only successfully queued mail."""
    rows = []
    for scope, key_hash in sorted(protection.scopes(user_id, client_ip)):
        session.execute(
            insert(EnrollmentMailThrottle)
            .values(
                scope=scope,
                key_hash=key_hash,
                window_started_at=checked_at,
                issued=0,
                last_issued_at=None,
            )
            .on_conflict_do_nothing()
        )
        rows.append(
            session.scalar(
                select(EnrollmentMailThrottle)
                .where(
                    EnrollmentMailThrottle.scope == scope,
                    EnrollmentMailThrottle.key_hash == key_hash,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
    now = _database_now(session)
    if now < checked_at:
        raise MailProtectionError("Mail protection unavailable")
    if now >= expires_at:
        return None
    for row in rows:
        if now < row.window_started_at or (
            row.last_issued_at is not None and now < row.last_issued_at
        ):
            return None
        if (
            row.scope == "account"
            and row.last_issued_at is not None
            and (now - row.last_issued_at < COOLDOWN)
        ):
            return None
        count = 0 if now - row.window_started_at >= WINDOW else row.issued
        if count >= (3 if row.scope == "account" else 20):
            return None
    for row in rows:
        if now - row.window_started_at >= WINDOW:
            row.window_started_at, row.issued = now, 0
        row.issued += 1
        row.last_issued_at = now
    session.flush()
    return now


def discard_mail(session: Session, challenge_id: UUID, now: datetime) -> None:
    """After challenge lock; erase encrypted payload, keep minimal evidence."""
    row = session.scalar(
        select(EnrollmentMailOutbox)
        .where(EnrollmentMailOutbox.challenge_id == challenge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is not None and row.discarded_at is None:
        row.key_id = row.nonce = row.ciphertext = None
        row.discarded_at = max(now, row.created_at)
        session.flush()


def purge_expired_mail(engine: Engine, *, limit: int = 100) -> int:
    """Explicit bounded maintenance, not scheduled yet. No challenge/user locks.

    Only takes outbox locks and never waits for an ancestor, preserving lock order.
    Storage backups/WAL have their own retention; this is not secure erasure.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid mail maintenance limit")
    try:
        with create_session_factory(engine).begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            now = _database_now(session)
            rows = session.scalars(
                select(EnrollmentMailOutbox)
                .where(
                    EnrollmentMailOutbox.expires_at <= now,
                    EnrollmentMailOutbox.discarded_at.is_(None),
                )
                .order_by(
                    EnrollmentMailOutbox.expires_at, EnrollmentMailOutbox.challenge_id
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                row.key_id = row.nonce = row.ciphertext = None
                row.discarded_at = max(now, row.created_at)
            session.flush()
            result = len(rows)
        return result
    except SQLAlchemyError:
        raise MailProtectionError("Mail protection unavailable") from None
