"""Bounded Linux subprocess execution, without inheriting server credentials.

This is resource isolation, not an OS security sandbox: the child retains the
container UID/filesystem/network permissions. Do not mount storage secrets there.
"""

import hashlib
import struct
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryFile
from threading import BoundedSemaphore

from app.modules.catalog.images import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_SIDE,
    InvalidProductImage,
    PreparedProductImage,
)

_SLOT = BoundedSemaphore(1)
_WORKER = Path(__file__).with_name("image_worker.py")
WALL_TIMEOUT_SECONDS = 10


class ImageProcessingUnavailable(RuntimeError):
    """Busy, timeout, decoder crash or invalid worker output; safe to expose."""


def process_product_image(content: bytes) -> PreparedProductImage:
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_INPUT_BYTES:
        raise InvalidProductImage("Tamaño de archivo no permitido.")
    if sys.platform != "linux" or not _SLOT.acquire(blocking=False):
        raise ImageProcessingUnavailable("Procesamiento de imagen no disponible.")
    try:
        # Anonymous private files bound IPC memory and require no shared paths.
        with TemporaryFile() as source, TemporaryFile() as target:
            source.write(content)
            source.seek(0)
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(_WORKER)],
                stdin=source,
                stdout=target,
                stderr=subprocess.DEVNULL,
                env={"LANG": "C.UTF-8"},
                cwd=_WORKER.parent,
                close_fds=True,
                timeout=WALL_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode == 2:
                raise InvalidProductImage(
                    "El archivo no es una imagen estática válida."
                )
            if result.returncode != 0:
                raise ImageProcessingUnavailable("No se pudo procesar la imagen.")
            target.seek(0)
            payload = target.read(MAX_OUTPUT_BYTES + 1)
        if (
            not 33 <= len(payload) <= MAX_OUTPUT_BYTES
            or payload[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        ):
            raise ImageProcessingUnavailable("Respuesta de imagen no válida.")
        width, height = struct.unpack(">II", payload[16:24])
        if not 1 <= min(width, height) <= max(width, height) <= MAX_OUTPUT_SIDE:
            raise ImageProcessingUnavailable("Respuesta de imagen no válida.")
        return PreparedProductImage(
            payload, width, height, hashlib.sha256(payload).hexdigest()
        )
    except (OSError, subprocess.TimeoutExpired):
        # subprocess.run kills and waits for the child on timeout.
        raise ImageProcessingUnavailable("No se pudo procesar la imagen.") from None
    finally:
        _SLOT.release()
