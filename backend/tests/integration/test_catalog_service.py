"""Real transactions: session authority, audit atomicity and SKU collisions."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access.membership import AccessDenied
from app.modules.access.models import AuditEvent, SessionRecord, TenantMembership
from app.modules.access.reauthentication import RecentAuthenticationRequired
from app.modules.access.sessions import issue_session, revoke_session
from app.modules.catalog.inputs import CategoryCreation, ProductCreation
from app.modules.catalog.models import Category, Product, ProductChangeRecord
from app.modules.catalog.service import (
    CatalogAdministrator,
    CatalogConflict,
    CatalogUnavailable,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def catalog_state(http_identity, recent_session):
    state = dict(http_identity)
    with Session(state["runtime"]) as session, session.begin():
        state["token"] = issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        ).token
    recent_session(state["token"])
    state["service"] = CatalogAdministrator(state["runtime"])
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            for model in (ProductChangeRecord, AuditEvent, Product, Category):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )


def create(state, **changes):
    return state["service"].create_product(
        state["token"],
        expected_actor=state["membership"],
        data=ProductCreation(name="Filtro", price="12.34", **changes),
    )


def counts(state):
    with Session(state["runtime"]) as session:
        return tuple(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == state["tenant"])
            )
            for model in (Product, AuditEvent)
        )


def test_creation_and_typed_evidence_commit_together(catalog_state):
    state = catalog_state
    category = state["service"].create_category(
        state["token"],
        expected_actor=state["membership"],
        data=CategoryCreation(name="Repuestos"),
    )
    product = create(state, category_id=category.id, sku="AB-1")
    assert product.price == Decimal("12.34")
    assert "tenant_id" not in product.model_dump()
    with Session(state["runtime"]) as session:
        evidence = session.scalar(
            select(AuditEvent).where(AuditEvent.catalog_product_id == product.id)
        )
        assert evidence.action == "catalog_product_created"
        assert evidence.tenant_id == state["tenant"]
        assert evidence.actor_membership_id == state["membership"]
        assert evidence.target_membership_id is None
        assert evidence.catalog_category_id == category.id
        assert (
            evidence.catalog_name,
            evidence.catalog_sku,
            evidence.catalog_price,
        ) == ("Filtro", "AB-1", Decimal("12.34"))
    assert counts(state) == (1, 2)


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_current_role_not_cached_context_authorizes_creation(catalog_state, role):
    state = catalog_state
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    with pytest.raises(AccessDenied):
        create(state)
    assert counts(state) == (0, 0)


@pytest.mark.parametrize("mode", ["revoked", "wrong_actor", "stale", "invalid"])
def test_missing_authority_leaves_no_catalog_or_evidence(catalog_state, mode):
    state = catalog_state
    if mode == "revoked":
        with Session(state["runtime"]) as session, session.begin():
            revoke_session(session, state["token"])
    if mode == "wrong_actor":
        state["membership"] = state["foreign_membership"]
    if mode == "invalid":
        state["token"] = SecretStr("invalid")
    if mode == "stale":
        with state["owner"].begin() as connection:
            connection.execute(
                update(SessionRecord)
                .where(SessionRecord.membership_id == state["membership"])
                .values(reauthenticated_at=None, reauthentication_fingerprint=None)
            )
    with pytest.raises((AccessDenied, RecentAuthenticationRequired)):
        create(state)
    assert counts(state) == (0, 0)


@pytest.mark.parametrize("mode", ["foreign", "missing", "inactive"])
def test_category_reference_is_resolved_inside_session_tenant(catalog_state, mode):
    state = catalog_state
    category_id = uuid4()
    if mode != "missing":
        with Session(state["runtime"]) as session, session.begin():
            session.add(
                Category(
                    id=category_id,
                    name="Hidden",
                    is_active=mode != "inactive",
                    tenant_id=state["other"] if mode == "foreign" else state["tenant"],
                )
            )
    with pytest.raises(AccessDenied, match="^Access denied$"):
        create(state, category_id=category_id)
    assert counts(state) == (0, 0)


@pytest.mark.parametrize("kind", ["product", "category"])
def test_real_audit_insert_failure_rolls_back_catalog(catalog_state, kind):
    state = catalog_state

    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_events"):
            return "INSERT INTO audit_events (nonexistent_column) VALUES (1)", {}
        return statement, parameters

    event.listen(state["runtime"], "before_cursor_execute", fail_audit, retval=True)
    try:
        with pytest.raises(CatalogUnavailable, match="^Catalog unavailable$"):
            if kind == "product":
                create(state)
            else:
                state["service"].create_category(
                    state["token"],
                    expected_actor=state["membership"],
                    data=CategoryCreation(name="Rollback"),
                )
    finally:
        event.remove(state["runtime"], "before_cursor_execute", fail_audit)
    assert counts(state) == (0, 0)
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Category)
                .where(Category.tenant_id == state["tenant"])
            )
            == 0
        )


def test_failed_commit_does_not_return_or_persist_product(catalog_state):
    state = catalog_state

    def fail_commit(session):
        raise SQLAlchemyError("synthetic commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(CatalogUnavailable):
            create(state)
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert counts(state) == (0, 0)


def test_final_authorization_failure_reverts_both_writes(catalog_state, monkeypatch):
    state = catalog_state
    original = state["service"]._authorize
    calls = 0

    def authorize(*args):
        nonlocal calls
        calls += 1
        actor = original(*args)
        if calls == 2:
            raise RecentAuthenticationRequired("Recent authentication required")
        return actor

    monkeypatch.setattr(state["service"], "_authorize", authorize)
    with pytest.raises(RecentAuthenticationRequired):
        create(state)
    assert counts(state) == (0, 0)


def test_competing_duplicate_sku_has_one_commit_and_one_audit(catalog_state):
    state = catalog_state

    def attempt():
        try:
            return create(state, sku="ONE")
        except CatalogConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert counts(state) == (1, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"catalog_name": None},
        {"catalog_price": None},
        {"catalog_product_id": None},
        {"after_role": "admin"},
        {"before_active": False},
        {"target_membership_id": "actor"},
        {"catalog_category_id": "foreign"},
    ],
)
def test_typed_audit_rejects_incomplete_or_foreign_evidence(catalog_state, changes):
    state = catalog_state
    product = create(state)
    foreign = Category(tenant_id=state["other"], name="Foreign")
    with Session(state["runtime"]) as session, session.begin():
        session.add(foreign)
        session.flush()
        foreign_id = foreign.id
    values = dict(
        tenant_id=state["tenant"],
        actor_membership_id=state["membership"],
        action="catalog_product_created",
        catalog_product_id=product.id,
        catalog_name="Filtro",
        catalog_price=Decimal("12.34"),
        after_active=True,
    )
    changes = {
        key: state["membership"]
        if value == "actor"
        else foreign_id
        if value == "foreign"
        else value
        for key, value in changes.items()
    }
    values.update(changes)
    with Session(state["runtime"]) as session:
        from app.modules.access.sessions import _database_now

        values["created_at"] = _database_now(session)
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(AuditEvent(**values))
            session.flush()
    assert counts(state) == (1, 1)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_catalog_audit_is_append_only_for_runtime(catalog_state, operation):
    state = catalog_state
    product = create(state)
    with (
        Session(state["runtime"]) as session,
        session.begin(),
        pytest.raises(DBAPIError),
        session.begin_nested(),
    ):
        statement = (
            delete(AuditEvent)
            if operation == "delete"
            else (update(AuditEvent).values(catalog_name="Modified"))
        )
        session.execute(statement.where(AuditEvent.catalog_product_id == product.id))
    assert counts(state) == (1, 1)


def test_catalog_audit_downgrade_preserves_committed_evidence(catalog_state):
    state = catalog_state
    create(state)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with (
        pytest.raises(DBAPIError, match="Downgrade refused"),
        state["owner"].begin() as connection,
    ):
        config.attributes["connection"] = connection
        command.downgrade(config, "0030_catalog")
    with state["owner"].connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == (
            "0040_product_changes"
        )
    assert counts(state) == (1, 1)
