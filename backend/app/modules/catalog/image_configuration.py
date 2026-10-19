"""Explicit private configuration file; no shell interpolation or SDK fallback."""

import os
import stat
from pathlib import Path

from dotenv import dotenv_values

from app.modules.catalog.image_storage import ImageStorageSettings


class ImageConfigurationError(RuntimeError):
    """Configuration unavailable; deliberately excludes secret values."""


def load_image_configuration(path: Path) -> ImageStorageSettings:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            # Docker secrets may be root-owned and readable by the runtime UID.
            # Outside that explicit mount directory require owner-only access.
            parent = path.parent.stat()
            docker_secret = (
                path.parent == Path("/run/secrets")
                and parent.st_uid == 0
                and not stat.S_IMODE(parent.st_mode) & 0o022
            )
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > 16384
                or not docker_secret
                and info.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(info.st_mode) & (0o022 if docker_secret else 0o077)
            ):
                raise ValueError
            values = dotenv_values(stream=stream, interpolate=False)
        fields = {
            "S3_ENDPOINT",
            "S3_BUCKET",
            "S3_REGION",
            "S3_ADDRESSING",
            "S3_ACCESS_KEY",
            "S3_SECRET_KEY",
        }
        if set(values) != fields or values["S3_ADDRESSING"] != "path":
            raise ValueError
        return ImageStorageSettings(
            endpoint=values["S3_ENDPOINT"],
            bucket=values["S3_BUCKET"],
            region=values["S3_REGION"],
            access_key=values["S3_ACCESS_KEY"],
            secret_key=values["S3_SECRET_KEY"],
        )
    except (OSError, ValueError, UnicodeError, TypeError):
        raise ImageConfigurationError(
            "Image storage configuration unavailable"
        ) from None
