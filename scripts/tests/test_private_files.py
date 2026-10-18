import os
import tempfile
import unittest
from pathlib import Path

from scripts.private_files import write_mount_secret
from scripts.setup_local import generate


class PrivateMountTests(unittest.TestCase):
    def test_restrictive_umask_does_not_break_unprivileged_secret_mounts(self):
        with tempfile.TemporaryDirectory() as folder:
            previous = os.umask(0o077)
            try:
                config = generate("test", base=Path(folder))
            finally:
                os.umask(previous)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            directory = config.parent / "secrets"
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            for path in directory.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o444)
                self.assertTrue(path.stat().st_size)

    def test_public_directory_is_rejected_before_creating_a_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            directory.chmod(0o755)
            target = directory / "secret"
            with self.assertRaises(ValueError):
                write_mount_secret(target, "synthetic")
            self.assertFalse(target.exists())

    def test_existing_secret_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "secret"
            write_mount_secret(target, "synthetic-original")
            with self.assertRaises(FileExistsError):
                write_mount_secret(target, "synthetic-replacement")
            self.assertEqual(target.read_text(), "synthetic-original")


if __name__ == "__main__":
    unittest.main()
