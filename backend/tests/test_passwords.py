"""Real Argon2 computations; no application configuration or database required."""

import secrets

import pytest
from argon2 import PasswordHasher, Type, extract_parameters
from pydantic import SecretStr

from app.modules.access.passwords import (
    hash_password,
    password_needs_rehash,
    verify_password,
)


def test_argon2id_parameters_salts_and_verification():
    password = SecretStr(secrets.token_urlsafe(32) + " ñ🔧 ")
    first, second = hash_password(password), hash_password(password)
    params = extract_parameters(first)
    assert (params.type, params.version) == (Type.ID, 19)
    assert (params.memory_cost, params.time_cost, params.parallelism) == (65536, 3, 4)
    assert (params.salt_len, params.hash_len) == (16, 32)
    assert first != second
    assert verify_password(password, first)
    assert not verify_password(SecretStr(secrets.token_urlsafe(32)), first)
    assert not verify_password(SecretStr(password.get_secret_value().strip()), first)
    assert not password_needs_rehash(first)
    assert password.get_secret_value() not in first


@pytest.mark.parametrize("encoded", ["", "synthetic-test-hash", "$argon2id$broken"])
def test_malformed_hash_fails_closed(encoded):
    assert not verify_password(SecretStr(secrets.token_urlsafe(24)), encoded)
    assert password_needs_rehash(encoded)


def test_old_parameters_require_rehash_after_successful_verification():
    password = SecretStr(secrets.token_urlsafe(24))
    old = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(
        password.get_secret_value()
    )
    assert verify_password(password, old)
    assert password_needs_rehash(old)
