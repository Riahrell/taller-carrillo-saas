"""Credential-preserving upgrade and generation of local HTTP secrets."""

import re
import tempfile
import unittest
from pathlib import Path

from scripts.setup_http import upgrade
from scripts.setup_local import generate


class HttpSetupTests(unittest.TestCase):
    def test_legacy_upgrade_preserves_database_credentials_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = generate("test", base=base)
            secret_dir = config.parent / "secrets"
            (secret_dir / "csrf_signing_key").unlink()
            config.write_text(
                "\n".join(
                    line
                    for line in config.read_text().splitlines()
                    if not line.startswith("PUBLIC_ORIGIN=")
                )
                + "\n"
            )
            before = {p.name: p.read_bytes() for p in secret_dir.iterdir()}
            upgrade("test", base=base)
            for name, content in before.items():
                self.assertTrue(
                    (secret_dir / name).read_bytes() == content, "DB credential changed"
                )
            key = (secret_dir / "csrf_signing_key").read_bytes()
            self.assertTrue(bool(re.fullmatch(rb"[0-9a-f]{64}\n", key)))
            upgraded = config.read_bytes()
            upgrade("test", base=base)
            self.assertTrue((secret_dir / "csrf_signing_key").read_bytes() == key)
            self.assertEqual(config.read_bytes(), upgraded)

    def test_new_environments_receive_independent_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            a, b = generate("test", base=base), generate("development", base=base)
            self.assertTrue(
                (a.parent / "secrets/csrf_signing_key").read_bytes()
                != (b.parent / "secrets/csrf_signing_key").read_bytes()
            )
            self.assertIn("PUBLIC_ORIGIN=http://localhost:8080", a.read_text())

    def test_unexpected_secret_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = generate("test", base=base)
            config.write_text("DB_SECRETS_DIR=/unexpected\n")
            with self.assertRaises(ValueError):
                upgrade("test", base=base)


if __name__ == "__main__":
    unittest.main()
