"""Mailbox challenge and initial-factor grant; no real email or operational users."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
from threading import Event
from uuid import uuid4

import pyotp
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
    mail_delivery,
    mailbox_verification,
    mfa_consumption,
    mfa_enrollment,
    mfa_pending,
    mfa_verification,
    password_reset,
    security_state,
    sessions,
)
from app.modules.access import enrollment_email as service
from app.modules.access.mfa_crypto import match_recovery_code
from app.modules.access.mfa_sessions import MfaSessionIssuer
from app.modules.access.models import (
    EnrollmentEmailChallenge,
    EnrollmentMailDelivery,
    EnrollmentMailOutbox,
    EnrollmentMailThrottle,
    IdentityMailDelivery,
    LoginChallenge,
    LoginThrottle,
    MailboxChallenge,
    MfaCredential,
    MfaEnrollment,
    MfaPending,
    MfaRecoveryCode,
    MfaRecoverySet,
    MfaRotation,
    MfaThrottle,
    PasswordResetChallenge,
    PasswordResetThrottle,
    ResetRequestInbox,
    SecurityEvent,
    SecurityNotice,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
    VerifiedMailbox,
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
        (mailbox_verification, "_database_now"),
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
            from identity_cleanup import cleanup_identity

            cleanup_identity(connection, [state["identity"]])
            connection.execute(delete(IdentityMailDelivery))
            connection.execute(delete(ResetRequestInbox))
            connection.execute(
                delete(MfaRotation).where(MfaRotation.user_id == state["identity"])
            )
            connection.execute(
                delete(SecurityNotice).where(
                    SecurityNotice.event_id.in_(
                        select(SecurityEvent.id).where(
                            SecurityEvent.user_id == state["identity"]
                        )
                    )
                )
            )
            connection.execute(delete(PasswordResetChallenge))
            connection.execute(delete(PasswordResetThrottle))
            connection.execute(delete(VerifiedMailbox))
            connection.execute(
                delete(MailboxChallenge).where(
                    MailboxChallenge.user_id == state["identity"]
                )
            )
            connection.execute(delete(MfaEnrollment))
            connection.execute(
                delete(SecurityEvent).where(SecurityEvent.user_id == state["identity"])
            )
            connection.execute(
                delete(MfaRecoveryCode).where(
                    MfaRecoveryCode.set_id.in_(
                        select(MfaRecoverySet.id).where(
                            MfaRecoverySet.user_id == state["identity"]
                        )
                    )
                )
            )
            connection.execute(
                delete(MfaRecoverySet).where(
                    MfaRecoverySet.user_id == state["identity"]
                )
            )
            connection.execute(delete(EnrollmentMailDelivery))
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


class RecordingTransport:
    """Memory only; never SMTP, URL construction or actual delivery."""

    def __init__(self, state):
        self.state = state
        # db_session intentionally keeps its guard transaction checked out.
        # The dispatcher must return every EXTRA connection before transport.
        self.baseline_checkouts = state["runtime"].pool.checkedout()
        self.calls = []
        self.action = lambda: mail_delivery.TransportOutcome.ACCEPTED

    def send(self, payload, *, message_id, expires_at, deadline):
        assert self.state["runtime"].pool.checkedout() == self.baseline_checkouts
        self.calls.append((payload, message_id, expires_at, deadline))
        return self.action()


@pytest.fixture
def dispatch(state, monkeypatch):
    monkeypatch.setattr(mail_delivery, "_database_now", lambda session: state["now"])
    transport = RecordingTransport(state)
    worker = mail_delivery.MailDispatcher(
        state["runtime"], protection=state["protection"], transport=transport
    )
    return worker, transport


def delivery_rows(state):
    with Session(state["runtime"]) as session:
        return list(session.scalars(select(EnrollmentMailDelivery)))


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("ACCEPTED", "accepted"),
        ("NOT_SENT", "retry"),
        ("REJECTED", "rejected"),
        ("UNKNOWN", "uncertain"),
    ],
)
def test_dispatch_settles_explicit_transport_outcomes_outside_transactions(
    state, dispatch, outcome, expected
):
    worker, transport = dispatch
    draft = issue(state)
    transport.action = lambda: getattr(mail_delivery.TransportOutcome, outcome)
    assert worker.run_one().value == expected
    assert len(transport.calls) == 1
    payload, message_id, expires_at, deadline = transport.calls[0]
    assert payload.recipient == draft.recipient and payload.token == draft.token
    assert expires_at == draft.expires_at and deadline == state["now"] + timedelta(
        seconds=30
    )
    (row,) = delivery_rows(state)
    assert (
        row.challenge_id == message_id and row.attempts == 1 and row.state == expected
    )
    assert (outbox_rows(state)[0].ciphertext is not None) is (expected == "retry")
    assert worker.run_one() == mail_delivery.DispatchResult.IDLE
    assert len(transport.calls) == 1
    # Even provider acceptance is not mailbox confirmation or session authority.
    assert snapshot(state)[0] is None and snapshot(state)[1][0][3] is None


@pytest.mark.parametrize("failure", ["timeout", "invalid_result"])
def test_dispatch_never_retries_ambiguous_failure_or_publishes_provider_error(
    state, dispatch, failure
):
    worker, transport = dispatch
    issue(state)

    def action():
        if failure == "timeout":
            raise TimeoutError("Synthetic sensitive provider details")
        return "accepted"

    transport.action = action
    assert worker.run_one() == mail_delivery.DispatchResult.UNCERTAIN
    state["now"] += timedelta(seconds=60)
    assert worker.run_one() == mail_delivery.DispatchResult.IDLE
    assert len(transport.calls) == 1 and outbox_rows(state)[0].ciphertext is None


@pytest.mark.parametrize(
    "change", ["inactive", "email", "password", "revision", "pending", "challenge"]
)
def test_dispatch_revalidates_source_before_sending(state, dispatch, change):
    worker, transport = dispatch
    issue(state)
    with Session(state["runtime"]) as session, session.begin():
        user = session.get(User, state["identity"])
        if change == "inactive":
            user.is_active = False
        elif change == "email":
            user.email = "changed-" + secrets.token_hex(4) + "@example.invalid"
        elif change == "password":
            user.password_hash = hash_password(SecretStr(secrets.token_urlsafe(32)))
        elif change == "revision":
            user.security_revision += 1
        elif change == "pending":
            session.execute(
                update(MfaPending)
                .where(MfaPending.user_id == user.id)
                .values(finished_at=state["now"])
            )
        else:
            session.execute(
                update(EnrollmentEmailChallenge)
                .where(EnrollmentEmailChallenge.user_id == user.id)
                .values(closed_at=state["now"])
            )
    assert worker.run_one() == mail_delivery.DispatchResult.CANCELLED
    assert not transport.calls and outbox_rows(state)[0].ciphertext is None


def test_dispatch_exact_expiry_discards_without_calling_transport(state, dispatch):
    worker, transport = dispatch
    draft = issue(state)
    state["now"] = draft.expires_at
    assert worker.run_one() == mail_delivery.DispatchResult.CANCELLED
    assert not transport.calls


def test_dispatch_known_not_sent_retries_after_delay_at_most_three_attempts(
    state, dispatch
):
    worker, transport = dispatch
    issue(state)
    before = quota_rows(state)
    transport.action = lambda: mail_delivery.TransportOutcome.NOT_SENT
    for attempt in range(1, 4):
        expected = "retry" if attempt < 3 else "rejected"
        assert worker.run_one().value == expected
        assert delivery_rows(state)[0].attempts == attempt
        assert worker.run_one() == mail_delivery.DispatchResult.IDLE
        state["now"] += timedelta(seconds=59)
        assert worker.run_one() == mail_delivery.DispatchResult.IDLE
        state["now"] += timedelta(seconds=1)
    assert worker.run_one() == mail_delivery.DispatchResult.IDLE
    assert len(transport.calls) == 3 and len({r[1] for r in transport.calls}) == 1
    assert quota_rows(state) == before


def test_dispatch_crashed_lease_becomes_uncertain_and_stale_ack_cannot_reopen(
    state, dispatch
):
    worker, transport = dispatch
    issue(state)
    claim = worker._claim()
    assert delivery_rows(state)[0].state == "leased"
    assert worker.run_one() == mail_delivery.DispatchResult.IDLE
    state["now"] += timedelta(seconds=30)
    assert worker.run_one() == mail_delivery.DispatchResult.UNCERTAIN
    assert (
        worker._finish(claim, mail_delivery.TransportOutcome.ACCEPTED)
        == mail_delivery.DispatchResult.STALE
    )
    assert not transport.calls and delivery_rows(state)[0].state == "uncertain"


def test_dispatch_revocation_between_claim_and_authorize_prevents_send(
    state, dispatch, monkeypatch
):
    worker, transport = dispatch
    issue(state)
    original = worker._authorize

    def revoke(claim):
        with Session(state["runtime"]) as session, session.begin():
            login_challenges.revoke_user_challenges(session, state["identity"])
        return original(claim)

    monkeypatch.setattr(worker, "_authorize", revoke)
    assert worker.run_one() == mail_delivery.DispatchResult.CANCELLED
    assert not transport.calls and delivery_rows(state)[0].state == "cancelled"


def test_dispatch_revocation_during_transport_keeps_token_invalid_even_if_accepted(
    state, dispatch
):
    worker, transport = dispatch
    draft = issue(state)

    def revoke():
        with Session(state["runtime"]) as session, session.begin():
            login_challenges.revoke_user_challenges(session, state["identity"])
        return mail_delivery.TransportOutcome.ACCEPTED

    transport.action = revoke
    assert worker.run_one() == mail_delivery.DispatchResult.ACCEPTED
    assert len(transport.calls) == 1 and confirm(state, draft, inspect=True) is False
    assert outbox_rows(state)[0].ciphertext is None


def test_dispatch_late_transport_answer_is_uncertain_without_retry(state, dispatch):
    worker, transport = dispatch
    issue(state)

    def late():
        state["now"] += timedelta(seconds=30)
        return mail_delivery.TransportOutcome.ACCEPTED

    transport.action = late
    assert worker.run_one() == mail_delivery.DispatchResult.UNCERTAIN
    assert worker.run_one() == mail_delivery.DispatchResult.IDLE
    assert len(transport.calls) == 1


@pytest.mark.parametrize("stage,calls", [(1, 0), (2, 0), (3, 1)])
def test_dispatch_commit_failure_never_sends_before_claim_or_authorization_commit(
    state, dispatch, stage, calls
):
    worker, transport = dispatch
    issue(state)
    count = 0

    def fail(session):
        nonlocal count
        count += 1
        if count == stage:
            raise SQLAlchemyError("Synthetic dispatcher commit failure")

    event.listen(worker._factory, "before_commit", fail)
    with pytest.raises(
        mail_delivery.MailDeliveryUnavailable, match="^Mail delivery unavailable$"
    ):
        worker.run_one()
    assert len(transport.calls) == calls
    event.remove(worker._factory, "before_commit", fail)
    if stage == 1:
        assert not delivery_rows(state)
    else:
        assert delivery_rows(state)[0].state == "leased"
        state["now"] += timedelta(seconds=30)
        assert worker.run_one() == mail_delivery.DispatchResult.UNCERTAIN
        assert len(transport.calls) == calls


def test_dispatch_ciphertext_tampering_fails_closed_without_transport_or_lease(
    state, dispatch
):
    worker, transport = dispatch
    issue(state)
    (row,) = outbox_rows(state)
    with state["runtime"].begin() as connection:
        connection.execute(
            update(EnrollmentMailOutbox).values(
                ciphertext=bytes([row.ciphertext[0] ^ 1]) + row.ciphertext[1:]
            )
        )
    with pytest.raises(mail_delivery.MailDeliveryUnavailable):
        worker.run_one()
    assert not transport.calls and not delivery_rows(state)


def test_dispatch_concurrent_consumers_do_not_send_twice(state, dispatch):
    worker, transport = dispatch
    issue(state)
    entered, release = Event(), Event()

    def hold():
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Synthetic transport barrier")
        return mail_delivery.TransportOutcome.ACCEPTED

    transport.action = hold
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker.run_one)
        try:
            assert entered.wait(5)
            second = pool.submit(worker.run_one)
            assert second.result(timeout=5) == mail_delivery.DispatchResult.IDLE
            assert delivery_rows(state)[0].state == "leased"
        finally:
            release.set()
        assert first.result(timeout=5) == mail_delivery.DispatchResult.ACCEPTED
    assert len(transport.calls) == 1 and delivery_rows(state)[0].attempts == 1


def test_dispatch_delete_permissions_constraints_and_downgrade_guard(state, dispatch):
    worker, _ = dispatch
    issue(state)
    assert worker.run_one() == mail_delivery.DispatchResult.ACCEPTED
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(delete(EnrollmentMailDelivery))
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(EnrollmentMailDelivery).values(attempts=4))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0017_enrollment_mail_outbox")
    assert delivery_rows(state)[0].state == "accepted"


@pytest.fixture
def enrollment(state, monkeypatch):
    monkeypatch.setattr(mfa_enrollment, "_database_now", lambda session: state["now"])
    monkeypatch.setattr(mfa_verification, "_now", lambda session: state["now"])
    monkeypatch.setattr(mfa_consumption, "_database_now", lambda session: state["now"])
    # Synthetic fixture seed already avoids TOTP collisions across tested windows.
    monkeypatch.setattr(mfa_enrollment, "generate_totp_seed", lambda: state["seed"])
    worker = mfa_enrollment.MfaEnrollmentService(
        state["runtime"], keyring=state["keyring"]
    )
    permit = confirm(state, issue(state))
    return worker, permit


def setup_factor(state, enrollment):
    worker, permit = enrollment
    return worker.begin(permit_token=permit.token, browser_binding=state["binding"])


def otp_for(state, setup):
    return SecretStr(
        pyotp.TOTP(setup.seed.base32().get_secret_value()).at(state["now"])
    )


def activate_factor(state, enrollment, setup, **changes):
    return enrollment[0].activate(
        **(
            dict(
                setup_token=setup.token,
                browser_binding=state["binding"],
                code=otp_for(state, setup),
                client_ip=ip_address("127.0.0.1"),
            )
            | changes
        )
    )


def enrollment_rows(state):
    with Session(state["runtime"]) as session:
        return list(session.scalars(select(MfaEnrollment)))


def test_enrollment_setup_requires_mail_proof_and_does_not_activate_or_reveal_again(
    state, enrollment
):
    setup = setup_factor(state, enrollment)
    assert setup.expires_at == state["pending"].expires_at
    assert setup_factor(state, enrollment) is None
    (stage,) = enrollment_rows(state)
    assert setup.seed.base32().get_secret_value() not in repr(setup)
    assert setup.seed.value.get_secret_value() not in stage.ciphertext
    assert (
        stage.token_hash != setup.token.get_secret_value() and stage.closed_at is None
    )
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(MfaCredential).where(MfaCredential.user_id == state["identity"])
            )
            is None
        )


def test_enrollment_activates_first_totp_offline_hashes_and_revokes_global_access(
    state, enrollment
):
    setup = setup_factor(state, enrollment)
    other_binding = SecretStr(secrets.token_urlsafe(32))
    other_pending = start(state, binding=other_binding)
    with Session(state["runtime"]) as session, session.begin():
        old_session = SessionRecord(
            tenant_id=state["tenant"],
            membership_id=state["member"],
            token_hash=secrets.token_hex(32),
            security_revision=1,
            created_at=state["now"],
            last_seen_at=state["now"],
            expires_at=state["now"] + timedelta(hours=8),
        )
        session.add(old_session)
        session.flush()
        old_session_id = old_session.id
    bundle = activate_factor(state, enrollment, setup)
    assert len(bundle.codes) == 10 and bundle.security_revision == 2
    raw = [c.value.get_secret_value() for c in bundle.codes]
    assert len(set(raw)) == 10 and all(value not in repr(bundle) for value in raw)
    (stage,) = enrollment_rows(state)
    assert stage.activated_at == stage.closed_at == state["now"]
    assert stage.ciphertext is stage.nonce is stage.key_id is None
    assert activate_factor(state, enrollment, setup) is None
    with Session(state["runtime"]) as session:
        factor = session.get(MfaCredential, bundle.credential_id)
        assert factor.last_counter == int(state["now"].timestamp()) // 30
        assert factor.last_verified_at == state["now"]
        recovery_set = session.scalar(
            select(MfaRecoverySet).where(MfaRecoverySet.credential_id == factor.id)
        )
        hashes = list(
            session.scalars(
                select(MfaRecoveryCode).where(MfaRecoveryCode.set_id == recovery_set.id)
            )
        )
        assert len(hashes) == 10 and all(
            row.used_at is None and len(row.code_hash) == 32 for row in hashes
        )
        assert all(
            match_recovery_code(
                bundle.codes[row.position],
                row.code_hash,
                user_id=state["identity"],
                set_id=recovery_set.id,
            )
            for row in hashes
        )
        assert session.get(SessionRecord, old_session_id).revoked_at == state["now"]
        assert all(
            row.finished_at is not None
            for row in session.scalars(
                select(MfaPending).where(MfaPending.user_id == state["identity"])
            )
        )
        events = list(
            session.scalars(
                select(SecurityEvent).where(SecurityEvent.action == "mfa_enrolled")
            )
        )
        assert len(events) == 1 and events[0].security_revision == 2
        assert events[0].credential_id == factor.id
        assert (
            service._consume_permit(
                session, token=enrollment[1].token, browser_binding=state["binding"]
            )
            is None
        )
    # First OTP cannot also be used to log in; next normal login requires TOTP.
    with Session(state["runtime"]) as session, session.begin():
        assert (
            mfa_consumption.consume_totp(
                session,
                user_id=state["identity"],
                credential_id=bundle.credential_id,
                code=otp_for(state, setup),
                keyring=state["keyring"],
            )
            is None
        )
    next_pending = start(state)
    assert next_pending.purpose == mfa_pending.PendingPurpose.LOGIN_TOTP
    assert other_pending.token != next_pending.token


def test_enrollment_bad_otp_commits_shared_failures_without_activating(
    state, enrollment
):
    setup = setup_factor(state, enrollment)
    otp = pyotp.TOTP(setup.seed.base32().get_secret_value())
    invalid = next(
        f"{i:06d}"
        for i in range(10)
        if f"{i:06d}"
        not in {
            otp.at(state["now"] + timedelta(seconds=offset)) for offset in (-30, 0, 30)
        }
    )
    for _ in range(5):
        assert (
            activate_factor(state, enrollment, setup, code=SecretStr(invalid)) is None
        )
    assert activate_factor(state, enrollment, setup) is None
    assert enrollment_rows(state)[0].activated_at is None
    with Session(state["runtime"]) as session:
        assert {r.failures for r in session.scalars(select(MfaThrottle))} == {5}
        assert (
            session.scalar(
                select(MfaCredential).where(MfaCredential.user_id == state["identity"])
            )
            is None
        )


@pytest.mark.parametrize(
    "what", ["binding", "token", "email", "password", "revision", "inactive", "expired"]
)
def test_enrollment_revalidates_current_identity_proof_browser_and_deadline(
    state, enrollment, what
):
    setup = setup_factor(state, enrollment)
    changes = {}
    if what == "binding":
        changes["browser_binding"] = SecretStr(secrets.token_urlsafe(32))
    elif what == "token":
        changes["setup_token"] = enrollment[1].token
    elif what == "expired":
        state["now"] = setup.expires_at
    else:
        with Session(state["runtime"]) as session, session.begin():
            user = session.get(User, state["identity"])
            if what == "email":
                user.email = secrets.token_hex(6) + "@example.invalid"
            elif what == "password":
                user.password_hash = hash_password(SecretStr(secrets.token_urlsafe(32)))
            elif what == "revision":
                user.security_revision += 1
            else:
                user.is_active = False
    assert activate_factor(state, enrollment, setup, **changes) is None
    assert enrollment_rows(state)[0].activated_at is None


@pytest.mark.parametrize("reason", ["browser", "global", "expired"])
def test_enrollment_revocation_and_expiry_purge_remove_staged_seed(
    state, enrollment, reason
):
    setup = setup_factor(state, enrollment)
    if reason == "browser":
        start(state)
    elif reason == "global":
        with Session(state["runtime"]) as session, session.begin():
            login_challenges.revoke_user_challenges(session, state["identity"])
    else:
        state["now"] = setup.expires_at
        assert mfa_enrollment.purge_expired_enrollments(state["runtime"], limit=1) == 1
        assert mfa_enrollment.purge_expired_enrollments(state["runtime"]) == 0
    (stage,) = enrollment_rows(state)
    assert stage.closed_at is not None and stage.ciphertext is None
    assert activate_factor(state, enrollment, setup) is None


@pytest.mark.parametrize("stage", ["begin", "activate"])
def test_enrollment_commit_failure_never_publishes_seed_codes_or_partial_factor(
    state, enrollment, stage
):
    worker, _ = enrollment
    setup = setup_factor(state, enrollment) if stage == "activate" else None

    def fail(session):
        raise SQLAlchemyError("Synthetic enrollment commit failure")

    event.listen(worker._factory, "before_commit", fail, once=True)
    with pytest.raises(mfa_enrollment.MfaEnrollmentUnavailable):
        activate_factor(state, enrollment, setup) if setup else setup_factor(
            state, enrollment
        )
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(MfaCredential).where(MfaCredential.user_id == state["identity"])
            )
            is None
        )
        user = session.get(User, state["identity"])
        assert user.security_revision == 1
    if stage == "begin":
        assert not enrollment_rows(state)
        assert setup_factor(state, enrollment) is not None
    else:
        assert enrollment_rows(state)[0].closed_at is None
        assert activate_factor(state, enrollment, setup) is not None


@pytest.mark.parametrize(
    "table", ["security_events", "mfa_recovery_codes", "mfa_credentials"]
)
def test_enrollment_real_write_failure_rolls_back_activation_and_revocation(
    state, enrollment, table
):
    setup = setup_factor(state, enrollment)
    with state["owner"].begin() as connection:
        connection.execute(text(f"REVOKE INSERT ON {table} FROM carrillo_app"))
    try:
        with pytest.raises(mfa_enrollment.MfaEnrollmentUnavailable):
            activate_factor(state, enrollment, setup)
        assert enrollment_rows(state)[0].closed_at is None
        with Session(state["runtime"]) as session:
            assert session.get(User, state["identity"]).security_revision == 1
            assert (
                session.scalar(
                    select(MfaCredential).where(
                        MfaCredential.user_id == state["identity"]
                    )
                )
                is None
            )
    finally:
        with state["owner"].begin() as connection:
            connection.execute(text(f"GRANT INSERT ON {table} TO carrillo_app"))
    assert activate_factor(state, enrollment, setup) is not None


def test_enrollment_concurrent_activation_returns_codes_once(
    state, enrollment, monkeypatch
):
    worker, _ = enrollment
    setup = setup_factor(state, enrollment)
    entered, release, started = Event(), Event(), Event()
    pids = []
    original = mfa_verification._lock_bucket

    def observe(session, scope, key_hash):
        if entered.is_set() and not release.is_set():
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
        return original(session, scope, key_hash)

    def pause(session):
        if not entered.is_set():
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Synthetic activation contention")

    event.listen(worker._factory, "before_commit", pause)
    monkeypatch.setattr(mfa_verification, "_lock_bucket", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(activate_factor, state, enrollment, setup)
        try:
            assert entered.wait(5)
            second = pool.submit(activate_factor, state, enrollment, setup)
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
                pytest.fail("Second activation did not block on the shared MFA gate")
        finally:
            release.set()
        assert first.result(timeout=5) is not None and second.result(timeout=5) is None
    with Session(state["runtime"]) as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(SecurityEvent).where(
                            SecurityEvent.action == "mfa_enrolled"
                        )
                    )
                )
            )
            == 1
        )


def test_enrollment_delete_checks_and_downgrade_preserve_staged_evidence(
    state, enrollment
):
    setup_factor(state, enrollment)
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(delete(MfaEnrollment))
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(MfaEnrollment).values(ciphertext=None))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0018_mail_delivery")


@pytest.mark.parametrize("failure", ["missing_key", "tamper"])
def test_enrollment_seed_protection_failure_never_activates_factor(
    state, enrollment, failure
):
    setup = setup_factor(state, enrollment)
    if failure == "missing_key":
        from app.modules.access.mfa_crypto import SeedKeyring

        enrollment[0]._keyring = SeedKeyring(
            {"other": SecretBytes(secrets.token_bytes(32))}, active_key_id="other"
        )
    else:
        (row,) = enrollment_rows(state)
        with state["runtime"].begin() as connection:
            connection.execute(
                update(MfaEnrollment).values(
                    ciphertext=bytes([row.ciphertext[0] ^ 1]) + row.ciphertext[1:]
                )
            )
    with pytest.raises(
        mfa_enrollment.MfaEnrollmentUnavailable, match="^MFA enrollment unavailable$"
    ):
        activate_factor(state, enrollment, setup)
    assert enrollment_rows(state)[0].closed_at is None
    with Session(state["runtime"]) as session:
        assert session.get(User, state["identity"]).security_revision == 1
        assert (
            session.scalar(
                select(MfaCredential).where(MfaCredential.user_id == state["identity"])
            )
            is None
        )
        assert not list(session.scalars(select(MfaThrottle)))


def test_enrollment_expiry_after_activation_work_rolls_back_every_effect(
    state, enrollment, monkeypatch
):
    setup = setup_factor(state, enrollment)
    count = 0

    def clock(session):
        nonlocal count
        count += 1
        return setup.expires_at if count == 3 else state["now"]

    monkeypatch.setattr(mfa_enrollment, "_database_now", clock)
    assert activate_factor(state, enrollment, setup) is None
    assert count == 3 and enrollment_rows(state)[0].closed_at is None
    with Session(state["runtime"]) as session:
        assert session.get(User, state["identity"]).security_revision == 1
        assert (
            session.scalar(
                select(MfaCredential).where(MfaCredential.user_id == state["identity"])
            )
            is None
        )
        assert not list(
            session.scalars(
                select(SecurityEvent).where(SecurityEvent.action == "mfa_enrolled")
            )
        )


def eligible(state, user_id=None):
    with Session(state["runtime"]) as session, session.begin():
        record = mailbox_verification.lock_eligible_mailbox(
            session, user_id=user_id or state["identity"]
        )
        return record.id if record else None


def test_mailbox_requires_explicit_confirmation_not_issuance_or_inspection(state):
    assert eligible(state) is None
    draft = issue(state)
    assert eligible(state) is None
    assert confirm(state, draft, inspect=True) is True
    assert eligible(state) is None
    permit = confirm(state, draft)
    assert permit is not None
    identity = eligible(state)
    assert identity is not None
    assert confirm(state, draft) is None
    assert eligible(state) == identity
    assert eligible(state, state["user"]) is None
    with Session(state["runtime"]) as session:
        row = session.get(VerifiedMailbox, identity)
        challenge = session.get(EnrollmentEmailChallenge, row.challenge_id)
        assert row.verified_at == challenge.consumed_at == state["now"]
        assert row.user_id == challenge.user_id == state["identity"]
        assert row.address_hash == mailbox_verification._address_hash(
            session.get(User, row.user_id)
        )
        assert row.address_hash != challenge.email_fingerprint
        assert row.invalidated_at is None


@pytest.mark.parametrize(
    "change",
    ["password", "revision", "closed-source", "expired-source", "inactive-member"],
)
def test_verified_mailbox_survives_authentication_lifecycle_without_granting_access(
    state, change
):
    assert confirm(state, issue(state)) is not None
    identity = eligible(state)
    with state["owner"].begin() as connection:
        if change == "password":
            connection.execute(
                update(User)
                .where(User.id == state["identity"])
                .values(
                    password_hash=hash_password(SecretStr(secrets.token_urlsafe(32)))
                )
            )
        elif change == "revision":
            connection.execute(
                update(User)
                .where(User.id == state["identity"])
                .values(security_revision=2)
            )
        elif change == "closed-source":
            connection.execute(
                update(EnrollmentEmailChallenge)
                .where(EnrollmentEmailChallenge.user_id == state["identity"])
                .values(closed_at=state["now"])
            )
        elif change == "inactive-member":
            connection.execute(
                update(TenantMembership)
                .where(TenantMembership.id == state["member"])
                .values(is_active=False)
            )
        else:
            state["now"] += timedelta(hours=1)
    assert eligible(state) == identity
    with Session(state["runtime"]) as session:
        assert session.scalar(select(SessionRecord.id)) is None
        assert (
            session.scalar(
                select(MfaCredential.id).where(
                    MfaCredential.user_id == state["identity"]
                )
            )
            is None
        )


@pytest.mark.parametrize(
    "change", ["inactive", "address-hash", "origin-hash", "clock", "invalidated"]
)
def test_mailbox_eligibility_rejects_inactive_or_inconsistent_evidence(state, change):
    assert confirm(state, issue(state)) is not None
    with state["owner"].begin() as connection:
        if change == "inactive":
            connection.execute(
                update(User).where(User.id == state["identity"]).values(is_active=False)
            )
        elif change == "address-hash":
            connection.execute(update(VerifiedMailbox).values(address_hash="a" * 64))
        elif change == "origin-hash":
            connection.execute(
                update(EnrollmentEmailChallenge)
                .where(EnrollmentEmailChallenge.user_id == state["identity"])
                .values(email_fingerprint="a" * 64)
            )
        elif change == "clock":
            state["now"] -= timedelta(microseconds=1)
        else:
            connection.execute(
                update(VerifiedMailbox).values(invalidated_at=state["now"])
            )
    assert eligible(state) is None


def test_email_change_invalidates_atomically_and_reverting_does_not_revive_proof(state):
    assert confirm(state, issue(state)) is not None
    identity = eligible(state)
    with state["runtime"].begin() as connection:
        connection.execute(
            update(User)
            .where(User.id == state["identity"])
            .values(email="changed@example.invalid")
        )
    assert eligible(state) is None
    with state["runtime"].begin() as connection:
        connection.execute(
            update(User)
            .where(User.id == state["identity"])
            .values(email=state["email"])
        )
    assert eligible(state) is None
    with Session(state["runtime"]) as session:
        assert session.get(VerifiedMailbox, identity).invalidated_at is not None


def test_email_change_rollback_and_unchanged_address_preserve_verification(state):
    assert confirm(state, issue(state)) is not None
    identity = eligible(state)
    with pytest.raises(RuntimeError), state["runtime"].begin() as connection:
        connection.execute(
            update(User)
            .where(User.id == state["identity"])
            .values(email="rollback@example.invalid")
        )
        raise RuntimeError("Synthetic rollback")
    assert eligible(state) == identity
    with state["runtime"].begin() as connection:
        connection.execute(
            update(User)
            .where(User.id == state["identity"])
            .values(email=state["email"])
        )
    assert eligible(state) == identity


@pytest.mark.parametrize("failure", ["commit", "insert", "expiry"])
def test_mailbox_record_and_confirmation_are_atomic(state, monkeypatch, failure):
    draft = issue(state)
    if failure == "commit":

        def fail(session):
            raise SQLAlchemyError("Synthetic commit failure")

        event.listen(state["mail"]._factory, "before_commit", fail, once=True)
    elif failure == "insert":
        with state["owner"].begin() as connection:
            connection.execute(
                text("REVOKE INSERT ON verified_mailboxes FROM carrillo_app")
            )
    else:
        original = service.record_confirmed_mailbox

        def expire(*args, **kwargs):
            original(*args, **kwargs)
            state["now"] = state["pending"].expires_at

        monkeypatch.setattr(service, "record_confirmed_mailbox", expire)
    try:
        if failure == "expiry":
            assert confirm(state, draft) is None
        else:
            with pytest.raises(service.EnrollmentEmailUnavailable):
                confirm(state, draft)
        assert eligible(state) is None
        with Session(state["runtime"]) as session:
            row = session.scalar(
                select(EnrollmentEmailChallenge).where(
                    EnrollmentEmailChallenge.user_id == state["identity"]
                )
            )
            assert row.consumed_at is row.permit_token_hash is None
            assert session.scalar(select(VerifiedMailbox.id)) is None
            assert (
                session.scalar(
                    select(SecurityEvent.id).where(
                        SecurityEvent.action == "mfa_enrollment_email_confirmed"
                    )
                )
                is None
            )
    finally:
        if failure == "insert":
            with state["owner"].begin() as connection:
                connection.execute(
                    text("GRANT INSERT ON verified_mailboxes TO carrillo_app")
                )


def test_verification_database_constraints_owner_retention_and_downgrade(state):
    assert confirm(state, issue(state)) is not None
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(VerifiedMailbox).values(user_id=state["user"]))
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(VerifiedMailbox).values(address_hash="invalid"))
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(delete(VerifiedMailbox))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(
            DBAPIError, match="verified mailbox evidence|identity cutover evidence"
        ),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0022_mfa_code_regeneration")


def test_new_confirmation_supersedes_evidence_without_deleting_history(state):
    assert confirm(state, issue(state)) is not None
    previous = eligible(state)
    state["now"] += timedelta(seconds=61)
    state["pending"] = start(state)
    assert confirm(state, issue(state)) is not None
    assert eligible(state) not in (None, previous)
    with Session(state["runtime"]) as session:
        assert session.get(VerifiedMailbox, previous).invalidated_at == state["now"]
        assert len(list(session.scalars(select(VerifiedMailbox)))) == 2


def test_confirmation_waiting_for_changed_email_cannot_verify_old_address(
    state, monkeypatch
):
    draft = issue(state)
    reached = Event()
    original = mfa_pending._lock_user

    def observe(session, identity):
        reached.set()
        return original(session, identity)

    monkeypatch.setattr(mfa_pending, "_lock_user", observe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with state["runtime"].begin() as connection:
            connection.execute(
                update(User)
                .where(User.id == state["identity"])
                .values(email="race@example.invalid")
            )
            waiting = pool.submit(confirm, state, draft)
            assert reached.wait(timeout=3)
        assert waiting.result(timeout=5) is None
    assert eligible(state) is None


@pytest.fixture
def reset(state, monkeypatch):
    monkeypatch.setattr(password_reset, "_database_now", lambda session: state["now"])
    protection = email_outbox.MailProtection(
        {"reset-test": SecretBytes(secrets.token_bytes(32))},
        active_key_id="reset-test",
        quota_key=SecretBytes(secrets.token_bytes(32)),
        purpose=email_outbox.MailPurpose.PASSWORD_RESET,
    )
    return password_reset.PasswordResetIssuer(
        state["runtime"], protection=protection
    ), protection


def request_reset(state, reset, *, email=None, address="127.0.0.1"):
    return reset[0].issue(
        password_reset.PasswordResetRequest(email=email or state["email"]),
        client_ip=ip_address(address),
    )


def reset_rows(state):
    with Session(state["runtime"]) as session:
        return list(
            session.scalars(
                select(PasswordResetChallenge).order_by(
                    PasswordResetChallenge.created_at
                )
            )
        )


def reset_quotas(state):
    with Session(state["runtime"]) as session:
        return list(
            session.scalars(
                select(PasswordResetThrottle).order_by(PasswordResetThrottle.scope)
            )
        )


def test_reset_issue_stages_only_encrypted_purpose_bound_payload_without_authority(
    state, reset
):
    assert confirm(state, issue(state)) is not None
    before = snapshot(state)
    with Session(state["runtime"]) as session:
        original = session.get(User, state["identity"])
        snapshot_user = (
            original.password_hash,
            original.security_revision,
            original.mfa_required,
        )
    assert request_reset(state, reset) is None
    (row,) = reset_rows(state)
    payload = reset[1].decrypt(
        email_outbox.MailEnvelope(
            key_id=row.key_id, nonce=row.nonce, ciphertext=row.ciphertext
        ),
        user_id=row.user_id,
        challenge_id=row.id,
        expires_at=row.expires_at,
    )
    assert payload.recipient.get_secret_value() == state["email"]
    assert row.token_hash == mfa_pending._hash(
        payload.token, "password-reset-token-v1:"
    )
    assert row.created_at == state["now"] and row.expires_at == state[
        "now"
    ] + timedelta(minutes=15)
    assert row.consumed_at is row.closed_at is row.discarded_at is None
    assert payload.token.get_secret_value().encode() not in row.ciphertext
    assert state["email"].encode() not in row.ciphertext
    assert payload.model_dump() == {}
    assert snapshot(state) == before
    with Session(state["runtime"]) as session:
        user = session.get(User, state["identity"])
        assert (
            user.password_hash,
            user.security_revision,
            user.mfa_required,
        ) == snapshot_user
        assert session.scalar(select(SessionRecord.id)) is None
    # The token has no enrollment purpose or session authority.
    assert service._hash(payload.token, "enrollment-mail-permit-v1:") != row.token_hash
    with Session(state["runtime"]) as session, session.begin():
        assert (
            service._consume_permit(
                session, token=payload.token, browser_binding=state["binding"]
            )
            is None
        )


@pytest.mark.parametrize("kind", ["unknown", "unverified", "inactive", "invalidated"])
def test_reset_issue_returns_same_result_and_counts_requests_without_eligibility(
    state, reset, kind
):
    email = state["email"]
    if kind == "unknown":
        email = "missing@example.com"
    elif kind != "unverified":
        assert confirm(state, issue(state)) is not None
        with state["runtime"].begin() as connection:
            if kind == "inactive":
                connection.execute(
                    update(User)
                    .where(User.id == state["identity"])
                    .values(is_active=False)
                )
            else:
                connection.execute(
                    update(User)
                    .where(User.id == state["identity"])
                    .values(email="new@example.com")
                )
                email = "new@example.com"
    assert request_reset(state, reset, email=email) is None
    assert not reset_rows(state)
    assert [r.requested for r in reset_quotas(state)] == [1, 1]
    assert all(email not in r.key_hash for r in reset_quotas(state))


def test_reset_address_quota_cooldown_and_fixed_window_do_not_revoke_existing_links(
    state, reset
):
    assert confirm(state, issue(state)) is not None
    request_reset(state, reset)
    first = reset_rows(state)[0]
    request_reset(
        state,
        reset,
        email="  " + state["email"].upper() + "  ",
        address="::ffff:127.0.0.1",
    )
    assert len(reset_rows(state)) == 1
    assert [r.requested for r in reset_quotas(state)] == [1, 2]
    for _ in range(2):
        state["now"] += timedelta(seconds=60)
        request_reset(state, reset, address="127.0.0.2")
    state["now"] += timedelta(seconds=60)
    request_reset(state, reset, address="127.0.0.3")
    assert len(reset_rows(state)) == 3
    assert all(row.closed_at is None for row in reset_rows(state))
    state["now"] = first.created_at + timedelta(minutes=15)
    request_reset(state, reset)
    assert len(reset_rows(state)) == 4
    # A later request never extends an earlier link's absolute deadline.
    assert reset_rows(state)[0].expires_at == first.expires_at


def test_reset_ip_quota_counts_unknown_addresses_and_address_cooldown_denials(
    state, reset
):
    for _ in range(20):
        assert request_reset(state, reset, email="missing@example.com") is None
    assert sorted(r.requested for r in reset_quotas(state)) == [1, 20]
    request_reset(state, reset, email="another@example.com")
    assert len(reset_quotas(state)) == 2
    assert not reset_rows(state)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(select(LoginThrottle.scope)) is not None
        )  # Existing login only.
        assert session.scalar(select(MfaThrottle.scope)) is None


def test_reset_protection_is_separate_from_enrollment_and_binds_all_aad_fields(
    state, reset
):
    keys = {"shared-test": SecretBytes(secrets.token_bytes(32))}
    quota_key = SecretBytes(secrets.token_bytes(32))
    enrollment_protection = email_outbox.MailProtection(
        keys, active_key_id="shared-test", quota_key=quota_key
    )
    reset_protection = email_outbox.MailProtection(
        keys,
        active_key_id="shared-test",
        quota_key=quota_key,
        purpose=email_outbox.MailPurpose.PASSWORD_RESET,
    )
    values = dict(
        recipient=SecretStr(state["email"]),
        token=SecretStr(secrets.token_urlsafe(32)),
        user_id=state["identity"],
        challenge_id=uuid4(),
        expires_at=state["now"] + timedelta(minutes=15),
    )
    envelope = reset_protection.encrypt(**values)
    aad = {key: values[key] for key in ("user_id", "challenge_id", "expires_at")}
    assert reset_protection.decrypt(envelope, **aad).token == values["token"]
    for changes in (
        {},
        {"user_id": state["user"]},
        {"challenge_id": uuid4()},
        {"expires_at": values["expires_at"] + timedelta(microseconds=1)},
    ):
        reader = enrollment_protection if not changes else reset_protection
        with pytest.raises(
            email_outbox.MailProtectionError, match="^Mail protection unavailable$"
        ):
            reader.decrypt(envelope, **(aad | changes))
    with pytest.raises(password_reset.PasswordResetUnavailable):
        password_reset.PasswordResetIssuer(
            state["runtime"], protection=enrollment_protection
        )


@pytest.mark.parametrize("failure", ["commit", "insert", "crypto", "expiry", "clock"])
def test_reset_issue_rolls_back_challenge_payload_and_quota_on_failure(
    state, reset, monkeypatch, failure
):
    assert confirm(state, issue(state)) is not None
    before = snapshot(state)
    if failure == "commit":

        def fail(session):
            raise SQLAlchemyError("Synthetic commit failure")

        event.listen(reset[0]._factory, "before_commit", fail, once=True)
    elif failure == "insert":
        with state["owner"].begin() as connection:
            connection.execute(
                text("REVOKE INSERT ON password_reset_challenges FROM carrillo_app")
            )
    elif failure == "crypto":

        def fail(**kwargs):
            raise email_outbox.MailProtectionError("Synthetic protection failure")

        monkeypatch.setattr(reset[1], "encrypt", fail)
    else:
        real = reset[1].encrypt

        def shift(**kwargs):
            result = real(**kwargs)
            state["now"] += (
                timedelta(minutes=15) if failure == "expiry" else -timedelta(seconds=1)
            )
            return result

        monkeypatch.setattr(reset[1], "encrypt", shift)
    try:
        with pytest.raises(
            password_reset.PasswordResetUnavailable,
            match="^Password reset unavailable$",
        ):
            request_reset(state, reset)
        assert not reset_rows(state) and not reset_quotas(state)
        assert snapshot(state) == before
    finally:
        if failure == "insert":
            with state["owner"].begin() as connection:
                connection.execute(
                    text("GRANT INSERT ON password_reset_challenges TO carrillo_app")
                )


def test_reset_issuance_concurrency_has_one_payload_and_shared_cooldown(state, reset):
    assert confirm(state, issue(state)) is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: request_reset(state, reset), range(2))) == [
            None,
            None,
        ]
    assert len(reset_rows(state)) == 1
    assert [row.requested for row in reset_quotas(state)] == [1, 2]


def test_reset_purge_has_exact_expiry_clears_ciphertext_and_retains_quotas(
    state, reset
):
    assert confirm(state, issue(state)) is not None
    request_reset(state, reset)
    assert password_reset.purge_expired_resets(state["runtime"]) == 0
    state["now"] += timedelta(minutes=15)
    assert password_reset.purge_expired_resets(state["runtime"], limit=1) == 1
    (row,) = reset_rows(state)
    assert row.key_id is row.nonce is row.ciphertext is None
    assert row.closed_at == row.discarded_at == state["now"]
    assert row.consumed_at is None and row.token_hash
    assert password_reset.purge_expired_resets(state["runtime"]) == 0
    assert [r.requested for r in reset_quotas(state)] == [1, 1]


def test_reset_database_constraints_and_downgrade_preserve_payload_and_quota(
    state, reset
):
    assert confirm(state, issue(state)) is not None
    request_reset(state, reset)
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(PasswordResetChallenge).values(user_id=state["user"]))
    with pytest.raises(IntegrityError), state["runtime"].begin() as connection:
        connection.execute(update(PasswordResetChallenge).values(ciphertext=None))
    with pytest.raises(DBAPIError), state["runtime"].begin() as connection:
        connection.execute(delete(PasswordResetChallenge))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(
            DBAPIError,
            match="password reset evidence and quotas|identity cutover evidence",
        ),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0023_verified_mailboxes")


def test_reset_requests_preserve_active_mfa_codes_and_real_session(
    state, reset, enrollment
):
    bundle = activate_factor(state, enrollment, setup_factor(state, enrollment))
    assert bundle is not None
    state["now"] += timedelta(seconds=30)
    source = start(state)
    proof = mfa_verification.MfaVerifier(
        state["runtime"], keyring=state["keyring"]
    ).verify_login_totp(
        token=source.token,
        browser_binding=state["binding"],
        code=SecretStr(
            pyotp.TOTP(state["seed"].base32().get_secret_value()).at(state["now"])
        ),
        client_ip=ip_address("127.0.0.1"),
    )
    issued = MfaSessionIssuer(state["runtime"]).select_membership(
        proof_token=proof.token,
        browser_binding=state["binding"],
        membership_id=state["member"],
    )
    assert issued is not None
    with Session(state["runtime"]) as session:
        user = session.get(User, state["identity"])
        prior = (user.password_hash, user.security_revision, user.mfa_required)
        counter = session.get(MfaCredential, bundle.credential_id).last_counter
    assert request_reset(state, reset) is None
    with Session(state["runtime"]) as session, session.begin():
        assert (
            sessions.resolve_session(session, issued.token).user_id == state["identity"]
        )
        user = session.get(User, state["identity"])
        assert (user.password_hash, user.security_revision, user.mfa_required) == prior
        factor = session.get(MfaCredential, bundle.credential_id)
        assert factor.last_counter == counter and factor.revoked_at is None
        current = session.scalar(
            select(MfaRecoverySet).where(MfaRecoverySet.credential_id == factor.id)
        )
        assert current.invalidated_at is None
        assert all(
            row.used_at is None
            for row in session.scalars(
                select(MfaRecoveryCode).where(MfaRecoveryCode.set_id == current.id)
            )
        )


def test_reset_issue_waiting_on_account_does_not_queue_after_email_changes(
    state, reset, monkeypatch
):
    assert confirm(state, issue(state)) is not None
    reached = Event()
    original = password_reset.lock_eligible_mailbox

    def observe(session, **kwargs):
        reached.set()
        return original(session, **kwargs)

    monkeypatch.setattr(password_reset, "lock_eligible_mailbox", observe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with state["runtime"].begin() as connection:
            connection.execute(
                update(User)
                .where(User.id == state["identity"])
                .values(email="race-reset@example.com")
            )
            waiting = pool.submit(request_reset, state, reset)
            assert reached.wait(timeout=3)
        assert waiting.result(timeout=5) is None
    assert not reset_rows(state)
    assert [row.requested for row in reset_quotas(state)] == [1, 1]
