"""Private current-image archives. Offline verification precedes conditional restore."""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.core.config import load_settings
from app.core.database import build_engine
from app.modules.catalog.image_configuration import load_image_configuration
from app.modules.catalog.image_models import ProductImage, ProductImageHead
from app.modules.catalog.image_storage import (
    ImageStorageConflict,
    PrivateImageStorage,
    StoredProductImage,
    _validated_key,
)
from app.modules.catalog.images import PreparedProductImage


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    size: int = Field(gt=0, le=12_582_912)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(pattern=r"^image/(png|webp)$")
    width: int = Field(ge=1, le=1600)
    height: int = Field(ge=1, le=1600)

    @field_validator("key")
    @classmethod
    def canonical_key(cls, value):
        return _validated_key(value)

    def reference(self):
        return StoredProductImage(self.key, self.size, self.sha256, self.content_type)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, le=1)
    entries: list[Entry]


def private_directory(folder):
    info = folder.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Archive directory must be private and operator-owned")


def write_new(path, payload):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def read_private(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > limit
        ):
            raise ValueError("Invalid private archive file")
        value = source.read(limit + 1)
        if len(value) > limit:
            raise ValueError("Archive file exceeds bounds")
        return value


def references(connection):
    rows = connection.execute(
        select(ProductImage.__table__)
        .join(
            ProductImageHead,
            (
                (ProductImageHead.tenant_id == ProductImage.tenant_id)
                & (ProductImageHead.product_id == ProductImage.product_id)
                & (ProductImageHead.image_id == ProductImage.id)
            ),
        )
        .order_by(ProductImage.tenant_id, ProductImage.product_id)
    ).mappings()
    for row in rows:
        yield Entry(
            key=row.object_key,
            size=row.size,
            sha256=row.sha256,
            content_type=row.content_type,
            width=row.width,
            height=row.height,
        )
        if row.content_type == "image/webp":
            yield Entry(
                key=row.object_key[:-5] + ".thumb.webp",
                size=row.thumbnail_size,
                sha256=row.thumbnail_sha256,
                content_type="image/webp",
                width=row.thumbnail_width,
                height=row.thumbnail_height,
            )


def export_archive(folder, entries, storage):
    # A failed export has no manifest and must never be treated as complete.
    folder.mkdir(mode=0o700)
    private_directory(folder)
    saved = []
    for index, entry in enumerate(entries):
        payload = storage.read(entry.reference())
        if (
            len(payload) != entry.size
            or hashlib.sha256(payload).hexdigest() != entry.sha256
        ):
            raise ValueError("Object integrity mismatch")
        write_new(folder / f"{index}.blob", payload)
        saved.append(entry)
    manifest = Manifest(version=1, entries=saved)
    write_new(folder / "manifest.json", manifest.model_dump_json().encode())
    return len(saved)


def verify_archive(folder):
    private_directory(folder)
    manifest = Manifest.model_validate_json(
        read_private(folder / "manifest.json", 16 << 20)
    )
    keys = set()
    for index, entry in enumerate(manifest.entries):
        if entry.key in keys:
            raise ValueError("Duplicate object key")
        keys.add(entry.key)
        payload = read_private(folder / f"{index}.blob", entry.size)
        if (
            len(payload) != entry.size
            or hashlib.sha256(payload).hexdigest() != entry.sha256
        ):
            raise ValueError("Object integrity mismatch")
    return manifest


def check_database(folder, connection):
    """Require exact current image references before reopening service."""
    manifest = verify_archive(folder)
    archived = {entry.key: entry for entry in manifest.entries}
    current = {entry.key: entry for entry in references(connection)}
    if current != archived:
        raise ValueError("Archive and database image references differ")
    return len(current)


def restore_archive(folder, storage):
    manifest = verify_archive(folder)  # Validate every file before the first write.
    for index, entry in enumerate(manifest.entries):
        payload = read_private(folder / f"{index}.blob", entry.size)
        if hashlib.sha256(payload).hexdigest() != entry.sha256:
            raise ValueError("Archive changed during restoration")
        image = PreparedProductImage(
            payload, entry.width, entry.height, entry.sha256, entry.content_type
        )
        try:
            storage.put(entry.reference(), image)
        except ImageStorageConflict:
            # Existing objects are never overwritten, even if different.
            if storage.read(entry.reference()) != payload:
                raise ValueError("Existing object mismatch") from None
        if storage.read(entry.reference()) != payload:
            raise ValueError("Restored object mismatch")
    return len(manifest.entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=["export", "verify", "check-database", "restore"]
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--storage-config", type=Path)
    parser.add_argument("--maintenance-confirmed", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    storage = engine = None
    try:
        if args.operation == "verify":
            count = len(verify_archive(args.directory).entries)
        elif args.operation == "check-database":
            engine = build_engine(load_settings())
            with (
                engine.connect().execution_options(
                    isolation_level="REPEATABLE READ"
                ) as connection,
                connection.begin(),
            ):
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                count = check_database(args.directory, connection)
        else:
            if not args.maintenance_confirmed or not args.storage_config:
                raise ValueError("Maintenance and explicit storage config required")
            if args.operation == "restore" and not args.apply:
                raise ValueError("Explicit restore application required")
            storage = PrivateImageStorage(load_image_configuration(args.storage_config))
            if args.operation == "export":
                engine = build_engine(load_settings())
                with (
                    engine.connect().execution_options(
                        isolation_level="REPEATABLE READ"
                    ) as connection,
                    connection.begin(),
                ):
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    count = export_archive(
                        args.directory, references(connection), storage
                    )
            else:
                count = restore_archive(args.directory, storage)
        print(json.dumps({"operation": args.operation, "objects": count, "ok": True}))
        return 0
    except Exception:
        print(
            "Image archive failed; no secret details. Incomplete exports are unusable."
        )
        return 1
    finally:
        if storage is not None:
            storage.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
