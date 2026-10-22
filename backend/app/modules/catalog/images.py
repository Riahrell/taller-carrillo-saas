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
    thumbnail: "PreparedProductImage | None" = None


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


MAX_WEBP_BYTES = 4 * 1024 * 1024
MAX_THUMB_BYTES = 256 * 1024
THUMB_SIDE = 300


def webp_dimensions(payload: bytes) -> tuple[int, int]:
    """Small pure parser: static, metadata-free WebP emitted by our worker."""
    if len(payload) < 30 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
        raise ValueError("Invalid WebP")
    if int.from_bytes(payload[4:8], "little") + 8 != len(payload):
        raise ValueError("Invalid WebP length")
    offset, dimensions, data_chunks = 12, None, 0
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValueError("Invalid WebP chunk")
        kind = payload[offset : offset + 4]
        size = int.from_bytes(payload[offset + 4 : offset + 8], "little")
        start, end = offset + 8, offset + 8 + size
        if end > len(payload) or kind not in (b"VP8X", b"ALPH", b"VP8 ", b"VP8L"):
            raise ValueError("Unsupported WebP chunk")
        chunk = payload[start:end]
        if kind == b"VP8X":
            if size != 10 or chunk[0] & 0x2E or dimensions is not None:
                raise ValueError("Animated or metadata-bearing WebP")
            dimensions = (
                int.from_bytes(chunk[4:7], "little") + 1,
                int.from_bytes(chunk[7:10], "little") + 1,
            )
        elif kind == b"VP8 ":
            if size < 10 or chunk[3:6] != b"\x9d\x01\x2a":
                raise ValueError("Invalid WebP frame")
            dimensions = dimensions or (
                int.from_bytes(chunk[6:8], "little") & 0x3FFF,
                int.from_bytes(chunk[8:10], "little") & 0x3FFF,
            )
            data_chunks += 1
        elif kind == b"VP8L":
            if size < 5 or chunk[0] != 0x2F:
                raise ValueError("Invalid lossless frame")
            bits = int.from_bytes(chunk[1:5], "little")
            dimensions = dimensions or (
                (bits & 0x3FFF) + 1,
                ((bits >> 14) & 0x3FFF) + 1,
            )
            data_chunks += 1
        offset = end + (size % 2)
    if (
        offset != len(payload)
        or data_chunks != 1
        or dimensions is None
        or min(dimensions) < 1
    ):
        raise ValueError("Invalid WebP")
    return dimensions


def prepare_product_variants(content: bytes) -> PreparedProductImage:
    """Sanitize once; retain only two bounded WebP derivatives, never raw input."""
    sanitized = prepare_product_image(content)
    with Image.open(BytesIO(sanitized.content), formats=("PNG",)) as pixels:
        pixels.load()

        def encode(side, quality, maximum):
            with pixels.copy() as image, _BoundedOutput() as output:
                image.thumbnail((side, side), Image.Resampling.LANCZOS)
                image.save(output, format="WEBP", quality=quality, method=4)
                payload = output.getvalue()
                if len(payload) > maximum:
                    raise InvalidProductImage(
                        "La imagen procesada supera el límite permitido."
                    )
                if webp_dimensions(payload) != image.size:
                    raise InvalidProductImage("Respuesta de imagen no válida.")
                return PreparedProductImage(
                    payload,
                    image.width,
                    image.height,
                    hashlib.sha256(payload).hexdigest(),
                    "image/webp",
                )

        detail = encode(MAX_OUTPUT_SIDE, 82, MAX_WEBP_BYTES)
        thumbnail = encode(THUMB_SIDE, 75, MAX_THUMB_BYTES)
        return PreparedProductImage(
            detail.content,
            detail.width,
            detail.height,
            detail.sha256,
            detail.content_type,
            thumbnail,
        )
