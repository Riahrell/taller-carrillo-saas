"""Regenerate only the current session owner's offline codes; internal, no HTTP.

A complete persisted MFA login within five minutes supplies both fresh factors.
Never accept caller identities, roles, factor IDs or a transferable success flag.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import create_session_factory
from app.modules.access import sessions
from app.modules.access.membership import AccessDenied
from app.modules.access.mfa_crypto import generate_recovery_codes, recovery_digest
from app.modules.access.mfa_enrollment import RecoveryBundle
from app.modules.access.mfa_verification import _timeouts
from app.modules.access.models import (
    MfaCredential,
    MfaPending,
    MfaRecoveryCode,
    MfaRecoverySet,
    SecurityEvent,
    SessionRecord,
    User,
)
from app.modules.access.reauthentication import RECENT_AUTHENTICATION_WINDOW
from app.modules.access.security_state import advance_security_revision


class MfaRegenerationUnavailable(RuntimeError):
    """Generic infrastructure error without SQL or secret material."""


def _clock(session: Session, earliest: datetime, deadline: datetime) -> datetime:
    now = sessions._database_now(session)
    if now < earliest or now >= deadline:
        raise AccessDenied("Access denied")
    return now


class MfaCodeRegenerator:
    """Own transaction; commit before publishing ten newly generated codes.

    Reuses a fresh combined login, not a password-only confirmation or an old
    session. Later MFA reauthentication must integrate through persisted evidence.
    Regeneration revokes every session, including its authorizing session, so a
    repeated or concurrent request cannot rotate/reveal another bundle.
    """

    def __init__(self, engine: Engine):
        self._factory = create_session_factory(engine)

    def regenerate(
        self, *, session_token: SecretStr, expected_actor: UUID | None = None
    ) -> RecoveryBundle | None:
        if not isinstance(session_token, SecretStr):
            raise ValueError("Invalid internal MFA input")
        try:
            with self._factory.begin() as session:
                _timeouts(session)
                context = sessions.resolve_session(session, session_token)
                if (
                    expected_actor is not None
                    and context.membership_id != expected_actor
                ):
                    raise AccessDenied("Access denied")
                record = session.scalar(
                    select(SessionRecord).where(
                        SessionRecord.token_hash == sessions._token_hash(session_token)
                    )
                )
                if record.mfa_pending_id is None:
                    raise AccessDenied("Access denied")
                # resolve_session holds the owner lock and validates current proof,
                # fingerprint, revision, factor and active tenant membership.
                proof = session.get(MfaPending, record.mfa_pending_id)
                password_at, factor_at = (
                    proof.password_verified_at,
                    proof.factor_verified_at,
                )
                if record.mfa_reauthenticated_at is not None:
                    from app.modules.access.login_challenges import (
                        _password_fingerprint,
                    )

                    current_user = session.get(User, context.user_id)
                    if (
                        record.mfa_reauthentication_credential_id != proof.credential_id
                        or record.reauthenticated_at is None
                        or record.reauthentication_fingerprint
                        != _password_fingerprint(current_user)
                        or not record.created_at
                        <= record.reauthenticated_at
                        <= record.mfa_reauthenticated_at
                        <= sessions._database_now(session)
                    ):
                        raise AccessDenied("Access denied")
                    password_at, factor_at = (
                        record.reauthenticated_at,
                        record.mfa_reauthenticated_at,
                    )
                deadline = min(
                    record.expires_at,
                    min(password_at, factor_at) + RECENT_AUTHENTICATION_WINDOW,
                )
                checked_at = _clock(session, record.last_seen_at, deadline)
                factor = session.scalar(
                    select(MfaCredential)
                    .where(
                        MfaCredential.user_id == context.user_id,
                        MfaCredential.id == proof.credential_id,
                        MfaCredential.revoked_at.is_(None),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                old_set = session.scalar(
                    select(MfaRecoverySet)
                    .where(
                        MfaRecoverySet.user_id == context.user_id,
                        MfaRecoverySet.credential_id == proof.credential_id,
                        MfaRecoverySet.invalidated_at.is_(None),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if factor is None:
                    raise AccessDenied("Access denied")
                now = _clock(
                    session,
                    max(
                        checked_at,
                        old_set.created_at if old_set is not None else checked_at,
                    ),
                    deadline,
                )
                codes, set_id = generate_recovery_codes(), uuid4()
                if old_set is not None:
                    old_set.invalidated_at = now
                session.flush()  # Release current-set partial uniqueness atomically.
                session.add(
                    MfaRecoverySet(
                        id=set_id,
                        user_id=context.user_id,
                        credential_id=factor.id,
                        created_at=now,
                    )
                )
                session.flush()
                session.add_all(
                    MfaRecoveryCode(
                        set_id=set_id,
                        position=position,
                        code_hash=recovery_digest(
                            code, user_id=context.user_id, set_id=set_id
                        ),
                    )
                    for position, code in enumerate(codes)
                )
                user = session.get(User, context.user_id)
                state = advance_security_revision(
                    session,
                    user_id=user.id,
                    expected_revision=user.security_revision,
                )
                from app.modules.access.security_notices import enqueue_notice

                enqueue_notice(
                    session,
                    user=user,
                    event=SecurityEvent(
                        user_id=user.id,
                        credential_id=factor.id,
                        action="mfa_codes_regenerated",
                        security_revision=state.revision,
                        occurred_at=now,
                    ),
                    now=now,
                )
                session.flush()
                _clock(session, now, deadline)
                result = RecoveryBundle(factor.id, state.revision, codes)
            return result
        except AccessDenied:
            return None
        except SQLAlchemyError:
            raise MfaRegenerationUnavailable("MFA regeneration unavailable") from None
