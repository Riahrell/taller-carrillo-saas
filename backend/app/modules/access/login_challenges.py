"""Internal verified-login state. HTTP transport and CSRF remain separate."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.email_outbox import discard_mail
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    EnrollmentEmailChallenge,
    LoginChallenge,
    MfaEnrollment,
    MfaPending,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.sessions import (
    IssuedSession,
    _database_now,
    _lock_user,
    issue_session,
)

CHALLENGE_LIFETIME = timedelta(minutes=5)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}", flags=re.ASCII)
_BINDING = re.compile(r"[A-Za-z0-9_.-]{43,256}", flags=re.ASCII)


@dataclass(frozen=True)
class MembershipChoice:
    membership_id: UUID
    tenant_name: str


@dataclass(frozen=True)
class PendingLogin:
    token: SecretStr
    expires_at: datetime
    choices: tuple[MembershipChoice, ...]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _token_digest(token: SecretStr) -> str:
    value = token.get_secret_value()
    if not _TOKEN.fullmatch(value):
        raise AccessDenied("Access denied")
    return _digest(value)


def _binding_digest(binding: SecretStr) -> str:
    """Opaque server cookie already verified by the future HTTP/CSRF adapter.

    Format/length alone cannot prove its source or entropy. Never pass a body field
    or treat this cookie as proof that credentials have been verified.
    """
    value = binding.get_secret_value()
    if not _BINDING.fullmatch(value):
        raise AccessDenied("Access denied")
    return _digest("pending-login-browser-v1:" + value)


def _password_fingerprint(user: User) -> str:
    return hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()


def _active_memberships(session: Session, user_id: UUID):
    return session.execute(
        select(TenantMembership, Tenant.name)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
        .order_by(Tenant.name, TenantMembership.id)
        .execution_options(populate_existing=True)
    ).all()


def _close_pending(session: Session, user_id: UUID) -> None:
    # Caller holds the user lock. All paths use user -> challenges (UUID) -> sessions.
    records = session.scalars(
        select(LoginChallenge)
        .where(LoginChallenge.user_id == user_id, LoginChallenge.finished_at.is_(None))
        .order_by(LoginChallenge.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if records:
        now = _database_now(session)
        for record in records:
            record.finished_at = max(now, record.created_at)
        session.flush()


def _close_enrollments(
    session: Session, user_id: UUID, binding_hash: str | None
) -> None:
    query = (
        select(MfaEnrollment)
        .join(
            EnrollmentEmailChallenge,
            EnrollmentEmailChallenge.id == MfaEnrollment.challenge_id,
        )
        .join(MfaPending, MfaPending.id == EnrollmentEmailChallenge.pending_id)
        .where(
            EnrollmentEmailChallenge.user_id == user_id,
            MfaEnrollment.closed_at.is_(None),
        )
    )
    if binding_hash is not None:
        query = query.where(MfaPending.binding_hash == binding_hash)
    rows = session.scalars(
        query.order_by(MfaEnrollment.id)
        .with_for_update(of=MfaEnrollment)
        .execution_options(populate_existing=True)
    ).all()
    if rows:
        now = _database_now(session)
        for row in rows:
            row.closed_at = max(now, row.created_at)
            row.key_id = row.nonce = row.ciphertext = None
        session.flush()


def _close_enrollment_emails(
    session: Session, user_id: UUID, *, binding_hash: str | None = None
) -> None:
    """Caller holds user and preceding pending locks; mailbox children come next."""
    query = (
        select(EnrollmentEmailChallenge)
        .join(MfaPending, MfaPending.id == EnrollmentEmailChallenge.pending_id)
        .where(
            EnrollmentEmailChallenge.user_id == user_id,
            EnrollmentEmailChallenge.closed_at.is_(None),
        )
    )
    if binding_hash is not None:
        query = query.where(MfaPending.binding_hash == binding_hash)
    rows = session.scalars(
        query.order_by(EnrollmentEmailChallenge.id)
        .with_for_update(of=EnrollmentEmailChallenge)
        .execution_options(populate_existing=True)
    ).all()
    if rows:
        now = _database_now(session)
        for row in rows:
            row.closed_at = max(now, row.created_at, row.consumed_at or row.created_at)
            discard_mail(session, row.id, row.closed_at)
        session.flush()
    _close_enrollments(session, user_id, binding_hash)


def revoke_user_challenges(session: Session, user_id: UUID) -> None:
    """Same transaction as account/password changes; caller owns commit/rollback."""
    if _lock_user(session, user_id) is not None:
        _close_pending(session, user_id)
        # Global lock order: user -> login challenges -> MFA pendings -> sessions.
        records = session.scalars(
            select(MfaPending)
            .where(MfaPending.user_id == user_id, MfaPending.finished_at.is_(None))
            .order_by(MfaPending.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if records:
            now = _database_now(session)
            for record in records:
                record.finished_at = max(now, record.created_at)
            session.flush()
        _close_enrollment_emails(session, user_id)


def _issue_challenge(
    session: Session,
    *,
    user: User,
    binding_hash: str,
    choices: tuple[MembershipChoice, ...],
) -> PendingLogin:
    # Private to credential authentication: caller verified password under user lock.
    token = SecretStr(secrets.token_urlsafe(32))
    now = _database_now(session)
    expires_at = now + CHALLENGE_LIFETIME
    session.add(
        LoginChallenge(
            user_id=user.id,
            token_hash=_token_digest(token),
            binding_hash=binding_hash,
            password_fingerprint=_password_fingerprint(user),
            created_at=now,
            expires_at=expires_at,
        )
    )
    session.flush()
    return PendingLogin(token=token, expires_at=expires_at, choices=choices)


def _select_membership(
    session: Session, *, token_hash: str, binding_hash: str, membership_id: UUID
) -> IssuedSession | None:
    user_id = session.scalar(
        select(LoginChallenge.user_id).where(LoginChallenge.token_hash == token_hash)
    )
    if user_id is None or (user := _lock_user(session, user_id)) is None:
        return None
    record = session.scalar(
        select(LoginChallenge)
        .where(
            LoginChallenge.token_hash == token_hash, LoginChallenge.user_id == user_id
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None or record.finished_at is not None:
        return None
    now = _database_now(session)
    if not record.created_at <= now < record.expires_at:
        return None
    if not hmac.compare_digest(record.binding_hash, binding_hash):
        return None
    # A bound selection consumes the challenge even when the membership is rejected.
    record.finished_at = now
    session.flush()
    if not user.is_active or not hmac.compare_digest(
        record.password_fingerprint, _password_fingerprint(user)
    ):
        return None
    membership = session.scalar(
        select(TenantMembership)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(
            TenantMembership.id == membership_id,
            TenantMembership.user_id == user_id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if membership is None:
        return None
    try:
        return issue_session(session, user_id=user_id, tenant_id=membership.tenant_id)
    except AccessDenied:
        return None
