"""Private SMTP preparation never executes dotenv content or overwrites keys."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.setup_smtp import prepare


class SmtpPreparationTests(unittest.TestCase):
    def test_private_configuration_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resend.env"
            source.write_text("RESEND_API_KEY=synthetic-only-test-value-123\n")
            source.chmod(0o600)
            target = root / "smtp"
            prepare(source, target, "Taller Carrillo <sender@example.com>")
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            config = json.loads((target / "smtp.json").read_text())
            self.assertNotIn("synthetic-only-test-value-123", json.dumps(config))
            self.assertEqual(config["port"], 465)
            self.assertEqual(config["password_file"], "/run/secrets/smtp_password")
            with self.assertRaises(FileExistsError):
                prepare(source, target, "sender@example.com")

    def test_rejects_public_source_and_shell_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resend.env"
            source.write_text("RESEND_API_KEY=synthetic-only-test-value-123\n")
            source.chmod(0o644)
            with self.assertRaises(ValueError):
                prepare(source, root / "smtp", "sender@example.com")
            source.chmod(0o600)
            source.write_text("RESEND_API_KEY=$(touch unexpected-file)\n")
            with self.assertRaises(ValueError):
                prepare(source, root / "smtp", "sender@example.com")
            self.assertFalse((root / "smtp").exists())
