"""Session persistence, deadlines, tenant integrity and transactional revocation."""

import hashlib
import secrets
from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.modules.access import sessions as service
from app.modules.access.membership import AccessDenied
from app.modules.access.models import (
    MembershipRole,
    SessionRecord,
    Tenant,
    TenantMembership,
    User,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def identities(db_session):
    first, second = Tenant(name="A"), Tenant(name="B")
    user = User(email="session@example.invalid", password_hash="synthetic")
    other = User(email="other@example.invalid", password_hash="synthetic")
    db_session.add_all([first, second, user, other])
    db_session.flush()
    memberships = [
        TenantMembership(tenant_id=first.id, user_id=user.id, role="admin"),
        TenantMembership(tenant_id=second.id, user_id=user.id, role="mechanic"),
        TenantMembership(tenant_id=second.id, user_id=other.id, role="read_only"),
    ]
    db_session.add_all(memberships)
    db_session.flush()
    return first, second, user, other, memberships


def issue(db_session, identities):
    first, _, user, _, _ = identities
    return service.issue_session(db_session, user_id=user.id, tenant_id=first.id)


def record(db_session, issued):
    digest = hashlib.sha256(issued.token.get_secret_value().encode("ascii")).hexdigest()
    return db_session.scalar(
        select(SessionRecord).where(SessionRecord.token_hash == digest)
    )


def test_tokens_are_random_hidden_and_only_hashes_are_persisted(db_session, identities):
    first, _, user, _, memberships = identities
    issued, second = issue(db_session, identities), issue(db_session, identities)
    assert issued.token != second.token
    assert len(issued.token.get_secret_value()) == 43
    assert issued.token.get_secret_value() not in repr(issued)
    row = record(db_session, issued)
    assert row.token_hash != issued.token.get_secret_value()
    assert row.membership_id == memberships[0].id
    assert row.created_at == row.last_seen_at
    assert row.expires_at == issued.expires_at == row.created_at + timedelta(hours=8)
    context = service.resolve_session(db_session, issued.token)
    assert (context.tenant_id, context.user_id, context.role) == (
        first.id,
        user.id,
        MembershipRole.ADMIN,
    )
    assert service.resolve_session(db_session, second.token) == context


def test_context_comes_from_bound_membership_in_each_tenant(db_session, identities):
    first, second, user, other, _ = identities
    admin = issue(db_session, identities)
    mechanic = service.issue_session(db_session, user_id=user.id, tenant_id=second.id)
    assert service.resolve_session(db_session, admin.token).tenant_id == first.id
    context = service.resolve_session(db_session, mechanic.token)
    assert (context.tenant_id, context.role) == (second.id, MembershipRole.MECHANIC)
    with pytest.raises(AccessDenied):
        service.issue_session(db_session, user_id=other.id, tenant_id=first.id)


@pytest.mark.parametrize("target", ["user", "tenant", "membership"])
def test_inactive_identity_prevents_issue_and_existing_token_use(
    db_session, identities, target
):
    first, _, user, _, memberships = identities
    issued = issue(db_session, identities)
    row = record(db_session, issued)
    last_seen = row.last_seen_at
    obj = {"user": user, "tenant": first, "membership": memberships[0]}[target]
    db_session.execute(
        update(type(obj)).where(type(obj).id == obj.id).values(is_active=False)
    )
    with pytest.raises(AccessDenied, match="^Access denied$"):
        service.issue_session(db_session, user_id=user.id, tenant_id=first.id)
    with pytest.raises(AccessDenied, match="^Access denied$"):
        service.resolve_session(db_session, issued.token)
    db_session.refresh(row)
    assert row.last_seen_at == last_seen


@pytest.mark.parametrize("deadline", ["idle", "absolute"])
def test_exact_deadline_is_expired_even_with_recent_activity(
    db_session, identities, monkeypatch, deadline
):
    issued = issue(db_session, identities)
    row = record(db_session, issued)
    if deadline == "absolute":
        row.last_seen_at = row.expires_at - timedelta(minutes=1)
        now = row.expires_at
    else:
        now = row.last_seen_at + timedelta(minutes=30)
    db_session.flush()
    before = row.last_seen_at
    monkeypatch.setattr(service, "_database_now", lambda _: now)
    with pytest.raises(AccessDenied, match="^Access denied$"):
        service.resolve_session(db_session, issued.token)
    db_session.refresh(row)
    assert row.last_seen_at == before


def test_activity_extends_idle_window_but_never_absolute_deadline(
    db_session, identities, monkeypatch
):
    issued = issue(db_session, identities)
    row = record(db_session, issued)
    created, expires = row.created_at, row.expires_at
    for minutes in (29, 58, 87):
        now = created + timedelta(minutes=minutes)
        monkeypatch.setattr(service, "_database_now", lambda _, now=now: now)
        service.resolve_session(db_session, issued.token)
        assert row.last_seen_at == now
        assert row.expires_at == expires


def test_unknown_token_and_digest_cannot_authenticate(db_session, identities):
    issued = issue(db_session, identities)
    digest = record(db_session, issued).token_hash
    for token in (SecretStr(secrets.token_urlsafe(32)), SecretStr(digest)):
        with pytest.raises(AccessDenied, match="^Access denied$"):
            service.resolve_session(db_session, token)


def test_logout_is_idempotent_and_does_not_revoke_other_sessions(
    db_session, identities
):
    issued, other = issue(db_session, identities), issue(db_session, identities)
    service.revoke_session(db_session, issued.token)
    row = record(db_session, issued)
    revoked_at = row.revoked_at
    service.revoke_session(db_session, issued.token)
    service.revoke_session(db_session, SecretStr(secrets.token_urlsafe(32)))
    assert row.revoked_at == revoked_at
    with pytest.raises(AccessDenied):
        service.resolve_session(db_session, issued.token)
    service.resolve_session(db_session, other.token)


@pytest.mark.parametrize("scope", ["membership", "user"])
def test_privilege_and_password_changes_can_revoke_in_the_same_transaction(
    db_session, identities, scope
):
    _, second, user, other, memberships = identities
    tokens = [issue(db_session, identities), issue(db_session, identities)]
    second_tenant = service.issue_session(
        db_session, user_id=user.id, tenant_id=second.id
    )
    other_user = service.issue_session(
        db_session, user_id=other.id, tenant_id=second.id
    )
    if scope == "membership":
        memberships[0].role = "read_only"
        db_session.flush()
        service.revoke_membership_sessions(db_session, memberships[0].id)
        service.resolve_session(db_session, second_tenant.token)
    else:
        user.password_hash = "synthetic-changed-hash"
        db_session.flush()
        service.revoke_user_sessions(db_session, user.id)
        with pytest.raises(AccessDenied):
            service.resolve_session(db_session, second_tenant.token)
    for issued in tokens:
        with pytest.raises(AccessDenied):
            service.resolve_session(db_session, issued.token)
    service.resolve_session(db_session, other_user.token)


def test_issue_and_revoke_obey_caller_rollback(db_session, identities):
    first, _, user, _, memberships = identities
    issued = issue(db_session, identities)
    row = record(db_session, issued)
    original = row.last_seen_at
    with pytest.raises(RuntimeError), db_session.begin_nested():
        service.resolve_session(db_session, issued.token)
        service.revoke_user_sessions(db_session, user.id)
        memberships[0].role = "read_only"
        db_session.flush()
        rolled_back = service.issue_session(
            db_session, user_id=user.id, tenant_id=first.id
        )
        raise RuntimeError("deliberate rollback")
    db_session.refresh(row)
    assert row.revoked_at is None and row.last_seen_at == original
    assert (
        service.resolve_session(db_session, issued.token).role == MembershipRole.ADMIN
    )
    with pytest.raises(AccessDenied):
        service.resolve_session(db_session, rolled_back.token)


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ("tenant_id = :other_tenant", "fk_sessions_tenant_membership"),
        ("token_hash = 'plain-text-token'", "ck_sessions_token_hash_sha256"),
        (
            "expires_at = expires_at + interval '1 second'",
            "ck_sessions_absolute_lifetime",
        ),
        (
            "last_seen_at = created_at - interval '1 second'",
            "ck_sessions_activity_window",
        ),
        ("last_seen_at = expires_at", "ck_sessions_activity_window"),
        (
            "revoked_at = created_at - interval '1 second'",
            "ck_sessions_revocation_window",
        ),
    ],
)
def test_database_constraints_reject_direct_sql(
    db_session, identities, change, constraint
):
    issued = issue(db_session, identities)
    row = record(db_session, issued)
    with pytest.raises(IntegrityError) as caught, db_session.begin_nested():
        db_session.execute(
            text("UPDATE sessions SET " + change + " WHERE id = :id"),
            {"id": row.id, "other_tenant": identities[1].id},
        )
    assert caught.value.orig.diag.constraint_name == constraint


def test_duplicate_hash_and_deleting_referenced_membership_are_rejected(
    db_session, identities
):
    first, second = issue(db_session, identities), issue(db_session, identities)
    a, b = record(db_session, first), record(db_session, second)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            update(SessionRecord)
            .where(SessionRecord.id == b.id)
            .values(token_hash=a.token_hash)
        )
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            delete(TenantMembership).where(TenantMembership.id == a.membership_id)
        )
