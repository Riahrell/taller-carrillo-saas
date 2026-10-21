"""Exact mailbox evidence for future password reset, not reset authorization.

No HTTP, enumeration API or transferable proof. Trusted callers own the bounded
transaction; issuance/consumption of purpose-specific reset tokens is separate.
"""

import hashlib
import hmac
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    EnrollmentEmailChallenge,
    MailboxChallenge,
    User,
    VerifiedMailbox,
)
from app.modules.access.sessions import _database_now, _lock_user


def enrollment_address_fingerprint(user: User) -> str:
    """Keep the existing challenge domain stable; never use as a bearer secret."""
    return hashlib.sha256(
        b"carrillo:enrollment-mail-address:v1\x00"
        + user.id.bytes
        + b"\x00"
        + user.email.encode("utf-8")
    ).hexdigest()


def _address_hash(user: User) -> str:
    return hashlib.sha256(
        b"carrillo:verified-mailbox-address:v1\x00"
        + user.id.bytes
        + b"\x00"
        + user.email.encode("utf-8")
    ).hexdigest()


def record_confirmed_mailbox(
    session: Session, *, user: User, challenge: EnrollmentEmailChallenge, now: datetime
) -> None:
    """Compose ONLY after fresh password/browser/mail confirmation under owner lock.

    Does not verify bearer secrets itself. No historical backfill, separate commit
    or callable endpoint. Any later failure must roll back this record as well.
    """
    if (
        not user.is_active
        or challenge.user_id != user.id
        or challenge.consumed_at != now
        or challenge.closed_at is not None
        or not challenge.created_at <= now < challenge.expires_at
        or not hmac.compare_digest(
            challenge.email_fingerprint, enrollment_address_fingerprint(user)
        )
    ):
        raise AccessDenied("Access denied")
    previous = session.scalars(
        select(VerifiedMailbox)
        .where(
            VerifiedMailbox.user_id == user.id,
            VerifiedMailbox.invalidated_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for row in previous:
        if now < row.verified_at:
            raise AccessDenied("Access denied")
        row.invalidated_at = now
    session.flush()
    session.add(
        VerifiedMailbox(
            user_id=user.id,
            challenge_id=challenge.id,
            address_hash=_address_hash(user),
            verified_at=now,
        )
    )
    session.flush()


def lock_eligible_mailbox(session: Session, *, user_id: UUID) -> VerifiedMailbox | None:
    """Private reset composition, under user -> challenge -> evidence locks.

    Result is valid only inside caller transaction. No token/session authority.
    Historical mailbox evidence survives password/revision/MFA changes, while an
    address change invalidates it irreversibly via the database trigger.
    """
    if not isinstance(user_id, UUID):
        raise ValueError("Invalid server user identity")
    user = _lock_user(session, user_id)
    if user is None or not user.is_active:
        return None
    hint = session.execute(
        select(
            VerifiedMailbox.id,
            VerifiedMailbox.challenge_id,
            VerifiedMailbox.general_challenge_id,
            VerifiedMailbox.email_login_challenge_id,
        ).where(
            VerifiedMailbox.user_id == user.id, VerifiedMailbox.invalidated_at.is_(None)
        )
    ).one_or_none()
    if hint is None:
        return None
    from app.modules.access.email_login_models import EmailLoginChallenge

    source = (
        EmailLoginChallenge
        if hint.email_login_challenge_id is not None
        else EnrollmentEmailChallenge
        if hint.challenge_id is not None
        else MailboxChallenge
    )
    source_id = (
        hint.email_login_challenge_id or hint.challenge_id or hint.general_challenge_id
    )
    challenge = session.scalar(
        select(source)
        .where(source.id == source_id, source.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    record = session.scalar(
        select(VerifiedMailbox)
        .where(
            VerifiedMailbox.id == hint.id,
            VerifiedMailbox.user_id == user.id,
            VerifiedMailbox.invalidated_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _database_now(session)
    if (
        record is None
        or challenge is None
        or (
            challenge.verified_at
            if hint.email_login_challenge_id is not None
            else challenge.consumed_at
        )
        != record.verified_at
        or not challenge.created_at <= record.verified_at < challenge.expires_at
        or now < record.verified_at
        or not hmac.compare_digest(record.address_hash, _address_hash(user))
        or not hmac.compare_digest(
            challenge.email_fingerprint,
            enrollment_address_fingerprint(user)
            if hint.challenge_id is not None
            else _address_hash(user),
        )
    ):
        return None
    return record
