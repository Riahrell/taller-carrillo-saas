"""Session-bound general mailbox proof; it never enrolls MFA or issues reset tokens."""

import hmac
import secrets
from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address

from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access import mfa_pending
from app.modules.access.email_outbox import (
    MailProtection,
    MailProtectionError,
    MailPurpose,
    reserve_mail,
)
from app.modules.access.mailbox_verification import _address_hash
from app.modules.access.membership import AccessDenied
from app.modules.access.mfa_verification import _timeouts
from app.modules.access.models import (
    MailboxChallenge,
    SecurityEvent,
    SessionRecord,
    User,
    VerifiedMailbox,
)
from app.modules.access.sessions import _database_now, _token_hash, resolve_session


class MailboxUnavailable(RuntimeError):
    pass


class MailboxService:
    def __init__(self, engine: Engine, *, protection: MailProtection):
        if protection.purpose != MailPurpose.MAILBOX_VERIFICATION:
            raise MailboxUnavailable("Mailbox unavailable")
        self._factory, self._protection = create_session_factory(engine), protection

    def issue(
        self,
        token: SecretStr,
        *,
        client_ip: IPv4Address | IPv6Address,
        expected_actor=None,
    ) -> None:
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                actor = resolve_session(session, token)
                if expected_actor is not None and actor.membership_id != expected_actor:
                    raise AccessDenied("Access denied")
                user = session.get(User, actor.user_id)
                record = session.scalar(
                    select(SessionRecord).where(
                        SessionRecord.token_hash == _token_hash(token)
                    )
                )
                now = _database_now(session)
                expires = min(now + timedelta(minutes=15), record.expires_at)
                reserved = reserve_mail(
                    session,
                    self._protection,
                    user_id=user.id,
                    client_ip=client_ip,
                    checked_at=now,
                    expires_at=expires,
                )
                if reserved is None:
                    return
                code = SecretStr(secrets.token_urlsafe(32))
                from uuid import uuid4

                identity = uuid4()
                envelope = self._protection.encrypt(
                    recipient=SecretStr(user.email),
                    token=code,
                    user_id=user.id,
                    challenge_id=identity,
                    expires_at=expires,
                )
                session.add(
                    MailboxChallenge(
                        id=identity,
                        user_id=user.id,
                        session_id=record.id,
                        token_hash=mfa_pending._hash(
                            code, "mailbox-verification-token-v1:"
                        ),
                        email_fingerprint=_address_hash(user),
                        security_revision=user.security_revision,
                        created_at=reserved,
                        expires_at=expires,
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                    )
                )
                session.flush()
                if not reserved <= _database_now(session) < expires:
                    raise MailboxUnavailable("Mailbox unavailable")
        except (SQLAlchemyError, MailProtectionError):
            raise MailboxUnavailable("Mailbox unavailable") from None

    def confirm(
        self,
        session_token: SecretStr,
        *,
        token: SecretStr,
        expected_actor=None,
    ) -> bool:
        try:
            digest = mfa_pending._hash(token, "mailbox-verification-token-v1:")
        except AccessDenied:
            return False
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                actor = resolve_session(session, session_token)
                if expected_actor is not None and actor.membership_id != expected_actor:
                    raise AccessDenied("Access denied")
                user = session.get(User, actor.user_id)
                current = session.scalar(
                    select(SessionRecord).where(
                        SessionRecord.token_hash == _token_hash(session_token)
                    )
                )
                row = session.scalar(
                    select(MailboxChallenge)
                    .where(
                        MailboxChallenge.token_hash == digest,
                        MailboxChallenge.user_id == user.id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                now = _database_now(session)
                if (
                    row is None
                    or row.session_id != current.id
                    or row.closed_at is not None
                    or row.security_revision != user.security_revision
                    or not row.created_at <= now < row.expires_at
                    or not hmac.compare_digest(
                        row.email_fingerprint, _address_hash(user)
                    )
                ):
                    return False
                previous = session.scalars(
                    select(VerifiedMailbox)
                    .where(
                        VerifiedMailbox.user_id == user.id,
                        VerifiedMailbox.invalidated_at.is_(None),
                    )
                    .with_for_update()
                ).all()
                for proof in previous:
                    if now < proof.verified_at:
                        raise MailboxUnavailable("Mailbox unavailable")
                    proof.invalidated_at = now
                session.flush()
                row.consumed_at = row.closed_at = row.discarded_at = now
                row.key_id = row.nonce = row.ciphertext = None
                session.add(
                    VerifiedMailbox(
                        user_id=user.id,
                        challenge_id=None,
                        general_challenge_id=row.id,
                        address_hash=_address_hash(user),
                        verified_at=now,
                    )
                )
                session.add(
                    SecurityEvent(
                        user_id=user.id,
                        action="mailbox_verified",
                        security_revision=user.security_revision,
                        occurred_at=now,
                    )
                )
                session.flush()
                if not now <= _database_now(session) < row.expires_at:
                    raise _Expired
            return True
        except _Expired:
            return False
        except SQLAlchemyError:
            raise MailboxUnavailable("Mailbox unavailable") from None


class _Expired(Exception):
    pass
