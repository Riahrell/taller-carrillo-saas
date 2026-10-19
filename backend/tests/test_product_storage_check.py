import secrets
from contextlib import nullcontext
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from app.modules.catalog.image_storage import ImageStorageConflict, StoredProductImage
from app.tools import check_product_storage as checker


@pytest.mark.parametrize("status", [200, 401, 403, 404])
def test_smoke_requires_auth_denial_and_always_cleans_owned_object(
    monkeypatch, tmp_path, status
):
    config = tmp_path / "s3.env"
    config.write_text(
        "S3_ENDPOINT=https://storage.example.com\n"
        "S3_BUCKET=test-images\nS3_REGION=default\nS3_ADDRESSING=path\n"
        f"S3_ACCESS_KEY={secrets.token_hex(16)}\n"
        f"S3_SECRET_KEY={secrets.token_hex(32)}\n"
    )
    config.chmod(0o600)
    deleted = []
    closed = []
    reference = StoredProductImage(
        f"product-images/{uuid4()}/{uuid4()}/{uuid4()}.png", 1, "0" * 64
    )

    class Storage:
        def __init__(self, settings):
            self.image = None

        def allocate(self, tenant, product, image):
            return reference

        def put(self, ref, image):
            assert ref is reference
            if self.image is not None:
                raise ImageStorageConflict("existing")
            self.image = image

        def read(self, ref):
            return self.image.content

        def delete_unreferenced(self, key):
            deleted.append(key)

        def close(self):
            closed.append(True)

    class Response:
        pass

    response = Response()
    response.status = status

    class Opener:
        def open(self, url, timeout):
            if status != 200:
                raise HTTPError(url, status, "test", {}, None)
            return nullcontext(response)

    monkeypatch.setattr(checker, "PrivateImageStorage", Storage)
    monkeypatch.setattr(checker, "build_opener", lambda *args: Opener())
    if status in (401, 403):
        checker.check(str(config))
    else:
        with pytest.raises(RuntimeError):
            checker.check(str(config))
    assert deleted == [reference.key]
    assert closed == [True]
