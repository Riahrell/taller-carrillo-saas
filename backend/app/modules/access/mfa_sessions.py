"""Own the verified-pending -> current membership -> session transaction.

Internal only. HTTP login/selection and MFA enrollment are integrated separately.
An opaque pending token is never accepted as a session cookie.
"""

from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import create_session_factory
from app.modules.access import mfa_pending as pending
from app.modules.access import sessions
from app.modules.access.membership import AccessDenied, resolve_membership
from app.modules.access.models import Tenant, TenantMembership


class MfaSessionUnavailable(RuntimeError):
    """Generic transaction/time failure; never publish an uncommitted token."""


class MfaSessionIssuer:
    def __init__(self, engine: Engine) -> None:
        self._factory = create_session_factory(engine)

    def select_membership(
        self,
        *,
        proof_token: SecretStr,
        browser_binding: SecretStr,
        membership_id: UUID,
    ) -> sessions.IssuedSession | None:
        """A bound selection has one attempt; membership is a candidate only.

        No user, tenant, role, revision or MFA flag supplied by the caller. Return
        only after commit. Do not call from within a business transaction.
        """
        if not isinstance(membership_id, UUID) or not all(
            isinstance(value, SecretStr) for value in (proof_token, browser_binding)
        ):
            return None
        try:
            with self._factory.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                session.execute(text("SET LOCAL statement_timeout = '10s'"))
                locked = pending._lock_pending(
                    session,
                    token=proof_token,
                    binding=browser_binding,
                    purpose=pending.PendingPurpose.LOGIN_VERIFIED,
                )
                if locked is None:
                    return None
                user, proof, checked_at = locked
                member = session.scalar(
                    select(TenantMembership)
                    .join(Tenant, Tenant.id == TenantMembership.tenant_id)
                    .where(
                        TenantMembership.id == membership_id,
                        TenantMembership.user_id == user.id,
                        TenantMembership.is_active.is_(True),
                        Tenant.is_active.is_(True),
                    )
                    .execution_options(populate_existing=True)
                )
                context = (
                    resolve_membership(
                        session, user_id=user.id, tenant_id=member.tenant_id
                    )
                    if member is not None
                    else None
                )
                now = sessions._database_now(session)
                if now < checked_at:
                    raise MfaSessionUnavailable("MFA session unavailable")
                if now >= proof.expires_at:
                    return None
                proof.finished_at = now
                session.flush()
                if context is None:
                    return None
                issued = sessions._create_session(
                    session,
                    user=user,
                    context=context,
                    now=now,
                    mfa_pending_id=proof.id,
                )
            return issued
        except AccessDenied:
            return None
        except SQLAlchemyError:
            raise MfaSessionUnavailable("MFA session unavailable") from None
