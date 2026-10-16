"""Internal session services; callers own the transaction and credential checks."""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.modules.access.membership import (
    AccessDenied,
    MembershipContext,
    resolve_membership,
)
from app.modules.access.models import SessionRecord, TenantMembership, User

IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=8)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}", flags=re.ASCII)


@dataclass(frozen=True)
class IssuedSession:
    token: SecretStr
    expires_at: datetime


def _token_hash(token: SecretStr) -> str:
    value = token.get_secret_value()
    if not _TOKEN_PATTERN.fullmatch(value):
        raise AccessDenied("Access denied")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _database_now(session: Session) -> datetime:
    # Read after acquiring row locks: transaction_timestamp() can be stale.
    return session.scalar(select(func.clock_timestamp()))


def _lock_user(session: Session, user_id: UUID) -> User | None:
    # All session operations lock the user first, then session rows by ID.
    return session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def issue_session(session: Session, *, user_id: UUID, tenant_id: UUID) -> IssuedSession:
    """Only after verified credentials; never publish the token before commit.

    Credential verification must hold this same user's FOR UPDATE lock until
    commit, so concurrent password changes cannot race a successful login.
    IDs are internal server state, not an unauthenticated client payload. No role
    or token can be supplied by the caller. Tenant selection requires membership.
    """
    if _lock_user(session, user_id) is None:
        raise AccessDenied("Access denied")
    context = resolve_membership(session, user_id=user_id, tenant_id=tenant_id)
    token = SecretStr(secrets.token_urlsafe(32))
    now = _database_now(session)
    expires_at = now + ABSOLUTE_TIMEOUT
    session.add(
        SessionRecord(
            tenant_id=context.tenant_id,
            membership_id=context.membership_id,
            token_hash=_token_hash(token),
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
    )
    session.flush()
    return IssuedSession(token=token, expires_at=expires_at)


def resolve_session(session: Session, token: SecretStr) -> MembershipContext:
    """Resolve fresh server context and touch activity, within caller transaction.

    User/session row locks serialize touches with revocation. They do not authorize
    business operations or replace their transaction/privilege locking protocol.
    """
    digest = _token_hash(token)
    user_id = _session_user_id(session, digest)
    if user_id is None or _lock_user(session, user_id) is None:
        raise AccessDenied("Access denied")
    record = session.scalar(
        select(SessionRecord)
        .join(TenantMembership, TenantMembership.id == SessionRecord.membership_id)
        .where(SessionRecord.token_hash == digest, TenantMembership.user_id == user_id)
        .with_for_update(of=SessionRecord)
        .execution_options(populate_existing=True)
    )
    if record is None or record.revoked_at is not None:
        raise AccessDenied("Access denied")
    now = _database_now(session)
    if (
        now < record.created_at
        or now >= record.expires_at
        or now >= record.last_seen_at + IDLE_TIMEOUT
    ):
        raise AccessDenied("Access denied")
    membership = session.get(
        TenantMembership, record.membership_id, populate_existing=True
    )
    if membership is None:
        raise AccessDenied("Access denied")
    context = resolve_membership(
        session, user_id=membership.user_id, tenant_id=record.tenant_id
    )
    record.last_seen_at = max(record.last_seen_at, now)
    session.flush()
    return context


def _revoke_where(session: Session, condition: ColumnElement[bool]) -> None:
    records = list(
        session.scalars(
            select(SessionRecord)
            .where(condition, SessionRecord.revoked_at.is_(None))
            .order_by(SessionRecord.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if records:
        now = _database_now(session)
        for record in records:
            record.revoked_at = max(record.created_at, now)
        session.flush()


def _session_user_id(session: Session, digest: str) -> UUID | None:
    return session.scalar(
        select(TenantMembership.user_id)
        .join(SessionRecord, SessionRecord.membership_id == TenantMembership.id)
        .where(SessionRecord.token_hash == digest)
    )


def revoke_session(session: Session, token: SecretStr) -> None:
    """Idempotent; never disclose whether a token exists."""
    try:
        digest = _token_hash(token)
    except AccessDenied:
        return
    user_id = _session_user_id(session, digest)
    if user_id is None or _lock_user(session, user_id) is None:
        return
    _revoke_where(session, SessionRecord.token_hash == digest)


def revoke_membership_sessions(session: Session, membership_id: UUID) -> None:
    """Call in the same transaction as membership/privilege changes."""
    user_id = session.scalar(
        select(TenantMembership.user_id).where(TenantMembership.id == membership_id)
    )
    if user_id is None or _lock_user(session, user_id) is None:
        return
    _revoke_where(session, SessionRecord.membership_id == membership_id)


def revoke_user_sessions(session: Session, user_id: UUID) -> None:
    """Call in the same transaction as password changes or user deactivation."""
    if _lock_user(session, user_id) is None:
        return
    memberships = select(TenantMembership.id).where(TenantMembership.user_id == user_id)
    _revoke_where(session, SessionRecord.membership_id.in_(memberships))
