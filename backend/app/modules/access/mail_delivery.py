"""Internal delivery kernel with injected test transport; no SMTP/config/worker.

Claim and revalidate in DB, call transport outside transactions, then settle.
Uncertain results and abandoned leases NEVER retry automatically. Provider
acceptance does not prove inbox delivery and never grants authentication.
"""

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import mfa_pending
from app.modules.access.email_outbox import (
    MailEnvelope,
    MailPayload,
    MailProtection,
    MailProtectionError,
    discard_mail,
)
from app.modules.access.enrollment_email import (
    _email_fingerprint,
    _initial_factor_allowed,
)
from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.models import (
    EnrollmentEmailChallenge,
    EnrollmentMailDelivery,
    EnrollmentMailOutbox,
    MfaPending,
    User,
)
from app.modules.access.sessions import _database_now

LEASE = timedelta(seconds=30)
RETRY_DELAY = timedelta(seconds=60)


class TransportOutcome(StrEnum):
    ACCEPTED = "accepted"
    NOT_SENT = "not_sent"  # Positive assurance: no provider acceptance/side effect.
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class DispatchResult(StrEnum):
    IDLE = "idle"
    ACCEPTED = "accepted"
    RETRY = "retry"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"
    STALE = "stale"


class MailDeliveryUnavailable(RuntimeError):
    """No provider exception, SQL, address, token or key material is exposed."""


class MailTransport(Protocol):
    def send(
        self,
        payload: MailPayload,
        *,
        message_id: UUID,
        expires_at: datetime,
        deadline: datetime,
    ) -> TransportOutcome:
        """Future adapters need a total deadline below the persisted lease.

        message_id is stable across known-not-sent retries. Do not construct
        URLs from request headers or interpret generic exceptions as NOT_SENT.
        No real adapter is supplied or enabled in this phase.
        """
        ...


@dataclass(frozen=True)
class _Claim:
    challenge_id: UUID
    claim_id: UUID


def _limits(session: Session):
    session.execute(text("SET LOCAL lock_timeout = '3s'"))
    session.execute(text("SET LOCAL statement_timeout = '10s'"))


def _locked(session: Session, challenge_id: UUID):
    hint = session.execute(
        select(
            EnrollmentEmailChallenge.user_id,
            EnrollmentEmailChallenge.pending_id,
        ).where(EnrollmentEmailChallenge.id == challenge_id)
    ).one_or_none()
    if hint is None:
        return None
    # Busy identities are skipped; never hold a child lock while waiting on user.
    user = session.scalar(
        select(User)
        .where(User.id == hint.user_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if user is None:
        return None
    pending = session.scalar(
        select(MfaPending)
        .where(MfaPending.id == hint.pending_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    challenge = session.scalar(
        select(EnrollmentEmailChallenge)
        .where(
            EnrollmentEmailChallenge.id == challenge_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    outbox = session.scalar(
        select(EnrollmentMailOutbox)
        .where(
            EnrollmentMailOutbox.challenge_id == challenge_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    delivery = session.scalar(
        select(EnrollmentMailDelivery)
        .where(
            EnrollmentMailDelivery.challenge_id == challenge_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pending is None or challenge is None or outbox is None:
        return None
    return user, pending, challenge, outbox, delivery


def _eligible(session, user, pending, challenge, outbox, now):
    return (
        _initial_factor_allowed(session, user)
        and pending.user_id == challenge.user_id == user.id
        and pending.id == challenge.pending_id
        and pending.purpose == mfa_pending.PendingPurpose.ENROLLMENT.value
        and pending.finished_at is None
        and pending.security_revision == user.security_revision
        and challenge.closed_at is None
        and challenge.consumed_at is None
        and outbox.discarded_at is None
        and pending.password_verified_at
        <= pending.created_at
        <= challenge.created_at
        == outbox.created_at
        <= now
        < outbox.expires_at
        == challenge.expires_at
        == pending.expires_at
        and hmac.compare_digest(
            pending.password_fingerprint, _password_fingerprint(user)
        )
        and hmac.compare_digest(challenge.email_fingerprint, _email_fingerprint(user))
    )


def _payload(protection, user, challenge, outbox):
    payload = protection.decrypt(
        MailEnvelope(
            key_id=outbox.key_id, nonce=outbox.nonce, ciphertext=outbox.ciphertext
        ),
        user_id=user.id,
        challenge_id=challenge.id,
        expires_at=outbox.expires_at,
    )
    if not (
        hmac.compare_digest(
            payload.recipient.get_secret_value().encode("utf-8"),
            user.email.encode("utf-8"),
        )
        and hmac.compare_digest(
            mfa_pending._hash(payload.token, "enrollment-mail-token-v1:"),
            challenge.token_hash,
        )
    ):
        return None
    return payload


def _terminal(session, delivery, state, now):
    delivery.state = state
    delivery.finished_at = now
    delivery.next_attempt_at = None
    discard_mail(session, delivery.challenge_id, now)
    session.flush()


class MailDispatcher:
    """One bounded attempt per call; transport must be injected explicitly."""

    def __init__(
        self, engine: Engine, *, protection: MailProtection, transport: MailTransport
    ):
        self._factory = create_session_factory(engine)
        self._protection, self._transport = protection, transport

    def _claim(self) -> _Claim | DispatchResult:
        with self._factory.begin() as session:
            _limits(session)
            now = _database_now(session)
            expired_lease = and_(
                EnrollmentMailDelivery.state == "leased",
                EnrollmentMailDelivery.lease_until <= now,
            )
            candidates = session.scalars(
                select(EnrollmentMailOutbox.challenge_id)
                .outerjoin(
                    EnrollmentMailDelivery,
                    EnrollmentMailDelivery.challenge_id
                    == EnrollmentMailOutbox.challenge_id,
                )
                .where(
                    or_(
                        expired_lease,
                        and_(
                            EnrollmentMailOutbox.discarded_at.is_(None),
                            or_(
                                EnrollmentMailDelivery.challenge_id.is_(None),
                                and_(
                                    EnrollmentMailDelivery.state == "retry",
                                    EnrollmentMailDelivery.next_attempt_at <= now,
                                ),
                            ),
                        ),
                    )
                )
                .order_by(
                    EnrollmentMailOutbox.expires_at, EnrollmentMailOutbox.challenge_id
                )
                .limit(20)
            ).all()
            result = DispatchResult.IDLE
            for challenge_id in candidates:
                locked = _locked(session, challenge_id)
                if locked is None:
                    continue
                user, pending, challenge, outbox, delivery = locked
                checked_at = _database_now(session)
                if checked_at < now:
                    raise MailDeliveryUnavailable("Mail delivery unavailable")
                if delivery is not None:
                    if checked_at < delivery.started_at:
                        raise MailDeliveryUnavailable("Mail delivery unavailable")
                    if delivery.state == "leased":
                        if checked_at < delivery.lease_until:
                            continue
                        _terminal(session, delivery, "uncertain", checked_at)
                        result = DispatchResult.UNCERTAIN
                        break
                    if (
                        delivery.state != "retry"
                        or checked_at < delivery.next_attempt_at
                    ):
                        continue
                if not _eligible(session, user, pending, challenge, outbox, checked_at):
                    if delivery is not None:
                        _terminal(session, delivery, "cancelled", checked_at)
                    else:
                        discard_mail(session, challenge_id, checked_at)
                    result = DispatchResult.CANCELLED
                    break
                payload = _payload(self._protection, user, challenge, outbox)
                send_at = _database_now(session)
                if send_at < checked_at:
                    raise MailDeliveryUnavailable("Mail delivery unavailable")
                if payload is None or not _eligible(
                    session, user, pending, challenge, outbox, send_at
                ):
                    if delivery is not None:
                        _terminal(session, delivery, "cancelled", send_at)
                    else:
                        discard_mail(session, challenge_id, send_at)
                    result = DispatchResult.CANCELLED
                    break
                if delivery is None:
                    delivery = EnrollmentMailDelivery(
                        challenge_id=challenge_id, attempts=1
                    )
                    session.add(delivery)
                else:
                    delivery.attempts += 1
                delivery.state, delivery.claim_id = "leased", uuid4()
                delivery.started_at = send_at
                delivery.lease_until = min(send_at + LEASE, outbox.expires_at)
                delivery.next_attempt_at = delivery.finished_at = None
                session.flush()
                result = _Claim(challenge_id, delivery.claim_id)
                break
        return result

    def _authorize(self, claim: _Claim):
        with self._factory.begin() as session:
            _limits(session)
            locked = _locked(session, claim.challenge_id)
            if locked is None:
                return None
            user, pending, challenge, outbox, delivery = locked
            if (
                delivery is None
                or delivery.state != "leased"
                or delivery.claim_id != claim.claim_id
            ):
                return None
            now = _database_now(session)
            if now < delivery.started_at:
                raise MailDeliveryUnavailable("Mail delivery unavailable")
            if now >= delivery.lease_until or not _eligible(
                session, user, pending, challenge, outbox, now
            ):
                _terminal(session, delivery, "cancelled", now)
                return None
            payload = _payload(self._protection, user, challenge, outbox)
            checked_at = _database_now(session)
            if checked_at < now:
                raise MailDeliveryUnavailable("Mail delivery unavailable")
            if payload is None or checked_at >= delivery.lease_until:
                _terminal(session, delivery, "cancelled", checked_at)
                return None
            result = payload, outbox.expires_at, delivery.lease_until
        return result

    def _finish(self, claim: _Claim, outcome: TransportOutcome) -> DispatchResult:
        with self._factory.begin() as session:
            _limits(session)
            # Descendant-only update: outbox -> delivery; never wait for user here.
            outbox = session.scalar(
                select(EnrollmentMailOutbox)
                .where(EnrollmentMailOutbox.challenge_id == claim.challenge_id)
                .with_for_update()
            )
            delivery = session.scalar(
                select(EnrollmentMailDelivery)
                .where(EnrollmentMailDelivery.challenge_id == claim.challenge_id)
                .with_for_update()
            )
            if (
                delivery is None
                or delivery.claim_id != claim.claim_id
                or delivery.state != "leased"
            ):
                return DispatchResult.STALE
            now = _database_now(session)
            if now < delivery.started_at:
                raise MailDeliveryUnavailable("Mail delivery unavailable")
            if now >= delivery.lease_until:
                outcome = TransportOutcome.UNKNOWN
            if outcome == TransportOutcome.ACCEPTED:
                result = DispatchResult.ACCEPTED
            elif outcome == TransportOutcome.UNKNOWN:
                result = DispatchResult.UNCERTAIN
            elif (
                outcome == TransportOutcome.NOT_SENT and outbox.discarded_at is not None
            ):
                result = DispatchResult.CANCELLED
            elif (
                outcome == TransportOutcome.NOT_SENT
                and delivery.attempts < 3
                and now + RETRY_DELAY < outbox.expires_at
            ):
                delivery.state, delivery.finished_at = "retry", now
                delivery.next_attempt_at = now + RETRY_DELAY
                session.flush()
                result = DispatchResult.RETRY
            else:
                result = DispatchResult.REJECTED
            if result != DispatchResult.RETRY:
                _terminal(session, delivery, result.value, now)
        return result

    def run_one(self) -> DispatchResult:
        try:
            claim = self._claim()
            if isinstance(claim, DispatchResult):
                return claim
            authorized = self._authorize(claim)
            if authorized is None:
                result = self._finish(claim, TransportOutcome.NOT_SENT)
                return (
                    DispatchResult.CANCELLED
                    if result == DispatchResult.STALE
                    else result
                )
            payload, expires_at, deadline = authorized
            try:
                outcome = self._transport.send(
                    payload,
                    message_id=claim.challenge_id,
                    expires_at=expires_at,
                    deadline=deadline,
                )
                if not isinstance(outcome, TransportOutcome):
                    outcome = TransportOutcome.UNKNOWN
            except Exception:
                # Timeout/disconnect may follow acceptance. Do not publish exception.
                outcome = TransportOutcome.UNKNOWN
            return self._finish(claim, outcome)
        except (SQLAlchemyError, MailProtectionError):
            raise MailDeliveryUnavailable("Mail delivery unavailable") from None
