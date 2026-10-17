"""Session-authorized reads, new users and atomic, audited membership changes."""

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

from argon2.exceptions import HashingError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    field_validator,
)
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access.authentication import Credentials, _limit_transaction
from app.modules.access.login_challenges import revoke_user_challenges
from app.modules.access.membership import AccessDenied, MembershipContext
from app.modules.access.models import (
    AuditEvent,
    MembershipRole,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password
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


class UserCreation(Credentials):
    """New global identity only; never attach or reset an existing account."""

    password: SecretStr = Field(min_length=15, max_length=128, repr=False, exclude=True)
    role: MembershipRole


class UserCreationConflict(Exception):
    """Email unavailable; do not disclose identity, state or other tenants."""


class LastAdministratorRequired(Exception):
    """An ordinary membership change must retain an active administrator."""


class AdministrationUnavailable(RuntimeError):
    """Safe infrastructure error, including a failed commit; do not auto-retry."""


class AdministrationDenied(AccessDenied):
    """Valid identity without permission, or absent/foreign target; same response."""


class MembershipStateChanged(Exception):
    """The authorized target no longer matches the reviewed representation."""


class MembershipView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    membership_id: UUID
    role: MembershipRole
    is_active: bool
    actor_membership_id: UUID | None = Field(default=None, exclude=True, repr=False)

    @property
    def etag(self) -> str:
        # Representation validator, not authorization, history version or secret.
        digest = hashlib.sha256(self.model_dump_json().encode()).hexdigest()
        return f'"membership-v1-{digest}"'


class MembershipListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    limit: int = Field(default=25, ge=1, le=100, strict=True)
    after: UUID | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def canonical_limit(cls, value: object) -> object:
        if isinstance(value, str):
            if re.fullmatch(r"[1-9][0-9]{0,2}", value, flags=re.ASCII) is None:
                raise ValueError("Invalid page limit")
            return int(value)
        return value


class MembershipListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    membership_id: UUID
    email: str
    role: MembershipRole
    is_active: bool
    user_is_active: bool


class MembershipPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[MembershipListItem, ...]
    next_after: UUID | None
    actor_membership_id: UUID | None = Field(default=None, exclude=True, repr=False)


@dataclass(frozen=True)
class MembershipChangeResult:
    membership_id: UUID
    role: MembershipRole
    is_active: bool
    changed: bool
    session_revoked: bool = False
    actor_membership_id: UUID | None = None


class MembershipAdministrator:
    """Own a fresh READ COMMITTED transaction; return only after commit.

    No caller-supplied actor, tenant or context. An adapter must pass the private
    session cookie and independently enforce CSRF before calling this service.
    Creation shares the lock protocol; future global user changes must do so too.
    """

    def __init__(self, engine: Engine) -> None:
        self._factory = create_session_factory(engine)

    def create_user(
        self,
        token: SecretStr,
        *,
        data: UserCreation,
        expected_actor: UUID,
    ) -> MembershipView:
        if not isinstance(data, UserCreation) or not isinstance(expected_actor, UUID):
            raise AccessDenied("Access denied")
        digest = _token_hash(token)
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                tenant, actor_user = self._locked_tenant(session, digest)
                if _lock_user(session, actor_user) is None:
                    raise AccessDenied("Access denied")
                actor = self._administrator(session, token, tenant.id)
                if actor.membership_id != expected_actor:
                    raise AdministrationDenied("Access denied")
                # No existing identity is locked, attached, reactivated or reset.
                if session.scalar(select(User.id).where(User.email == str(data.email))):
                    raise UserCreationConflict("User creation unavailable")
                encoded = hash_password(data.password)
                user_id = session.scalar(
                    insert(User)
                    .values(
                        email=str(data.email), password_hash=encoded, is_active=True
                    )
                    .on_conflict_do_nothing(index_elements=[User.email])
                    .returning(User.id)
                )
                if user_id is None:
                    # A competing tenant can win the global email uniqueness race.
                    raise UserCreationConflict("User creation unavailable")
                member = TenantMembership(
                    tenant_id=actor.tenant_id,
                    user_id=user_id,
                    role=data.role,
                    is_active=True,
                )
                session.add(member)
                session.flush()
                session.add(
                    AuditEvent(
                        tenant_id=actor.tenant_id,
                        actor_membership_id=actor.membership_id,
                        target_membership_id=member.id,
                        action="membership_created",
                        before_role=None,
                        before_active=None,
                        after_role=data.role,
                        after_active=True,
                        created_at=_database_now(session),
                    )
                )
                session.flush()
                result = self._view(member).model_copy(
                    update={"actor_membership_id": actor.membership_id}
                )
            return result
        except (SQLAlchemyError, HashingError):
            raise AdministrationUnavailable("Administration unavailable") from None

    def change_membership(
        self,
        token: SecretStr,
        *,
        membership_id: UUID,
        change: MembershipChange,
        expected_etag: str | None = None,
        expected_actor: UUID | None = None,
    ) -> MembershipChangeResult:
        if not isinstance(membership_id, UUID) or not isinstance(
            change, MembershipChange
        ):
            raise AccessDenied("Access denied")
        digest = _token_hash(token)
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor, target = self._authorized_target(
                    session, token, digest, membership_id
                )
                if expected_actor is not None and actor.membership_id != expected_actor:
                    raise AdministrationDenied("Access denied")
                if (
                    expected_etag is not None
                    and expected_etag != self._view(target).etag
                ):
                    raise MembershipStateChanged("Membership state changed")
                result = self._change(session, actor, target, change)
            return result
        except SQLAlchemyError:
            raise AdministrationUnavailable("Administration unavailable") from None

    def current_membership(
        self, token: SecretStr, *, membership_id: UUID
    ) -> MembershipView:
        if not isinstance(membership_id, UUID):
            raise AccessDenied("Access denied")
        digest = _token_hash(token)
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                actor, target = self._authorized_target(
                    session, token, digest, membership_id
                )
                result = self._view(target).model_copy(
                    update={"actor_membership_id": actor.membership_id}
                )
            return result
        except SQLAlchemyError:
            raise AdministrationUnavailable("Administration unavailable") from None

    def list_memberships(
        self, token: SecretStr, *, query: MembershipListQuery
    ) -> MembershipPage:
        if not isinstance(query, MembershipListQuery):
            raise AccessDenied("Access denied")
        digest = _token_hash(token)
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                tenant, user_id = self._locked_tenant(session, digest)
                if _lock_user(session, user_id) is None:
                    raise AccessDenied("Access denied")
                actor = self._administrator(session, token, tenant.id)
                statement = (
                    select(
                        TenantMembership.id,
                        User.email,
                        TenantMembership.role,
                        TenantMembership.is_active,
                        User.is_active.label("user_is_active"),
                    )
                    .join(User, User.id == TenantMembership.user_id)
                    .where(TenantMembership.tenant_id == actor.tenant_id)
                    .order_by(TenantMembership.id)
                    .limit(query.limit + 1)
                )
                if query.after is not None:
                    statement = statement.where(TenantMembership.id > query.after)
                # Plain read of targets: never lock more users after the actor's
                # session. The tenant lock already serializes coordinated changes.
                rows = session.execute(statement).all()
                items = tuple(
                    MembershipListItem(
                        membership_id=row.id,
                        email=row.email,
                        role=row.role,
                        is_active=row.is_active,
                        user_is_active=row.user_is_active,
                    )
                    for row in rows[: query.limit]
                )
                result = MembershipPage(
                    actor_membership_id=actor.membership_id,
                    items=items,
                    next_after=items[-1].membership_id
                    if len(rows) > query.limit
                    else None,
                )
            return result
        except SQLAlchemyError:
            raise AdministrationUnavailable("Administration unavailable") from None

    @staticmethod
    def _view(target: TenantMembership) -> MembershipView:
        return MembershipView(
            membership_id=target.id, role=target.role, is_active=target.is_active
        )

    @staticmethod
    def _locked_tenant(session: Session, digest: str) -> tuple[Tenant, UUID]:
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
        return tenant, hint.user_id

    @staticmethod
    def _administrator(
        session: Session, token: SecretStr, tenant_id: UUID
    ) -> MembershipContext:
        actor = resolve_session(session, token)
        if actor.tenant_id != tenant_id or actor.role != MembershipRole.ADMIN:
            raise AdministrationDenied("Access denied")
        return actor

    @classmethod
    def _authorized_target(
        cls,
        session: Session,
        token: SecretStr,
        digest: str,
        membership_id: UUID,
    ) -> tuple[MembershipContext, TenantMembership]:
        tenant, actor_user = cls._locked_tenant(session, digest)
        target_user = session.scalar(
            select(TenantMembership.user_id).where(
                TenantMembership.id == membership_id,
                TenantMembership.tenant_id == tenant.id,
            )
        )
        # Lock ALL involved users before any child row. This also prevents
        # crossed actor/target operations in different tenants from deadlocking.
        for user_id in sorted({actor_user, target_user} - {None}):
            if _lock_user(session, user_id) is None:
                raise AccessDenied("Access denied")
        actor = cls._administrator(session, token, tenant.id)
        if target_user is None:
            raise AdministrationDenied("Access denied")
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
            raise AdministrationDenied("Access denied")
        return actor, target

    def _change(
        self,
        session: Session,
        actor: MembershipContext,
        target: TenantMembership,
        change: MembershipChange,
    ) -> MembershipChangeResult:
        changed = (target.role, target.is_active) != (change.role, change.is_active)
        if not changed:
            return MembershipChangeResult(
                target.id,
                change.role,
                change.is_active,
                False,
                actor_membership_id=actor.membership_id,
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
        return MembershipChangeResult(
            target.id,
            change.role,
            change.is_active,
            True,
            session_revoked=actor.membership_id == target.id,
            actor_membership_id=actor.membership_id,
        )

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
