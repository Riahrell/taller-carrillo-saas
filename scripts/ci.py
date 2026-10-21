"""Run the same isolated validation locally and in GitHub Actions."""

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from scripts.setup_local import generate

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PATTERN = re.compile(r"carrillo-ci-[0-9a-f]{12}")


def isolated_environment() -> dict[str, str]:
    """Do not let local Compose or application settings override test config."""
    excluded = {
        "BACKEND_IMAGE",
        "FRONTEND_IMAGE",
        "POSTGRES_IMAGE",
        "PROXY_IMAGE",
        "APP_ENV",
        "DB_NAME",
        "DB_SECRETS_DIR",
        "CI_PROJECT_NAME",
        "PUBLIC_ORIGIN",
        "TRUSTED_PROXY_HOST",
        "RUN_PROXY_INTEGRATION",
        "RUN_BROWSER_INTEGRATION",
        "PRODUCT_IMAGES_CONFIG_FILE",
        "S3_CONFIG_FILE",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key not in excluded
        and not key.startswith(
            ("COMPOSE_", "DATABASE_URL", "MIGRATION_DATABASE_URL", "CSRF_SIGNING_KEY")
        )
    }


class Pipeline:
    def __init__(self, root: Path, workspace: Path, project: str) -> None:
        if not PROJECT_PATTERN.fullmatch(project):
            raise ValueError("Refusing a non-CI project name")
        if not workspace.resolve().is_relative_to((root / ".local").resolve()):
            raise ValueError("CI workspace must be inside the ignored local directory")
        self.root = root
        self.project = project
        self.env = isolated_environment()
        env_file = generate("test", base=workspace)
        with env_file.open("a") as stream:
            stream.write(f"CI_PROJECT_NAME={project}\n")
        self.prefix = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "-f",
            "compose.yaml",
            "-f",
            "compose.test.yaml",
            "-f",
            "compose.ci.yaml",
            "--profile",
            "test",
            "--profile",
            "tools",
            "--profile",
            "ci",
        ]

    def run(self, args: list[str], *, capture: bool = False, timeout: int = 600):
        return subprocess.run(
            args,
            check=True,
            cwd=self.root,
            env=self.env,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )

    def compose(self, *args: str, capture: bool = False, timeout: int = 600):
        return self.run([*self.prefix, *args], capture=capture, timeout=timeout)

    def validate_isolation(self) -> None:
        config = json.loads(
            self.compose("config", "--format", "json", capture=True).stdout
        )
        services = config["services"]
        db = services["db"]
        if (
            config["name"] != self.project
            or db.get("volumes")
            or "/var/lib/postgresql" not in db.get("tmpfs", [])
            or db["environment"]["POSTGRES_DB"] != "carrillo_test"
            or services["backend"]["environment"]["APP_ENV"] != "test"
            or not config["networks"]["private"]["internal"]
            or not config["networks"]["api"]["internal"]
            or set(services["backend"]["networks"]) != {"private", "api"}
            or set(services["frontend"]["networks"]) != {"edge"}
            or set(services["proxy"]["networks"]) != {"edge", "api"}
        ):
            raise RuntimeError("Refusing a non-isolated CI configuration")
        for name in ("db", "backend", "frontend"):
            if services[name].get("ports"):
                raise RuntimeError("Only the CI proxy may publish a port")
        ports = services["proxy"].get("ports", [])
        if (
            len(ports) != 1
            or ports[0].get("host_ip") != "127.0.0.1"
            or ports[0].get("published") not in (None, "", "0", 0)
        ):
            raise RuntimeError("CI proxy must use an ephemeral loopback port")
        for name in (
            "db",
            "proxy",
            "backend",
            "tests",
            "migrate",
            "frontend",
            "frontend-tests",
        ):
            if not services[name]["image"].startswith(self.project + "-"):
                raise RuntimeError("Refusing shared development image tags")

    def steps(self) -> None:
        self.validate_isolation()
        self.run([sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests"])
        self.run([sys.executable, "-m", "scripts.security"], timeout=900)
        self.run(["sh", "-n", "ops/postgres/init.sh"])
        self.compose(
            "build",
            "db",
            "proxy",
            "backend",
            "tests",
            "frontend",
            "frontend-tests",
            timeout=1200,
        )
        self.compose("run", "--rm", "--no-deps", "workflow-lint")
        self.compose("run", "--rm", "--no-deps", "backend-lint")
        self.compose("run", "--rm", "--no-deps", "frontend-tests")
        self.compose("up", "-d", "--wait", "--wait-timeout", "90", "db")
        self.compose("run", "--rm", "migrate")
        self.compose("run", "--rm", "tests", timeout=1800)
        self.compose("up", "-d", "--wait", "--wait-timeout", "90", "proxy")
        address = self.compose("port", "proxy", "8080", capture=True).stdout.strip()
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", address):
            raise RuntimeError("Unexpected CI proxy address")
        self.bind_origin(address)
        self.compose(
            "run",
            "--rm",
            "-e",
            "RUN_PROXY_INTEGRATION=1",
            "tests",
            "python",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "tests/integration/proxy_access.py",
        )
        self.check_access_logs()
        self.run(
            [
                sys.executable,
                "scripts/smoke_web.py",
                "--base-url",
                f"http://{address}",
            ]
        )

        self.scan_runtime_images()

    def scan_runtime_images(self) -> None:
        config = json.loads(
            self.compose("config", "--format", "json", capture=True).stdout
        )
        arguments = [sys.executable, "-m", "scripts.image_security"]
        for name in ("backend", "frontend", "db", "proxy"):
            arguments.extend(["--image", config["services"][name]["image"]])
        self.run(arguments, timeout=1200)

    def bind_origin(self, address: str) -> None:
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", address):
            raise RuntimeError("Unexpected CI proxy address")
        self.env["PUBLIC_ORIGIN"] = f"http://{address}"
        self.compose("up", "-d", "--wait", "--no-deps", "backend")
        # Reload DNS without a container restart, which may reallocate its port.
        self.compose("exec", "-T", "proxy", "nginx", "-s", "reload")
        self.compose("up", "-d", "--wait", "proxy")
        actual = self.compose("port", "proxy", "8080", capture=True).stdout.strip()
        if actual != address:
            raise RuntimeError("CI proxy port changed while binding its origin")

    def check_access_logs(self) -> None:
        logs = self.compose(
            "logs", "--no-color", "backend", "proxy", capture=True
        ).stdout
        if "carrillo-http-log-canary" in logs:
            raise RuntimeError("Access logs exposed the synthetic request canary")

    def cleanup(self) -> None:
        # The name is generated and validated before any operation. Never dev/test.
        self.compose(
            "down", "--volumes", "--remove-orphans", "--timeout", "5", timeout=90
        )
        for suffix in (
            "backend",
            "backend-tests",
            "frontend",
            "frontend-tests",
            "postgres",
            "proxy",
        ):
            subprocess.run(
                ["docker", "image", "rm", f"{self.project}-{suffix}:ci"],
                cwd=self.root,
                env=self.env,
                check=False,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def execute(self) -> None:
        try:
            self.steps()
        finally:
            self.cleanup()


def main() -> int:
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    project = "carrillo-ci-" + uuid.uuid4().hex[:12]
    print(f"CI project: {project}; temporary credentials hidden", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="ci-", dir=local) as directory:
            Pipeline(ROOT, Path(directory), project).execute()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print(
            f"CI failed for {project}; review the preceding validation output.",
            file=sys.stderr,
        )
        return 1
    print(
        "CI passed: security scans, workflow, lint, format, types, tests, "
        "builds, HTTP smoke and runtime image/SBOM scans."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
