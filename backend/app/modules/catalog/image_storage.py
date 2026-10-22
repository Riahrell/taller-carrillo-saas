"""Private S3 transport. Callers must authorize references before each operation."""

import base64
import hashlib
import re
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from app.modules.catalog.images import MAX_OUTPUT_BYTES, PreparedProductImage

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY = re.compile(
    rf"product-images/{_UUID}/{_UUID}/{_UUID}(?:\.png|\.webp|\.thumb\.webp)"
)


class ImageStorageUnavailable(RuntimeError):
    """Safe transport failure; never include SDK messages/URLs/credentials."""


class ImageStorageConflict(ImageStorageUnavailable):
    """Conditional write refused: existing immutable object is preserved."""


class ImageStorageSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    endpoint: str
    bucket: str
    region: str
    access_key: SecretStr
    secret_key: SecretStr

    @field_validator("endpoint")
    @classmethod
    def endpoint_is_https_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,63}", host)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("An HTTPS DNS origin is required")
        try:
            ip_address(host)
        except ValueError:
            return value.rstrip("/")
        raise ValueError("An HTTPS DNS origin is required")

    @field_validator("bucket")
    @classmethod
    def bucket_is_canonical(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", value):
            raise ValueError("A canonical private bucket name is required")
        return value

    @field_validator("region")
    @classmethod
    def region_is_explicit(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9-]{1,63}", value):
            raise ValueError("An explicit S3 region is required")
        return value

    @field_validator("access_key", "secret_key")
    @classmethod
    def credentials_are_explicit(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Explicit storage credentials are required")
        return value


@dataclass(frozen=True)
class StoredProductImage:
    key: str
    size: int
    sha256: str
    content_type: str = "image/png"


def _validated_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("Invalid internal image reference")
    return key


class PrivateImageStorage:
    def __init__(self, settings: ImageStorageSettings) -> None:
        self._bucket = settings.bucket
        # Explicit credentials: no environment/profile/instance metadata fallback.
        self._client = boto3.session.Session().client(
            "s3",
            endpoint_url=settings.endpoint,
            region_name=settings.region,
            aws_access_key_id=settings.access_key.get_secret_value(),
            aws_secret_access_key=settings.secret_key.get_secret_value(),
            verify=True,
            config=Config(
                signature_version="s3v4",
                connect_timeout=3,
                read_timeout=10,
                retries={"total_max_attempts": 1, "mode": "standard"},
                proxies={},
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def allocate(
        self, tenant_id: UUID, product_id: UUID, image: PreparedProductImage
    ) -> StoredProductImage:
        if not isinstance(tenant_id, UUID) or not isinstance(product_id, UUID):
            raise ValueError("Server UUID references are required")
        payload = image.content
        digest = hashlib.sha256(payload).hexdigest()
        if (
            not 0 < len(payload) <= MAX_OUTPUT_BYTES
            or image.content_type not in ("image/png", "image/webp")
            or digest != image.sha256
            or not _signature(payload, image.content_type)
        ):
            raise ValueError("Invalid prepared image")
        extension = "webp" if image.content_type == "image/webp" else "png"
        key = f"product-images/{tenant_id}/{product_id}/{uuid4()}.{extension}"
        return StoredProductImage(key, len(payload), digest, image.content_type)

    def put(self, reference: StoredProductImage, image: PreparedProductImage) -> None:
        """Persist reservation in DB before calling; timeout may have committed S3."""
        key = _validated_key(reference.key)
        payload = image.content
        digest = hashlib.sha256(payload).hexdigest()
        if (
            not 0 < len(payload) == reference.size <= MAX_OUTPUT_BYTES
            or image.content_type not in ("image/png", "image/webp")
            or digest != reference.sha256
            or digest != image.sha256
            or reference.content_type != image.content_type
            or not _signature(payload, image.content_type)
        ):
            raise ValueError("Invalid prepared image")
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentLength=len(payload),
                ContentType=image.content_type,
                ContentMD5=base64.b64encode(
                    hashlib.md5(payload, usedforsecurity=False).digest()
                ).decode("ascii"),
                CacheControl="private, no-store",
                ACL="private",
                IfNoneMatch="*",
                Metadata={"sha256": digest},
            )
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in (
                409,
                412,
            ):
                raise ImageStorageConflict("La imagen ya existe.") from None
            raise ImageStorageUnavailable("Almacenamiento no disponible.") from None
        except BotoCoreError:
            raise ImageStorageUnavailable("Almacenamiento no disponible.") from None

    def read(self, reference: StoredProductImage) -> bytes:
        key = _validated_key(reference.key)
        if reference.content_type not in ("image/png", "image/webp"):
            raise ValueError("Invalid content type")
        if not 0 < reference.size <= MAX_OUTPUT_BYTES or not re.fullmatch(
            r"[0-9a-f]{64}", reference.sha256
        ):
            raise ValueError("Invalid internal image metadata")
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            try:
                if (
                    response.get("ContentLength") != reference.size
                    or response.get("ContentType") != reference.content_type
                ):
                    raise ImageStorageUnavailable("Imagen almacenada no válida.")
                payload = body.read(reference.size + 1)
            finally:
                body.close()
        except (BotoCoreError, ClientError, OSError):
            raise ImageStorageUnavailable("Almacenamiento no disponible.") from None
        if (
            len(payload) != reference.size
            or hashlib.sha256(payload).hexdigest() != reference.sha256
            or not _signature(payload, reference.content_type)
        ):
            raise ImageStorageUnavailable("Imagen almacenada no válida.")
        return payload

    def delete_unreferenced(self, key: str) -> None:
        """Only after caller proves no DB references; never expose as an API."""
        key = _validated_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError):
            raise ImageStorageUnavailable("Almacenamiento no disponible.") from None

    def close(self) -> None:
        self._client.close()


def _signature(payload, content_type):
    if content_type == "image/png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    return (
        len(payload) >= 30
        and payload[:4] == b"RIFF"
        and payload[8:12] == b"WEBP"
        and int.from_bytes(payload[4:8], "little") + 8 == len(payload)
    )
