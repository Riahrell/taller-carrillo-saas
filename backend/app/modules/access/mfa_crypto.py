"""Internal MFA primitives; matching is NOT transactional consumption or authority.

Callers must obtain time/state from the server and atomically persist consumption.
Secrets are excluded from model serialization, not securely erased from memory.
"""

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

import pyotp
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
    field_validator,
)


class _SecretModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class TotpSeed(_SecretModel):
    value: SecretBytes = Field(repr=False, exclude=True)

    @field_validator("value")
    @classmethod
    def validate_seed(cls, value: SecretBytes) -> SecretBytes:
        if len(value.get_secret_value()) != 20:
            raise ValueError("Invalid TOTP seed")
        return value

    def base32(self) -> SecretStr:
        return SecretStr(
            base64.b32encode(self.value.get_secret_value()).decode("ascii")
        )


class RecoveryCode(_SecretModel):
    value: SecretStr = Field(repr=False, exclude=True)

    @field_validator("value")
    @classmethod
    def validate_code(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        # Reject aliases, whitespace, lowercase and noncanonical trailing bits.
        if not re.fullmatch(r"[A-Z2-7]{26}", raw):
            raise ValueError("Invalid recovery code")
        decoded = base64.b32decode(raw + "======")
        if base64.b32encode(decoded).decode("ascii").rstrip("=") != raw:
            raise ValueError("Invalid recovery code")
        return value


class EncryptedSeed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    version: int = Field(default=1, ge=1, le=1)
    key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    nonce: bytes = Field(min_length=12, max_length=12, repr=False)
    ciphertext: bytes = Field(min_length=36, max_length=36, repr=False)


class SeedProtectionError(ValueError):
    """Generic failure: do not reveal key availability or authentication details."""


def generate_totp_seed() -> TotpSeed:
    return TotpSeed(value=SecretBytes(secrets.token_bytes(20)))


def _timestamp(value: datetime) -> int:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Server time must be timezone-aware")
    timestamp = int(value.timestamp())
    if value.timestamp() < 0:
        raise ValueError("Invalid server time")
    return timestamp


def match_totp(
    seed: TotpSeed,
    code: SecretStr,
    *,
    now: datetime,
    last_counter: int | None,
    last_verified_at: datetime | None,
) -> int | None:
    """Return a unique unused counter, never an authorization/session proof.

    The persisted counter and server verification time must be supplied together.
    A caller must lock/CAS and consume the result across ALL MFA purposes.
    """
    timestamp = _timestamp(now)
    if (last_counter is None) != (last_verified_at is None):
        raise ValueError("Incomplete persisted TOTP state")
    if last_counter is not None:
        if type(last_counter) is not int or last_counter < 0:
            raise ValueError("Invalid persisted TOTP state")
        assert last_verified_at is not None
        _timestamp(last_verified_at)
        if now < last_verified_at:
            return None
    raw = code.get_secret_value()
    if not re.fullmatch(r"[0-9]{6}", raw):
        return None
    otp = pyotp.TOTP(
        seed.base32().get_secret_value(), digits=6, interval=30, digest=hashlib.sha1
    )
    current = timestamp // 30
    matches = [
        counter
        for counter in range(max(0, current - 1), current + 2)
        if hmac.compare_digest(otp.generate_otp(counter), raw)
    ]
    # Detect collisions before filtering used counters, including used+new matches.
    if len(matches) != 1:
        return None
    matched = matches[0]
    return matched if last_counter is None or matched > last_counter else None


def _identity(user_id: UUID, identity_id: UUID) -> bytes:
    if not isinstance(user_id, UUID) or not isinstance(identity_id, UUID):
        raise ValueError("Invalid MFA identity")
    return user_id.bytes + identity_id.bytes


def _seed_aad(user_id: UUID, credential_id: UUID, key_id: str) -> bytes:
    return (
        b"carrillo:mfa:totp-seed:v1\x00"
        + _identity(user_id, credential_id)
        + key_id.encode("ascii")
    )


class SeedKeyring:
    """Injected external keys; old keys read, only active key writes. No defaults."""

    def __init__(self, keys: Mapping[str, SecretBytes], *, active_key_id: str):
        valid = bool(keys) and active_key_id in keys
        for key_id, key in keys.items():
            valid = (
                valid
                and isinstance(key_id, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id) is not None
                and isinstance(key, SecretBytes)
                and len(key.get_secret_value()) == 32
            )
        if not valid:
            raise SeedProtectionError("MFA seed protection failed")
        self._keys = MappingProxyType(dict(keys))
        self._active_key_id = active_key_id

    def encrypt(
        self, seed: TotpSeed, *, user_id: UUID, credential_id: UUID
    ) -> EncryptedSeed:
        key_id = self._active_key_id
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._keys[key_id].get_secret_value()).encrypt(
            nonce,
            seed.value.get_secret_value(),
            _seed_aad(user_id, credential_id, key_id),
        )
        return EncryptedSeed(key_id=key_id, nonce=nonce, ciphertext=ciphertext)

    def decrypt(
        self, envelope: EncryptedSeed, *, user_id: UUID, credential_id: UUID
    ) -> TotpSeed:
        try:
            # Revalidate even model_construct()/model_copy(update=...) objects.
            envelope = EncryptedSeed.model_validate(envelope.model_dump())
            key = self._keys[envelope.key_id]
            plaintext = AESGCM(key.get_secret_value()).decrypt(
                envelope.nonce,
                envelope.ciphertext,
                _seed_aad(user_id, credential_id, envelope.key_id),
            )
            return TotpSeed(value=SecretBytes(plaintext))
        except (InvalidTag, KeyError, ValueError, TypeError):
            raise SeedProtectionError("MFA seed protection failed") from None


def generate_recovery_codes() -> tuple[RecoveryCode, ...]:
    # Retry duplicates rather than relying on their low probability.
    values: set[str] = set()
    codes: list[RecoveryCode] = []
    while len(codes) < 10:
        raw = base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
        if raw not in values:
            values.add(raw)
            codes.append(RecoveryCode(value=SecretStr(raw)))
    return tuple(codes)


def recovery_digest(code: RecoveryCode, *, user_id: UUID, set_id: UUID) -> bytes:
    """High-entropy code hash, bound to its owner and recovery set, not password."""
    return hashlib.sha256(
        b"carrillo:mfa:offline-recovery:v1\x00"
        + _identity(user_id, set_id)
        + code.value.get_secret_value().encode("ascii")
    ).digest()


def match_recovery_code(
    code: RecoveryCode, digest: bytes, *, user_id: UUID, set_id: UUID
) -> bool:
    """Matching alone does not consume a code; persistence must do so atomically."""
    if type(digest) is not bytes or len(digest) != 32:
        raise ValueError("Invalid recovery digest")
    return hmac.compare_digest(
        recovery_digest(code, user_id=user_id, set_id=set_id), digest
    )
