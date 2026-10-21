"""HTTP access transactions, bounded password work, and server session views."""

import hmac
from contextlib import contextmanager, suppress
from threading import BoundedSemaphore
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access import mfa_pending
from app.modules.access.authentication import (
    AuthenticationUnavailable,
    Authenticator,
    _limit_transaction,
)
from app.modules.access.email_login_models import EmailLoginChallenge
from app.modules.access.login_challenges import _binding_digest, _token_digest
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    LoginChallenge,
    MembershipRole,
    MfaPending,
    SessionRecord,
    Tenant,
)
from app.modules.access.sessions import (
    IssuedSession,
    _database_now,
    _lock_user,
    _session_user_id,
    _token_hash,
    resolve_session,
    revoke_session,
)


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: UUID
    membership_id: UUID
    tenant_id: UUID
    tenant_name: str
    role: MembershipRole


class HttpAccess:
    def __init__(self, engine: Engine) -> None:
        self.authenticator = Authenticator(engine)
        self._factory = create_session_factory(engine)
        self._hash_slots = BoundedSemaphore(2)

    @contextmanager
    def password_slot(self):
        if not self._hash_slots.acquire(blocking=False):
            raise AuthenticationUnavailable("Authentication unavailable")
        try:
            yield
        finally:
            self._hash_slots.release()

    def current(self, token: SecretStr) -> tuple[SessionView, IssuedSession] | None:
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                context = resolve_session(session, token)
                expires = session.scalar(
                    select(SessionRecord.expires_at).where(
                        SessionRecord.token_hash == _token_hash(token)
                    )
                )
                name = session.scalar(
                    select(Tenant.name).where(Tenant.id == context.tenant_id)
                )
                result = (
                    SessionView(
                        user_id=context.user_id,
                        membership_id=context.membership_id,
                        tenant_id=context.tenant_id,
                        tenant_name=name,
                        role=context.role,
                    ),
                    IssuedSession(token=token, expires_at=expires),
                )
            return result
        except AccessDenied:
            return None
        except SQLAlchemyError:
            raise AuthenticationUnavailable("Authentication unavailable") from None

    def logout(
        self,
        *,
        session_token: SecretStr | None,
        challenge_token: SecretStr | None,
        browser_binding: SecretStr | None,
    ) -> None:
        """Revoke browser session and bound pending login atomically, idempotently."""
        try:
            session_hash = _token_hash(session_token) if session_token else None
        except AccessDenied:
            session_hash = None
        try:
            challenge_hash = (
                _token_digest(challenge_token)
                if challenge_token and browser_binding
                else None
            )
            binding_hash = _binding_digest(browser_binding) if challenge_hash else None
        except AccessDenied:
            challenge_hash, binding_hash = None, None
        try:
            with self._factory.begin() as session:
                _limit_transaction(session)
                users = set()
                if session_hash:
                    users.add(_session_user_id(session, session_hash))
                if challenge_hash:
                    users.add(
                        session.scalar(
                            select(LoginChallenge.user_id).where(
                                LoginChallenge.token_hash == challenge_hash
                            )
                        )
                    )
                typed_binding = None
                if browser_binding is not None:
                    with suppress(AccessDenied):
                        typed_binding = mfa_pending._hash(
                            browser_binding, "mfa-pending-browser-v1:"
                        )
                if typed_binding is not None:
                    users.update(
                        session.scalars(
                            select(MfaPending.user_id).where(
                                MfaPending.binding_hash == typed_binding
                            )
                        )
                    )
                email_binding = None
                if browser_binding is not None:
                    with suppress(AccessDenied):
                        email_binding = _token_hash(browser_binding)
                if email_binding is not None:
                    users.update(
                        session.scalars(
                            select(EmailLoginChallenge.user_id).where(
                                EmailLoginChallenge.binding_hash == email_binding,
                                EmailLoginChallenge.finished_at.is_(None),
                                EmailLoginChallenge.invalidated_at.is_(None),
                            )
                        )
                    )
                # Mixed/stale cookies may refer to different users: lock in UUID order.
                for user_id in sorted(user for user in users if user is not None):
                    _lock_user(session, user_id)
                if typed_binding is not None:
                    for user_id in sorted(user for user in users if user is not None):
                        mfa_pending._close_browser_pendings(
                            session, user_id=user_id, binding=browser_binding
                        )
                if email_binding is not None:
                    for user_id in sorted(user for user in users if user is not None):
                        rows = session.scalars(
                            select(EmailLoginChallenge)
                            .where(
                                EmailLoginChallenge.user_id == user_id,
                                EmailLoginChallenge.binding_hash == email_binding,
                                EmailLoginChallenge.finished_at.is_(None),
                                EmailLoginChallenge.invalidated_at.is_(None),
                            )
                            .order_by(EmailLoginChallenge.id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        ).all()
                        for pending in rows:
                            pending.invalidated_at = _database_now(session)
                            pending.code_hash = None
                            pending.key_id = pending.nonce = pending.ciphertext = None
                if challenge_hash:
                    record = session.scalar(
                        select(LoginChallenge)
                        .where(LoginChallenge.token_hash == challenge_hash)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if (
                        record
                        and record.finished_at is None
                        and hmac.compare_digest(record.binding_hash, binding_hash)
                    ):
                        record.finished_at = max(
                            record.created_at, _database_now(session)
                        )
                        session.flush()
                if session_hash:
                    revoke_session(session, session_token)
        except SQLAlchemyError:
            raise AuthenticationUnavailable("Authentication unavailable") from None
