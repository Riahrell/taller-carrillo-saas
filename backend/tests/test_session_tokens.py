"""Malformed bearer tokens must not reach PostgreSQL or appear in errors."""

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from app.modules.access.membership import AccessDenied
from app.modules.access.sessions import resolve_session, revoke_session


@pytest.mark.parametrize("value", ["", "x" * 42, "x" * 44, "ñ" * 43, "a " * 22])
def test_malformed_token_is_rejected_without_database_access(value):
    session = MagicMock()
    with pytest.raises(AccessDenied, match="^Access denied$"):
        resolve_session(session, SecretStr(value))
    revoke_session(session, SecretStr(value))
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()
