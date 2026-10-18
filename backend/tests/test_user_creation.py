"""Validated new identity command, without any caller-selected authority."""

import secrets

import pytest
from pydantic import SecretStr, ValidationError

from app.modules.access.administration import UserCreation
from app.modules.access.models import MembershipRole


def payload(**changes):
    return {
        "email": " NEW@EXAMPLE.COM ",
        "password": SecretStr(secrets.token_urlsafe(24)),
        "role": "cashier",
    } | changes


@pytest.mark.parametrize("role", list(MembershipRole))
def test_creation_role_explicit_and_secret_not_serialized(role):
    password = SecretStr(" " + secrets.token_urlsafe(24) + " ")
    command = UserCreation(**payload(role=role, password=password))
    assert command.role == role and command.email == "new@example.com"
    assert command.password == password
    assert "password" not in command.model_dump()
    assert password.get_secret_value() not in repr(command) + command.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "not-an-email"},
        {"email": "a\nb@example.com"},
        {"password": SecretStr("x" * 14)},
        {"password": SecretStr("x" * 129)},
        {"password": SecretStr("\ud800" * 15)},
        {"role": "root"},
        {"tenant_id": "chosen"},
        {"user_id": "chosen"},
        {"actor_role": "admin"},
        {"password_hash": "chosen"},
        {"is_active": False},
        {"user_is_active": True},
    ],
)
def test_creation_rejects_invalid_or_authority_fields(changes):
    with pytest.raises(ValidationError):
        UserCreation(**payload(**changes))


@pytest.mark.parametrize("missing", ["email", "password", "role"])
def test_creation_has_no_credential_or_role_default(missing):
    values = payload()
    del values[missing]
    with pytest.raises(ValidationError):
        UserCreation(**values)


@pytest.mark.parametrize("length", [15, 128])
def test_creation_unicode_password_boundaries(length):
    command = UserCreation(
        **payload(password=SecretStr("🔧" + secrets.token_urlsafe(128)[: length - 1]))
    )
    assert len(command.password.get_secret_value()) == length
