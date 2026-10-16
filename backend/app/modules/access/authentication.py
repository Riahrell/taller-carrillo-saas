"""Credential authentication with committed, shared account/IP failure limits."""

import hashlib
import secrets
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID

from argon2 import Type, extract_parameters
from argon2.exceptions import HashingError, InvalidHashError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.login_challenges import (
    MembershipChoice,
    PendingLogin,
    _active_memberships,
    _binding_digest,
    _close_pending,
    _issue_challenge,
    _select_membership,
    _token_digest,
)
from app.modules.access.membership import AccessDenied, resolve_membership
from app.modules.access.models import LoginThrottle, User
from app.modules.access.passwords import (
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.modules.access.sessions import IssuedSession, issue_session

WINDOW = timedelta(minutes=15)
ACCOUNT_LIMIT = 5
IP_LIMIT = 30


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("password")
    @classmethod
    def valid_unicode(cls, value: SecretStr) -> SecretStr:
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise ValueError("Password must be valid Unicode") from None
        return value


class AuthenticationUnavailable(RuntimeError):
    """Infrastructure failure distinct from a generic credential rejection."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ip_digest(address: IPv4Address | IPv6Address) -> str:
    if not isinstance(address, (IPv4Address, IPv6Address)):
        raise ValueError("A validated peer IP address is required")
    if isinstance(address, IPv6Address):
        address = IPv6Address(address.packed)
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
    return _digest(address.compressed)


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _lock_bucket(session: Session, scope: str, key: str) -> LoginThrottle:
    session.execute(
        insert(LoginThrottle)
        .values(scope=scope, key_hash=key, window_started_at=_now(session), failures=0)
        .on_conflict_do_nothing(index_elements=["scope", "key_hash"])
    )
    return session.scalar(
        select(LoginThrottle)
        .where(LoginThrottle.scope == scope, LoginThrottle.key_hash == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _refresh(bucket: LoginThrottle, now: datetime) -> None:
    if now >= bucket.window_started_at + WINDOW:
        bucket.failures = 0
        bucket.window_started_at = now


def _usable_hash(encoded: str) -> bool:
    try:
        params = extract_parameters(encoded)
        return params.type == Type.ID and params.version == 19
    except InvalidHashError:
        return False


class Authenticator:
    """One instance per worker, initialized before accepting credential requests.

    This service owns its transaction so rejections commit counters. It must not
    be called inside another business transaction. The selected tenant is only a
    candidate: credentials and active membership must both pass in server state.
    """

    def __init__(self, engine: Engine) -> None:
        self._factory = create_session_factory(engine)
        self._dummy_hash = hash_password(SecretStr(secrets.token_urlsafe(32)))

    def authenticate(
        self,
        credentials: Credentials,
        *,
        client_ip: IPv4Address | IPv6Address,
        selected_tenant_id: UUID,
    ) -> IssuedSession | None:
        """Return token only after commit, or None for every ordinary rejection.

        client_ip must come from a trusted network adapter, never a request body
        or an unverified forwarding header. No user/role/hash can be supplied.
        """
        ip_key = _ip_digest(client_ip)
        try:
            with self._factory.begin() as session:
                result = self._authenticate(
                    session, credentials, ip_key=ip_key, tenant_id=selected_tenant_id
                )
            return result
        except (SQLAlchemyError, HashingError):
            raise AuthenticationUnavailable("Authentication unavailable") from None

    def _authenticate(
        self,
        session: Session,
        credentials: Credentials,
        *,
        ip_key: str,
        tenant_id: UUID | None,
        binding_hash: str | None = None,
    ) -> IssuedSession | PendingLogin | None:
        # Global order: IP bucket, account bucket, user, challenges, session rows.
        ip = _lock_bucket(session, "ip", ip_key)
        now = _now(session)
        _refresh(ip, now)
        if ip.failures >= IP_LIMIT:
            return None
        account = _lock_bucket(session, "account", _digest(str(credentials.email)))
        now = _now(session)
        _refresh(ip, now)
        _refresh(account, now)
        if account.failures >= ACCOUNT_LIMIT:
            return None

        user = session.scalar(
            select(User)
            .where(User.email == str(credentials.email))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        encoded = user.password_hash if user is not None else self._dummy_hash
        # Malformed stored hashes also take the dummy verification path.
        usable = _usable_hash(encoded)
        valid = verify_password(
            credentials.password, encoded if usable else self._dummy_hash
        )
        allowed = valid and usable and user is not None and user.is_active
        memberships = []
        if allowed and tenant_id is None:
            memberships = _active_memberships(session, user.id)
            allowed = bool(memberships) and binding_hash is not None
        elif allowed:
            try:
                resolve_membership(session, user_id=user.id, tenant_id=tenant_id)
            except AccessDenied:
                allowed = False
        if not allowed:
            now = _now(session)
            for bucket in (ip, account):
                _refresh(bucket, now)
                # The first failure, rather than an earlier success, starts the window.
                if bucket.failures == 0:
                    bucket.window_started_at = now
                bucket.failures += 1
            session.flush()
            return None

        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(credentials.password)
        _close_pending(session, user.id)
        if tenant_id is not None or len(memberships) == 1:
            selected_tenant = (
                tenant_id if tenant_id is not None else memberships[0][0].tenant_id
            )
            issued = issue_session(session, user_id=user.id, tenant_id=selected_tenant)
        else:
            issued = _issue_challenge(
                session,
                user=user,
                binding_hash=binding_hash,
                choices=tuple(
                    MembershipChoice(membership.id, name)
                    for membership, name in memberships
                ),
            )
        account.failures = 0
        account.window_started_at = _now(session)
        session.flush()
        return issued

    def begin_login(
        self,
        credentials: Credentials,
        *,
        client_ip: IPv4Address | IPv6Address,
        browser_binding: SecretStr,
    ) -> IssuedSession | PendingLogin | None:
        """Choose a sole membership or return verified, bound pending identity.

        The future HTTP adapter supplies a verified server cookie as binding and
        a trusted peer IP. Neither value may come from the credential payload.
        No companies or tokens are returned on credential/rate rejection.
        """
        ip_key = _ip_digest(client_ip)
        try:
            binding_hash = _binding_digest(browser_binding)
        except AccessDenied:
            return None
        try:
            with self._factory.begin() as session:
                result = self._authenticate(
                    session,
                    credentials,
                    ip_key=ip_key,
                    tenant_id=None,
                    binding_hash=binding_hash,
                )
            return result
        except (SQLAlchemyError, HashingError):
            raise AuthenticationUnavailable("Authentication unavailable") from None

    def select_membership(
        self,
        *,
        challenge_token: SecretStr,
        browser_binding: SecretStr,
        membership_id: UUID,
    ) -> IssuedSession | None:
        """One bound attempt, current membership checks, atomic issue/consume.

        Owns its transaction, including a rejected bound selection. Publish the
        session cookie only after this method returns successfully after commit.
        """
        if not isinstance(membership_id, UUID):
            return None
        try:
            token_hash = _token_digest(challenge_token)
            binding_hash = _binding_digest(browser_binding)
        except AccessDenied:
            return None
        try:
            with self._factory.begin() as session:
                result = _select_membership(
                    session,
                    token_hash=token_hash,
                    binding_hash=binding_hash,
                    membership_id=membership_id,
                )
            return result
        except SQLAlchemyError:
            raise AuthenticationUnavailable("Authentication unavailable") from None
