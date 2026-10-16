"""Resolve access from server-owned identity and tenant selection, never payloads."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.models import MembershipRole, Tenant, TenantMembership, User


class AccessDenied(Exception):
    """Intentionally does not disclose missing or inactive entities."""


@dataclass(frozen=True)
class MembershipContext:
    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    role: MembershipRole


def resolve_membership(
    session: Session, *, user_id: UUID, tenant_id: UUID
) -> MembershipContext:
    """Internal prerequisite for authorization, not an authentication mechanism.

    Callers must obtain both IDs from verified server state. This function grants
    no module permissions and does not commit or extend the caller's transaction.
    """
    membership = session.scalar(
        select(TenantMembership)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .join(User, User.id == TenantMembership.user_id)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
            User.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if membership is None:
        raise AccessDenied("Access denied")
    return MembershipContext(
        membership_id=membership.id,
        tenant_id=membership.tenant_id,
        user_id=membership.user_id,
        role=MembershipRole(membership.role),
    )
