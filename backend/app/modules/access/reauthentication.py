"""Internal confirmation contract and transactional guard; not an HTTP adapter."""

import hmac
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.login_challenges import _password_fingerprint
from app.modules.access.membership import AccessDenied, MembershipContext
from app.modules.access.models import MembershipRole, MfaPending, SessionRecord, User
from app.modules.access.sessions import _database_now, _token_hash, resolve_session

RECENT_AUTHENTICATION_WINDOW = timedelta(minutes=5)


class PasswordConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    password: SecretStr = Field(min_length=1, max_length=128, repr=False, exclude=True)

    @field_validator("password")
    @classmethod
    def valid_unicode(cls, value: SecretStr) -> SecretStr:
        try:
            value.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise ValueError("Password must be valid Unicode") from None
        return value


class MfaPasswordConfirmation(PasswordConfirmation):
    code: SecretStr = Field(min_length=1, max_length=128, repr=False, exclude=True)


class RecentAuthenticationRequired(Exception):
    """Valid admin session, but no current password confirmation for this token."""


def require_recent_administrator(
    session: Session, token: SecretStr, *, expected_actor: UUID
) -> MembershipContext:
    """Must run inside the sensitive operation's transaction and lock protocol.

    Caller must roll back on any rejection; a separate preflight check is unsafe.
    This guard neither commits nor authorizes a target or a business operation.
    Ordinary reads/activity never renew the five-minute confirmation window.
    """
    if not isinstance(expected_actor, UUID):
        raise AccessDenied("Access denied")
    actor = resolve_session(session, token)
    if actor.membership_id != expected_actor or actor.role != MembershipRole.ADMIN:
        raise AccessDenied("Access denied")
    record = session.scalar(
        select(SessionRecord)
        .where(SessionRecord.token_hash == _token_hash(token))
        .execution_options(populate_existing=True)
    )
    user = session.get(User, actor.user_id, populate_existing=True)
    now = _database_now(session)
    if record.mfa_pending_id is not None:
        # resolve_session has validated ownership, revision, factor and fingerprint.
        proof = session.get(MfaPending, record.mfa_pending_id, populate_existing=True)
        if record.mfa_reauthenticated_at is not None:
            if (
                record.mfa_reauthentication_credential_id != proof.credential_id
                or record.reauthenticated_at is None
                or record.reauthentication_fingerprint is None
                or not hmac.compare_digest(
                    record.reauthentication_fingerprint, _password_fingerprint(user)
                )
                or not record.created_at
                <= record.reauthenticated_at
                <= record.mfa_reauthenticated_at
                <= now
            ):
                raise RecentAuthenticationRequired("Recent authentication required")
            oldest = min(record.reauthenticated_at, record.mfa_reauthenticated_at)
        else:
            oldest = min(proof.password_verified_at, proof.factor_verified_at)
        if (
            now < record.last_seen_at
            or now >= record.expires_at
            or now >= oldest + RECENT_AUTHENTICATION_WINDOW
        ):
            raise RecentAuthenticationRequired("Recent authentication required")
        return actor
    if (
        record.reauthenticated_at is None
        or record.reauthentication_fingerprint is None
        or now < record.reauthenticated_at
        or now >= record.reauthenticated_at + RECENT_AUTHENTICATION_WINDOW
        or now >= record.expires_at
        or not hmac.compare_digest(
            record.reauthentication_fingerprint, _password_fingerprint(user)
        )
    ):
        raise RecentAuthenticationRequired("Recent authentication required")
    return actor
