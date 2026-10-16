"""Safety regressions for CI orchestration; these tests never invoke Docker."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import Pipeline, isolated_environment
from scripts.setup_local import generate


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.workspace = self.root / ".local" / "ci-run"
        self.workspace.mkdir(parents=True)

    def pipeline(self) -> Pipeline:
        return Pipeline(self.root, self.workspace, "carrillo-ci-012345abcdef")

    def test_development_project_is_rejected_before_creating_credentials(self) -> None:
        with self.assertRaises(ValueError):
            Pipeline(self.root, self.workspace, "carrillo-dev")
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_workspace_outside_ignored_directory_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Pipeline(self.root, self.root, "carrillo-ci-012345abcdef")

    def test_failure_still_cleans_up_and_propagates_the_failure(self) -> None:
        pipeline = self.pipeline()
        with (
            patch.object(
                pipeline, "steps", side_effect=subprocess.CalledProcessError(1, "test")
            ),
            patch.object(pipeline, "cleanup") as cleanup,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            pipeline.execute()
        cleanup.assert_called_once()

    def test_success_also_cleans_up(self) -> None:
        pipeline = self.pipeline()
        with (
            patch.object(pipeline, "steps"),
            patch.object(pipeline, "cleanup") as cleanup,
        ):
            pipeline.execute()
        cleanup.assert_called_once()

    def test_local_settings_cannot_override_the_test_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "DB_NAME": "operational",
                "COMPOSE_PROJECT_NAME": "carrillo-dev",
                "DATABASE_URL": "do-not-use",
                "PUBLIC_ORIGIN": "https://untrusted.example",
                "CSRF_SIGNING_KEY": "do-not-use",
            },
        ):
            environment = isolated_environment()
        for key in (
            "APP_ENV",
            "DB_NAME",
            "COMPOSE_PROJECT_NAME",
            "DATABASE_URL",
            "PUBLIC_ORIGIN",
            "CSRF_SIGNING_KEY",
        ):
            self.assertNotIn(key, environment)

    def test_origin_binding_keeps_proxy_port_and_reloads_without_restart(self):
        pipeline = self.pipeline()
        address = "127.0.0.1:49123"
        with patch.object(pipeline, "compose") as compose:
            compose.return_value.stdout = address + "\n"
            pipeline.bind_origin(address)
        self.assertEqual(pipeline.env["PUBLIC_ORIGIN"], f"http://{address}")
        calls = [call.args for call in compose.call_args_list]
        self.assertIn(("exec", "-T", "proxy", "nginx", "-s", "reload"), calls)
        self.assertFalse(any(call[0] == "restart" for call in calls))

    def test_origin_binding_rejects_a_changed_published_port(self):
        pipeline = self.pipeline()
        with patch.object(pipeline, "compose") as compose:
            compose.return_value.stdout = "127.0.0.1:49124\n"
            with self.assertRaisesRegex(RuntimeError, "port changed"):
                pipeline.bind_origin("127.0.0.1:49123")

    def test_credentials_are_not_overwritten(self) -> None:
        path = generate("test", base=self.workspace)
        secret = path.parent / "secrets" / "database_url"
        original = secret.read_bytes()
        with self.assertRaises(FileExistsError):
            generate("test", base=self.workspace)
        self.assertTrue(secret.read_bytes() == original, "Credential file changed")


if __name__ == "__main__":
    unittest.main()
