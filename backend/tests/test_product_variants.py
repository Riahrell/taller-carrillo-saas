"""Bounded WebP detail/thumbnail, alpha, metadata and worker-envelope checks."""

import hashlib
import random
import struct
from io import BytesIO

import pytest
from PIL import Image

from app.modules.catalog.image_processing import (
    ImageProcessingUnavailable,
    _decode_variants,
    process_product_variants,
)
from app.modules.catalog.images import prepare_product_variants, webp_dimensions


def encoded(size=(640, 480), *, exif=None):
    payload = random.Random(42).randbytes(size[0] * size[1] * 3)
    with Image.frombytes("RGB", size, payload) as image, BytesIO() as stream:
        image.save(stream, "PNG", **({"exif": exif} if exif else {}))
        return stream.getvalue()


def test_two_static_webp_variants_and_actual_size_reduction():
    raw = encoded()
    detail = process_product_variants(raw)
    thumb = detail.thumbnail
    assert detail.content_type == thumb.content_type == "image/webp"
    assert (detail.width, detail.height) == (640, 480)
    assert (thumb.width, thumb.height) == (300, 225)
    assert len(thumb.content) < len(detail.content) < len(raw)
    for item in (detail, thumb):
        assert item.sha256 == hashlib.sha256(item.content).hexdigest()
        assert webp_dimensions(item.content) == (item.width, item.height)
        with Image.open(BytesIO(item.content)) as image:
            assert image.format == "WEBP" and image.n_frames == 1
            assert not image.getexif()
            assert not ({"exif", "icc_profile", "xmp"} & set(image.info))


def test_orientation_and_no_enlargement_or_private_metadata():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private location"
    detail = prepare_product_variants(encoded((12, 8), exif=exif))
    assert (detail.width, detail.height) == (8, 12)
    assert (detail.thumbnail.width, detail.thumbnail.height) == (8, 12)
    assert b"private location" not in detail.content + detail.thumbnail.content


def test_transparency_is_preserved():
    with Image.new("RGBA", (12, 8), (12, 24, 36, 128)) as source, BytesIO() as output:
        source.save(output, "PNG")
        detail = prepare_product_variants(output.getvalue())
    for item in (detail, detail.thumbnail):
        with Image.open(BytesIO(item.content)) as image:
            assert image.getpixel((0, 0))[3] == 128


def test_worker_envelope_rejects_wrong_sizes_dimensions_or_extra_data():
    detail = prepare_product_variants(encoded((12, 8)))
    thumb = detail.thumbnail
    body = detail.content + thumb.content
    header = (
        len(detail.content),
        detail.width,
        detail.height,
        len(thumb.content),
        thumb.width,
        thumb.height,
    )
    for bad in (
        b"invalid",
        b"TCWEBP1\n" + struct.pack(">6I", *header) + body + b"extra",
        b"TCWEBP1\n" + struct.pack(">6I", *header[:4], 301, header[5]) + body,
    ):
        with pytest.raises(ImageProcessingUnavailable):
            _decode_variants(bad)


def test_webp_rejects_metadata_chunks_and_wrong_riff_length():
    detail = prepare_product_variants(encoded((12, 8)))
    bad = bytearray(detail.content)
    bad[4:8] = (1).to_bytes(4, "little")
    with pytest.raises(ValueError):
        webp_dimensions(bytes(bad))
    with pytest.raises(ValueError):
        webp_dimensions(detail.content + b"EXIF")
