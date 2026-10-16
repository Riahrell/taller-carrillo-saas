"""Malformed pending login inputs never reach persistence or leak secrets."""

import secrets
from ipaddress import ip_address
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.modules.access.authentication import Authenticator, Credentials


@pytest.mark.parametrize("token", ["", "x" * 42, "x" * 44, "ñ" * 43, " " * 43])
def test_bad_challenge_token_does_not_touch_database(token):
    service = Authenticator.__new__(Authenticator)
    service._factory = MagicMock()
    assert (
        service.select_membership(
            challenge_token=SecretStr(token),
            browser_binding=SecretStr(secrets.token_urlsafe(32)),
            membership_id=uuid4(),
        )
        is None
    )
    service._factory.begin.assert_not_called()


@pytest.mark.parametrize(
    "binding", ["", "x" * 42, "x" * 257, "ñ" * 43, "\n" + "x" * 43, " " * 43]
)
def test_bad_browser_binding_is_rejected_before_database(binding):
    service = Authenticator.__new__(Authenticator)
    service._factory = MagicMock()
    assert (
        service.begin_login(
            Credentials(
                email="synthetic@example.com",
                password=SecretStr(secrets.token_urlsafe(32)),
            ),
            client_ip=ip_address("127.0.0.1"),
            browser_binding=SecretStr(binding),
        )
        is None
    )
    assert (
        service.select_membership(
            challenge_token=SecretStr(secrets.token_urlsafe(32)),
            browser_binding=SecretStr(binding),
            membership_id=uuid4(),
        )
        is None
    )
    service._factory.begin.assert_not_called()
