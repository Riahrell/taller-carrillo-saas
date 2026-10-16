"""Access invariants exercised with the actual restricted PostgreSQL app role."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import IntegrityError

from app.modules.access.membership import AccessDenied, resolve_membership
from app.modules.access.models import MembershipRole, Tenant, TenantMembership, User

pytestmark = pytest.mark.integration


@pytest.fixture
def identities(db_session):
    first = Tenant(name="Taller A")
    second = Tenant(name="Taller B")
    # Synthetic storage fixture; this value cannot be used to authenticate.
    user = User(email="  USER@EXAMPLE.INVALID  ", password_hash="synthetic-test-hash")
    outsider = User(email="other@example.invalid", password_hash="synthetic-test-hash")
    db_session.add_all([first, second, user, outsider])
    db_session.flush()
    first_membership = TenantMembership(
        tenant_id=first.id, user_id=user.id, role=MembershipRole.ADMIN
    )
    second_membership = TenantMembership(
        tenant_id=second.id, user_id=user.id, role=MembershipRole.MECHANIC
    )
    db_session.add_all([first_membership, second_membership])
    db_session.flush()
    return first, second, user, outsider, first_membership, second_membership


def test_membership_role_is_resolved_within_selected_tenant(db_session, identities):
    first, second, user, _, first_membership, second_membership = identities
    admin = resolve_membership(db_session, user_id=user.id, tenant_id=first.id)
    mechanic = resolve_membership(db_session, user_id=user.id, tenant_id=second.id)
    assert (admin.membership_id, admin.role) == (
        first_membership.id,
        MembershipRole.ADMIN,
    )
    assert (mechanic.membership_id, mechanic.role) == (
        second_membership.id,
        MembershipRole.MECHANIC,
    )
    assert admin.user_id == mechanic.user_id == user.id
    assert admin.tenant_id != mechanic.tenant_id
    assert user.email == "user@example.invalid"
    assert user.created_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("missing", ["tenant", "user", "membership"])
def test_missing_access_is_denied_generically(db_session, identities, missing):
    first, _, user, outsider, _, _ = identities
    user_id = outsider.id if missing == "membership" else user.id
    if missing == "user":
        user_id = uuid4()
    tenant_id = uuid4() if missing == "tenant" else first.id
    with pytest.raises(AccessDenied, match="^Access denied$"):
        resolve_membership(db_session, user_id=user_id, tenant_id=tenant_id)


@pytest.mark.parametrize("inactive", ["tenant", "user", "membership"])
def test_inactive_access_is_rechecked_without_trusting_cached_context(
    db_session, identities, inactive
):
    first, _, user, _, membership, _ = identities
    resolve_membership(db_session, user_id=user.id, tenant_id=first.id)
    record = {"tenant": first, "user": user, "membership": membership}[inactive]
    # Bypass ORM identity synchronization to exercise a repeated database check.
    db_session.execute(
        update(type(record))
        .where(type(record).id == record.id)
        .values(is_active=False),
        execution_options={"synchronize_session": False},
    )
    with pytest.raises(AccessDenied, match="^Access denied$"):
        resolve_membership(db_session, user_id=user.id, tenant_id=first.id)


def test_role_change_is_read_again(db_session, identities):
    first, _, user, _, membership, _ = identities
    resolve_membership(db_session, user_id=user.id, tenant_id=first.id)
    db_session.execute(
        update(TenantMembership)
        .where(TenantMembership.id == membership.id)
        .values(role=MembershipRole.READ_ONLY),
        execution_options={"synchronize_session": False},
    )
    assert (
        resolve_membership(db_session, user_id=user.id, tenant_id=first.id).role
        == MembershipRole.READ_ONLY
    )


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO tenants (id, name) VALUES (:id, '   ')",
            "ck_tenants_name_not_blank",
        ),
        (
            "INSERT INTO users (id, email, password_hash) "
            "VALUES (:id, 'UPPER@EXAMPLE.INVALID', 'synthetic')",
            "ck_users_email_canonical",
        ),
        (
            "INSERT INTO users (id, email, password_hash) "
            "VALUES (:id, ' user@example.invalid ', 'synthetic')",
            "ck_users_email_canonical",
        ),
        (
            "INSERT INTO users (id, email, password_hash) "
            "VALUES (:id, '', 'synthetic')",
            "ck_users_email_canonical",
        ),
        (
            "INSERT INTO users (id, email, password_hash) "
            "VALUES (:id, 'new@example.invalid', ' ')",
            "ck_users_password_hash_not_blank",
        ),
        (
            "INSERT INTO users (id, email, password_hash) "
            "VALUES (:id, 'user@example.invalid', 'synthetic')",
            "uq_users_email",
        ),
        (
            "INSERT INTO tenant_memberships (id, tenant_id, user_id, role) "
            "VALUES (:id, :tenant, :outsider, 'root')",
            "ck_tenant_memberships_role_allowed",
        ),
        (
            "INSERT INTO tenant_memberships (id, tenant_id, user_id, role) "
            "VALUES (:id, :tenant, :user, 'cashier')",
            "uq_tenant_memberships_tenant_id",
        ),
        (
            "INSERT INTO tenant_memberships (id, tenant_id, user_id, role) "
            "VALUES (:id, :missing, :outsider, 'cashier')",
            "fk_tenant_memberships_tenant_id_tenants",
        ),
        (
            "INSERT INTO tenant_memberships (id, tenant_id, user_id, role) "
            "VALUES (:id, :tenant, :missing, 'cashier')",
            "fk_tenant_memberships_user_id_users",
        ),
    ],
)
def test_constraints_reject_direct_sql(db_session, identities, statement, constraint):
    first, _, user, outsider, _, _ = identities
    with pytest.raises(IntegrityError) as caught, db_session.begin_nested():
        db_session.execute(
            text(statement),
            {
                "id": uuid4(),
                "tenant": first.id,
                "user": user.id,
                "outsider": outsider.id,
                "missing": uuid4(),
            },
        )
    assert caught.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize("entity", ["tenant", "user"])
def test_memberships_prevent_deleting_referenced_identity(
    db_session, identities, entity
):
    first, _, user, _, _, _ = identities
    record = first if entity == "tenant" else user
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(delete(type(record)).where(type(record).id == record.id))


def test_role_has_no_default(db_session, identities):
    first, _, _, outsider, _, _ = identities
    with pytest.raises(IntegrityError) as caught, db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO tenant_memberships (id, tenant_id, user_id) "
                "VALUES (:id, :tenant, :user)"
            ),
            {"id": uuid4(), "tenant": first.id, "user": outsider.id},
        )
    assert caught.value.orig.diag.column_name == "role"


def test_transaction_failure_leaves_no_partial_identity(db_session):
    tenant_id, user_id = uuid4(), uuid4()
    with pytest.raises(RuntimeError, match="deliberate"), db_session.begin_nested():
        db_session.add(Tenant(id=tenant_id, name="Rollback"))
        db_session.add(
            User(
                id=user_id, email="rollback@example.invalid", password_hash="synthetic"
            )
        )
        db_session.flush()
        db_session.add(
            TenantMembership(tenant_id=tenant_id, user_id=user_id, role="admin")
        )
        db_session.flush()
        raise RuntimeError("deliberate")
    assert db_session.get(Tenant, tenant_id) is None
    assert db_session.get(User, user_id) is None
