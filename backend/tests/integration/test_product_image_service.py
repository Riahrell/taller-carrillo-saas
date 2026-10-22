"""Real restricted PostgreSQL transactions and fault-injected object transport."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
from threading import Barrier, Event
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.access.membership import AccessDenied
from app.modules.access.models import TenantMembership
from app.modules.access.sessions import issue_session, revoke_session
from app.modules.catalog import image_service
from app.modules.catalog.image_models import (
    ProductImage,
    ProductImageEvent,
    ProductImageHead,
)
from app.modules.catalog.image_service import (
    ProductImageAdministrator,
    ProductImagesConflict,
    ProductImagesUnavailable,
)
from app.modules.catalog.image_storage import (
    ImageStorageConflict,
    ImageStorageUnavailable,
    StoredProductImage,
)
from app.modules.catalog.models import Product

pytestmark = pytest.mark.integration


def png(color="red"):
    with Image.new("RGB", (8, 4), color) as image, BytesIO() as stream:
        image.save(stream, "PNG")
        return stream.getvalue()


class Storage:
    def __init__(self):
        self.objects = {}
        self.on_put = None
        self.on_read = None
        self.fail_delete = False
        self.barrier = None
        self.put_entered = Event()

    def allocate(self, tenant, product, image):
        return StoredProductImage(
            f"product-images/{tenant}/{product}/{uuid4()}."
            + ("webp" if image.content_type == "image/webp" else "png"),
            len(image.content),
            image.sha256,
            image.content_type,
        )

    def put(self, ref, image):
        if ref.key in self.objects:
            raise ImageStorageConflict("Already present")
        self.objects[ref.key] = image.content
        self.put_entered.set()
        if self.barrier:
            self.barrier.wait(timeout=20)
        if self.on_put:
            self.on_put()

    def read(self, ref):
        if self.on_read:
            self.on_read()
        return self.objects[ref.key]

    def delete_unreferenced(self, key):
        if self.fail_delete:
            raise ImageStorageUnavailable("Unavailable")
        self.objects.pop(key, None)


@pytest.fixture
def images_state(http_identity, recent_session):
    state = dict(http_identity)
    with Session(state["runtime"]) as session, session.begin():
        state["token"] = issue_session(
            session, user_id=state["user"], tenant_id=state["tenant"]
        ).token
        product = Product(
            tenant_id=state["tenant"], name="Private image", price=Decimal(10)
        )
        foreign = Product(
            tenant_id=state["other"], name="Foreign image", price=Decimal(10)
        )
        session.add_all([product, foreign])
        session.flush()
        state["product"], state["foreign_product"] = product.id, foreign.id
    recent_session(state["token"])
    state["storage"] = Storage()
    state["service"] = ProductImageAdministrator(state["runtime"], state["storage"])
    try:
        yield state
    finally:
        with state["owner"].begin() as connection:
            for model in (ProductImageEvent, ProductImageHead):
                connection.execute(
                    delete(model).where(
                        model.tenant_id.in_([state["tenant"], state["other"]])
                    )
                )
            connection.execute(
                delete(ProductImage).where(
                    ProductImage.tenant_id.in_([state["tenant"], state["other"]])
                )
            )
            connection.execute(
                delete(Product).where(
                    Product.tenant_id.in_([state["tenant"], state["other"]])
                )
            )


def replace(state, *, key=None, content=None, product=None):
    return state["service"].replace(
        state["token"],
        expected_actor=state["membership"],
        product_id=product or state["product"],
        operation_key=key or uuid4(),
        content=content if content is not None else png(),
    )


def rows(state):
    with Session(state["runtime"]) as session:
        return tuple(
            session.scalars(
                select(ProductImage)
                .where(ProductImage.tenant_id == state["tenant"])
                .order_by(ProductImage.created_at, ProductImage.id)
            )
        )


def test_attach_replay_replace_and_cleanup_preserve_current(images_state):
    state = images_state
    key = uuid4()
    first = replace(state, key=key)
    assert replace(state, key=key) == first
    assert first.is_current
    assert "tenant_id" not in first.model_dump()
    assert "object_key" not in first.model_dump()
    second = replace(state, content=png("blue"))
    assert (
        state["service"]
        .read(
            state["token"],
            expected_actor=state["membership"],
            product_id=state["product"],
        )[0]
        .id
        == second.id
    )
    assert state["service"].cleanup_one(second.id) is False
    assert state["service"].cleanup_one(first.id) is True
    assert state["service"].cleanup_one(first.id) is True
    assert len(state["storage"].objects) == 2
    replay = replace(state, key=key)
    assert replay.id == first.id and not replay.is_current
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProductImageEvent)
                .where(ProductImageEvent.tenant_id == state["tenant"])
            )
            == 2
        )
        assert session.get(Product, state["product"]).price == Decimal(10)


def test_foreign_product_and_mismatched_operation_are_denied(images_state):
    state = images_state
    with pytest.raises(AccessDenied):
        replace(state, product=state["foreign_product"])
    with pytest.raises(AccessDenied):
        state["service"].read(
            state["token"],
            expected_actor=state["membership"],
            product_id=state["foreign_product"],
        )
    assert not state["storage"].objects
    key = uuid4()
    replace(state, key=key)
    with pytest.raises(ProductImagesConflict):
        replace(state, key=key, content=png("green"))
    assert len(rows(state)) == 1


@pytest.mark.parametrize("role", ["cashier", "mechanic", "read_only"])
def test_insufficient_role_cannot_process_or_store(images_state, role):
    state = images_state
    with state["owner"].begin() as connection:
        connection.execute(
            update(TenantMembership)
            .where(TenantMembership.id == state["membership"])
            .values(role=role)
        )
    with pytest.raises(AccessDenied):
        replace(state)
    assert rows(state) == ()
    assert not state["storage"].objects


def test_lost_storage_ack_is_recoverable_using_same_reserved_key(images_state):
    state = images_state
    first = replace(state)

    def fail():
        raise ImageStorageUnavailable("Lost acknowledgment")

    state["storage"].on_put = fail
    key = uuid4()
    with pytest.raises(ImageStorageUnavailable):
        replace(state, key=key, content=png("blue"))
    pending = next(image for image in rows(state) if image.state == "reserved")
    assert state["service"].cleanup_one(pending.id) is False
    state["storage"].on_put = None
    recovered = replace(state, key=key, content=png("blue"))
    assert recovered.id == pending.id
    assert recovered.id != first.id
    assert len(rows(state)) == 2


def test_revoke_during_storage_keeps_previous_image(images_state):
    state = images_state
    previous = replace(state)

    def revoke():
        with Session(state["runtime"]) as session, session.begin():
            revoke_session(session, state["token"])

    state["storage"].on_put = revoke
    with pytest.raises(AccessDenied):
        replace(state, content=png("blue"))
    with Session(state["runtime"]) as session:
        assert (
            session.get(ProductImageHead, (state["tenant"], state["product"])).image_id
            == previous.id
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProductImageEvent)
                .where(ProductImageEvent.tenant_id == state["tenant"])
            )
            == 1
        )


def test_attachment_failure_rolls_back_pointer_and_event(images_state):
    state = images_state
    first = replace(state)

    def reject(session, context, instances):
        if any(isinstance(item, ProductImageEvent) for item in session.new):
            raise SQLAlchemyError("Synthetic audit storage failure")

    event.listen(Session, "before_flush", reject)
    try:
        with pytest.raises(ProductImagesUnavailable):
            replace(state, content=png("blue"))
    finally:
        event.remove(Session, "before_flush", reject)
    with Session(state["runtime"]) as session:
        assert (
            session.get(ProductImageHead, (state["tenant"], state["product"])).image_id
            == first.id
        )
    assert sorted(image.state for image in rows(state)) == ["attached", "reserved"]


def test_competing_reservations_do_not_silently_overwrite(images_state):
    state = images_state
    state["storage"].barrier = Barrier(2)

    def attempt(color):
        try:
            return replace(state, content=png(color))
        except ProductImagesConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, "red")
        assert state["storage"].put_entered.wait(timeout=20)
        second = pool.submit(attempt, "blue")
        results = [first.result(timeout=30), second.result(timeout=30)]
    assert sum(result is not None for result in results) == 1
    assert sorted(image.state for image in rows(state)) == ["attached", "garbage"]
    stale = next(image for image in rows(state) if image.state == "garbage")
    state["service"].cleanup_one(stale.id)
    assert len(state["storage"].objects) == 2


def test_expired_reservation_and_failed_delete_are_recoverable(
    images_state, monkeypatch
):
    state = images_state

    def fail():
        raise ImageStorageUnavailable("Lost acknowledgment")

    state["storage"].on_put = fail
    key = uuid4()
    with pytest.raises(ImageStorageUnavailable):
        replace(state, key=key)
    pending = rows(state)[0]
    monkeypatch.setattr(
        image_service, "RESERVATION_LIFETIME", image_service.timedelta(seconds=-1)
    )
    state["storage"].fail_delete = True
    with pytest.raises(ImageStorageUnavailable):
        state["service"].cleanup_one(pending.id)
    assert rows(state)[0].state == "deleting"
    state["storage"].fail_delete = False
    assert state["service"].cleanup_one(pending.id)
    assert rows(state)[0].state == "deleted"
    with pytest.raises(ProductImagesConflict):
        replace(state, key=key)


@pytest.mark.parametrize(
    "table,column",
    [
        (ProductImage, "sha256"),
        (ProductImageEvent, "actor_membership_id"),
        (ProductImageHead, "product_id"),
    ],
)
def test_runtime_cannot_rewrite_image_evidence(images_state, table, column):
    state = images_state
    replace(state)
    with Session(state["runtime"]) as session, pytest.raises(DBAPIError):
        value = "a" * 64 if column == "sha256" else uuid4()
        session.execute(
            update(table)
            .where(table.tenant_id == state["tenant"])
            .values({column: value})
        )


def test_read_revalidates_revocation_after_network(images_state):
    state = images_state
    replace(state)

    def revoke():
        with Session(state["runtime"]) as session, session.begin():
            revoke_session(session, state["token"])

    state["storage"].on_read = revoke
    with pytest.raises(AccessDenied):
        state["service"].read(
            state["token"],
            expected_actor=state["membership"],
            product_id=state["product"],
        )


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"width": 1601}, "ck_product_images_dimensions"),
        ({"height": 0}, "ck_product_images_dimensions"),
        ({"size": 12582913}, "ck_product_images_size_bounds"),
        ({"sha256": "not-a-hash"}, "ck_product_images_hashes"),
        ({"state": "public"}, "ck_product_images_state_allowed"),
        ({"object_key": "other/image.png"}, "ck_product_images_key_source"),
    ],
)
def test_database_rejects_invalid_image_metadata(images_state, changes, expected):
    state = images_state
    replace(state)
    original = rows(state)[0]
    identifier = uuid4()
    values = {
        column.name: getattr(original, column.name)
        for column in ProductImage.__table__.columns
    }
    values.update(
        id=identifier,
        operation_key=uuid4(),
        object_key=f"product-images/{state['tenant']}/{state['product']}/{identifier}.webp",
    )
    with Session(state["runtime"]) as session, pytest.raises(IntegrityError) as error:
        session.add(ProductImage(**(values | changes)))
        session.flush()
    assert error.value.orig.diag.constraint_name == expected


def test_database_rejects_cross_tenant_actor_and_head(images_state):
    state = images_state
    replace(state)
    original = rows(state)[0]
    identifier = uuid4()
    values = {
        column.name: getattr(original, column.name)
        for column in ProductImage.__table__.columns
    }
    values.update(
        id=identifier,
        operation_key=uuid4(),
        actor_membership_id=state["foreign_membership"],
        object_key=f"product-images/{state['tenant']}/{state['product']}/{identifier}.webp",
    )
    with Session(state["runtime"]) as session, pytest.raises(IntegrityError):
        session.add(ProductImage(**values))
        session.flush()
    with Session(state["runtime"]) as session, pytest.raises(IntegrityError):
        session.add(
            ProductImageHead(
                tenant_id=state["other"],
                product_id=state["foreign_product"],
                image_id=original.id,
            )
        )
        session.flush()


def test_pending_admission_is_bounded_before_more_objects(images_state):
    state = images_state

    def fail():
        raise ImageStorageUnavailable("Lost acknowledgment")

    state["storage"].on_put = fail
    for _ in range(3):
        with pytest.raises(ImageStorageUnavailable):
            replace(state)
    with pytest.raises(ProductImagesConflict, match="capacity"):
        replace(state)
    assert len(rows(state)) == 3
    assert len(state["storage"].objects) == 3


def test_cleanup_cannot_compete_with_in_flight_upload(images_state):
    state = images_state

    def competing_cleanup():
        pending = rows(state)[0]
        with pytest.raises(ProductImagesConflict):
            state["service"].cleanup_one(pending.id)

    state["storage"].on_put = competing_cleanup
    result = replace(state)
    assert result.is_current
    assert len(state["storage"].objects) == 2


def test_lost_database_commit_ack_replays_without_second_attachment(images_state):
    state = images_state
    key = uuid4()

    def mark(session, context, instances):
        if any(isinstance(item, ProductImageEvent) for item in session.new):
            session.info["image_attachment"] = True

    def lose_ack(session):
        if session.info.get("image_attachment"):
            raise SQLAlchemyError("Synthetic lost commit response")

    event.listen(Session, "before_flush", mark)
    event.listen(Session, "after_commit", lose_ack)
    try:
        with pytest.raises(ProductImagesUnavailable):
            replace(state, key=key)
    finally:
        event.remove(Session, "before_flush", mark)
        event.remove(Session, "after_commit", lose_ack)
    recovered = replace(state, key=key)
    assert recovered.is_current
    assert len(rows(state)) == 1
    with Session(state["runtime"]) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProductImageEvent)
                .where(ProductImageEvent.tenant_id == state["tenant"])
            )
            == 1
        )
