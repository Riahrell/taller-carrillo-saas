import secrets

import pytest

from app.modules.catalog.image_configuration import (
    ImageConfigurationError,
    load_image_configuration,
)


def config_file(tmp_path):
    path = tmp_path / "storage.env"
    path.write_text(
        "S3_ENDPOINT=https://storage.example.com\nS3_BUCKET=private-images\n"
        "S3_REGION=default\nS3_ADDRESSING=path\n"
        f"S3_ACCESS_KEY={secrets.token_hex(16)}\nS3_SECRET_KEY={secrets.token_hex(32)}\n"
    )
    path.chmod(0o600)
    return path


def test_external_configuration_is_explicit_and_secrets_are_redacted(tmp_path):
    path = config_file(tmp_path)
    settings = load_image_configuration(path)
    assert settings.bucket == "private-images"
    assert settings.secret_key.get_secret_value() not in repr(settings)


@pytest.mark.parametrize(
    "change", ["readable", "symlink", "unknown", "missing", "oversize"]
)
def test_unsafe_configuration_fails_without_secret_details(tmp_path, change):
    path = config_file(tmp_path)
    original = path.read_text()
    if change == "readable":
        path.chmod(0o644)
    elif change == "symlink":
        link = tmp_path / "link.env"
        link.symlink_to(path)
        path = link
    elif change == "unknown":
        path.write_text(original + "UNEXPECTED=value\n")
    elif change == "missing":
        path = tmp_path / "missing.env"
    else:
        path.write_text("x" * 16385)
    with pytest.raises(ImageConfigurationError) as error:
        load_image_configuration(path)
    assert str(error.value) == "Image storage configuration unavailable"
