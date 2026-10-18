"""Public blind reset submission and private at-most-once eligibility processing."""

import secrets
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.email_outbox import (
    MailEnvelope,
    MailProtectionError,
    MailPurpose,
)
from app.modules.access.mfa_verification import _timeouts
from app.modules.access.models import ResetRequestInbox
from app.modules.access.password_reset import (
    WINDOW,
    PasswordResetRequest,
    PasswordResetUnavailable,
    _clock,
    _reserve_request,
)
from app.modules.access.sessions import _database_now


class ResetInbox:
    def __init__(self, engine, *, protection, issuer):
        if protection.purpose != MailPurpose.RESET_REQUEST:
            raise PasswordResetUnavailable("Password reset unavailable")
        self._factory, self.protection, self.issuer = (
            create_session_factory(engine),
            protection,
            issuer,
        )

    def submit(self, data: PasswordResetRequest, *, client_ip):
        # Key derivation is from the reset issuer's quota key/purpose, not the
        # inbox encryption AAD. No query references the users table here.
        scopes = self.issuer._protection.request_scopes(
            SecretStr(str(data.email)), client_ip
        )
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                now = _reserve_request(session, scopes)
                if now is None:
                    return
                identity = uuid4()
                expires = now + WINDOW
                envelope = self.protection.encrypt(
                    recipient=SecretStr(str(data.email)),
                    token=SecretStr(secrets.token_urlsafe(32)),
                    user_id=identity,
                    challenge_id=identity,
                    expires_at=expires,
                )
                session.add(
                    ResetRequestInbox(
                        id=identity,
                        created_at=now,
                        expires_at=expires,
                        key_id=envelope.key_id,
                        nonce=envelope.nonce,
                        ciphertext=envelope.ciphertext,
                    )
                )
                session.flush()
                if _clock(session, now) >= expires:
                    raise PasswordResetUnavailable("Password reset unavailable")
        except (SQLAlchemyError, MailProtectionError):
            raise PasswordResetUnavailable("Password reset unavailable") from None

    def process_one(self):
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                now = _database_now(session)
                row = session.scalar(
                    select(ResetRequestInbox)
                    .where(
                        ResetRequestInbox.claimed_at.is_(None),
                        ResetRequestInbox.finished_at.is_(None),
                        ResetRequestInbox.expires_at > now,
                    )
                    .order_by(ResetRequestInbox.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    return False
                payload = self.protection.decrypt(
                    MailEnvelope(
                        key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext
                    ),
                    user_id=row.id,
                    challenge_id=row.id,
                    expires_at=row.expires_at,
                )
                identity, created, claimed = row.id, row.created_at, now
                row.claimed_at = claimed
            # No other path obtains inbox then a user lock. Claim is committed
            # first, and never retried automatically after process interruption.
            with self._factory.begin() as session:
                _timeouts(session)
                now = _database_now(session)
                if now < claimed:
                    raise PasswordResetUnavailable("Password reset unavailable")
                data = PasswordResetRequest(email=payload.recipient.get_secret_value())
                row = session.get(ResetRequestInbox, identity, populate_existing=True)
                if row.finished_at is not None or row.claimed_at != claimed:
                    return False
                if now < row.expires_at:
                    self.issuer._stage(
                        session,
                        data,
                        claimed,
                        supplied_token=payload.token,
                        original_start=created,
                    )
                row = session.get(
                    ResetRequestInbox,
                    identity,
                    with_for_update=True,
                    populate_existing=True,
                )
                if row.finished_at is not None or row.claimed_at != claimed:
                    raise _InboxChanged
                row.finished_at = max(_database_now(session), created)
                row.key_id = row.nonce = row.ciphertext = None
                session.flush()
            return True
        except _InboxChanged:
            return False
        except (SQLAlchemyError, MailProtectionError):
            raise PasswordResetUnavailable("Password reset unavailable") from None


class _InboxChanged(Exception):
    pass
