"""Reset revocation kernel. Caller holds the global user lock and owns transaction."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.access.models import PasswordResetChallenge


def close_password_resets(session: Session, user_id: UUID) -> None:
    rows = session.scalars(
        select(PasswordResetChallenge)
        .where(
            PasswordResetChallenge.user_id == user_id,
            PasswordResetChallenge.closed_at.is_(None),
        )
        .order_by(PasswordResetChallenge.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    now = session.scalar(select(func.clock_timestamp()))
    for row in rows:
        row.closed_at = row.discarded_at = max(now, row.created_at)
        row.key_id = row.nonce = row.ciphertext = None
    session.flush()
