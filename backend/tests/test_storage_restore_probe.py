from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from app.tools.storage_restore_probe import is_missing, validate_target


@pytest.mark.parametrize(
    "bucket,expected,forbidden,endpoint",
    [
        ("real-images", "restore-test", "real-images", "https://storage.example.com"),
        ("real-images", "real-images", "real-images", "https://storage.example.com"),
        ("restore-test", "restore-test", "real-images", "https://other.example.com"),
    ],
)
def test_reject_unexpected_or_forbidden_destination(
    bucket, expected, forbidden, endpoint
):
    settings = SimpleNamespace(bucket=bucket, endpoint="https://storage.example.com")
    with pytest.raises(ValueError, match="unexpected"):
        validate_target(settings, expected, forbidden, endpoint)


def test_accept_only_explicit_separate_destination():
    settings = SimpleNamespace(
        bucket="restore-test", endpoint="https://storage.example.com"
    )
    validate_target(settings, "restore-test", "real-images", settings.endpoint)


@pytest.mark.parametrize("status,missing", [(404, True), (403, None), (500, None)])
def test_only_404_proves_absence(status, missing):
    client = Mock()
    client.head_object.side_effect = ClientError(
        {
            "Error": {"Code": str(status)},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "HeadObject",
    )
    storage = SimpleNamespace(_client=client, _bucket="restore-test")
    reference = SimpleNamespace(key="only-run-owned-key")
    if missing:
        assert is_missing(storage, reference) is True
    else:
        with pytest.raises(ValueError, match="not confirmed"):
            is_missing(storage, reference)
    client.head_object.assert_called_once_with(Bucket="restore-test", Key=reference.key)


def test_existing_object_does_not_prove_loss():
    storage = SimpleNamespace(_client=Mock(), _bucket="restore-test")
    assert is_missing(storage, SimpleNamespace(key="own-key")) is False


@pytest.mark.parametrize("invalid", ["bucket", "database"])
def test_invalid_target_never_constructs_provider_client(monkeypatch, invalid):
    from pydantic import SecretStr

    from app.tools import storage_restore_probe as probe

    settings = SimpleNamespace(
        bucket="real-images" if invalid == "bucket" else "restore-test",
        endpoint="https://storage.example.com",
    )
    config = SimpleNamespace(
        app_env="production", database_url=SecretStr("postgresql://db/carrillo_prod")
    )
    client, engine = Mock(), Mock()
    monkeypatch.setattr(probe, "PrivateImageStorage", client)
    monkeypatch.setattr(probe, "build_engine", engine)
    monkeypatch.setattr(probe, "load_image_configuration", lambda _: settings)
    monkeypatch.setattr(probe, "load_settings", lambda: config)
    monkeypatch.setattr(
        "sys.argv",
        [
            "probe",
            "upload",
            "--storage-config",
            "/unused/config",
            "--expected-bucket",
            "restore-test",
            "--forbidden-bucket",
            "real-images",
            "--expected-endpoint",
            settings.endpoint,
            "--directory",
            "/unused/archive",
        ],
    )
    assert probe.main() == 1
    client.assert_not_called()
    engine.assert_not_called()
