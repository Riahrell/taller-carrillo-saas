"""Explicit manual UI fixture in an isolated ephemeral Compose project.

Run python3 -m scripts.browser_qa. No credentials on stdout. Finish by touching
/tmp/carrillo-browser-finished inside the printed fixture container.
"""

import tempfile
import uuid
from pathlib import Path

from scripts.ci import ROOT, Pipeline


def main() -> None:
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    project = "carrillo-ci-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="browser-", dir=local) as folder:
        pipeline = Pipeline(ROOT, Path(folder), project)
        try:
            pipeline.validate_isolation()
            pipeline.compose("build", "backend", "tests", "frontend", timeout=1200)
            pipeline.compose("up", "-d", "--wait", "db")
            pipeline.compose("run", "--rm", "migrate")
            pipeline.compose("up", "-d", "--wait", "proxy")
            address = pipeline.compose(
                "port", "proxy", "8080", capture=True
            ).stdout.strip()
            pipeline.bind_origin(address)
            container = project + "-browser-fixture"
            print(f"Browser QA: http://{address}/; fixture: {container}", flush=True)
            pipeline.compose(
                "run",
                "--rm",
                "--name",
                container,
                "-e",
                "RUN_BROWSER_INTEGRATION=1",
                "tests",
                "python",
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-s",
                "tests/integration/browser_fixture.py",
                timeout=1900,
            )
        finally:
            pipeline.cleanup()


if __name__ == "__main__":
    main()
