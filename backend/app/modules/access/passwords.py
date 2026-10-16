"""Argon2id with explicit RFC 9106 low-memory parameters and random salts."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.profiles import RFC_9106_LOW_MEMORY
from pydantic import SecretStr

_hasher = PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)


def hash_password(password: SecretStr) -> str:
    return _hasher.hash(password.get_secret_value())


def verify_password(password: SecretStr, encoded_hash: str) -> bool:
    # Only server-stored hashes are valid inputs; never accept client hash parameters.
    if not encoded_hash.startswith("$argon2id$"):
        return False
    try:
        return _hasher.verify(encoded_hash, password.get_secret_value())
    except (VerificationError, InvalidHashError, UnicodeError):
        return False


def password_needs_rehash(encoded_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(encoded_hash)
    except (InvalidHashError, VerificationError):
        return True
