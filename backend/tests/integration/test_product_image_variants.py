"""Atomic variant publication, retry, legacy compatibility and private routes."""

import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_product_image_http import image_web as image_web
from test_product_image_service import images_state as images_state
from test_product_image_service import png, replace, rows

from app.modules.catalog.image_models import ProductImage, ProductImageHead
from app.modules.catalog.image_storage import ImageStorageUnavailable

pytestmark = pytest.mark.integration


def test_new_upload_has_two_immutable_variants_and_safe_cleanup(images_state):
    state = images_state
    first = replace(state)
    image = rows(state)[0]
    assert image.content_type == "image/webp" and image.thumbnail_size > 0
    assert len(state["storage"].objects) == 2
    for thumbnail in (False, True):
        metadata, content = state["service"].read(
            state["token"],
            expected_actor=state["membership"],
            product_id=state["product"],
            thumbnail=thumbnail,
        )
        assert metadata.id == first.id and metadata.content_type == "image/webp"
        with Image.open(BytesIO(content)) as decoded:
            assert max(decoded.size) <= (300 if thumbnail else 1600)
    with (
        state["runtime"].begin() as connection,
        pytest.raises(SQLAlchemyError),
        connection.begin_nested(),
    ):
        connection.execute(
            update(ProductImage)
            .where(ProductImage.id == first.id)
            .values(thumbnail_size=1)
        )
    second = replace(state, content=png("blue"))
    assert state["service"].cleanup_one(second.id) is False
    assert state["service"].cleanup_one(first.id)
    assert state["service"].cleanup_one(first.id)
    assert len(state["storage"].objects) == 2


def test_thumbnail_failure_does_not_attach_and_same_key_recovers(images_state):
    state = images_state
    first = replace(state)
    original = state["storage"].put

    def fail(ref, image):
        if ref.key.endswith(".thumb.webp"):
            raise ImageStorageUnavailable("injected thumbnail failure")
        original(ref, image)

    state["storage"].put = fail
    key = uuid4()
    with pytest.raises(ImageStorageUnavailable):
        replace(state, key=key, content=png("blue"))
    with Session(state["runtime"]) as session:
        assert (
            session.get(ProductImageHead, (state["tenant"], state["product"])).image_id
            == first.id
        )
    assert len(state["storage"].objects) == 3
    state["storage"].put = original
    second = replace(state, key=key, content=png("blue"))
    assert second.id != first.id and second.is_current
    assert replace(state, key=key, content=png("blue")) == second
    assert len(state["storage"].objects) == 4


def test_legacy_png_remains_readable_and_can_be_replaced(images_state):
    state = images_state
    identifier, payload = uuid4(), png()
    key = f"product-images/{state['tenant']}/{state['product']}/{identifier}.png"
    state["storage"].objects[key] = payload
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            ProductImage(
                id=identifier,
                tenant_id=state["tenant"],
                product_id=state["product"],
                actor_membership_id=state["membership"],
                operation_key=uuid4(),
                object_key=key,
                input_sha256=hashlib.sha256(payload).hexdigest(),
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
                width=8,
                height=4,
                state="attached",
            )
        )
        session.flush()
        session.add(
            ProductImageHead(
                tenant_id=state["tenant"],
                product_id=state["product"],
                image_id=identifier,
            )
        )
    metadata, content = state["service"].read(
        state["token"],
        expected_actor=state["membership"],
        product_id=state["product"],
        thumbnail=True,
    )
    assert metadata.content_type == "image/png" and content == payload
    replace(state)
    assert state["service"].cleanup_one(identifier)
    assert key not in state["storage"].objects


def test_http_thumbnail_is_private_and_rechecks_current_head(image_web):
    client, state, policy = image_web
    headers = {
        "Origin": policy.origin,
        "X-CSRF-Token": client.cookies[policy.csrf_cookie],
        "X-Expected-Membership": str(state["membership"]),
        "Idempotency-Key": str(uuid4()),
        "Content-Type": "application/octet-stream",
    }
    path = f"/catalog/products/{state['product']}/image"
    assert client.post(path, content=png(), headers=headers).status_code == 200
    read = {
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "X-Expected-Membership": str(state["membership"]),
    }
    result = client.get(path + "/thumbnail", headers=read)
    assert result.status_code == 200 and result.headers["Content-Type"] == "image/webp"
    assert (
        result.headers["Cache-Control"] == "no-store" and result.content[:4] == b"RIFF"
    )
    with Image.open(BytesIO(result.content)) as image:
        assert max(image.size) <= 300
    assert client.get(path + "/thumbnail").status_code == 403
    assert (
        client.get(
            path + "/thumbnail?tenant_id=" + str(state["other"]), headers=read
        ).status_code
        == 422
    )


def test_failed_thumbnail_delete_keeps_retryable_reference(images_state):
    state = images_state
    old = replace(state)
    replace(state, content=png("blue"))
    original = state["storage"].delete_unreferenced

    def fail(key):
        if key.endswith(".thumb.webp"):
            raise ImageStorageUnavailable("injected delete failure")
        original(key)

    state["storage"].delete_unreferenced = fail
    with pytest.raises(ImageStorageUnavailable):
        state["service"].cleanup_one(old.id)
    assert next(row for row in rows(state) if row.id == old.id).state == "deleting"
    assert len(state["storage"].objects) == 3
    state["storage"].delete_unreferenced = original
    assert state["service"].cleanup_one(old.id)
    assert len(state["storage"].objects) == 2


def test_legacy_uncertain_png_reservation_can_finish_after_upgrade(images_state):
    from app.modules.catalog.image_processing import process_product_image

    state = images_state
    identifier, operation, raw = uuid4(), uuid4(), png()
    prepared = process_product_image(raw)
    key = f"product-images/{state['tenant']}/{state['product']}/{identifier}.png"
    state["storage"].objects[key] = prepared.content
    with Session(state["runtime"]) as session, session.begin():
        session.add(
            ProductImage(
                id=identifier,
                tenant_id=state["tenant"],
                product_id=state["product"],
                actor_membership_id=state["membership"],
                operation_key=operation,
                object_key=key,
                input_sha256=hashlib.sha256(raw).hexdigest(),
                sha256=prepared.sha256,
                size=len(prepared.content),
                width=prepared.width,
                height=prepared.height,
                state="reserved",
            )
        )
        session.flush()
        session.add(
            ProductImageHead(tenant_id=state["tenant"], product_id=state["product"])
        )
    result = replace(state, key=operation, content=raw)
    assert result.is_current and result.id == identifier
    assert (
        state["service"].read(
            state["token"],
            expected_actor=state["membership"],
            product_id=state["product"],
            thumbnail=True,
        )[1]
        == prepared.content
    )
    assert len(state["storage"].objects) == 1


def test_archive_round_trip_only_current_variants(images_state, tmp_path):
    from test_product_image_service import Storage

    from app.tools.image_archive import (
        export_archive,
        references,
        restore_archive,
        verify_archive,
    )

    state = images_state
    old = replace(state)
    current = replace(state, content=png("blue"))
    folder = tmp_path / "archive"
    with state["runtime"].connect() as connection:
        refs = list(references(connection))
    assert len(refs) == 2
    assert all(str(current.id) in entry.key for entry in refs)
    assert all(str(old.id) not in entry.key for entry in refs)
    assert export_archive(folder, refs, state["storage"]) == 2
    manifest = verify_archive(folder)
    restored = Storage()
    assert restore_archive(folder, restored) == 2
    assert restore_archive(folder, restored) == 2
    for entry in manifest.entries:
        assert restored.read(entry.reference()) == state["storage"].read(
            entry.reference()
        )
        with Image.open(BytesIO(restored.read(entry.reference()))) as image:
            assert image.size == (entry.width, entry.height)
