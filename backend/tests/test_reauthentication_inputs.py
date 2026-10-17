"""Password-only confirmation never selects an identity or exposes its secret."""

import pytest
from pydantic import SecretStr, ValidationError

from app.modules.access.reauthentication import PasswordConfirmation


def test_confirmation_preserves_unicode_and_spaces_and_excludes_secret():
    value = "  contraseña de prueba 🔐  "
    data = PasswordConfirmation(password=value)
    assert data.password.get_secret_value() == value
    assert value not in repr(data)
    assert data.model_dump() == {}
    assert data.model_dump_json() == "{}"
    assert PasswordConfirmation(password="x").password == SecretStr("x")
    assert (
        PasswordConfirmation(password="🔐" * 128).password.get_secret_value()
        == "🔐" * 128
    )


@pytest.mark.parametrize("password", ["", "x" * 129, "\ud800", None, 123, {}])
def test_invalid_confirmation_is_rejected_without_echo(password):
    with pytest.raises(ValidationError) as caught:
        PasswordConfirmation(password=password)
    assert "input_value" not in str(caught.value)


@pytest.mark.parametrize(
    "field", ["email", "user_id", "tenant_id", "role", "verified_at"]
)
def test_confirmation_forbids_identity_authority_and_timestamp_fields(field):
    with pytest.raises(ValidationError):
        PasswordConfirmation(password="synthetic password", **{field: "forbidden"})
