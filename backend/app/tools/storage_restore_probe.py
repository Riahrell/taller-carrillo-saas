"""Opt-in synthetic object recovery, restricted to isolated PostgreSQL and bucket."""

import argparse
import json
import os
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
from uuid import UUID

from botocore.exceptions import ClientError
from PIL import Image
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import load_settings
from app.core.database import build_engine
from app.modules.access.models import TenantMembership
from app.modules.catalog.image_configuration import load_image_configuration
from app.modules.catalog.image_storage import PrivateImageStorage
from app.modules.catalog.images import prepare_product_variants
from app.tools.check_product_storage import _NoRedirect
from app.tools.image_archive import (
    Entry,
    check_database,
    export_archive,
    references,
    restore_archive,
    verify_archive,
)
from app.tools.inventory_restore_probe import _id


def synthetic_image():
    buffer = BytesIO()
    with Image.new("RGB", (640, 480), (35, 90, 120)) as image:
        image.save(buffer, format="PNG")
    return prepare_product_variants(buffer.getvalue())


def validate_target(settings, expected, forbidden, endpoint):
    if (
        not expected
        or not forbidden
        or expected == forbidden
        or settings.bucket != expected
        or settings.bucket == forbidden
        or settings.endpoint != endpoint
    ):
        raise ValueError("Refusing unexpected object storage target")


def is_missing(storage, reference):
    try:
        storage._client.head_object(Bucket=storage._bucket, Key=reference.key)
    except ClientError as error:
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            return True
        raise ValueError("Object absence was not confirmed") from None
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=["upload", "archive", "lose", "restore", "verify", "cleanup"]
    )
    parser.add_argument("--storage-config", type=Path, required=True)
    parser.add_argument("--expected-bucket", required=True)
    parser.add_argument("--forbidden-bucket", required=True)
    parser.add_argument("--expected-endpoint", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    engine = storage = None
    try:
        settings = load_image_configuration(args.storage_config)
        validate_target(
            settings,
            args.expected_bucket,
            args.forbidden_bucket,
            args.expected_endpoint,
        )
        config = load_settings()
        url = make_url(config.database_url.get_secret_value())
        if (
            config.app_env != "test"
            or url.host != "db"
            or url.database != "carrillo_test"
            or url.username != "carrillo_owner"
        ):
            raise ValueError("Refusing non-isolated database")
        data = json.loads(Path(os.environ["RESTORE_PROBE_FILE"]).read_text())
        if data.get("real_image_fixture") is not True:
            raise ValueError("Synthetic image fixture required")
        user = UUID(data["user"])
        engine = build_engine(config)
        with Session(engine) as session:
            member = session.scalar(
                select(TenantMembership).where(TenantMembership.user_id == user)
            )
            if member is None:
                raise ValueError("Synthetic membership missing")
            prefix = (
                f"product-images/{member.tenant_id}/"
                f"{_id(user, 'product')}/{_id(user, 'image')}"
            )
        if args.phase == "cleanup":
            prepared = synthetic_image()
            entries = [
                Entry(
                    key=prefix + suffix,
                    size=len(image.content),
                    sha256=image.sha256,
                    content_type=image.content_type,
                    width=image.width,
                    height=image.height,
                )
                for suffix, image in [
                    (".webp", prepared),
                    (".thumb.webp", prepared.thumbnail),
                ]
            ]
        else:
            with engine.connect() as connection:
                entries = list(references(connection))
        if len(entries) != 2 or {e.key for e in entries} != {
            prefix + ".webp",
            prefix + ".thumb.webp",
        }:
            raise ValueError("Refusing references outside this synthetic run")
        # No provider client exists before all destination/DB/key checks pass.
        storage = PrivateImageStorage(settings)
        if args.phase == "upload":
            prepared = synthetic_image()
            for entry in entries:
                image = (
                    prepared.thumbnail
                    if entry.key.endswith(".thumb.webp")
                    else prepared
                )
                storage.put(entry.reference(), image)
                if storage.read(entry.reference()) != image.content:
                    raise ValueError("Synthetic upload mismatch")
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            url = f"{settings.endpoint}/{settings.bucket}/{entries[0].key}"
            try:
                with opener.open(url, timeout=10):
                    raise ValueError("Anonymous object access was allowed")
            except HTTPError as error:
                try:
                    if error.code not in (401, 403):
                        raise ValueError("Anonymous access denial was not confirmed")
                finally:
                    error.close()
            print("Synthetic anonymous access: denied.", flush=True)
        elif args.phase == "archive":
            export_archive(args.directory, entries, storage)
            verify_archive(args.directory)
            with engine.connect() as connection:
                check_database(args.directory, connection)
        elif args.phase in ("lose", "cleanup"):
            # Never list/delete a bucket. Only these two random run-owned objects.
            for entry in entries:
                if args.phase == "cleanup" and is_missing(storage, entry.reference()):
                    continue
                storage.read(entry.reference())  # integrity before destructive test
                storage.delete_unreferenced(entry.key)
                if not is_missing(storage, entry.reference()):
                    raise ValueError("Object still exists")
        elif args.phase == "restore":
            with engine.connect() as connection:
                check_database(args.directory, connection)
            restore_archive(args.directory, storage)
        else:
            manifest = verify_archive(args.directory)
            with engine.connect() as connection:
                check_database(args.directory, connection)
            for entry in manifest.entries:
                payload = storage.read(entry.reference())
                with Image.open(BytesIO(payload)) as image:
                    image.load()
                    if image.format != "WEBP" or image.size != (
                        entry.width,
                        entry.height,
                    ):
                        raise ValueError("Restored image decode mismatch")
        print(
            f"Storage restore {args.phase}: passed (2 synthetic objects).", flush=True
        )
        return 0
    except Exception:
        print("Storage restore probe failed; no secret details.", flush=True)
        return 1
    finally:
        if storage is not None:
            storage.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
