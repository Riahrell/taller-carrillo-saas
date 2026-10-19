"""Run bounded image reconciliation; never expose this tool over HTTP."""

import argparse
import signal
import time
from pathlib import Path

from app.core.config import load_settings
from app.core.database import build_engine
from app.modules.catalog.image_configuration import load_image_configuration
from app.modules.catalog.image_reconciliation import ImageReconciler
from app.modules.catalog.image_service import ProductImageAdministrator
from app.modules.catalog.image_storage import PrivateImageStorage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, choices=range(1, 101), default=25)
    args = parser.parse_args()
    engine = storage = None
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        storage = PrivateImageStorage(load_image_configuration(args.storage_config))
        engine = build_engine(load_settings())
        reconciler = ImageReconciler(ProductImageAdministrator(engine, storage))
        while not stopping:
            result = reconciler.run_batch(
                limit=args.batch_size, stopping=lambda: stopping
            )
            print(
                f"Images: inspected={result.inspected} deleted={result.deleted} "
                f"deferred={result.deferred}",
                flush=True,
            )
            if args.once:
                return 1 if result.deferred else 0
            # Tombstones are revisited each cycle. Back off between complete
            # cycles; interrupted/failed batches are replayable after restart.
            delay = 900 if result.cycle_complete else 15
            until = time.monotonic() + delay
            while not stopping and time.monotonic() < until:
                time.sleep(min(1, max(0, until - time.monotonic())))
        return 0
    except Exception:
        print("Image reconciliation unavailable; no secret details.", flush=True)
        return 1
    finally:
        if storage is not None:
            storage.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
