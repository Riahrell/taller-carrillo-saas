"""Only a verified socket peer may supply exactly one Nginx-observed IP."""

import socket
from ipaddress import ip_address

import pytest
from starlette.datastructures import Headers

from app.core.config import ConfigurationError, Settings
from app.modules.access.authentication import AuthenticationUnavailable
from app.modules.access.http_security import RequestRejected
from app.modules.access.trusted_proxy import TrustedProxy, _proxy_addresses


def test_proxy_configuration_is_required_by_http(monkeypatch):
    from app.main import create_app

    monkeypatch.delenv("TRUSTED_PROXY_HOST")
    with pytest.raises(ConfigurationError, match="TRUSTED_PROXY_HOST"):
        create_app(Settings(app_env="test"))


@pytest.mark.parametrize(
    "host",
    ["", "*", "proxy:8000", "Proxy", "127.0.0.1", "proxy.", "bad..name", "-proxy"],
)
def test_proxy_config_rejects_ambiguous_hostnames(monkeypatch, host):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TRUSTED_PROXY_HOST", host)
    from app.core.config import load_settings

    with pytest.raises(ConfigurationError, match="trusted_proxy_host"):
        load_settings()


@pytest.mark.parametrize(
    "forwarded",
    [
        "",
        " 127.0.0.1",
        "127.0.0.1,127.0.0.2",
        "127.0.0.1:80",
        "unknown",
        "fe80::1%eth0",
    ],
)
def test_invalid_real_ip_is_not_a_rate_limit_key(monkeypatch, forwarded):
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    policy = TrustedProxy(Settings(app_env="test"))
    with pytest.raises(RequestRejected, match="^Request rejected$"):
        policy.client_ip(
            {"client": ("127.0.0.2", 10000)}, Headers({"X-Real-IP": forwarded})
        )


def test_duplicate_real_ip_or_foreign_socket_peer_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses",
        lambda _: {ip_address("127.0.0.2")},
    )
    policy = TrustedProxy(Settings(app_env="test"))
    for peer, headers in [
        (
            "127.0.0.3",
            Headers({"X-Real-IP": "127.0.0.1", "X-Forwarded-For": "127.0.0.2"}),
        ),
        (
            "127.0.0.2",
            Headers(raw=[(b"x-real-ip", b"127.0.0.1"), (b"x-real-ip", b"127.0.0.3")]),
        ),
        ("127.0.0.2", Headers()),
        ("testclient", Headers({"X-Real-IP": "127.0.0.1"})),
    ]:
        with pytest.raises(RequestRejected):
            policy.client_ip({"client": (peer, 10000)}, headers)


def test_proxy_replacement_and_ipv4_mapped_address(monkeypatch):
    observed = {ip_address("127.0.0.2")}
    monkeypatch.setattr(
        "app.modules.access.trusted_proxy._proxy_addresses", lambda _: observed
    )
    policy = TrustedProxy(Settings(app_env="test"))
    headers = Headers(
        {
            "X-Real-IP": "::ffff:127.0.0.1",
            "X-Forwarded-For": "untrusted",
            "Forwarded": "untrusted",
        }
    )
    assert policy.client_ip({"client": ("127.0.0.2", 10000)}, headers) == ip_address(
        "127.0.0.1"
    )
    observed.clear()
    observed.add(ip_address("127.0.0.3"))
    with pytest.raises(RequestRejected):
        policy.client_ip({"client": ("127.0.0.2", 10000)}, headers)
    assert policy.client_ip({"client": ("127.0.0.3", 10000)}, headers) == ip_address(
        "127.0.0.1"
    )


def test_dns_failure_is_sanitized_and_never_trusts_headers(monkeypatch):
    def failed(*args, **kwargs):
        raise socket.gaierror("sensitive-dns-canary")

    monkeypatch.setattr("app.modules.access.trusted_proxy.socket.getaddrinfo", failed)
    with pytest.raises(
        AuthenticationUnavailable, match="^Authentication unavailable$"
    ) as caught:
        _proxy_addresses("proxy")
    assert caught.value.__suppress_context__ is True
