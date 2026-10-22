import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.production_backup import WRITERS, backup


class BackupResumeTests(unittest.TestCase):
    def test_failed_dump_or_stop_always_resumes_writers(self):
        for fail_at in ("stop", "exec"):
            with (
                self.subTest(fail_at=fail_at),
                tempfile.TemporaryDirectory() as directory,
            ):
                config = {
                    "directory": str(Path(directory) / "backups"),
                    "compose_wrapper": "/operator/compose",
                    "recipient": "A" * 40,
                    "database": "carrillo_prod",
                }

                def command(args, *, selected=fail_at, **kwargs):
                    if args[1] == selected:
                        raise RuntimeError("injected")
                    return b""

                with (
                    patch("scripts.production_backup.run", side_effect=command) as run,
                    self.assertRaises(RuntimeError),
                ):
                    backup(config)
                self.assertTrue(
                    any(
                        call.args[0][1:3] == ["up", "-d"]
                        and call.args[0][-3:] == WRITERS
                        for call in run.call_args_list
                    )
                )
