"""Child-only rotation closure under caller's user lock; never grants authority."""

from sqlalchemy import func, select

from app.modules.access.models import MfaRotation


def close_rotations(session, user_id=None, *, session_id=None):
    query = select(MfaRotation).where(MfaRotation.closed_at.is_(None))
    query = (
        query.where(MfaRotation.user_id == user_id)
        if user_id is not None
        else query.where(MfaRotation.session_id == session_id)
    )
    rows = session.scalars(
        query.order_by(MfaRotation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    now = session.scalar(select(func.clock_timestamp()))
    for row in rows:
        row.closed_at = max(now, row.created_at)
        row.key_id = row.nonce = row.ciphertext = None
    session.flush()
