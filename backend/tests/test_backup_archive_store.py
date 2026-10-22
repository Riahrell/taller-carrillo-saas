import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.tools.backup_archive_store import upload

KEY = "respaldos/20261022T120000Z-" + "a" * 32 + ".tar.gpg"


def test_private_encrypted_copy_is_verified_after_upload(tmp_path):
    path = tmp_path / "copy.gpg"
    payload = b"\x85synthetic encrypted packet"
    path.write_bytes(payload)
    path.chmod(0o600)
    client = Mock()
    client.get_object.return_value = {
        "Body": io.BytesIO(payload),
        "ContentLength": len(payload),
        "ContentType": "application/pgp-encrypted",
    }
    storage = SimpleNamespace(_client=client, _bucket="restore-test")
    upload(storage, path, KEY)
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "restore-test"
    assert kwargs["ACL"] == "private"
    assert kwargs["IfNoneMatch"] == "*"
    assert kwargs["Key"] == KEY
    client.get_object.assert_called_once_with(Bucket="restore-test", Key=KEY)


@pytest.mark.parametrize(
    "unsafe", ["plaintext", "public-file", "wrong-key", "corrupt-readback"]
)
def test_invalid_copies_cannot_succeed(tmp_path, unsafe):
    path = tmp_path / "copy.gpg"
    payload = b"\x85synthetic encrypted packet"
    path.write_bytes(b"plaintext database" if unsafe == "plaintext" else payload)
    path.chmod(0o644 if unsafe == "public-file" else 0o600)
    client = Mock()
    client.get_object.return_value = {
        "Body": io.BytesIO(b"damaged"),
        "ContentLength": 7,
        "ContentType": "application/pgp-encrypted",
    }
    with pytest.raises(ValueError):
        upload(
            SimpleNamespace(_client=client, _bucket="restore-test"),
            path,
            "../other" if unsafe == "wrong-key" else KEY,
        )
    if unsafe != "corrupt-readback":
        client.put_object.assert_not_called()
