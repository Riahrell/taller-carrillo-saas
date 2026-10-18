"""Recovery cancellation shared by sessions/revocations, without import cycles."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.access.models import MfaPending, MfaRecoveryPermit


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def close_recovery_permits(
    session: Session, user_id: UUID, *, binding_hash: str | None = None
) -> None:
    """Caller holds user lock; children before sessions; no commit or code refund."""
    query = select(MfaRecoveryPermit).where(
        MfaRecoveryPermit.user_id == user_id, MfaRecoveryPermit.closed_at.is_(None)
    )
    if binding_hash is not None:
        query = query.join(
            MfaPending, MfaPending.id == MfaRecoveryPermit.pending_id
        ).where(MfaPending.binding_hash == binding_hash)
    rows = session.scalars(
        query.order_by(MfaRecoveryPermit.id)
        .with_for_update(of=MfaRecoveryPermit)
        .execution_options(populate_existing=True)
    ).all()
    if rows:
        now = _now(session)
        for row in rows:
            row.closed_at = max(now, row.created_at, row.used_at or row.created_at)
        session.flush()
