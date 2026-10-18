"""Private queue outcomes, bounded retry and revalidation with no real email."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_enrollment_email as enrollment_tests
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session
from test_identity_completion import prepare

from app.modules.access import identity_delivery
from app.modules.access.email_outbox import MailPurpose
from app.modules.access.mail_delivery import TransportOutcome
from app.modules.access.models import IdentityMailDelivery, PasswordResetChallenge, User
from app.modules.access.password_reset import PasswordResetConsumer

state_fixture = enrollment_tests.state
reset_fixture = enrollment_tests.reset
pytestmark = pytest.mark.integration


@pytest.fixture
def state(state_fixture):
    return state_fixture


@pytest.fixture
def reset(reset_fixture):
    return reset_fixture


class Transport:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def send_message(self, message, *, message_id, deadline):
        self.calls.append((message_id, deadline))
        assert (
            "https://identity.example.com/recuperar#reset="
            in message.body.get_secret_value()
        )
        assert message.recipient.get_secret_value()
        return self.outcomes.pop(0)


def dispatcher(state, reset, transport, monkeypatch):
    monkeypatch.setattr(
        identity_delivery, "_database_now", lambda session: state["now"]
    )
    identity = SimpleNamespace(mail={MailPurpose.PASSWORD_RESET: reset[1]})
    identity.require = lambda: identity
    return identity_delivery.IdentityMailDispatcher(
        state["runtime"],
        identity=identity,
        transport=transport,
        origin="https://identity.example.com",
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (TransportOutcome.ACCEPTED, "accepted"),
        (TransportOutcome.UNKNOWN, "uncertain"),
        (TransportOutcome.REJECTED, "rejected"),
    ],
)
def test_terminal_delivery_never_retries_or_invalidates_bearer_lease(
    state, reset, monkeypatch, outcome, expected
):
    data = prepare(state, reset)
    provider = Transport(outcome)
    worker = dispatcher(state, reset, provider, monkeypatch)
    assert worker.dispatch_one() == expected
    assert worker.dispatch_one() == "idle"
    assert len(provider.calls) == 1
    with Session(state["runtime"]) as session:
        delivery = session.scalar(select(IdentityMailDelivery))
        assert delivery.state == expected and delivery.claim_id is None
        row = session.scalar(select(PasswordResetChallenge))
        assert row.ciphertext is None and row.closed_at is None
    # Delivery ambiguity is separate from proof consumption. If the holder did
    # receive the token, the original one-use lease remains enforceable.
    assert PasswordResetConsumer(state["runtime"]).consume(data)


def test_positive_non_delivery_has_three_bounded_attempts_and_stable_message_id(
    state, reset, monkeypatch
):
    prepare(state, reset)
    provider = Transport(*([TransportOutcome.NOT_SENT] * 3))
    worker = dispatcher(state, reset, provider, monkeypatch)
    assert worker.dispatch_one() == "pending"
    assert worker.dispatch_one() == "idle"
    state["now"] += timedelta(seconds=60)
    assert worker.dispatch_one() == "pending"
    state["now"] += timedelta(seconds=60)
    assert worker.dispatch_one() == "rejected"
    state["now"] += timedelta(seconds=60)
    assert worker.dispatch_one() == "idle"
    assert len(provider.calls) == 3
    assert len({identity for identity, _ in provider.calls}) == 1


def test_mailbox_change_after_claim_cancels_send_and_token_consumption(
    state, reset, monkeypatch
):
    data = prepare(state, reset)
    provider = Transport()
    worker = dispatcher(state, reset, provider, monkeypatch)
    commits = 0

    def change_after_claim(session):
        nonlocal commits
        commits += 1
        if commits == 2:
            with state["runtime"].begin() as connection:
                connection.execute(
                    update(User)
                    .where(User.id == state["identity"])
                    .values(email="changed-" + uuid4().hex + "@example.com")
                )

    event.listen(worker._factory, "after_commit", change_after_claim)
    try:
        assert worker.dispatch_one() == "cancelled"
    finally:
        event.remove(worker._factory, "after_commit", change_after_claim)
    assert not provider.calls
    assert not PasswordResetConsumer(state["runtime"]).consume(data)
