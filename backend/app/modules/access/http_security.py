"""Cookie policy and bounded, session-bound signed double-submit CSRF proofs."""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.responses import Response

from app.core.config import ConfigurationError, Settings
from app.modules.access.login_challenges import PendingLogin
from app.modules.access.sessions import IssuedSession

PROOF_LIFETIME = timedelta(minutes=15)
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
_PROOF = re.compile(
    r"v1\.([1-9][0-9]{0,10})\.([A-Za-z0-9_-]{43})\.([0-9a-f]{64})", re.ASCII
)


class RequestRejected(Exception):
    """No tokens, header values or identity details in errors."""


def _now() -> datetime:
    return datetime.now(UTC)


def validate_origin(value: str, environment: str) -> bool:
    """Require the canonical ASCII origin a browser serializes, no URL components."""
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (
            not value.isascii()
            or "%" in value
            or any(char.isspace() for char in value)
            or parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or port is not None
            and not 1 <= port <= 65535
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
            canonical_host = (
                f"[{address.compressed}]" if address.version == 6 else str(address)
            )
            loopback = address.is_loopback
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError from None
            if len(host) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            ):
                raise ValueError from None
            canonical_host, loopback = host, host == "localhost"
        default_port = 443 if parsed.scheme == "https" else 80
        canonical = f"{parsed.scheme}://{canonical_host}"
        if port is not None and port != default_port:
            canonical += f":{port}"
        if canonical != value:
            raise ValueError
        if parsed.scheme == "http" and (environment == "production" or not loopback):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ConfigurationError(
            "Invalid canonical public origin for this environment"
        ) from None
    return parsed.scheme == "https"


class HttpSecurity:
    def __init__(self, settings: Settings) -> None:
        if settings.public_origin is None or settings.csrf_signing_key is None:
            raise ConfigurationError(
                "HTTP requires PUBLIC_ORIGIN and "
                "CSRF_SIGNING_KEY_FILE or CSRF_SIGNING_KEY"
            )
        self.origin = settings.public_origin
        self.secure = validate_origin(self.origin, settings.app_env)
        self._key = settings.csrf_signing_key
        prefix = "__Host-" if self.secure else ""
        self.session_cookie = prefix + "carrillo_session"
        self.preauth_cookie = prefix + "carrillo_preauth"
        self.csrf_cookie = prefix + "carrillo_csrf"
        self.challenge_cookie = prefix + "carrillo_login"
        self.browser_cookie = prefix + "carrillo_verified_browser"

    def _mac(self, purpose: str, binding: str, expiry: str, nonce: str) -> str:
        # Structured encoding avoids ambiguous concatenation; binding is never returned.
        message = json.dumps(
            [purpose, binding, expiry, nonce], separators=(",", ":")
        ).encode()
        return hmac.new(
            bytes.fromhex(self._key.get_secret_value()), message, hashlib.sha256
        ).hexdigest()

    def _sign(self, purpose: str, binding: str, expires: datetime) -> SecretStr:
        expiry, nonce = str(int(expires.timestamp())), secrets.token_urlsafe(32)
        return SecretStr(
            f"v1.{expiry}.{nonce}.{self._mac(purpose, binding, expiry, nonce)}"
        )

    def _verify(self, proof: str, purpose: str, binding: str) -> bool:
        match = _PROOF.fullmatch(proof)
        if match is None:
            return False
        expiry, nonce, signature = match.groups()
        now = int(_now().timestamp())
        return now < int(expiry) <= now + int(
            PROOF_LIFETIME.total_seconds()
        ) and hmac.compare_digest(signature, self._mac(purpose, binding, expiry, nonce))

    def _set(
        self,
        response: Response,
        name: str,
        value: SecretStr,
        expires: datetime,
        *,
        http_only: bool,
    ) -> None:
        response.set_cookie(
            name,
            value.get_secret_value(),
            expires=expires.astimezone(UTC),
            max_age=max(0, int((expires - _now()).total_seconds())),
            secure=self.secure,
            httponly=http_only,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"

    def start_preauth(self, response: Response) -> None:
        expires = _now() + PROOF_LIFETIME
        nonce_cookie = self._sign("preauth-cookie", "", expires)
        proof = self._sign("csrf-preauth", nonce_cookie.get_secret_value(), expires)
        self._set(response, self.preauth_cookie, nonce_cookie, expires, http_only=True)
        self._set(response, self.csrf_cookie, proof, expires, http_only=False)

    def set_session(self, response: Response, issued: IssuedSession) -> None:
        """Call only with the result of a committed authentication transaction."""
        if (
            not _OPAQUE.fullmatch(issued.token.get_secret_value())
            or issued.expires_at <= _now()
        ):
            raise RequestRejected("Request rejected")
        # Browser-session cookie: server idle/absolute deadlines still apply.
        response.set_cookie(
            self.session_cookie,
            issued.token.get_secret_value(),
            secure=self.secure,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.delete_cookie(
            self.preauth_cookie,
            path="/",
            secure=self.secure,
            httponly=True,
            samesite="lax",
        )
        self.refresh_csrf(response, issued)
        self.clear_challenge(response)

    def clear_challenge(self, response: Response) -> None:
        response.delete_cookie(
            self.challenge_cookie,
            path="/",
            secure=self.secure,
            httponly=True,
            samesite="lax",
        )
        response.headers["Cache-Control"] = "no-store"

    def set_challenge(self, response: Response, pending: PendingLogin) -> None:
        if (
            not _OPAQUE.fullmatch(pending.token.get_secret_value())
            or pending.expires_at <= _now()
        ):
            raise RequestRejected("Request rejected")
        self._set(
            response,
            self.challenge_cookie,
            pending.token,
            pending.expires_at,
            http_only=True,
        )

    def verify_browser_read(self, headers: Headers) -> None:
        # Cross-origin JS requires a preflight for this header; CORS stays closed.
        if headers.getlist("x-requested-with") != ["XMLHttpRequest"]:
            raise RequestRejected("Request rejected")
        origins, sites = headers.getlist("origin"), headers.getlist("sec-fetch-site")
        if origins and origins != [self.origin] or sites and sites != ["same-origin"]:
            raise RequestRejected("Request rejected")

    def read_cookies(self, headers: Headers) -> dict[str, str]:
        cookies: dict[str, str] = {}
        relevant = {
            self.browser_cookie,
            self.session_cookie,
            self.preauth_cookie,
            self.csrf_cookie,
            self.challenge_cookie,
        }
        raw = headers.getlist("cookie")
        if len(raw) > 1 or raw and len(raw[0]) > 8192:
            raise RequestRejected("Request rejected")
        for part in raw[0].split(";") if raw else []:
            name, separator, value = part.strip().partition("=")
            if name in relevant:
                if not separator or name in cookies:
                    raise RequestRejected("Request rejected")
                cookies[name] = value
        return cookies

    def login_binding(self, headers: Headers) -> SecretStr:
        if self.session_cookie in self.read_cookies(headers):
            raise RequestRejected("Request rejected")
        return self.preauth_binding(headers)

    def preauth_binding(self, headers: Headers) -> SecretStr:
        nonce = self.read_cookies(headers).get(self.preauth_cookie, "")
        if not self._verify(nonce, "preauth-cookie", ""):
            raise RequestRejected("Request rejected")
        return SecretStr(nonce)

    def bootstrap_preauth(self, response: Response, headers: Headers) -> None:
        """Preserve a live binding so refreshing CSRF does not cancel selection."""
        nonce = self.read_cookies(headers).get(self.preauth_cookie, "")
        if self._verify(nonce, "preauth-cookie", ""):
            expires = datetime.fromtimestamp(int(_PROOF.fullmatch(nonce).group(1)), UTC)
            self._set(
                response,
                self.csrf_cookie,
                self._sign("csrf-preauth", nonce, expires),
                expires,
                http_only=False,
            )
        else:
            self.clear_challenge(response)
            self.start_preauth(response)

    def refresh_csrf(self, response: Response, issued: IssuedSession) -> None:
        """Requires a live DB session checked by the caller; does not extend it."""
        if issued.expires_at <= _now() or not _OPAQUE.fullmatch(
            issued.token.get_secret_value()
        ):
            raise RequestRejected("Request rejected")
        expires = min(issued.expires_at, _now() + PROOF_LIFETIME)
        proof = self._sign("csrf-session", issued.token.get_secret_value(), expires)
        self._set(response, self.csrf_cookie, proof, expires, http_only=False)

    def clear_cookies(self, response: Response) -> None:
        for name in (
            self.session_cookie,
            self.preauth_cookie,
            self.csrf_cookie,
            self.challenge_cookie,
        ):
            response.delete_cookie(
                name,
                path="/",
                secure=self.secure,
                httponly=name != self.csrf_cookie,
                samesite="lax",
            )
        response.headers["Cache-Control"] = "no-store"

    def verify_request(self, headers: Headers) -> None:
        origins = headers.getlist("origin")
        if origins != [self.origin]:
            raise RequestRejected("Request rejected")
        sites = headers.getlist("sec-fetch-site")
        if sites and sites != ["same-origin"]:
            raise RequestRejected("Request rejected")
        cookies = self.read_cookies(headers)
        proofs = headers.getlist("x-csrf-token")
        cookie_proof = cookies.get(self.csrf_cookie, "")
        if (
            len(proofs) != 1
            or _PROOF.fullmatch(proofs[0]) is None
            or _PROOF.fullmatch(cookie_proof) is None
            or not hmac.compare_digest(proofs[0], cookie_proof)
        ):
            raise RequestRejected("Request rejected")
        if self.session_cookie in cookies:
            binding, purpose = cookies[self.session_cookie], "csrf-session"
            if not _OPAQUE.fullmatch(binding):
                raise RequestRejected("Request rejected")
        else:
            binding, purpose = cookies.get(self.preauth_cookie, ""), "csrf-preauth"
            if not self._verify(binding, "preauth-cookie", ""):
                raise RequestRejected("Request rejected")
        if not self._verify(cookie_proof, purpose, binding):
            raise RequestRejected("Request rejected")
