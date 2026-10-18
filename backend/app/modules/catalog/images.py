"""Raster sanitization primitive; never expose without an isolated upload worker.

No network, paths, tenant selection or persistence. Hard execution time/memory
limits and concurrency admission belong to the future worker, not this function.
"""

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_INPUT_PIXELS = 20_000_000
MAX_INPUT_SIDE = 8192
MAX_OUTPUT_SIDE = 1600
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")


class InvalidProductImage(ValueError):
    """Safe rejection with no decoder details or attacker-controlled content."""


@dataclass(frozen=True)
class PreparedProductImage:
    content: bytes
    width: int
    height: int
    sha256: str
    content_type: str = "image/png"


class _BoundedOutput(BytesIO):
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_OUTPUT_BYTES:
            raise InvalidProductImage("La imagen procesada supera el límite permitido.")
        return super().write(data)


def _check_header(image: Image.Image) -> None:
    width, height = image.size
    if (
        image.format not in ALLOWED_FORMATS
        or min(width, height) < 1
        or max(width, height) > MAX_INPUT_SIDE
        or width * height > MAX_INPUT_PIXELS
        or getattr(image, "n_frames", 1) != 1
        or getattr(image, "is_animated", False)
    ):
        raise InvalidProductImage("Formato o dimensiones de imagen no permitidos.")


def prepare_product_image(content: bytes) -> PreparedProductImage:
    """Validate actual bytes and emit pixel-only static PNG, preserving alpha.

    Call only inside a resource-limited worker. Pillow warning filters are scoped
    here; do not call concurrently in threads sharing Python warning state.
    """
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_INPUT_BYTES:
        raise InvalidProductImage("Tamaño de archivo no permitido.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=ALLOWED_FORMATS) as probe:
                _check_header(probe)
                probe.verify()
            # verify invalidates the first decoder; reopen and force full decode.
            with Image.open(BytesIO(content), formats=ALLOWED_FORMATS) as source:
                _check_header(source)
                source.load()
                with ImageOps.exif_transpose(source) as oriented:
                    oriented.thumbnail(
                        (MAX_OUTPUT_SIDE, MAX_OUTPUT_SIDE), Image.Resampling.LANCZOS
                    )
                    # New pixel canvas drops EXIF, ICC, XMP, comments and text.
                    with (
                        oriented.convert("RGBA") as pixels,
                        Image.new("RGBA", pixels.size) as clean,
                    ):
                        clean.paste(pixels)
                        with _BoundedOutput() as output:
                            clean.save(output, format="PNG", compress_level=6)
                            prepared = output.getvalue()
                        return PreparedProductImage(
                            content=prepared,
                            width=clean.width,
                            height=clean.height,
                            sha256=hashlib.sha256(prepared).hexdigest(),
                        )
    except InvalidProductImage:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        raise InvalidProductImage(
            "El archivo no es una imagen estática válida."
        ) from None
