"""Credential contract and trusted IP normalization, without database access."""

import secrets
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

import pytest
from pydantic import SecretStr, ValidationError

from app.modules.access.authentication import Credentials, _ip_digest, _refresh
from app.modules.access.models import LoginThrottle


def test_credentials_normalize_email_and_hide_password():
    password = SecretStr(" " + secrets.token_urlsafe(24) + " ")
    credentials = Credentials(email=" ADMIN@EXAMPLE.COM ", password=password)
    assert credentials.email == "admin@example.com"
    assert credentials.password == password
    assert password.get_secret_value() not in repr(credentials)


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "invalid"},
        {"password": SecretStr("")},
        {"password": SecretStr("x" * 129)},
        {"password": SecretStr("\ud800" * 20)},
        {"role": "admin"},
        {"tenant_id": "client-chosen"},
        {"user_id": "client-chosen"},
    ],
)
def test_invalid_credentials_and_authority_fields_are_rejected(changes):
    data = {
        "email": "admin@example.com",
        "password": SecretStr(secrets.token_urlsafe(24)),
    }
    with pytest.raises(ValidationError):
        Credentials(**(data | changes))


@pytest.mark.parametrize(
    "a,b",
    [
        ("127.0.0.1", "::ffff:127.0.0.1"),
        ("::1", "0:0:0:0:0:0:0:1"),
        ("fe80::1%eth0", "fe80::1%eth1"),
    ],
)
def test_equivalent_peer_addresses_share_bucket(a, b):
    assert _ip_digest(ip_address(a)) == _ip_digest(ip_address(b))


def test_unparsed_peer_ip_is_refused_before_database_access():
    with pytest.raises(ValueError, match="validated peer"):
        _ip_digest("127.0.0.1")


def test_window_expires_at_exact_boundary_and_does_not_slide_on_rejection():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bucket = LoginThrottle(
        scope="account", key_hash="a" * 64, failures=5, window_started_at=start
    )
    _refresh(bucket, start + timedelta(minutes=15) - timedelta(microseconds=1))
    assert bucket.failures == 5 and bucket.window_started_at == start
    _refresh(bucket, start + timedelta(minutes=15))
    assert bucket.failures == 0
    assert bucket.window_started_at == start + timedelta(minutes=15)
