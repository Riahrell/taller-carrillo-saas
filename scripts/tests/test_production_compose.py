import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ci import ROOT, isolated_environment
from scripts.setup_local import generate


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI required")
class ProductionComposeTests(unittest.TestCase):
    def test_all_production_processes_receive_real_secret_mounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envfile = generate("production", base=root)
            placeholder = root / "config"
            placeholder.write_text("")
            extras = {
                name: str(placeholder)
                for name in [
                    "S3_CONFIG_FILE",
                    "SMTP_CONFIG_FILE",
                    "SMTP_PASSWORD_FILE",
                    "TLS_CERTIFICATE_FILE",
                    "TLS_PRIVATE_KEY_FILE",
                    "TLS_PROXY_CONFIG_FILE",
                ]
            }
            extras["ACME_WEBROOT_DIR"] = str(root)
            extras.update(
                {
                    name: "checked/" + name.lower() + ":release"
                    for name in [
                        "BACKEND_IMAGE",
                        "FRONTEND_IMAGE",
                        "POSTGRES_IMAGE",
                        "PROXY_IMAGE",
                    ]
                }
            )
            with envfile.open("a") as output:
                output.write(
                    "".join(f"{key}={value}\n" for key, value in extras.items())
                )
            command = [
                "docker",
                "compose",
                "--project-name",
                "carrillo-production-check",
                "--env-file",
                str(envfile),
            ]
            for name in [
                "compose.yaml",
                "compose.images.yaml",
                "compose.identity.yaml",
                "compose.production.yaml",
            ]:
                command.extend(["-f", str(ROOT / name)])
            result = subprocess.run(
                [*command, "--profile", "tools", "config", "--format", "json"],
                cwd=ROOT,
                env=isolated_environment(),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            services = json.loads(result.stdout)["services"]
            for name in ["backend", "migrate", "identity-worker", "image-worker"]:
                self.assertEqual(services[name]["environment"]["APP_ENV"], "production")
                self.assertEqual(
                    services[name]["environment"]["IDENTITY_KEYS_FILE"],
                    "/run/secrets/identity_keys",
                )
                self.assertIn(
                    "identity_keys",
                    {entry["source"] for entry in services[name]["secrets"]},
                )
                self.assertFalse(services[name].get("ports"))
            self.assertIn(
                "migration_database_url",
                {entry["source"] for entry in services["migrate"]["secrets"]},
            )
            self.assertEqual(
                {int(x["published"]) for x in services["proxy"]["ports"]}, {80, 443}
            )
