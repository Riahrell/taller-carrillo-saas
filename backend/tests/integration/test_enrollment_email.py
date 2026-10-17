"""Mailbox challenge and initial-factor grant; no real email or operational users."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretBytes, SecretStr
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access import (
    authentication,
    email_outbox,
    login_challenges,
    mfa_pending,
    security_state,
    sessions,
)
from app.modules.access import enrollment_email as service
from app.modules.access.mfa_sessions import MfaSessionIssuer
from app.modules.access.models import (
    EnrollmentEmailChallenge,
    EnrollmentMailOutbox,
    EnrollmentMailThrottle,
    LoginChallenge,
    LoginThrottle,
    MfaCredential,
    MfaPending,
    SecurityEvent,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)
from app.modules.access.passwords import hash_password

pytestmark = pytest.mark.integration


@pytest.fixture
def state(mfa_state, monkeypatch):
    state = dict(
        mfa_state,
        identity=mfa_state["other"],
        binding=SecretStr(secrets.token_urlsafe(32)),
        password=SecretStr(secrets.token_urlsafe(32)),
    )
    for module, name in (
        (authentication, "_now"),
        (authentication, "_database_now"),
        (mfa_pending, "_database_now"),
        (service, "_database_now"),
        (email_outbox, "_database_now"),
        (security_state, "_database_now"),
        (sessions, "_database_now"),
        (login_challenges, "_database_now"),
    ):
        monkeypatch.setattr(module, name, lambda session: state["now"])
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, state["identity"])
        user.password_hash = hash_password(state["password"])
        state["email"] = user.email
        tenant = Tenant(name="Mailbox fixture")
        session.add(tenant)
        session.flush()
        member = TenantMembership(tenant_id=tenant.id, user_id=user.id, role="admin")
        session.add(member)
        session.flush()
        state["tenant"], state["member"] = tenant.id, member.id
    state["auth"] = authentication.Authenticator(state["runtime"])
    state["pending"] = start(state)
    assert state["pending"].purpose is mfa_pending.PendingPurpose.ENROLLMENT
    state["protection"] = email_outbox.MailProtection(
        {"test": SecretBytes(secrets.token_bytes(32))},
        active_key_id="test",
        quota_key=SecretBytes(secrets.token_bytes(32)),
    )
    state["mail"] = service.EnrollmentEmailService(
        state["runtime"], protection=state["protection"]
    )
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                delete(EnrollmentMailOutbox).where(
                    EnrollmentMailOutbox.challenge_id.in_(
                        select(EnrollmentEmailChallenge.id).where(
                            EnrollmentEmailChallenge.user_id.in_(
                                [state["user"], state["identity"]]
                            )
                        )
                    )
                )
            )
            connection.execute(delete(EnrollmentMailThrottle))
            connection.execute(
                delete(SessionRecord).where(
                    SessionRecord.membership_id == state["member"]
                )
            )
            connection.execute(
                delete(EnrollmentEmailChallenge).where(
                    EnrollmentEmailChallenge.user_id.in_(
                        [state["user"], state["identity"]]
                    )
                )
            )
            connection.execute(
                delete(MfaPending).where(
                    MfaPending.user_id.in_([state["user"], state["identity"]])
                )
            )
            connection.execute(
                delete(LoginChallenge).where(
                    LoginChallenge.user_id == state["identity"]
                )
            )
            connection.execute(
                delete(MfaCredential).where(MfaCredential.user_id == state["identity"])
            )
            connection.execute(
                delete(TenantMembership).where(TenantMembership.id == state["member"])
            )
            connection.execute(delete(Tenant).where(Tenant.id == state["tenant"]))
            connection.execute(delete(LoginThrottle))


def start(state, binding=None):
    return state["auth"].begin_mfa_login(
        authentication.Credentials(email=state["email"], password=state["password"]),
        client_ip=ip_address("127.0.0.1"),
        browser_binding=binding or state["binding"],
    )


def issue(state):
    return state["mail"].issue(
        enrollment_token=state["pending"].token,
        browser_binding=state["binding"],
        client_ip=ip_address("127.0.0.1"),
    )


def confirm(state, draft, *, inspect=False, **changes):
    action = state["mail"].inspect if inspect else state["mail"].confirm
    values = dict(
        enrollment_token=state["pending"].token,
        browser_binding=state["binding"],
        email_token=draft.token,
    )
    return action(**(values | changes))


def consume(state, permit, binding=None):
    with Session(state["runtime"]) as session, session.begin():
        return service._consume_permit(
            session, token=permit.token, browser_binding=binding or state["binding"]
        )


def snapshot(state):
    with Session(state["runtime"]) as session:
        pending = session.scalar(
            select(MfaPending).where(
                MfaPending.token_hash
                == mfa_pending._hash(state["pending"].token, "mfa-pending-token-v1:")
            )
        )
        rows = list(
            session.scalars(
                select(EnrollmentEmailChallenge)
                .where(EnrollmentEmailChallenge.user_id == state["identity"])
                .order_by(EnrollmentEmailChallenge.id)
            )
        )
        events = list(
            session.scalars(
                select(SecurityEvent)
                .where(SecurityEvent.user_id == state["identity"])
                .order_by(SecurityEvent.id)
            )
        )
        return (
            pending.finished_at,
            [
                (
                    r.id,
                    r.token_hash,
                    r.email_fingerprint,
                    r.consumed_at,
                    r.permit_token_hash,
                    r.permit_used_at,
                    r.closed_at,
                    r.expires_at,
                )
                for r in rows
            ],
            [(e.action, e.security_revision) for e in events],
        )


def test_draft_uses_registered_address_and_hashes_only_with_no_read_consumption(state):
    draft = issue(state)
    assert draft.recipient.get_secret_value() == state["email"]
    assert draft.expires_at == state["pending"].expires_at
    assert draft.token.get_secret_value() not in repr(draft) and state[
        "email"
    ] not in repr(draft)
    before = snapshot(state)
    assert before[1][0][1] != draft.token.get_secret_value()
    assert before[1][0][2] != state["email"]
    assert issue(state) is None  # Never recover token or resend by duplicate issue.
    assert confirm(state, draft, inspect=True) is True
    assert confirm(state, draft, inspect=True) is True
    assert snapshot(state) == before


def test_confirm_consumes_mail_and_source_and_emits_only_single_use_restricted_grant(
    state,
):
    draft = issue(state)
    permit = confirm(state, draft)
    assert permit is not None and permit.token != draft.token
    assert permit.expires_at == state["pending"].expires_at
    data = snapshot(state)
    assert data[0] == data[1][0][3] == state["now"]
    assert data[1][0][4] != permit.token.get_secret_value()
    assert ("mfa_enrollment_email_confirmed", 1) in data[2]
    assert (
        confirm(state, draft) is None and confirm(state, draft, inspect=True) is False
    )
    assert (
        MfaSessionIssuer(state["runtime"]).select_membership(
            proof_token=permit.token,
            browser_binding=state["binding"],
            membership_id=state["member"],
        )
        is None
    )
    context = consume(state, permit)
    assert (
        context.user_id == state["identity"] and context.expires_at == permit.expires_at
    )
    assert consume(state, permit) is None
    with Session(state["runtime"]) as session:
        assert not list(
            session.scalars(
                select(SessionRecord).where(
                    SessionRecord.membership_id == state["member"]
                )
            )
        )
        assert not list(
            session.scalars(
                select(MfaCredential).where(MfaCredential.user_id == state["identity"])
            )
        )


@pytest.mark.parametrize("kind", ["email_token", "binding", "pending", "malformed"])
def test_wrong_secret_or_browser_never_consumes_valid_challenge(state, kind):
    draft = issue(state)
    changes = {
        "email_token": dict(email_token=SecretStr(secrets.token_urlsafe(32))),
        "binding": dict(browser_binding=SecretStr(secrets.token_urlsafe(32))),
        "pending": dict(enrollment_token=SecretStr(secrets.token_urlsafe(32))),
        "malformed": dict(email_token=SecretStr("bad")),
    }
    before = snapshot(state)
    assert confirm(state, draft, **changes[kind]) is None
    assert confirm(state, draft, inspect=True, **changes[kind]) is False
    assert snapshot(state) == before
    assert confirm(state, draft) is not None


@pytest.mark.parametrize("kind", ["email", "password", "revision", "inactive"])
def test_current_exact_address_identity_and_security_state_are_required(state, kind):
    draft = issue(state)
    changes = {
        "email": dict(email="changed@example.com"),
        "password": dict(password_hash="changed-synthetic"),
        "revision": dict(security_revision=2),
        "inactive": dict(is_active=False),
    }
    with state["runtime"].begin() as connection:
        connection.execute(
            update(User).where(User.id == state["identity"]).values(**changes[kind])
        )
    assert confirm(state, draft) is None
    assert snapshot(state)[0] is None and snapshot(state)[1][0][3] is None


@pytest.mark.parametrize(
    "offset,accepted", [(899, True), (900, False), (901, False), (-1, False)]
)
def test_exact_original_deadline_and_regressed_time(state, offset, accepted):
    draft = issue(state)
    state["now"] += timedelta(seconds=offset)
    result = confirm(state, draft)
    assert (result is not None) is accepted
    if accepted:
        assert (
            result.expires_at == draft.expires_at and consume(state, result) is not None
        )


def test_confirmation_rechecks_clock_after_child_lock_and_rejects_expiry(
    state, monkeypatch
):
    draft = issue(state)
    before = snapshot(state)
    monkeypatch.setattr(service, "_database_now", lambda session: draft.expires_at)
    assert confirm(state, draft) is None
    assert snapshot(state) == before


def test_clock_regression_during_confirmation_is_generic_unavailability(
    state, monkeypatch
):
    draft = issue(state)
    before = snapshot(state)
    monkeypatch.setattr(
        service,
        "_database_now",
        lambda session: state["now"] - timedelta(microseconds=1),
    )
    with pytest.raises(
        service.EnrollmentEmailUnavailable, match="^Enrollment email unavailable$"
    ):
        confirm(state, draft)
    assert snapshot(state) == before


@pytest.mark.parametrize("stage", ["issue", "confirm"])
def test_commit_failure_never_publishes_token_or_partial_proof(state, stage):
    draft = issue(state) if stage == "confirm" else None

    def fail(session):
        raise SQLAlchemyError("Synthetic commit failure")

    event.listen(state["mail"]._factory, "before_commit", fail, once=True)
    before = snapshot(state)
    with pytest.raises(service.EnrollmentEmailUnavailable):
        confirm(state, draft) if draft else issue(state)
    assert snapshot(state) == before


@pytest.mark.parametrize(
    "table,operation",
    [
        ("security_events", "INSERT"),
        ("enrollment_email_challenges", "UPDATE"),
        ("mfa_pendings", "UPDATE"),
    ],
)
def test_real_write_failure_rolls_back_consumption_permit_and_event(
    state, table, operation
):
    draft = issue(state)
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(text(f"REVOKE {operation} ON {table} FROM carrillo_app"))
    try:
        with pytest.raises(service.EnrollmentEmailUnavailable):
            confirm(state, draft)
        assert snapshot(state) == before
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text(f"GRANT {operation} ON {table} TO carrillo_app"))
    assert confirm(state, draft) is not None


def test_later_activation_failure_must_roll_back_permit_consumption(state):
    permit = confirm(state, issue(state))
    before = snapshot(state)
    with (
        pytest.raises(RuntimeError),
        Session(state["runtime"]) as session,
        session.begin(),
    ):
        assert service._consume_permit(
            session, token=permit.token, browser_binding=state["binding"]
        )
        raise RuntimeError("Synthetic later activation failure")
    assert snapshot(state) == before
    assert consume(state, permit) is not None


@pytest.mark.parametrize("kind", ["binding", "email", "revision", "expiry"])
def test_verified_grant_is_still_bound_and_does_not_renew_deadline(state, kind):
    draft = issue(state)
    state["now"] += timedelta(minutes=14)
    permit = confirm(state, draft)
    if kind == "email":
        with state["runtime"].begin() as connection:
            connection.execute(
                update(User)
                .where(User.id == state["identity"])
                .values(email="changed@example.com")
            )
    elif kind == "revision":
        with state["runtime"].begin() as connection:
            connection.execute(
                update(User)
                .where(User.id == state["identity"])
                .values(security_revision=2)
            )
    elif kind == "expiry":
        state["now"] = draft.expires_at
    binding = (
        SecretStr(secrets.token_urlsafe(32)) if kind == "binding" else state["binding"]
    )
    assert consume(state, permit, binding=binding) is None


@pytest.mark.parametrize("same_browser", [True, False])
def test_restarting_password_closes_only_same_browser_confirmed_permit(
    state, same_browser
):
    permit = confirm(state, issue(state))
    binding = state["binding"] if same_browser else SecretStr(secrets.token_urlsafe(32))
    assert start(state, binding=binding) is not None
    assert (consume(state, permit) is None) is same_browser


@pytest.mark.parametrize("advance", [True, False])
def test_global_revocation_also_closes_confirmed_mail_permits(state, advance):
    permit = confirm(state, issue(state))
    with Session(state["runtime"]) as session, session.begin():
        if advance:
            security_state.advance_security_revision(
                session, user_id=state["identity"], expected_revision=1
            )
        else:
            login_challenges.revoke_user_challenges(session, state["identity"])
    assert snapshot(state)[1][0][6] is not None
    assert consume(state, permit) is None


def test_factor_history_blocks_mail_grant_from_becoming_replacement(state):
    permit = confirm(state, issue(state))
    credential_id = uuid4()
    envelope = state["keyring"].encrypt(
        state["seed"], user_id=state["identity"], credential_id=credential_id
    )
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            MfaCredential(
                id=credential_id,
                user_id=state["identity"],
                encryption_version=envelope.version,
                key_id=envelope.key_id,
                nonce=envelope.nonce,
                ciphertext=envelope.ciphertext,
                created_at=state["now"] - timedelta(seconds=1),
                revoked_at=state["now"],
            )
        )
    assert consume(state, permit) is None


def test_owner_fk_and_partial_permit_check_are_enforced_by_postgres(state):
    with (
        pytest.raises(IntegrityError) as error,
        Session(state["runtime"]) as session,
        session.begin(),
    ):
        pending_id = session.scalar(
            select(MfaPending.id).where(
                MfaPending.token_hash
                == mfa_pending._hash(state["pending"].token, "mfa-pending-token-v1:")
            )
        )
        session.add(
            EnrollmentEmailChallenge(
                user_id=state["user"],
                pending_id=pending_id,
                token_hash="a" * 64,
                email_fingerprint="b" * 64,
                created_at=state["now"],
                expires_at=state["pending"].expires_at,
            )
        )
        session.flush()
    assert (
        error.value.orig.diag.constraint_name
        == "fk_enrollment_email_challenges_pending_owner"
    )
    issue(state)
    with pytest.raises(IntegrityError) as error, state["runtime"].begin() as connection:
        connection.execute(
            update(EnrollmentEmailChallenge)
            .where(EnrollmentEmailChallenge.user_id == state["identity"])
            .values(permit_used_at=state["now"])
        )
    assert (
        error.value.orig.diag.constraint_name
        == "ck_enrollment_email_challenges_proof_state"
    )


def test_runtime_deletion_and_downgrade_cannot_discard_mailbox_evidence(state):
    issue(state)
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(
            delete(EnrollmentEmailChallenge).where(
                EnrollmentEmailChallenge.user_id == state["identity"]
            )
        )
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0015_mfa_sessions")
    assert len(snapshot(state)[1]) == 1


def test_concurrent_confirmations_have_one_permit_and_one_event(state, monkeypatch):
    draft = issue(state)
    entered, release, started = Event(), Event(), Event()
    pids = []
    original = mfa_pending._lock_user

    def pause(session):
        if not entered.is_set():
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic commit contention")

    def observe(session, user_id):
        if entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
        return original(session, user_id)

    event.listen(state["mail"]._factory, "before_commit", pause)
    monkeypatch.setattr(mfa_pending, "_lock_user", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm, state, draft)
        try:
            assert entered.wait(5)
            second = pool.submit(confirm, state, draft)
            assert started.wait(5)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with state["owner"].connect() as connection:
                    if connection.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pids[0]},
                    ):
                        break
                time.sleep(0.01)
            else:
                pytest.fail("Second connection did not block")
        finally:
            release.set()
        assert first.result(timeout=5) is not None and second.result(timeout=5) is None
    assert (
        len(
            [
                item
                for item in snapshot(state)[2]
                if item[0] == "mfa_enrollment_email_confirmed"
            ]
        )
        == 1
    )


def outbox_rows(state):
    with Session(state["runtime"]) as session:
        return list(
            session.scalars(
                select(EnrollmentMailOutbox).order_by(EnrollmentMailOutbox.challenge_id)
            )
        )


def quota_rows(state):
    with Session(state["runtime"]) as session:
        return [
            (r.scope, r.key_hash, r.issued, r.last_issued_at)
            for r in session.scalars(
                select(EnrollmentMailThrottle).order_by(
                    EnrollmentMailThrottle.scope, EnrollmentMailThrottle.key_hash
                )
            )
        ]


def test_issue_stages_only_encrypted_payload_and_reserves_account_and_ip(state):
    draft = issue(state)
    rows = outbox_rows(state)
    assert len(rows) == 1 and rows[0].expires_at == draft.expires_at
    row = rows[0]
    decoded = state["protection"].decrypt(
        email_outbox.MailEnvelope(
            key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext
        ),
        user_id=state["identity"],
        challenge_id=row.challenge_id,
        expires_at=row.expires_at,
    )
    assert decoded.recipient == draft.recipient and decoded.token == draft.token
    assert draft.token.get_secret_value().encode() not in row.ciphertext
    assert draft.recipient.get_secret_value().encode() not in row.ciphertext
    before = quota_rows(state)
    assert len(before) == 2 and {r[2] for r in before} == {1}
    assert all(len(r[1]) == 64 and "127.0.0.1" not in r[1] for r in before)
    assert issue(state) is None and quota_rows(state) == before


def test_missing_mail_keys_fails_closed_without_challenge_or_quota(state):
    mail = service.EnrollmentEmailService(state["runtime"])
    with pytest.raises(service.EnrollmentEmailUnavailable):
        mail.issue(
            enrollment_token=state["pending"].token,
            browser_binding=state["binding"],
            client_ip=ip_address("127.0.0.1"),
        )
    assert not outbox_rows(state) and not quota_rows(state)
    assert not snapshot(state)[1]


def test_mail_account_cooldown_window_and_limit_survive_browser_restart(state):
    initial = state["now"]
    assert issue(state) is not None
    state["pending"] = start(state)
    assert issue(state) is None
    state["now"] += timedelta(seconds=59)
    assert issue(state) is None
    state["now"] += timedelta(seconds=1)
    assert issue(state) is not None
    state["now"] += timedelta(seconds=60)
    state["pending"] = start(state)
    assert issue(state) is not None
    state["now"] += timedelta(seconds=60)
    state["pending"] = start(state)
    assert issue(state) is None
    assert {r[2] for r in quota_rows(state)} == {3}
    state["now"] = initial + timedelta(minutes=15)
    state["pending"] = start(state)
    assert issue(state) is not None
    assert {r[2] for r in quota_rows(state)} == {1}


def test_shared_ip_quota_rejects_without_spending_account_allowance(state):
    scope, key_hash = state["protection"].scopes(
        state["identity"], ip_address("127.0.0.1")
    )[1]
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            EnrollmentMailThrottle(
                scope=scope,
                key_hash=key_hash,
                window_started_at=state["now"],
                issued=20,
                last_issued_at=state["now"],
            )
        )
    assert issue(state) is None
    assert not snapshot(state)[1] and not outbox_rows(state)
    assert [(r[0], r[2]) for r in quota_rows(state)] == [("account", 0), ("ip", 20)]


@pytest.mark.parametrize("clock", ["expired", "regressed"])
def test_issue_rechecks_time_after_quota_locks(state, monkeypatch, clock):
    checked = state["now"]
    monkeypatch.setattr(
        email_outbox,
        "_database_now",
        lambda session: (
            state["pending"].expires_at
            if clock == "expired"
            else checked - timedelta(microseconds=1)
        ),
    )
    if clock == "expired":
        assert issue(state) is None
        assert all(r[2] == 0 for r in quota_rows(state))
    else:
        with pytest.raises(service.EnrollmentEmailUnavailable):
            issue(state)
        assert not quota_rows(state)
    assert not snapshot(state)[1] and not outbox_rows(state)


@pytest.mark.parametrize("reason", ["confirm", "restart", "revoke", "expire"])
def test_outbox_payload_is_discarded_without_deleting_evidence(state, reason):
    draft = issue(state)
    if reason == "confirm":
        assert confirm(state, draft) is not None
    elif reason == "restart":
        start(state)
    elif reason == "revoke":
        with Session(state["runtime"]) as session, session.begin():
            login_challenges.revoke_user_challenges(session, state["identity"])
    else:
        state["now"] = draft.expires_at - timedelta(microseconds=1)
        assert email_outbox.purge_expired_mail(state["runtime"]) == 0
        state["now"] = draft.expires_at
        assert email_outbox.purge_expired_mail(state["runtime"], limit=1) == 1
        assert email_outbox.purge_expired_mail(state["runtime"]) == 0
    (row,) = outbox_rows(state)
    assert row.discarded_at == state["now"]
    assert row.ciphertext is row.nonce is row.key_id is None
    assert len(snapshot(state)[1]) == 1


@pytest.mark.parametrize(
    "failure", ["outbox_insert", "quota_update", "crypto", "commit"]
)
def test_issue_rollback_preserves_atomic_challenge_outbox_and_mail_quota(
    state, monkeypatch, failure
):
    privileges = {
        "outbox_insert": ("enrollment_mail_outbox", "INSERT"),
        "quota_update": ("enrollment_mail_throttles", "UPDATE"),
    }
    if failure in privileges:
        table, operation = privileges[failure]
        with state["owner"].begin() as connection:
            connection.execute(text(f"REVOKE {operation} ON {table} FROM carrillo_app"))
    elif failure == "crypto":

        def fail(**kwargs):
            raise email_outbox.MailProtectionError("Mail protection unavailable")

        monkeypatch.setattr(state["protection"], "encrypt", fail)
    else:

        def fail_commit(session):
            raise SQLAlchemyError("Synthetic commit failure")

        event.listen(state["mail"]._factory, "before_commit", fail_commit, once=True)
    try:
        with pytest.raises(service.EnrollmentEmailUnavailable):
            issue(state)
        assert not outbox_rows(state) and not quota_rows(state)
        assert not snapshot(state)[1]
    finally:
        if failure in privileges:
            with state["owner"].begin() as connection:
                connection.execute(
                    text(f"GRANT {operation} ON {table} TO carrillo_app")
                )


def test_confirm_outbox_write_failure_rolls_back_mail_proof_and_audit(state):
    draft = issue(state)
    before = snapshot(state)
    with state["owner"].begin() as connection:
        connection.execute(
            text("REVOKE UPDATE ON enrollment_mail_outbox FROM carrillo_app")
        )
    try:
        with pytest.raises(service.EnrollmentEmailUnavailable):
            confirm(state, draft)
        assert (
            snapshot(state) == before and outbox_rows(state)[0].ciphertext is not None
        )
    finally:
        with state["owner"].begin() as connection:
            connection.execute(
                text("GRANT UPDATE ON enrollment_mail_outbox TO carrillo_app")
            )
    assert confirm(state, draft) is not None


def test_outbox_constraints_delete_and_downgrade_guard(state):
    issue(state)
    (row,) = outbox_rows(state)
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(EnrollmentMailOutbox).values(ciphertext=None))
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(delete(EnrollmentMailOutbox))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0016_enrollment_email")
    assert outbox_rows(state)[0].challenge_id == row.challenge_id


def test_concurrent_issuance_has_one_ciphertext_and_one_quota_reservation(
    state, monkeypatch
):
    second_binding = SecretStr(secrets.token_urlsafe(32))
    second_pending = start(state, binding=second_binding)
    entered, release, started = Event(), Event(), Event()
    pids = []
    original = mfa_pending._lock_user

    def pause(session):
        if not entered.is_set():
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic mail contention")

    def observe(session, user_id):
        if entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
        return original(session, user_id)

    event.listen(state["mail"]._factory, "before_commit", pause)
    monkeypatch.setattr(mfa_pending, "_lock_user", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(issue, state)
        try:
            assert entered.wait(5)
            second = pool.submit(
                state["mail"].issue,
                enrollment_token=second_pending.token,
                browser_binding=second_binding,
                client_ip=ip_address("127.0.0.1"),
            )
            assert started.wait(5)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with state["owner"].connect() as connection:
                    if connection.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pids[0]},
                    ):
                        break
                time.sleep(0.01)
            else:
                pytest.fail("Second mail issuer did not block")
        finally:
            release.set()
        assert first.result(timeout=5) is not None and second.result(timeout=5) is None
    assert len(outbox_rows(state)) == 1 and {r[2] for r in quota_rows(state)} == {1}
