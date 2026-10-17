"""Internal session services; callers own the transaction and credential checks."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.modules.access.membership import (
    AccessDenied,
    MembershipContext,
    resolve_membership,
)
from app.modules.access.models import (
    MfaCredential,
    MfaPending,
    SessionRecord,
    TenantMembership,
    User,
)

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


def _requires_mfa(session: Session, user: User) -> bool:
    # Role-based latching is integrated separately, before public MFA activation.
    return user.mfa_required or bool(
        session.scalar(
            select(
                exists().where(
                    MfaCredential.user_id == user.id, MfaCredential.revoked_at.is_(None)
                )
            )
        )
    )


def issue_session(session: Session, *, user_id: UUID, tenant_id: UUID) -> IssuedSession:
    """Only after verified credentials; never publish the token before commit.

    Credential verification must hold this same user's FOR UPDATE lock until
    commit, so concurrent password changes cannot race a successful login.
    IDs are internal server state, not an unauthenticated client payload. No role
    or token can be supplied by the caller. Tenant selection requires membership.
    """
    user = _lock_user(session, user_id)
    if user is None or not user.is_active or _requires_mfa(session, user):
        raise AccessDenied("Access denied")
    context = resolve_membership(session, user_id=user_id, tenant_id=tenant_id)
    return _create_session(
        session, user=user, context=context, now=_database_now(session)
    )


def _create_session(
    session: Session,
    *,
    user: User,
    context: MembershipContext,
    now: datetime,
    mfa_pending_id: UUID | None = None,
) -> IssuedSession:
    """Private persistence after authentication guards, under the user's lock.

    Never call from an adapter or publish before the owning transaction commits.
    MFA callers must consume the bound verified pending in this same transaction.
    """
    token = SecretStr(secrets.token_urlsafe(32))
    expires_at = now + ABSOLUTE_TIMEOUT
    record = SessionRecord(
        tenant_id=context.tenant_id,
        membership_id=context.membership_id,
        token_hash=_token_hash(token),
        security_revision=user.security_revision,
        mfa_pending_id=mfa_pending_id,
        created_at=now,
        last_seen_at=now,
        expires_at=expires_at,
    )
    if not user.is_active or context.user_id != user.id:
        raise AccessDenied("Access denied")
    _validate_session_proof(session, record, user)
    session.add(record)
    session.flush()
    return IssuedSession(token=token, expires_at=expires_at)


def _validate_session_proof(
    session: Session, record: SessionRecord, user: User
) -> None:
    if (
        record.security_revision is None
        or record.security_revision != user.security_revision
    ):
        raise AccessDenied("Access denied")
    if record.mfa_pending_id is None:
        if _requires_mfa(session, user):
            raise AccessDenied("Access denied")
        return
    proof = session.get(MfaPending, record.mfa_pending_id, populate_existing=True)
    # Local digest avoids the login_challenges -> sessions import cycle.
    fingerprint = hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()
    if (
        proof is None
        or proof.user_id != user.id
        or proof.purpose != "login_verified"
        or proof.security_revision != user.security_revision
        or proof.factor_verified_at is None
        or proof.credential_id is None
        or proof.finished_at != record.created_at
        or not proof.password_verified_at
        <= proof.factor_verified_at
        <= proof.created_at
        <= record.created_at
        < proof.expires_at
        or not hmac.compare_digest(proof.password_fingerprint, fingerprint)
        or session.scalar(
            select(MfaCredential.id).where(
                MfaCredential.id == proof.credential_id,
                MfaCredential.user_id == user.id,
                MfaCredential.revoked_at.is_(None),
            )
        )
        is None
    ):
        raise AccessDenied("Access denied")


def resolve_session(session: Session, token: SecretStr) -> MembershipContext:
    """Resolve fresh server context and touch activity, within caller transaction.

    User/session row locks serialize touches with revocation. They do not authorize
    business operations or replace their transaction/privilege locking protocol.
    """
    digest = _token_hash(token)
    user_id = _session_user_id(session, digest)
    if user_id is None or (user := _lock_user(session, user_id)) is None:
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
    _validate_session_proof(session, record, user)
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
