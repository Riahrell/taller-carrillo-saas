"""Trust a socket peer on the dedicated proxy/API network, never client headers."""

import socket
from ipaddress import IPv4Address, IPv6Address, ip_address

from starlette.datastructures import Headers
from starlette.types import Scope

from app.core.config import ConfigurationError, Settings
from app.modules.access.authentication import AuthenticationUnavailable
from app.modules.access.http_security import RequestRejected


def _address(value: str) -> IPv4Address | IPv6Address:
    if not value or value.strip() != value or "%" in value or len(value) > 45:
        raise ValueError("Invalid address")
    address = ip_address(value)
    return (
        address.ipv4_mapped or address if isinstance(address, IPv6Address) else address
    )


def _proxy_addresses(host: str) -> set[IPv4Address | IPv6Address]:
    try:
        return {
            _address(record[4][0])
            for record in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError):
        raise AuthenticationUnavailable("Authentication unavailable") from None


class TrustedProxy:
    def __init__(self, settings: Settings) -> None:
        if settings.trusted_proxy_host is None:
            raise ConfigurationError("HTTP requires TRUSTED_PROXY_HOST")
        self.host = settings.trusted_proxy_host

    def client_ip(self, scope: Scope, headers: Headers) -> IPv4Address | IPv6Address:
        """Uvicorn MUST run with --no-proxy-headers so scope.client is unmodified.

        Docker DNS and network membership are operator-controlled. This is not
        authentication for arbitrary hosts sharing the Docker daemon/network.
        Resolve on each credential request to handle proxy container replacement.
        """
        peer = scope.get("client")
        try:
            observed = _address(peer[0]) if peer else None
        except (ValueError, TypeError):
            raise RequestRejected("Request rejected") from None
        if observed not in _proxy_addresses(self.host):
            raise RequestRejected("Request rejected")
        forwarded = headers.getlist("x-real-ip")
        if len(forwarded) != 1:
            raise RequestRejected("Request rejected")
        try:
            return _address(forwarded[0])
        except ValueError:
            raise RequestRejected("Request rejected") from None
