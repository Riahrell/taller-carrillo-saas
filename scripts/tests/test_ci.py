"""Safety regressions for CI orchestration; these tests never invoke Docker."""

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
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

    def test_timeout_reports_limit_without_disclosing_command_or_output(self):
        pipeline = self.pipeline()
        error = subprocess.TimeoutExpired(["private-command"], 17, "private-output")
        diagnostic = io.StringIO()
        with (
            patch("scripts.ci.subprocess.run", side_effect=error),
            redirect_stderr(diagnostic),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            pipeline.run(["private-command"], timeout=17)
        self.assertEqual(
            diagnostic.getvalue(), "CI command exceeded its 17-second limit.\n"
        )

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

    def test_runtime_gate_scans_application_database_and_proxy_images(self):
        pipeline = self.pipeline()
        images = {
            name: {"image": "candidate-" + name}
            for name in ("backend", "frontend", "db", "proxy")
        }
        with (
            patch.object(pipeline, "compose") as compose,
            patch.object(pipeline, "run") as run,
        ):
            compose.return_value.stdout = json.dumps({"services": images})
            pipeline.scan_runtime_images()
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[1:3], ["-m", "scripts.image_security"])
        self.assertEqual(arguments[3::2], ["--image"] * 4)
        self.assertEqual(set(arguments[4::2]), {x["image"] for x in images.values()})

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
                "TRUSTED_PROXY_HOST": "untrusted.example",
                "RUN_PROXY_INTEGRATION": "1",
                "RUN_BROWSER_INTEGRATION": "1",
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
            "TRUSTED_PROXY_HOST",
            "RUN_PROXY_INTEGRATION",
            "RUN_BROWSER_INTEGRATION",
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

    def test_log_canary_is_rejected_without_echoing_logs(self):
        pipeline = self.pipeline()
        with patch.object(pipeline, "compose") as compose:
            compose.return_value.stdout = "sensitive-data carrillo-http-log-canary"
            with self.assertRaisesRegex(
                RuntimeError, "synthetic request canary"
            ) as caught:
                pipeline.check_access_logs()
            self.assertNotIn("sensitive-data", str(caught.exception))

    def test_logs_without_request_canary_are_accepted(self):
        pipeline = self.pipeline()
        with patch.object(pipeline, "compose") as compose:
            compose.return_value.stdout = "proxy GET /auth/login 401"
            pipeline.check_access_logs()


if __name__ == "__main__":
    unittest.main()
