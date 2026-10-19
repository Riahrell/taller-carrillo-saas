"""Explicit, isolated storage smoke: one new synthetic object, always cleaned."""

import argparse
import os
import stat
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

from dotenv import dotenv_values
from PIL import Image

from app.modules.catalog.image_processing import process_product_image
from app.modules.catalog.image_storage import (
    ImageStorageConflict,
    ImageStorageSettings,
    PrivateImageStorage,
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(secret_file: str) -> None:
    descriptor = os.open(secret_file, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > 16384
        ):
            raise ValueError("A private owner-only configuration file is required")
        values = dotenv_values(stream=stream, interpolate=False)
    if values.get("S3_ADDRESSING") != "path":
        raise ValueError("Explicit path-style configuration is required")
    settings = ImageStorageSettings(
        endpoint=values.get("S3_ENDPOINT"),
        bucket=values.get("S3_BUCKET"),
        region=values.get("S3_REGION"),
        access_key=values.get("S3_ACCESS_KEY"),
        secret_key=values.get("S3_SECRET_KEY"),
    )
    with Image.new("RGB", (4, 4), (30, 80, 50)) as image, BytesIO() as encoded:
        image.save(encoded, "PNG")
        prepared = process_product_image(encoded.getvalue())
    storage = PrivateImageStorage(settings)
    reference = storage.allocate(uuid4(), uuid4(), prepared)
    stage = "upload"
    try:
        storage.put(reference, prepared)
        stage = "authenticated read/integrity"
        if storage.read(reference) != prepared.content:
            raise RuntimeError("Roundtrip failed")
        stage = "conditional write enforcement"
        # Same owned synthetic key: prove conditional immutability is supported.
        try:
            storage.put(reference, prepared)
        except ImageStorageConflict:
            pass
        else:
            raise RuntimeError("Provider did not enforce conditional writes")
        stage = "anonymous access denial"
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(
                f"{settings.endpoint}/{settings.bucket}/{reference.key}", timeout=10
            ) as response:
                print(f"Anonymous object response: HTTP {response.status}.")
                raise RuntimeError("Object is accessible anonymously")
        except HTTPError as error:
            print(f"Anonymous object response: HTTP {error.code}.")
            if error.code not in (401, 403):
                raise RuntimeError("Anonymous denial was not verified") from None
            error.close()
        print(
            "Storage smoke passed: private read, integrity, conditional write, "
            "anonymous access denied."
        )
    except Exception as error:
        print(f"Storage check failed at: {stage} ({type(error).__name__}).")
        raise
    finally:
        try:
            # Random test reference has never been associated with any DB entity.
            try:
                storage.delete_unreferenced(reference.key)
            except Exception:
                print(f"Cleanup failed for owned synthetic object: {reference.key}")
                raise
            print("Synthetic object cleanup acknowledged.")
        finally:
            storage.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-file", required=True)
    args = parser.parse_args()
    try:
        check(args.secret_file)
    except Exception:
        # No SDK tracebacks: provider messages may contain URLs and headers.
        print(
            "Storage smoke failed; inspect configuration without sharing credentials."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
