"""Upload encrypted recovery copies to an explicitly separate private bucket."""

import argparse
import hashlib
import os
import re
import stat
from pathlib import Path

from app.modules.catalog.image_configuration import load_image_configuration
from app.modules.catalog.image_storage import PrivateImageStorage

KEY = re.compile(r"respaldos/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}\.tar\.gpg")
MAX_ARCHIVE = 4 * 1024**3


def upload(storage, path, key):
    if not KEY.fullmatch(key):
        raise ValueError("Invalid backup object key")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not 0 < info.st_size <= MAX_ARCHIVE
        ):
            raise ValueError("Private bounded archive required")
        first = source.read(1)
        packet = (first[0] & 0x3F) if first[0] & 0x40 else ((first[0] >> 2) & 15)
        if not first[0] & 0x80 or packet != 1:
            raise ValueError("Public-key encrypted archive required")
        source.seek(0)
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        source.seek(0)
        storage._client.put_object(
            Bucket=storage._bucket,
            Key=key,
            Body=source,
            ContentLength=info.st_size,
            ContentType="application/pgp-encrypted",
            ACL="private",
            CacheControl="private, no-store",
            IfNoneMatch="*",
            Metadata={"sha256": digest},
        )
    response = storage._client.get_object(Bucket=storage._bucket, Key=key)
    body = response["Body"]
    try:
        actual = hashlib.sha256()
        length = 0
        while chunk := body.read(1024 * 1024):
            length += len(chunk)
            if length > info.st_size:
                raise ValueError("Uploaded archive exceeds expected size")
            actual.update(chunk)
        if (
            length != info.st_size
            or actual.hexdigest() != digest
            or response.get("ContentLength") != info.st_size
            or response.get("ContentType") != "application/pgp-encrypted"
        ):
            raise ValueError("Uploaded archive mismatch")
    finally:
        body.close()
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-config", type=Path, required=True)
    parser.add_argument("--expected-bucket", required=True)
    parser.add_argument("--forbidden-bucket", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    storage = None
    try:
        settings = load_image_configuration(args.storage_config)
        if (
            settings.bucket != args.expected_bucket
            or settings.bucket == args.forbidden_bucket
        ):
            raise ValueError("Unexpected backup bucket")
        if not KEY.fullmatch(args.key):
            raise ValueError("Invalid backup object key")
        storage = PrivateImageStorage(settings)
        upload(storage, args.file, args.key)
    except Exception:
        print("Encrypted backup upload failed; no secret details.")
        return 1
    finally:
        if storage is not None:
            storage.close()
    print("Encrypted off-VPS backup uploaded and read-back integrity verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
