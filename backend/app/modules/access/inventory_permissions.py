"""Versioned administrative grant management and current operational read guard."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.administration import (
    AdministrationDenied,
    AdministrationUnavailable,
    MembershipStateChanged,
)
from app.modules.access.authentication import _limit_transaction
from app.modules.access.employee_invitations import _authorize
from app.modules.access.inventory_permission_models import (
    InventoryReadGrant,
    InventoryWithdrawalGrant,
)
from app.modules.access.membership import AccessDenied
from app.modules.access.models import SessionRecord, Tenant, TenantMembership, User
from app.modules.access.sessions import _database_now, _token_hash, resolve_session


class InventoryReadChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    granted: StrictBool


class InventoryReadPermission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    membership_id: UUID
    granted: bool
    eligible: bool
    effective: bool
    revision: str
    actor_membership_id: UUID = Field(exclude=True, repr=False)

    @property
    def etag(self):
        return f'"inventory-read-v1-{self.membership_id}-{self.revision}"'


def latest(session, tenant_id, membership_id, model=InventoryReadGrant):
    return session.scalar(
        select(model)
        .where(
            model.tenant_id == tenant_id,
            model.membership_id == membership_id,
        )
        .order_by(model.revision.desc())
        .limit(1)
    )


def authorize_inventory_read(session, token, expected_actor):
    if not isinstance(expected_actor, UUID):
        raise AccessDenied("Access denied")
    tenant_id = session.scalar(
        select(SessionRecord.tenant_id).where(
            SessionRecord.token_hash == _token_hash(token)
        )
    )
    if (
        tenant_id is None
        or session.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
            .with_for_update()
        )
        is None
    ):
        raise AccessDenied("Access denied")
    actor = resolve_session(session, token)
    if actor.tenant_id != tenant_id or actor.membership_id != expected_actor:
        raise AdministrationDenied("Access denied")
    if actor.role == "admin":
        return actor
    if actor.role == "cashier":
        grant = latest(
            session, actor.tenant_id, actor.membership_id, InventoryWithdrawalGrant
        )
        if grant is not None and grant.granted:
            return actor
    grant = latest(session, actor.tenant_id, actor.membership_id)
    if actor.role != "read_only" or grant is None or not grant.granted:
        raise AdministrationDenied("Access denied")
    return actor


class InventoryPermissions:
    grant_model = InventoryReadGrant
    eligible_role = "read_only"
    view_model = InventoryReadPermission

    def __init__(self, engine):
        self.factory = create_session_factory(engine)

    def view(self, session, actor, membership_id):
        row = session.execute(
            select(TenantMembership, User.is_active)
            .join(User, User.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == actor.tenant_id,
                TenantMembership.id == membership_id,
            )
        ).one_or_none()
        if row is None:
            raise AdministrationDenied("Access denied")
        member, user_active = row
        current = latest(session, actor.tenant_id, member.id, self.grant_model)
        granted = current.granted if current else False
        eligible = (
            member.role == self.eligible_role and member.is_active and user_active
        )
        return self.view_model(
            membership_id=member.id,
            granted=granted,
            eligible=eligible,
            effective=granted and eligible,
            revision=str(current.revision if current else 0),
            actor_membership_id=actor.membership_id,
        )

    def current(self, token, *, membership_id, expected_actor):
        try:
            with self.factory.begin() as session:
                _limit_transaction(session)
                actor = _authorize(session, token, expected_actor, recent=False)
                return self.view(session, actor, membership_id)
        except SQLAlchemyError:
            raise AdministrationUnavailable("Permissions unavailable") from None

    def change(self, token, *, membership_id, expected_actor, expected_etag, data):
        data = InventoryReadChange.model_validate(data.model_dump())
        try:
            with self.factory.begin() as session:
                _limit_transaction(session)
                actor = _authorize(session, token, expected_actor)
                before = self.view(session, actor, membership_id)
                if expected_etag != before.etag:
                    raise MembershipStateChanged("Permission changed")
                if data.granted and not before.eligible:
                    raise AdministrationDenied("Access denied")
                if data.granted != before.granted:
                    session.add(
                        self.grant_model(
                            tenant_id=actor.tenant_id,
                            membership_id=membership_id,
                            actor_membership_id=actor.membership_id,
                            revision=int(before.revision) + 1,
                            before_granted=before.granted,
                            granted=data.granted,
                            created_at=_database_now(session),
                        )
                    )
                    session.flush()
                _authorize(session, token, expected_actor)
                return self.view(session, actor, membership_id)
        except SQLAlchemyError:
            raise AdministrationUnavailable("Permissions unavailable") from None


class InventoryWithdrawalPermission(InventoryReadPermission):
    @property
    def etag(self):
        return f'"inventory-withdrawal-v1-{self.membership_id}-{self.revision}"'


class InventoryWithdrawalPermissions(InventoryPermissions):
    grant_model = InventoryWithdrawalGrant
    eligible_role = "cashier"
    view_model = InventoryWithdrawalPermission


def authorize_inventory_withdrawal(session, token, expected_actor):
    actor = authorize_inventory_read(session, token, expected_actor)
    if actor.role != "cashier":
        raise AdministrationDenied("Access denied")
    # The read guard already checked the current withdrawal grant for cashiers.
    return actor
