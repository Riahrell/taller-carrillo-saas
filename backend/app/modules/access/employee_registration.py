"""Invitation-bound mailbox verification; no membership until explicit approval."""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from functools import wraps
from uuid import uuid4

from argon2.exceptions import HashingError
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.administration import AdministrationDenied, MembershipListQuery
from app.modules.access.authentication import (
    AuthenticationUnavailable,
    _ip_digest,
    _limit_transaction,
    _lock_bucket,
    _record_failure,
    _refresh,
)
from app.modules.access.email_outbox import (
    MailEnvelope,
    MailProtectionError,
    MailPurpose,
    reserve_mail,
)
from app.modules.access.employee_invitation_models import EmployeeInvitation
from app.modules.access.employee_invitations import InvitationInput, _authorize
from app.modules.access.employee_registration_models import (
    EmployeeApproval,
    EmployeeRegistration,
)
from app.modules.access.mail_delivery import TransportOutcome
from app.modules.access.models import AuditEvent, Tenant, TenantMembership, User
from app.modules.access.password_policy import validate_new_password
from app.modules.access.passwords import hash_password
from app.modules.access.sessions import _database_now, _token_hash
from app.modules.access.smtp_transport import OutboundMessage


class RegistrationDenied(Exception):
    """Generic invitation/registration rejection; no identity enumeration."""


class RegistrationRate(Exception):
    pass


class RegistrationInput(InvitationInput):
    invitation: SecretStr = Field(min_length=43, max_length=43, repr=False)
    password: SecretStr = Field(min_length=15, max_length=128, repr=False)

    @field_validator("password")
    @classmethod
    def password_policy(cls, value):
        return validate_new_password(value)

    @field_validator("invitation")
    @classmethod
    def opaque(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value.get_secret_value()):
            raise ValueError("Invalid invitation")
        return value


def infrastructure(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (SQLAlchemyError, MailProtectionError, HashingError):
            raise AuthenticationUnavailable(
                "Employee registration unavailable"
            ) from None

    return call


def erase(row):
    row.key_id = row.nonce = row.ciphertext = None


def invitation_lock(session, invitation_id):
    tenant_id = session.scalar(
        select(EmployeeInvitation.tenant_id).where(
            EmployeeInvitation.id == invitation_id
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
        raise RegistrationDenied()
    row = session.scalar(
        select(EmployeeInvitation)
        .where(EmployeeInvitation.id == invitation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.revoked_at is not None:
        raise RegistrationDenied()
    return row


class EmployeeRegistrations:
    def __init__(self, engine, protection=None):
        self.factory = create_session_factory(engine)
        self.protection = protection

    def require(self):
        if (
            self.protection is None
            or self.protection.purpose != MailPurpose.EMPLOYEE_REGISTRATION
        ):
            raise AuthenticationUnavailable("Employee registration unavailable")

    def rate(self, peer):
        with self.factory.begin() as session:
            _limit_transaction(session)
            bucket = _lock_bucket(
                session,
                "ip",
                hashlib.sha256(
                    ("employee-registration:" + _ip_digest(peer)).encode()
                ).hexdigest(),
            )
            now = _database_now(session)
            _refresh(bucket, now)
            if bucket.failures >= 30:
                return False
            _record_failure(session, bucket)
            return True

    def queue(self, session, row, invitation, peer, now):
        reserved = reserve_mail(
            session,
            self.protection,
            user_id=row.id,
            client_ip=peer,
            checked_at=now,
            expires_at=row.expires_at,
        )
        if reserved is None:
            raise RegistrationRate()
        code = "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6)
        )
        envelope = self.protection.encrypt(
            recipient=SecretStr(invitation.email),
            token=SecretStr(code + secrets.token_urlsafe(28)[:37]),
            user_id=row.id,
            challenge_id=row.id,
            expires_at=row.expires_at,
        )
        row.code_hash = self.protection.registration_code_hash(row.id, code)
        row.key_id, row.nonce, row.ciphertext = (
            envelope.key_id,
            envelope.nonce,
            envelope.ciphertext,
        )
        row.send_state = "pending"

    @infrastructure
    def start(self, data, binding, peer):
        self.require()
        data = RegistrationInput.model_validate(
            dict(email=data.email, invitation=data.invitation, password=data.password)
        )
        if not self.rate(peer):
            raise RegistrationRate()
        with self.factory.begin() as session:
            _limit_transaction(session)
            digest = hashlib.sha256(
                data.invitation.get_secret_value().encode()
            ).hexdigest()
            identity = session.scalar(
                select(EmployeeInvitation.id).where(
                    EmployeeInvitation.token_hash == digest
                )
            )
            invitation = invitation_lock(session, identity)
            now = _database_now(session)
            if (
                invitation.email != str(data.email)
                or not invitation.created_at <= now < invitation.expires_at
            ):
                raise RegistrationDenied()
            row = session.scalar(
                select(EmployeeRegistration)
                .where(EmployeeRegistration.invitation_id == invitation.id)
                .with_for_update()
            )
            if row is not None and (
                row.verified_at is not None
                or (row.failures >= 5 and now < row.expires_at)
            ):
                raise RegistrationDenied()
            token = SecretStr(secrets.token_urlsafe(32))
            if row is None:
                row = EmployeeRegistration(
                    id=uuid4(),
                    tenant_id=invitation.tenant_id,
                    invitation_id=invitation.id,
                )
                session.add(row)
            if row.created_at is None or now >= row.expires_at:
                row.created_at, row.expires_at, row.failures = (
                    now,
                    min(now + timedelta(minutes=10), invitation.expires_at),
                    0,
                )
            row.token_hash, row.binding_hash = _token_hash(token), _token_hash(binding)
            row.password_hash = hash_password(data.password)
            self.queue(session, row, invitation, peer, now)
            session.flush()
            if not now <= _database_now(session) < row.expires_at:
                raise RegistrationDenied()
            return token, row.expires_at

    def locked(self, session, token, binding):
        hint = session.scalar(
            select(EmployeeRegistration.invitation_id).where(
                EmployeeRegistration.token_hash == _token_hash(token)
            )
        )
        invitation = invitation_lock(session, hint)
        row = session.scalar(
            select(EmployeeRegistration)
            .where(EmployeeRegistration.token_hash == _token_hash(token))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or not hmac.compare_digest(
            row.binding_hash, _token_hash(binding)
        ):
            raise RegistrationDenied()
        return invitation, row, _database_now(session)

    @infrastructure
    def status(self, token, binding):
        with self.factory.begin() as session:
            _limit_transaction(session)
            invitation, row, now = self.locked(session, token, binding)
            if row.verified_at:
                approved = session.scalar(
                    select(EmployeeApproval.id).where(
                        EmployeeApproval.registration_id == row.id
                    )
                )
                return "approved" if approved else "pending_approval"
            if row.failures >= 5 or not row.created_at <= now < min(
                row.expires_at, invitation.expires_at
            ):
                raise RegistrationDenied()
            return "awaiting_code"

    @infrastructure
    def resend(self, token, binding, peer):
        self.require()
        with self.factory.begin() as session:
            _limit_transaction(session)
            invitation, row, now = self.locked(session, token, binding)
            if (
                row.verified_at
                or row.failures >= 5
                or not row.created_at
                <= now
                < min(row.expires_at, invitation.expires_at)
            ):
                raise RegistrationDenied()
            self.queue(session, row, invitation, peer, now)
            session.flush()
            if not now <= _database_now(session) < row.expires_at:
                raise RegistrationDenied()

    @infrastructure
    def confirm(self, token, binding, code, peer):
        self.require()
        if not self.rate(peer):
            raise RegistrationRate()
        raw = code.get_secret_value().strip().upper()
        with self.factory.begin() as session:
            _limit_transaction(session)
            invitation, row, now = self.locked(session, token, binding)
            if row.verified_at:
                return True  # Opaque browser-bound receipt, never a session.
            if row.failures >= 5 or not row.created_at <= now < min(
                row.expires_at, invitation.expires_at
            ):
                raise RegistrationDenied()
            valid = (
                bool(re.fullmatch(r"[A-Z0-9]{6}", raw))
                and row.code_hash is not None
                and hmac.compare_digest(
                    row.code_hash, self.protection.registration_code_hash(row.id, raw)
                )
            )
            if not valid:
                row.failures += 1
                if row.failures >= 5:
                    row.code_hash = None
                    erase(row)
                return False
            # Email uniqueness races never attach or reset an existing identity.
            user = session.scalar(
                insert(User)
                .values(
                    email=invitation.email,
                    password_hash=row.password_hash,
                    is_active=True,
                )
                .on_conflict_do_nothing(index_elements=[User.email])
                .returning(User.id)
            )
            if user is None:
                raise RegistrationDenied()
            checked = _database_now(session)
            if not now <= checked < min(row.expires_at, invitation.expires_at):
                raise RegistrationDenied()
            row.verified_at, row.user_id, row.password_hash, row.code_hash = (
                checked,
                user,
                None,
                None,
            )
            erase(row)
            session.flush()
            return True

    @infrastructure
    def pending(self, token, *, query: MembershipListQuery):
        with self.factory.begin() as session:
            _limit_transaction(session)
            actor = _authorize(session, token, None, recent=False)
            statement = (
                select(EmployeeRegistration, EmployeeInvitation.email)
                .join(
                    EmployeeInvitation,
                    EmployeeInvitation.id == EmployeeRegistration.invitation_id,
                )
                .outerjoin(
                    EmployeeApproval,
                    EmployeeApproval.registration_id == EmployeeRegistration.id,
                )
                .where(
                    EmployeeRegistration.tenant_id == actor.tenant_id,
                    EmployeeRegistration.verified_at.is_not(None),
                    EmployeeInvitation.revoked_at.is_(None),
                    EmployeeApproval.id.is_(None),
                )
            )
            if query.after:
                statement = statement.where(EmployeeRegistration.id > query.after)
            rows = session.execute(
                statement.order_by(EmployeeRegistration.id).limit(query.limit + 1)
            ).all()
            return (
                [
                    dict(
                        id=str(row.id),
                        email=email,
                        verified_at=row.verified_at.isoformat(),
                    )
                    for row, email in rows[: query.limit]
                ],
                str(rows[query.limit - 1][0].id) if len(rows) > query.limit else None,
                actor.membership_id,
            )

    @infrastructure
    def approve(self, token, *, registration_id, expected_actor):
        with self.factory.begin() as session:
            _limit_transaction(session)
            actor = _authorize(session, token, expected_actor)
            row = session.scalar(
                select(EmployeeRegistration)
                .where(
                    EmployeeRegistration.id == registration_id,
                    EmployeeRegistration.tenant_id == actor.tenant_id,
                )
                .with_for_update()
            )
            if row is None or row.verified_at is None:
                raise AdministrationDenied("Access denied")
            invitation = session.get(EmployeeInvitation, row.invitation_id)
            if invitation.revoked_at is not None:
                raise AdministrationDenied("Access denied")
            previous = session.scalar(
                select(EmployeeApproval).where(
                    EmployeeApproval.registration_id == row.id
                )
            )
            if previous:
                return previous.membership_id, actor.membership_id
            user = session.scalar(
                select(User)
                .where(User.id == row.user_id, User.is_active.is_(True))
                .with_for_update()
            )
            if user is None:
                raise AdministrationDenied("Access denied")
            member = TenantMembership(
                tenant_id=actor.tenant_id,
                user_id=user.id,
                role="read_only",
                is_active=True,
            )
            session.add(member)
            session.flush()
            now = _database_now(session)
            session.add(
                EmployeeApproval(
                    tenant_id=actor.tenant_id,
                    registration_id=row.id,
                    membership_id=member.id,
                    actor_membership_id=actor.membership_id,
                    created_at=now,
                )
            )
            session.add(
                AuditEvent(
                    tenant_id=actor.tenant_id,
                    actor_membership_id=actor.membership_id,
                    target_membership_id=member.id,
                    action="membership_created",
                    before_role=None,
                    before_active=None,
                    after_role="read_only",
                    after_active=True,
                    created_at=now,
                )
            )
            session.flush()
            _authorize(session, token, expected_actor)
            return member.id, actor.membership_id

    @infrastructure
    def dispatch_one(self, transport):
        self.require()
        with self.factory.begin() as session:
            _limit_transaction(session)
            hint = session.scalar(
                select(EmployeeRegistration.invitation_id)
                .where(
                    EmployeeRegistration.send_state == "pending",
                    EmployeeRegistration.ciphertext.is_not(None),
                )
                .order_by(EmployeeRegistration.created_at)
                .limit(1)
            )
            if hint is None:
                return
            try:
                invitation = invitation_lock(session, hint)
            except RegistrationDenied:
                row = session.scalar(
                    select(EmployeeRegistration)
                    .where(EmployeeRegistration.invitation_id == hint)
                    .with_for_update()
                )
                row.send_state = "failed"
                erase(row)
                return
            row = session.scalar(
                select(EmployeeRegistration)
                .where(EmployeeRegistration.invitation_id == hint)
                .with_for_update()
            )
            if row.send_state != "pending" or row.ciphertext is None:
                return
            now = _database_now(session)
            if (
                row.verified_at
                or row.failures >= 5
                or not row.created_at
                <= now
                < min(row.expires_at, invitation.expires_at)
            ):
                row.send_state = "failed"
                erase(row)
                return
            payload = self.protection.decrypt(
                MailEnvelope(
                    key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext
                ),
                user_id=row.id,
                challenge_id=row.id,
                expires_at=row.expires_at,
            )
            code = payload.token.get_secret_value()[:6]
            if (
                payload.recipient.get_secret_value() != invitation.email
                or row.code_hash != self.protection.registration_code_hash(row.id, code)
            ):
                raise RegistrationDenied()
            message = OutboundMessage(
                recipient=payload.recipient,
                subject="Confirma tu registro en Taller Carrillo",
                body=SecretStr(
                    f"Tu código de registro es: {code}\n\n"
                    "Caduca en diez minutos. No inicia sesión ni concede permisos. "
                    "Después de verificar, espera la aprobación de tu administrador."
                ),
            )
            identity, deadline, sent_hash = row.id, row.expires_at, row.code_hash
            row.send_state = "claimed"
            erase(row)
        try:
            outcome = transport.send_message(
                message, message_id=identity, deadline=deadline
            )
        except Exception:
            outcome = TransportOutcome.UNKNOWN
        with self.factory.begin() as session:
            _limit_transaction(session)
            # Do not overwrite a later resend that replaced this claimed envelope.
            row = session.scalar(
                select(EmployeeRegistration)
                .where(EmployeeRegistration.id == identity)
                .with_for_update()
            )
            if row.send_state == "claimed" and row.code_hash == sent_hash:
                row.send_state = (
                    "sent" if outcome == TransportOutcome.ACCEPTED else "failed"
                )
