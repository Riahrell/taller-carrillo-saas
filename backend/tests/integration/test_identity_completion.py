"""Single-use reset authority, rollback and factor independence in PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import test_enrollment_email as enrollment_tests
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_enrollment_email import (  # noqa: F401
    confirm,
    issue,
    request_reset,
    reset_rows,
)

from app.modules.access.email_outbox import MailEnvelope
from app.modules.access.models import SecurityEvent, SecurityNotice, User
from app.modules.access.password_reset import (
    PasswordResetConfirmation,
    PasswordResetConsumer,
    PasswordResetUnavailable,
)
from app.modules.access.passwords import verify_password

state_fixture = enrollment_tests.state
reset_fixture = enrollment_tests.reset
pytestmark = pytest.mark.integration


@pytest.fixture
def state(state_fixture):
    return state_fixture


@pytest.fixture
def reset(reset_fixture):
    return reset_fixture


def prepare(state, reset):
    assert confirm(state, issue(state)) is not None
    request_reset(state, reset)
    (row,) = reset_rows(state)
    payload = reset[1].decrypt(
        MailEnvelope(key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext),
        user_id=row.user_id,
        challenge_id=row.id,
        expires_at=row.expires_at,
    )
    return PasswordResetConfirmation(
        token=payload.token,
        password=SecretStr("Synthetic test phrase with sufficient entropy 92!"),
    )


def test_reset_single_winner_and_no_factor_change(state, reset):
    data = prepare(state, reset)
    with Session(state["runtime"]) as session:
        before = session.get(User, state["identity"])
        original = (before.security_revision, before.mfa_required)
    consumer = PasswordResetConsumer(state["runtime"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consumer.consume(data), range(2)))
    assert sorted(results) == [False, True]
    with Session(state["runtime"]) as session:
        user = session.get(User, state["identity"])
        assert verify_password(data.password, user.password_hash)
        assert user.security_revision == original[0] + 1
        assert user.mfa_required == original[1]
        events = session.scalars(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user.id,
                SecurityEvent.action == "password_reset_completed",
            )
        ).all()
        assert len(events) == 1
        assert (
            session.scalar(
                select(SecurityNotice).where(SecurityNotice.event_id == events[0].id)
            )
            is not None
        )
    (row,) = reset_rows(state)
    assert row.consumed_at is not None and row.ciphertext is None


@pytest.mark.parametrize("failure", ["expiry", "commit"])
def test_reset_failure_preserves_password_and_revision(state, reset, failure):
    data = prepare(state, reset)
    consumer = PasswordResetConsumer(state["runtime"])
    with Session(state["runtime"]) as session:
        user = session.get(User, state["identity"])
        before = (user.password_hash, user.security_revision)
    if failure == "expiry":
        state["now"] += timedelta(minutes=15)
        assert consumer.consume(data) is False
    else:

        def fail(session):
            raise SQLAlchemyError("synthetic commit failure")

        event.listen(consumer._factory, "before_commit", fail, once=True)
        with pytest.raises(PasswordResetUnavailable):
            consumer.consume(data)
    with Session(state["runtime"]) as session:
        user = session.get(User, state["identity"])
        assert (user.password_hash, user.security_revision) == before
        assert not session.scalars(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user.id,
                SecurityEvent.action == "password_reset_completed",
            )
        ).all()


def test_public_reset_submission_never_queries_user_eligibility(
    state, reset, monkeypatch
):
    import re
    import secrets
    from ipaddress import ip_address

    from pydantic import SecretBytes

    from app.modules.access import reset_inbox
    from app.modules.access.email_outbox import MailProtection, MailPurpose
    from app.modules.access.models import PasswordResetChallenge, ResetRequestInbox
    from app.modules.access.password_reset import PasswordResetRequest

    monkeypatch.setattr(reset_inbox, "_database_now", lambda session: state["now"])
    protection = MailProtection(
        {"inbox": SecretBytes(secrets.token_bytes(32))},
        active_key_id="inbox",
        quota_key=SecretBytes(secrets.token_bytes(32)),
        purpose=MailPurpose.RESET_REQUEST,
    )
    inbox = reset_inbox.ResetInbox(
        state["runtime"], protection=protection, issuer=reset[0]
    )
    assert confirm(state, issue(state)) is not None
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(state["runtime"], "before_cursor_execute", capture)
    try:
        for address in (state["email"], "unknown-reset@example.com"):
            assert (
                inbox.submit(
                    PasswordResetRequest(email=address),
                    client_ip=ip_address("127.0.0.1"),
                )
                is None
            )
    finally:
        event.remove(state["runtime"], "before_cursor_execute", capture)
    assert not any(re.search(r"\busers\b", sql, re.I) for sql in statements)
    with Session(state["runtime"]) as session:
        assert len(session.scalars(select(ResetRequestInbox)).all()) == 2
        assert not session.scalars(select(PasswordResetChallenge)).all()
    assert inbox.process_one() is True
    assert inbox.process_one() is True
    assert inbox.process_one() is False
    with Session(state["runtime"]) as session:
        assert len(session.scalars(select(PasswordResetChallenge)).all()) == 1
        assert all(
            row.finished_at is not None and row.ciphertext is None
            for row in session.scalars(select(ResetRequestInbox))
        )
