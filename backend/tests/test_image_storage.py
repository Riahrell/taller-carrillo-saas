import secrets
from io import BytesIO
from uuid import uuid4

import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from PIL import Image
from pydantic import ValidationError

from app.modules.catalog.image_processing import (
    process_product_image,
    process_product_variants,
)
from app.modules.catalog.image_storage import (
    ImageStorageConflict,
    ImageStorageSettings,
    ImageStorageUnavailable,
    PrivateImageStorage,
    StoredProductImage,
)


def settings(**changes):
    return ImageStorageSettings(
        **(
            {
                "endpoint": "https://storage.example.com",
                "bucket": "private-test-images",
                "region": "default",
                "access_key": secrets.token_hex(16),
                "secret_key": secrets.token_hex(32),
            }
            | changes
        )
    )


def prepared(webp=False):
    with Image.new("RGB", (3, 2), "red") as image, BytesIO() as output:
        image.save(output, "PNG")
        return (process_product_variants if webp else process_product_image)(
            output.getvalue()
        )


@pytest.mark.parametrize("kind", ["png", "webp", "thumbnail"])
def test_worker_to_private_transport_and_verified_read(kind):
    storage = PrivateImageStorage(settings())
    config = storage._client.meta.config
    assert config.s3 == {"addressing_style": "path"}
    assert config.retries["total_max_attempts"] == 1
    assert config.proxies == {}
    assert (config.connect_timeout, config.read_timeout) == (3, 10)
    image = prepared(kind != "png")
    if kind == "thumbnail":
        image = image.thumbnail
    with Stubber(storage._client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "private-test-images",
                "Key": ANY,
                "Body": image.content,
                "ContentLength": len(image.content),
                "ContentType": image.content_type,
                "ContentMD5": ANY,
                "CacheControl": "private, no-store",
                "ACL": "private",
                "IfNoneMatch": "*",
                "Metadata": {"sha256": image.sha256},
            },
        )
        tenant, product = uuid4(), uuid4()
        reference = storage.allocate(tenant, product, image)
        if kind == "thumbnail":
            reference = StoredProductImage(
                reference.key[:-5] + ".thumb.webp",
                reference.size,
                reference.sha256,
                "image/webp",
            )
        storage.put(reference, image)
        assert reference.key.startswith(f"product-images/{tenant}/{product}/")
        body = StreamingBody(BytesIO(image.content), len(image.content))
        stub.add_response(
            "get_object",
            {
                "Body": body,
                "ContentLength": reference.size,
                "ContentType": image.content_type,
            },
            {"Bucket": "private-test-images", "Key": reference.key},
        )
        assert storage.read(reference) == image.content
        assert body._raw_stream.closed
        stub.assert_no_pending_responses()
    storage.close()


@pytest.mark.parametrize("status", [403, 412, 503])
def test_transport_errors_are_safe_and_no_write_retry(status):
    storage = PrivateImageStorage(settings())
    with Stubber(storage._client) as stub:
        stub.add_client_error(
            "put_object",
            service_message="sensitive provider details",
            http_status_code=status,
        )
        expected = ImageStorageConflict if status == 412 else ImageStorageUnavailable
        with pytest.raises(expected) as error:
            image = prepared()
            storage.put(storage.allocate(uuid4(), uuid4(), image), image)
        assert "sensitive" not in str(error.value)
        stub.assert_no_pending_responses()
    storage.close()


@pytest.mark.parametrize("change", ["bytes", "size", "type"])
def test_integrity_failure_closes_response(change):
    storage = PrivateImageStorage(settings())
    image = prepared()
    reference = StoredProductImage(
        f"product-images/{uuid4()}/{uuid4()}/{uuid4()}.png",
        len(image.content),
        image.sha256,
    )
    payload = b"x" * reference.size if change == "bytes" else image.content
    body = StreamingBody(BytesIO(payload), len(payload))
    with Stubber(storage._client) as stub:
        stub.add_response(
            "get_object",
            {
                "Body": body,
                "ContentLength": reference.size + (change == "size"),
                "ContentType": "text/html" if change == "type" else "image/png",
            },
        )
        with pytest.raises(ImageStorageUnavailable):
            storage.read(reference)
        assert body._raw_stream.closed
    storage.close()


@pytest.mark.parametrize("key", ["../other", "https://example.com/image", "other.png"])
def test_arbitrary_object_keys_never_reach_sdk(key):
    storage = PrivateImageStorage(settings())
    with Stubber(storage._client):
        with pytest.raises(ValueError):
            storage.delete_unreferenced(key)
        with pytest.raises(ValueError):
            storage.read(StoredProductImage(key, 1, "0" * 64))
    storage.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://storage.example.com",
        "https://127.0.0.1",
        "https://x.example/a",
        "https://user@x.example",
    ],
)
def test_endpoint_must_be_server_https_origin(endpoint):
    with pytest.raises(ValidationError):
        settings(endpoint=endpoint)


def test_empty_credentials_do_not_fall_back_to_environment():
    with pytest.raises(ValidationError):
        settings(access_key="")
