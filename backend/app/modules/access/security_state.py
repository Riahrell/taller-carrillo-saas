"""Global security revision kernel. Trusted callers own authorization and transaction.

Used by internal MFA login; public login and role/factor hooks are pending.
Never expose state mutations as a client-controlled flag. This result alone is
not a session proof.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.modules.access.login_challenges import revoke_user_challenges
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    MfaCredential,
    SecurityEvent,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.reset_state import close_password_resets
from app.modules.access.sessions import _database_now, _lock_user, revoke_user_sessions


class StaleSecurityRevision(Exception):
    """The caller's server-side security context is no longer current."""


@dataclass(frozen=True)
class SecurityState:
    user_id: UUID
    revision: int
    mfa_required: bool


def _user(session: Session, user_id: UUID) -> User:
    if not isinstance(user_id, UUID):
        raise ValueError("Invalid server user identity")
    user = _lock_user(session, user_id)
    if user is None or not user.is_active:
        raise AccessDenied("Access denied")
    return user


def _state(user: User) -> SecurityState:
    return SecurityState(user.id, user.security_revision, user.mfa_required)


def read_security_state(session: Session, *, user_id: UUID) -> SecurityState:
    return _state(_user(session, user_id))


def require_current_revision(
    session: Session, *, user_id: UUID, expected_revision: int
) -> SecurityState:
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Invalid server security revision")
    state = read_security_state(session, user_id=user_id)
    if state.revision != expected_revision:
        raise StaleSecurityRevision("Security context changed")
    return state


def _advance(session: Session, user: User, *, require_mfa: bool) -> SecurityState:
    user.security_revision += 1
    user.mfa_required = user.mfa_required or require_mfa
    # All transitions share the user -> challenges -> sessions lock order.
    from app.modules.access.rotation_state import close_rotations

    close_rotations(session, user.id)
    close_password_resets(session, user.id)
    revoke_user_challenges(session, user.id)
    revoke_user_sessions(session, user.id)
    session.add(
        SecurityEvent(
            user_id=user.id,
            credential_id=None,
            action="security_revision_advanced",
            security_revision=user.security_revision,
            occurred_at=_database_now(session),
        )
    )
    session.flush()
    return _state(user)


def advance_security_revision(
    session: Session, *, user_id: UUID, expected_revision: int
) -> SecurityState:
    require_current_revision(
        session, user_id=user_id, expected_revision=expected_revision
    )
    return _advance(session, _user(session, user_id), require_mfa=False)


def synchronize_mfa_requirement(
    session: Session, *, user_id: UUID, expected_revision: int
) -> SecurityState:
    """Latch current active-admin/factor requirement; never clear it on demotion.

    Future role/factor use cases must call inside their lock/transaction protocol.
    This query does not replace those hooks or authorize a membership change.
    """
    state = require_current_revision(
        session, user_id=user_id, expected_revision=expected_revision
    )
    if state.mfa_required:
        return state
    admin = session.scalar(
        select(
            exists().where(
                TenantMembership.user_id == user_id,
                TenantMembership.role == "admin",
                TenantMembership.is_active.is_(True),
                Tenant.id == TenantMembership.tenant_id,
                Tenant.is_active.is_(True),
            )
        )
    )
    factor = session.scalar(
        select(
            exists().where(
                MfaCredential.user_id == user_id,
                MfaCredential.revoked_at.is_(None),
            )
        )
    )
    if not admin and not factor:
        return state
    return _advance(session, _user(session, user_id), require_mfa=True)
