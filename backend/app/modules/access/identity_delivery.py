"""Leased reset/verification/notice delivery with revalidation; network outside TX."""

import hmac
from datetime import timedelta
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.email_outbox import (
    MailEnvelope,
    MailProtectionError,
    MailPurpose,
)
from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.mail_delivery import TransportOutcome
from app.modules.access.mailbox_verification import _address_hash, lock_eligible_mailbox
from app.modules.access.membership import AccessDenied, resolve_membership
from app.modules.access.mfa_verification import _timeouts
from app.modules.access.models import (
    IdentityMailDelivery,
    MailboxChallenge,
    PasswordResetChallenge,
    SecurityEvent,
    SecurityNotice,
    SessionRecord,
    TenantMembership,
    User,
)
from app.modules.access.sessions import (
    IDLE_TIMEOUT,
    _database_now,
    _validate_session_proof,
)
from app.modules.access.smtp_transport import OutboundMessage

SOURCE = {
    "reset": PasswordResetChallenge,
    "mailbox": MailboxChallenge,
    "notice": SecurityNotice,
}


class IdentityMailUnavailable(RuntimeError):
    pass


def _lock_user(session, user_id):
    return session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


def _source(session, kind, identity):
    model = SOURCE[kind]
    key = model.event_id if kind == "notice" else model.id
    return session.scalar(
        select(model)
        .where(key == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _erase(row, now):
    if hasattr(row, "ciphertext"):
        row.key_id = row.nonce = row.ciphertext = None
        row.discarded_at = max(now, row.created_at)


def _available(kind, key, now):
    blocked = (
        select(IdentityMailDelivery.id)
        .where(
            IdentityMailDelivery.kind == kind,
            IdentityMailDelivery.source_id == key,
            or_(
                IdentityMailDelivery.state.in_(
                    ["accepted", "uncertain", "rejected", "cancelled"]
                ),
                (IdentityMailDelivery.state == "leased")
                & (IdentityMailDelivery.lease_expires_at > now),
                (IdentityMailDelivery.state == "pending")
                & (IdentityMailDelivery.next_attempt_at > now),
            ),
        )
        .exists()
    )
    return ~blocked


class IdentityMailDispatcher:
    def __init__(self, engine, *, identity, transport, origin: str):
        from app.modules.access.http_security import validate_origin

        validate_origin(origin, "production")
        self._factory, self.identity, self.transport, self.origin = (
            create_session_factory(engine),
            identity.require(),
            transport,
            origin,
        )

    def _eligible(self, session, user, row, kind):
        now = _database_now(session)
        if not user.is_active or not row.created_at <= now < row.expires_at:
            return False
        if kind == "notice":
            event = session.get(SecurityEvent, row.event_id)
            return (
                event is not None
                and event.user_id == user.id
                and row.state == "pending"
                and hmac.compare_digest(row.address_hash, _address_hash(user))
            )
        if (
            row.user_id != user.id
            or row.closed_at is not None
            or row.security_revision != user.security_revision
        ):
            return False
        if kind == "reset":
            evidence = lock_eligible_mailbox(session, user_id=user.id)
            return (
                evidence is not None
                and evidence.id == row.verification_id
                and hmac.compare_digest(
                    row.password_fingerprint, _password_fingerprint(user)
                )
            )
        record = session.get(SessionRecord, row.session_id, populate_existing=True)
        if (
            record is None
            or record.revoked_at is not None
            or not record.created_at
            <= now
            < min(record.expires_at, record.last_seen_at + IDLE_TIMEOUT)
        ):
            return False
        member = session.get(TenantMembership, record.membership_id)
        if (
            member is None
            or member.user_id != user.id
            or not hmac.compare_digest(row.email_fingerprint, _address_hash(user))
        ):
            return False
        try:
            _validate_session_proof(session, record, user)
            resolve_membership(session, user_id=user.id, tenant_id=record.tenant_id)
        except AccessDenied:
            return False
        return True

    def _message(self, user, row, kind, session):
        if kind == "notice":
            event = session.get(SecurityEvent, row.event_id)
            action = event.action if event else ""
            subject = {
                "password_reset_completed": "Tu contraseña ha cambiado",
                "mfa_enrolled": "Se activó tu autenticador",
                "mfa_replaced": "Se reemplazó tu autenticador",
                "mfa_codes_regenerated": "Se regeneraron tus códigos de recuperación",
                "mfa_rotated": "Se cambió tu autenticador",
            }.get(action, "Cambio en la seguridad de tu cuenta")
            return OutboundMessage(
                recipient=SecretStr(user.email),
                subject=subject,
                body=SecretStr(
                    subject
                    + (
                        ". Si no solicitaste este cambio, utiliza tu canal "
                        "independiente de soporte para revisar la cuenta. Este aviso "
                        "no incluye contraseñas, semillas ni códigos."
                    )
                ),
            )

        purpose = (
            MailPurpose.PASSWORD_RESET
            if kind == "reset"
            else MailPurpose.MAILBOX_VERIFICATION
        )
        payload = self.identity.mail[purpose].decrypt(
            MailEnvelope(key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext),
            user_id=user.id,
            challenge_id=row.id,
            expires_at=row.expires_at,
        )
        if not hmac.compare_digest(payload.recipient.get_secret_value(), user.email):
            raise IdentityMailUnavailable("Identity mail unavailable")
        route, fragment = (
            ("recuperar", "reset") if kind == "reset" else ("verificar", "verify")
        )
        secret = payload.token.get_secret_value()
        body = (
            f"Continúa en {self.origin}/{route}#{fragment}={secret}\n\n"
            f"Código: {secret}\n\n"
            "Este mensaje no inicia sesión ni desactiva MFA. "
            "Si no lo solicitaste, puedes ignorarlo."
        )
        return OutboundMessage(
            recipient=payload.recipient,
            subject="Recupera tu contraseña"
            if kind == "reset"
            else "Confirma tu correo",
            body=SecretStr(body),
        )

    def dispatch_one(self):
        try:
            candidates = []
            with self._factory.begin() as session:
                _timeouts(session)
                now = _database_now(session)
                for kind, model in SOURCE.items():
                    if kind == "notice":
                        rows = session.execute(
                            select(model.event_id, SecurityEvent.user_id)
                            .join(SecurityEvent, model.event_id == SecurityEvent.id)
                            .where(
                                model.state == "pending",
                                model.expires_at > now,
                                _available(kind, model.event_id, now),
                            )
                            .limit(20)
                        ).all()
                    else:
                        rows = session.execute(
                            select(model.id, model.user_id)
                            .where(
                                model.closed_at.is_(None),
                                model.ciphertext.is_not(None),
                                model.expires_at > now,
                                _available(kind, model.id, now),
                            )
                            .order_by(model.created_at)
                            .limit(20)
                        ).all()
                    candidates.extend((kind, key, user) for key, user in rows)
            claim = None
            for kind, source_id, user_id in candidates:
                with self._factory.begin() as session:
                    _timeouts(session)
                    user = _lock_user(session, user_id)
                    if user is None:
                        continue
                    row = _source(session, kind, source_id)
                    delivery = session.scalar(
                        select(IdentityMailDelivery)
                        .where(
                            IdentityMailDelivery.kind == kind,
                            IdentityMailDelivery.source_id == source_id,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    now = _database_now(session)
                    if delivery is not None:
                        if (
                            delivery.state == "leased"
                            and delivery.lease_expires_at <= now
                        ):
                            (
                                delivery.state,
                                delivery.claim_id,
                                delivery.lease_expires_at,
                            ) = "uncertain", None, None
                            _erase(row, now)
                            if kind == "notice":
                                row.state = "uncertain"
                            continue
                        if (
                            delivery.state != "pending"
                            or delivery.next_attempt_at > now
                        ):
                            continue
                    if row is None or not self._eligible(session, user, row, kind):
                        if row is not None:
                            _erase(row, now)
                            if kind == "notice":
                                row.state = "expired"
                            else:
                                row.closed_at = max(now, row.created_at)
                        continue
                    self._message(
                        user, row, kind, session
                    )  # Check key/AAD before committing a lease.
                    lease = min(now + timedelta(seconds=30), row.expires_at)
                    if delivery is None:
                        delivery = IdentityMailDelivery(
                            user_id=user_id,
                            kind=kind,
                            source_id=source_id,
                            state="leased",
                            attempts=1,
                            claim_id=uuid4(),
                            lease_expires_at=lease,
                            next_attempt_at=now,
                            created_at=now,
                        )
                        session.add(delivery)
                    else:
                        delivery.state = "leased"
                        delivery.attempts += 1
                        delivery.claim_id = uuid4()
                        delivery.lease_expires_at = lease
                    session.flush()
                    claim = (kind, source_id, user_id, delivery.id, delivery.claim_id)
                break
            if claim is None:
                return "idle"
            kind, source_id, user_id, delivery_id, claim_id = claim
            outbound, deadline = None, None
            with self._factory.begin() as session:
                _timeouts(session)
                user = _lock_user(session, user_id)
                if user is not None:
                    row = _source(session, kind, source_id)
                    delivery = session.get(
                        IdentityMailDelivery,
                        delivery_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                    now = _database_now(session)
                    if (
                        delivery.state == "leased"
                        and delivery.claim_id == claim_id
                        and now < delivery.lease_expires_at
                        and row is not None
                        and self._eligible(session, user, row, kind)
                    ):
                        outbound, deadline = (
                            self._message(user, row, kind, session),
                            delivery.lease_expires_at,
                        )
            outcome = TransportOutcome.NOT_SENT
            if outbound is not None:
                try:
                    outcome = self.transport.send_message(
                        outbound, message_id=delivery_id, deadline=deadline
                    )
                except Exception:
                    outcome = TransportOutcome.UNKNOWN
            with self._factory.begin() as session:
                _timeouts(session)
                user = _lock_user(session, user_id)
                if user is None:
                    return "uncertain"
                row = _source(session, kind, source_id)
                delivery = session.get(
                    IdentityMailDelivery,
                    delivery_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                now = _database_now(session)
                if delivery.state != "leased" or delivery.claim_id != claim_id:
                    return "uncertain"
                if now >= delivery.lease_expires_at or not isinstance(
                    outcome, TransportOutcome
                ):
                    outcome = TransportOutcome.UNKNOWN
                retry = (
                    outcome == TransportOutcome.NOT_SENT
                    and delivery.attempts < 3
                    and row is not None
                    and outbound is not None
                    and now + timedelta(seconds=60) < row.expires_at
                )
                result = (
                    "pending"
                    if retry
                    else "cancelled"
                    if outbound is None
                    else {
                        TransportOutcome.ACCEPTED: "accepted",
                        TransportOutcome.REJECTED: "rejected",
                        TransportOutcome.UNKNOWN: "uncertain",
                        TransportOutcome.NOT_SENT: "rejected",
                    }[outcome]
                )
                delivery.state, delivery.claim_id, delivery.lease_expires_at = (
                    result,
                    None,
                    None,
                )
                if retry:
                    delivery.next_attempt_at = now + timedelta(seconds=60)
                elif row is not None:
                    _erase(row, now)
                    if kind == "notice":
                        row.state = result if result != "cancelled" else "expired"
                session.flush()
            return result
        except (SQLAlchemyError, MailProtectionError):
            raise IdentityMailUnavailable("Identity mail unavailable") from None
