import hashlib
from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from app.modules.catalog import images
from app.modules.catalog.images import InvalidProductImage, prepare_product_image


def encoded(format="PNG", size=(12, 8), **options):
    with Image.new("RGB", size, (10, 90, 150)) as image, BytesIO() as output:
        image.save(output, format=format, **options)
        return output.getvalue()


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_actual_raster_bytes_become_static_png_with_integrity(format):
    prepared = prepare_product_image(encoded(format))
    assert (prepared.width, prepared.height) == (12, 8)
    assert prepared.content_type == "image/png"
    assert prepared.sha256 == hashlib.sha256(prepared.content).hexdigest()
    with Image.open(BytesIO(prepared.content)) as result:
        result.load()
        assert result.format == "PNG"
        assert result.mode == "RGBA"
        assert result.n_frames == 1
        assert result.info == {}


@pytest.mark.parametrize("payload", [b"", b"not an image", b"<svg/>", b"%PDF-1.7"])
def test_invalid_content_is_rejected_without_echoing_input(payload):
    with pytest.raises(InvalidProductImage) as error:
        prepare_product_image(payload)
    assert str(error.value) in {
        "Tamaño de archivo no permitido.",
        "El archivo no es una imagen estática válida.",
    }


@pytest.mark.parametrize("payload", [None, "image", bytearray(b"image")])
def test_only_immutable_bounded_bytes_are_accepted(payload):
    with pytest.raises(InvalidProductImage, match="Tamaño"):
        prepare_product_image(payload)


def test_input_size_rejected_before_decoder(monkeypatch):
    monkeypatch.setattr(images, "MAX_INPUT_BYTES", 10)

    def forbidden(*args, **kwargs):
        pytest.fail("Oversized input reached decoder")

    monkeypatch.setattr(Image, "open", forbidden)
    with pytest.raises(InvalidProductImage, match="Tamaño"):
        prepare_product_image(b"x" * 11)


@pytest.mark.parametrize("limit", ["MAX_INPUT_PIXELS", "MAX_INPUT_SIDE"])
def test_dimensions_rejected_before_pixel_decode(monkeypatch, limit):
    payload = encoded(size=(20, 20))
    monkeypatch.setattr(images, limit, 10)
    with pytest.raises(InvalidProductImage, match="dimensiones"):
        prepare_product_image(payload)


@pytest.mark.parametrize("format", ["GIF", "BMP", "TIFF"])
def test_other_static_formats_are_rejected(format):
    with pytest.raises(InvalidProductImage):
        prepare_product_image(encoded(format))


@pytest.mark.parametrize("format", ["PNG", "WEBP", "GIF"])
def test_animated_images_are_rejected(format):
    with (
        Image.new("RGB", (10, 10), "red") as first,
        Image.new("RGB", (10, 10), "blue") as second,
        BytesIO() as output,
    ):
        first.save(
            output, format=format, save_all=True, append_images=[second], duration=100
        )
        payload = output.getvalue()
    with pytest.raises(InvalidProductImage):
        prepare_product_image(payload)


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_truncation_is_rejected(format):
    payload = encoded(format)
    with pytest.raises(InvalidProductImage):
        prepare_product_image(payload[: len(payload) // 2])


def test_orientation_is_applied_and_exif_is_removed():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private metadata"
    prepared = prepare_product_image(encoded("JPEG", exif=exif))
    assert (prepared.width, prepared.height) == (8, 12)
    with Image.open(BytesIO(prepared.content)) as result:
        assert not result.getexif()
        assert not result.info
    assert b"private metadata" not in prepared.content


def test_png_text_icc_exif_and_transparency():
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "private comment")
    with Image.new("RGBA", (2, 2), (255, 0, 0, 0)) as source, BytesIO() as output:
        source.putpixel((0, 0), (10, 20, 30, 128))
        source.save(output, "PNG", pnginfo=metadata, icc_profile=b"private profile")
        payload = output.getvalue()
    prepared = prepare_product_image(payload)
    with Image.open(BytesIO(prepared.content)) as result:
        assert not result.info
        assert result.getpixel((0, 0)) == (10, 20, 30, 128)
        assert result.getpixel((1, 1))[3] == 0


def test_palette_transparency_is_preserved():
    with Image.new("P", (2, 2)) as source, BytesIO() as output:
        source.save(output, "PNG", transparency=0)
        prepared = prepare_product_image(output.getvalue())
    with Image.open(BytesIO(prepared.content)) as result:
        assert result.getpixel((0, 0))[3] == 0
        assert not result.info


def test_resize_preserves_aspect_and_never_upscales():
    prepared = prepare_product_image(encoded(size=(3200, 1600)))
    assert (prepared.width, prepared.height) == (1600, 800)
    assert len(prepared.content) <= images.MAX_OUTPUT_BYTES
    small = prepare_product_image(encoded(size=(1, 1)))
    assert (small.width, small.height) == (1, 1)


@pytest.mark.parametrize("limit", [1, 100])
def test_pillow_bomb_error_and_warning_are_rejections(monkeypatch, limit):
    payload = encoded(size=(11, 10))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", limit)
    with pytest.raises(InvalidProductImage):
        prepare_product_image(payload)


def test_output_is_bounded_while_encoding(monkeypatch):
    monkeypatch.setattr(images, "MAX_OUTPUT_BYTES", 20)
    with pytest.raises(InvalidProductImage, match="procesada supera"):
        prepare_product_image(encoded())
