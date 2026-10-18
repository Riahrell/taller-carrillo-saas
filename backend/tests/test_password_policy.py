"""New-password screening is shared; existing login secrets are not reinterpreted."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.modules.access import password_policy
from app.modules.access.administration import UserCreation
from app.modules.access.bootstrap import BootstrapInput
from app.modules.access.password_reset import PasswordResetConfirmation
from app.modules.access.passwords import hash_password, verify_password


@pytest.mark.parametrize(
    "password", ["films+pic+galeries", "FILMS+PIC+GALERIES", "🔧" * 20]
)
@pytest.mark.parametrize("entry", ["bootstrap", "user", "reset"])
def test_all_new_password_entries_reject_common_or_single_character_passwords(
    password, entry
):
    values = {"password": SecretStr(password)}
    with pytest.raises(ValidationError):
        if entry == "bootstrap":
            BootstrapInput(tenant_name="Synthetic", email="test@example.com", **values)
        elif entry == "user":
            UserCreation(email="test@example.com", role="read_only", **values)
        else:
            PasswordResetConfirmation(token=SecretStr("a" * 43), **values)


def test_screening_fails_closed_if_the_bundled_file_is_corrupted(monkeypatch):
    read_bytes = Path.read_bytes

    def damaged(path):
        return (
            b"synthetic corruption"
            if path.name == "common-passwords.txt"
            else read_bytes(path)
        )

    monkeypatch.setattr(Path, "read_bytes", damaged)
    with pytest.raises(ValueError, match="^Password screening unavailable$"):
        password_policy._common.__wrapped__()


def test_existing_password_hashes_remain_verifiable_without_new_password_policy():
    old = SecretStr("synthetic legacy")
    assert verify_password(old, hash_password(old))
