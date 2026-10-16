"""Internal, session-authorized membership changes. No public administration API."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr, StrictBool
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.authentication import _limit_transaction
from app.modules.access.login_challenges import revoke_user_challenges
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    AuditEvent,
    MembershipRole,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.sessions import (
    _database_now,
    _lock_user,
    _token_hash,
    resolve_session,
    revoke_membership_sessions,
)


class MembershipChange(BaseModel):
    """Desired state, never proof of authority; both attributes are explicit."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    role: MembershipRole
    is_active: StrictBool


class LastAdministratorRequired(Exception):
    """An ordinary membership change must retain an active administrator."""


class AdministrationUnavailable(RuntimeError):
    """Safe infrastructure error, including a failed commit; do not auto-retry."""


@dataclass(frozen=True)
class MembershipChangeResult:
    membership_id: UUID
    role: MembershipRole
    is_active: bool
    changed: bool


class MembershipAdministrator:
    """Own a fresh READ COMMITTED transaction; return only after commit.

    No caller-supplied actor, tenant or context. An adapter must pass the private
    session cookie and independently enforce CSRF before calling this service.
    Future membership creation/global user changes must share the lock protocol.
    """

    def __init__(self, engine: Engine) -> None:
        self._factory = create_session_factory(engine)

    def change_membership(
        self, token: SecretStr, *, membership_id: UUID, change: MembershipChange
    ) -> MembershipChangeResult:
        if not isinstance(membership_id, UUID) or not isinstance(
            change, MembershipChange
        ):
            raise AccessDenied("Access denied")
        digest = _token_hash(token)
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                result = self._change(session, token, digest, membership_id, change)
            return result
        except SQLAlchemyError:
            raise AdministrationUnavailable("Administration unavailable") from None

    def _change(
        self,
        session: Session,
        token: SecretStr,
        digest: str,
        membership_id: UUID,
        change: MembershipChange,
    ) -> MembershipChangeResult:
        # Unlocked hints locate the parents. They confer no authority and all
        # relevant state is re-read after the ordered parent locks below.
        hint = session.execute(
            select(TenantMembership.user_id, SessionRecord.tenant_id)
            .join(SessionRecord, SessionRecord.membership_id == TenantMembership.id)
            .where(SessionRecord.token_hash == digest)
        ).one_or_none()
        if hint is None:
            raise AccessDenied("Access denied")
        tenant = session.scalar(
            select(Tenant)
            .where(Tenant.id == hint.tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if tenant is None or not tenant.is_active:
            raise AccessDenied("Access denied")
        target_user = session.scalar(
            select(TenantMembership.user_id).where(
                TenantMembership.id == membership_id,
                TenantMembership.tenant_id == tenant.id,
            )
        )
        if target_user is None:
            raise AccessDenied("Access denied")
        # Lock ALL involved users before any child row. This also prevents
        # crossed actor/target operations in different tenants from deadlocking.
        for user_id in sorted({hint.user_id, target_user}):
            if _lock_user(session, user_id) is None:
                raise AccessDenied("Access denied")
        actor = resolve_session(session, token)
        if actor.tenant_id != tenant.id or actor.role != MembershipRole.ADMIN:
            raise AccessDenied("Access denied")
        target = session.scalar(
            select(TenantMembership)
            .where(
                TenantMembership.id == membership_id,
                TenantMembership.tenant_id == actor.tenant_id,
                TenantMembership.user_id == target_user,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if target is None:
            raise AccessDenied("Access denied")
        changed = (target.role, target.is_active) != (change.role, change.is_active)
        if not changed:
            return MembershipChangeResult(
                target.id, change.role, change.is_active, False
            )
        self._retain_admin(session, target, change)
        before_role, before_active = target.role, target.is_active
        target.role, target.is_active = change.role, change.is_active
        # Pending identities are global: cancel all of this target user's
        # challenges, but revoke sessions only in the affected membership.
        revoke_user_challenges(session, target.user_id)
        revoke_membership_sessions(session, target.id)
        session.add(
            AuditEvent(
                tenant_id=actor.tenant_id,
                actor_membership_id=actor.membership_id,
                target_membership_id=target.id,
                action="membership_changed",
                before_role=before_role,
                after_role=change.role,
                before_active=before_active,
                after_active=change.is_active,
                created_at=_database_now(session),
            )
        )
        session.flush()
        return MembershipChangeResult(target.id, change.role, change.is_active, True)

    @staticmethod
    def _retain_admin(
        session: Session, target: TenantMembership, change: MembershipChange
    ) -> None:
        removing_admin = (
            target.role == MembershipRole.ADMIN
            and target.is_active
            and (change.role != MembershipRole.ADMIN or not change.is_active)
        )
        if not removing_admin:
            return
        others = session.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .join(User, User.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == target.tenant_id,
                TenantMembership.id != target.id,
                TenantMembership.role == MembershipRole.ADMIN,
                TenantMembership.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        target_user = session.get(User, target.user_id, populate_existing=True)
        if target_user.is_active and not others:
            raise LastAdministratorRequired("An active administrator is required")
