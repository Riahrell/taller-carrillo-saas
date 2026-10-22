import unittest
from unittest.mock import patch

from scripts import restore_drill


class RestoreArgumentsTest(unittest.TestCase):
    def test_partial_external_configuration_is_rejected_before_docker(self):
        for option in (
            "--storage-config",
            "--expected-bucket",
            "--forbidden-bucket",
            "--expected-endpoint",
        ):
            with (
                self.subTest(option=option),
                patch("sys.argv", ["drill", option, "x"]),
                patch.object(restore_drill, "Pipeline") as pipeline,
                patch("sys.stderr"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    restore_drill.main()
                self.assertEqual(raised.exception.code, 2)
                pipeline.assert_not_called()
