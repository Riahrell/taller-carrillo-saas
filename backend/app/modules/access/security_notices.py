"Notification enqueue in the credential-changing transaction, without secret payload."

from datetime import timedelta

from app.modules.access.mailbox_verification import _address_hash
from app.modules.access.models import SecurityNotice


def enqueue_notice(session, *, user, event, now):
    session.add(event)
    session.flush()
    session.add(
        SecurityNotice(
            event_id=event.id,
            address_hash=_address_hash(user),
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
    )
