"""Local common-password screening; no user's password is sent to a third party."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr


@lru_cache(maxsize=1)
def _common():
    folder = Path(__file__).resolve().parents[2] / "data"
    try:
        metadata = json.loads((folder / "common-passwords-source.json").read_text())
        raw = (folder / "common-passwords.txt").read_bytes()
        if (
            hashlib.sha256(raw).hexdigest() != metadata["sha256"]
            or len(raw.splitlines()) < 9000
        ):
            raise ValueError
        return frozenset(line.casefold() for line in raw.decode("utf-8").splitlines())
    except (OSError, ValueError, KeyError):
        raise ValueError("Password screening unavailable") from None


def validate_new_password(value: SecretStr) -> SecretStr:
    password = value.get_secret_value()
    try:
        password.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid password") from None
    if (
        not 15 <= len(password) <= 128
        or password.casefold() in _common()
        or len(set(password)) == 1
    ):
        raise ValueError("Choose a longer, uncommon password")
    return value
