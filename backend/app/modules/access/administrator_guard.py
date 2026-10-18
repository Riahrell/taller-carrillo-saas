"""Shared tenant-first administrator guard; callers own the transaction."""

from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.membership import AccessDenied, MembershipContext
from app.modules.access.models import MembershipRole, SessionRecord, Tenant
from app.modules.access.reauthentication import require_recent_administrator
from app.modules.access.sessions import _token_hash, resolve_session


class AdministrativeDenied(AccessDenied):
    """A valid session lacks the requested administrative context."""


def authorize_administrator(
    session: Session,
    token: SecretStr,
    expected_actor: UUID | None,
    *,
    recent: bool = True,
) -> MembershipContext:
    if (recent or expected_actor is not None) and not isinstance(expected_actor, UUID):
        raise AccessDenied("Access denied")
    # Unlocked hint locates the parent only; never grants authority.
    tenant_id = session.scalar(
        select(SessionRecord.tenant_id).where(
            SessionRecord.token_hash == _token_hash(token)
        )
    )
    if tenant_id is None:
        raise AccessDenied("Access denied")
    tenant = session.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tenant is None or not tenant.is_active:
        raise AccessDenied("Access denied")
    actor = resolve_session(session, token)
    if (
        actor.tenant_id != tenant.id
        or actor.role != MembershipRole.ADMIN
        or expected_actor is not None
        and expected_actor != actor.membership_id
    ):
        raise AdministrativeDenied("Access denied")
    if recent:
        require_recent_administrator(session, token, expected_actor=expected_actor)
    return actor
