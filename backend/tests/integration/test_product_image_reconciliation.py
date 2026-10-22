from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from test_product_image_service import images_state as images_state
from test_product_image_service import png, replace, rows

from app.modules.catalog.image_models import ProductImage
from app.modules.catalog.image_reconciliation import ImageReconciler
from app.modules.catalog.image_storage import ImageStorageUnavailable

pytestmark = pytest.mark.integration


def test_reconcile_current_safety_paging_and_late_put_tombstone(images_state):
    state = images_state
    first = replace(state)
    current = replace(state, content=png("blue"))
    reconciler = ImageReconciler(state["service"])
    result = reconciler.run_batch(limit=1)
    assert result.inspected == result.deleted == 1
    assert not result.cycle_complete
    assert reconciler.run_batch(limit=1).cycle_complete
    old = next(image for image in rows(state) if image.id == first.id)
    # A lost DB connection could release an advisory lock before an uncertain
    # provider write completes. Durable tombstones must be revisited.
    state["storage"].objects[old.object_key] = b"late synthetic object"
    assert reconciler.run_batch().deleted == 1
    assert old.object_key not in state["storage"].objects
    assert (
        next(image for image in rows(state) if image.id == current.id).state
        == "attached"
    )
    assert len(state["storage"].objects) == 2


def test_restart_replays_failed_cleanup_without_losing_reservation(images_state):
    state = images_state

    def fail():
        raise ImageStorageUnavailable("Synthetic lost acknowledgment")

    state["storage"].on_put = fail
    with pytest.raises(ImageStorageUnavailable):
        replace(state)
    reconciler = ImageReconciler(state["service"])
    assert reconciler.run_batch().inspected == 0
    pending = rows(state)[0]
    with state["owner"].begin() as connection:
        connection.execute(
            update(ProductImage)
            .where(ProductImage.id == pending.id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=2))
        )
    state["storage"].fail_delete = True
    result = reconciler.run_batch()
    assert result.deferred == 1 and result.deleted == 0
    assert rows(state)[0].state == "deleting"
    state["storage"].fail_delete = False
    restarted = ImageReconciler(state["service"])
    assert restarted.run_batch().deleted == 1
    assert rows(state)[0].state == "deleted"


def test_batch_admission_and_shutdown_do_not_discard_work(images_state):
    state = images_state
    replace(state)
    replace(state, content=png("blue"))
    reconciler = ImageReconciler(state["service"])
    with pytest.raises(ValueError):
        reconciler.run_batch(limit=101)
    assert reconciler.run_batch(stopping=lambda: True).inspected == 0
    assert reconciler.run_batch().deleted == 1
