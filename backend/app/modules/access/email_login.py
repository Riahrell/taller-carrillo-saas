"""Password-bound six-digit email login, fixed browser trust, single-use proof."""

import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access.email_login_models import EmailLoginChallenge, VerifiedBrowser
from app.modules.access.email_outbox import (
    MailEnvelope,
    MailProtectionError,
    reserve_mail,
)
from app.modules.access.login_challenges import (
    MembershipChoice,
    PendingLogin,
    _active_memberships,
    _password_fingerprint,
)
from app.modules.access.mail_delivery import TransportOutcome
from app.modules.access.mailbox_verification import _address_hash
from app.modules.access.membership import AccessDenied, resolve_membership
from app.modules.access.mfa_verification import _finish_attempt, _lock_quotas, _timeouts
from app.modules.access.models import VerifiedMailbox
from app.modules.access.sessions import (
    _create_session,
    _database_now,
    _lock_user,
    _token_hash,
)
from app.modules.access.smtp_transport import OutboundMessage


@dataclass(frozen=True)
class PendingEmailLogin:
    token: SecretStr
    expires_at: datetime


def _erase(row):
    row.key_id = row.nonce = row.ciphertext = None


def _trusted(row, user, now):
    return (
        row is not None
        and row.user_id == user.id
        and row.security_revision == user.security_revision
        and hmac.compare_digest(row.email_fingerprint, _address_hash(user))
        and row.revoked_at is None
        and row.created_at <= now < row.expires_at
    )


def infrastructure_guard(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (SQLAlchemyError, MailProtectionError):
            from app.modules.access.authentication import AuthenticationUnavailable

            raise AuthenticationUnavailable("Email login unavailable") from None

    return guarded


class EmailLogin:
    def __init__(self, engine, protection):
        self.factory = create_session_factory(engine)
        self.protection = protection

    def start(self, session, user, binding, remembered):
        """Caller has just verified password and holds user lock until commit."""
        now = _database_now(session)
        browser = None
        if remembered:
            try:
                digest = _token_hash(remembered)
                browser = session.scalar(
                    select(VerifiedBrowser).where(
                        VerifiedBrowser.token_hash == digest,
                        VerifiedBrowser.user_id == user.id,
                    )
                )
            except AccessDenied:
                pass
        # Close old attempts only in this browser; don't interfere with others.
        binding_hash = _token_hash(binding)
        previous = session.scalars(
            select(EmailLoginChallenge)
            .where(
                EmailLoginChallenge.user_id == user.id,
                EmailLoginChallenge.binding_hash == binding_hash,
                EmailLoginChallenge.finished_at.is_(None),
                EmailLoginChallenge.invalidated_at.is_(None),
            )
            .order_by(EmailLoginChallenge.id)
            .with_for_update()
        ).all()
        for row in previous:
            row.invalidated_at = now
            row.code_hash = None
            _erase(row)
        token = SecretStr(secrets.token_urlsafe(32))
        valid = _trusted(browser, user, now)
        challenge = EmailLoginChallenge(
            id=uuid4(),
            user_id=user.id,
            token_hash=_token_hash(token),
            binding_hash=binding_hash,
            security_revision=user.security_revision,
            password_fingerprint=_password_fingerprint(user),
            email_fingerprint=_address_hash(user),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            browser_id=browser.id if valid else None,
            verified_at=now if valid else None,
            send_state="none",
            failures=0,
        )
        session.add(challenge)
        session.flush()
        return (
            self.finish(session, user, challenge, token)
            if valid
            else PendingEmailLogin(token, challenge.expires_at)
        )

    def locked(self, session, token, binding):
        try:
            digest, bound = _token_hash(token), _token_hash(binding)
        except AccessDenied:
            return None
        hint = session.scalar(
            select(EmailLoginChallenge.user_id).where(
                EmailLoginChallenge.token_hash == digest
            )
        )
        user = _lock_user(session, hint) if hint else None
        if user is None or not user.is_active:
            return None
        row = session.scalar(
            select(EmailLoginChallenge)
            .where(
                EmailLoginChallenge.token_hash == digest,
                EmailLoginChallenge.user_id == user.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = _database_now(session)
        if (
            row is None
            or row.invalidated_at is not None
            or row.finished_at is not None
            or not hmac.compare_digest(row.binding_hash, bound)
            or row.security_revision != user.security_revision
            or not hmac.compare_digest(
                row.password_fingerprint, _password_fingerprint(user)
            )
            or not hmac.compare_digest(row.email_fingerprint, _address_hash(user))
            or not row.created_at <= now < row.expires_at
        ):
            return None
        return user, row, now

    @infrastructure_guard
    def issue(self, token, binding, peer):
        with self.factory.begin() as session:
            _timeouts(session)
            locked = self.locked(session, token, binding)
            if locked is None:
                return False
            user, row, now = locked
            if row.verified_at or row.failures >= 5:
                return False
            # reserve_mail locks account/IP mail quotas, under the user owner lock,
            # matching all existing mail issuers.
            reserved = reserve_mail(
                session,
                self.protection,
                user_id=user.id,
                client_ip=peer,
                checked_at=now,
                expires_at=row.expires_at,
            )
            if reserved is None:
                return False
            code = f"{secrets.randbelow(1_000_000):06d}"
            # Existing encrypted mail envelope accepts a 43-character secret.
            # Padding is never sent; only the first six digits are presented.
            envelope = self.protection.encrypt(
                recipient=SecretStr(user.email),
                token=SecretStr(code + secrets.token_urlsafe(28)[:37]),
                user_id=user.id,
                challenge_id=row.id,
                expires_at=row.expires_at,
            )
            row.code_hash = self.protection.login_code_hash(row.id, code)
            row.key_id, row.nonce, row.ciphertext = (
                envelope.key_id,
                envelope.nonce,
                envelope.ciphertext,
            )
            row.send_state = "pending"
            session.flush()
            if not reserved <= _database_now(session) < row.expires_at:
                raise AccessDenied("Expired email attempt")
            return True

    @infrastructure_guard
    def confirm(self, token, binding, code, peer):
        raw = code.get_secret_value()
        # Hint before quotas, never a user id from the client.
        with self.factory.begin() as session:
            _timeouts(session)
            try:
                hint = session.scalar(
                    select(EmailLoginChallenge.user_id).where(
                        EmailLoginChallenge.token_hash == _token_hash(token)
                    )
                )
            except AccessDenied:
                return None
            if hint is None:
                return None
            quotas = _lock_quotas(session, hint, peer)
            if quotas is None:
                return None
            locked = self.locked(session, token, binding)
            if locked is None:
                return None
            user, row, now = locked
            if row.verified_at is not None or row.failures >= 5:
                return None
            valid = (
                bool(re.fullmatch(r"[0-9]{6}", raw))
                and row.code_hash is not None
                and hmac.compare_digest(
                    row.code_hash, self.protection.login_code_hash(row.id, raw)
                )
            )
            checked = _finish_attempt(session, quotas, failed=not valid)
            if not valid:
                row.failures += 1
                if row.failures >= 5:
                    row.invalidated_at = checked
                    row.code_hash = None
                    _erase(row)
                return None
            if not now <= checked < row.expires_at:
                return None
            remembered = SecretStr(secrets.token_urlsafe(32))
            browser = VerifiedBrowser(
                id=uuid4(),
                user_id=user.id,
                token_hash=_token_hash(remembered),
                security_revision=user.security_revision,
                email_fingerprint=_address_hash(user),
                created_at=checked,
                expires_at=checked + timedelta(days=15),
            )
            session.add(browser)
            session.flush()
            row.browser_id, row.verified_at = browser.id, checked
            row.code_hash = None
            _erase(row)
            for old in session.scalars(
                select(VerifiedMailbox)
                .where(
                    VerifiedMailbox.user_id == user.id,
                    VerifiedMailbox.invalidated_at.is_(None),
                )
                .with_for_update()
            ).all():
                old.invalidated_at = checked
            session.flush()
            session.add(
                VerifiedMailbox(
                    user_id=user.id,
                    email_login_challenge_id=row.id,
                    address_hash=_address_hash(user),
                    verified_at=checked,
                )
            )
            session.flush()
            result = self.finish(session, user, row, token)
            if result is None:
                raise AccessDenied("No available membership")
            return result, remembered, browser.expires_at

    def finish(self, session, user, row, token, membership_id=None):
        now = _database_now(session)
        if (
            row.verified_at is None
            or row.finished_at is not None
            or not row.created_at <= now < row.expires_at
        ):
            return None
        browser = session.get(VerifiedBrowser, row.browser_id)
        if not _trusted(browser, user, now):
            return None
        choices = _active_memberships(session, user.id)
        if not choices:
            return None
        if membership_id is None and len(choices) > 1:
            return PendingLogin(
                token,
                row.expires_at,
                tuple(MembershipChoice(m.id, name) for m, name in choices),
            )
        selected = next(
            (m for m, _ in choices if membership_id is None or m.id == membership_id),
            None,
        )
        if selected is None:
            return None
        actor = resolve_membership(
            session, user_id=user.id, tenant_id=selected.tenant_id
        )
        row.finished_at = now
        session.flush()
        return _create_session(
            session, user=user, context=actor, now=now, email_login_id=row.id
        )

    @infrastructure_guard
    def select(self, token, binding, membership_id):
        with self.factory.begin() as session:
            _timeouts(session)
            locked = self.locked(session, token, binding)
            if locked is None:
                return None
            user, row, _ = locked
            return self.finish(session, user, row, token, membership_id)

    @infrastructure_guard
    def cancel(self, binding):
        with self.factory.begin() as session:
            _timeouts(session)
            digest = _token_hash(binding)
            owners = session.scalars(
                select(EmailLoginChallenge.user_id)
                .where(
                    EmailLoginChallenge.binding_hash == digest,
                    EmailLoginChallenge.finished_at.is_(None),
                    EmailLoginChallenge.invalidated_at.is_(None),
                )
                .distinct()
            ).all()
            for owner in sorted(owners):
                _lock_user(session, owner)
                rows = session.scalars(
                    select(EmailLoginChallenge)
                    .where(
                        EmailLoginChallenge.user_id == owner,
                        EmailLoginChallenge.binding_hash == digest,
                        EmailLoginChallenge.finished_at.is_(None),
                        EmailLoginChallenge.invalidated_at.is_(None),
                    )
                    .order_by(EmailLoginChallenge.id)
                    .with_for_update()
                ).all()
                for row in rows:
                    row.invalidated_at = _database_now(session)
                    row.code_hash = None
                    _erase(row)

    def dispatch_one(self, transport):
        # Claim exactly once before external I/O. An uncertain SMTP result is
        # never retried automatically, and reissue replaces the old code.
        with self.factory.begin() as session:
            _timeouts(session)
            hint = session.execute(
                select(EmailLoginChallenge.id, EmailLoginChallenge.user_id)
                .where(
                    EmailLoginChallenge.send_state == "pending",
                    EmailLoginChallenge.ciphertext.is_not(None),
                )
                .order_by(EmailLoginChallenge.created_at)
                .limit(1)
            ).first()
            if hint is None:
                return
            user = _lock_user(session, hint.user_id)
            row = session.scalar(
                select(EmailLoginChallenge)
                .where(EmailLoginChallenge.id == hint.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row.send_state != "pending" or row.ciphertext is None:
                return
            now = _database_now(session)
            if (
                not user
                or not user.is_active
                or row.invalidated_at
                or row.verified_at
                or row.security_revision != user.security_revision
                or row.email_fingerprint != _address_hash(user)
                or row.password_fingerprint != _password_fingerprint(user)
                or not row.created_at <= now < row.expires_at
            ):
                row.send_state = "failed"
                _erase(row)
                return
            payload = self.protection.decrypt(
                MailEnvelope(
                    key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext
                ),
                user_id=user.id,
                challenge_id=row.id,
                expires_at=row.expires_at,
            )
            code = payload.token.get_secret_value()[:6]
            if (
                not re.fullmatch(r"[0-9]{6}", code)
                or payload.recipient.get_secret_value() != user.email
                or row.code_hash is None
                or not hmac.compare_digest(
                    row.code_hash, self.protection.login_code_hash(row.id, code)
                )
            ):
                raise AccessDenied("Invalid mail payload")
            message = OutboundMessage(
                recipient=payload.recipient,
                subject="Código de acceso a Taller Carrillo",
                body=SecretStr(
                    f"Tu código de verificación es: {code}\n\n"
                    "Caduca diez minutos después de iniciar el acceso. "
                    "Úsalo solo en el navegador que lo solicitó. "
                    "No compartas este código."
                ),
            )
            identity, deadline = row.id, row.expires_at
            row.send_state = "claimed"
            _erase(row)
        # No secrets in errors/logs. Claim persists even if process crashes here.
        try:
            outcome = transport.send_message(
                message, message_id=identity, deadline=deadline
            )
        except Exception:
            outcome = TransportOutcome.UNKNOWN
        with self.factory.begin() as session:
            _timeouts(session)
            _lock_user(session, hint.user_id)
            row = session.scalar(
                select(EmailLoginChallenge)
                .where(EmailLoginChallenge.id == identity)
                .with_for_update()
            )
            if row.send_state == "claimed":
                row.send_state = (
                    "sent" if outcome == TransportOutcome.ACCEPTED else "failed"
                )
