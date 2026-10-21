"""Private, email-bound invitation preparation; never session authority."""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.administration import (
    AdministrationDenied,
    AdministrationUnavailable,
    MembershipListQuery,
)
from app.modules.access.administrator_guard import (
    AdministrativeDenied,
    authorize_administrator,
)
from app.modules.access.authentication import _limit_transaction
from app.modules.access.employee_invitation_models import (
    EmployeeInvitation,
    EmployeeInvitationEvent,
)
from app.modules.access.membership import AccessDenied
from app.modules.access.sessions import _database_now


def _authorize(session, token, expected_actor, *, recent=True):
    try:
        return authorize_administrator(session, token, expected_actor, recent=recent)
    except AdministrativeDenied:
        raise AdministrationDenied("Access denied") from None


class InvitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    email: EmailStr = Field(max_length=254)

    @field_validator("email", mode="before")
    @classmethod
    def normalize(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class InvitationView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    email: str
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "expired", "revoked"]


class InvitationConflict(Exception):
    """Existing open invitation or bounded tenant capacity; no secret details."""


def representation(row, now):
    return InvitationView(
        id=row.id,
        email=row.email,
        created_at=row.created_at,
        expires_at=row.expires_at,
        status="revoked"
        if row.revoked_at
        else "expired"
        if now >= row.expires_at
        else "pending",
    )


class EmployeeInvitations:
    def __init__(self, engine):
        self.factory = create_session_factory(engine)

    def create(self, token, *, data: InvitationInput, expected_actor: UUID):
        if type(data) is not InvitationInput:
            raise AccessDenied("Access denied")
        data = InvitationInput.model_validate(data.model_dump())
        try:
            with self.factory.begin() as session:
                _limit_transaction(session)
                actor = _authorize(session, token, expected_actor)
                open_count = session.scalar(
                    select(func.count())
                    .select_from(EmployeeInvitation)
                    .where(
                        EmployeeInvitation.tenant_id == actor.tenant_id,
                        EmployeeInvitation.revoked_at.is_(None),
                    )
                )
                if open_count >= 100:
                    raise InvitationConflict("Invitation unavailable")
                now = _database_now(session)
                secret = SecretStr(secrets.token_urlsafe(32))
                row = EmployeeInvitation(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    actor_membership_id=actor.membership_id,
                    email=str(data.email),
                    token_hash=hashlib.sha256(
                        secret.get_secret_value().encode()
                    ).hexdigest(),
                    created_at=now,
                    expires_at=now + timedelta(hours=24),
                )
                session.add(row)
                session.flush()
                session.add(
                    EmployeeInvitationEvent(
                        tenant_id=actor.tenant_id,
                        invitation_id=row.id,
                        actor_membership_id=actor.membership_id,
                        action="CREATED",
                        created_at=now,
                    )
                )
                session.flush()
                _authorize(session, token, expected_actor)
                result = representation(row, now), secret, actor.membership_id
            return result
        except IntegrityError:
            raise InvitationConflict("Invitation unavailable") from None
        except SQLAlchemyError:
            raise AdministrationUnavailable("Invitation unavailable") from None

    def list(self, token, *, query: MembershipListQuery):
        query = MembershipListQuery.model_validate(query.model_dump())
        try:
            with self.factory.begin() as session:
                _limit_transaction(session)
                actor = _authorize(session, token, None, recent=False)
                statement = select(EmployeeInvitation).where(
                    EmployeeInvitation.tenant_id == actor.tenant_id
                )
                if query.after is not None:
                    statement = statement.where(EmployeeInvitation.id > query.after)
                rows = session.scalars(
                    statement.order_by(EmployeeInvitation.id).limit(query.limit + 1)
                ).all()
                now = _database_now(session)
                result = (
                    [representation(row, now) for row in rows[: query.limit]],
                    rows[query.limit - 1].id if len(rows) > query.limit else None,
                    actor.membership_id,
                )
            return result
        except SQLAlchemyError:
            raise AdministrationUnavailable("Invitations unavailable") from None

    def revoke(self, token, *, invitation_id: UUID, expected_actor: UUID):
        try:
            with self.factory.begin() as session:
                _limit_transaction(session)
                actor = _authorize(session, token, expected_actor)
                row = session.scalar(
                    select(EmployeeInvitation)
                    .where(
                        EmployeeInvitation.id == invitation_id,
                        EmployeeInvitation.tenant_id == actor.tenant_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise AdministrationDenied("Access denied")
                now = _database_now(session)
                if row.revoked_at is None:
                    row.revoked_at = now
                    session.add(
                        EmployeeInvitationEvent(
                            tenant_id=actor.tenant_id,
                            invitation_id=row.id,
                            actor_membership_id=actor.membership_id,
                            action="REVOKED",
                            created_at=now,
                        )
                    )
                    session.flush()
                _authorize(session, token, expected_actor)
                result = representation(row, now), actor.membership_id
            return result
        except SQLAlchemyError:
            raise AdministrationUnavailable("Invitation unavailable") from None
