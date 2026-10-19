"""Explicit manual UI fixture in an isolated ephemeral Compose project.

Run python3 -m scripts.browser_qa. No credentials on stdout. Finish by touching
/tmp/carrillo-browser-finished inside the printed fixture container.
"""

import argparse
import json
import os
import stat
import tempfile
import uuid
from pathlib import Path

from scripts.ci import ROOT, Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-images-config", type=Path)
    args = parser.parse_args()
    storage_config = args.product_images_config
    if storage_config is not None:
        storage_config = storage_config.resolve(strict=True)
        parent = storage_config.parent.stat()
        source = storage_config.stat()
        if (
            storage_config.is_relative_to(ROOT)
            or not stat.S_ISREG(source.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) & 0o077
            or stat.S_IMODE(source.st_mode) & 0o022
        ):
            parser.error("Use an external configuration in an owner-only directory")
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    project = "carrillo-ci-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="browser-", dir=local) as folder:
        pipeline = Pipeline(ROOT, Path(folder), project)
        try:
            pipeline.validate_isolation()
            if storage_config is not None:
                baseline = json.loads(
                    pipeline.compose("config", "--format", "json", capture=True).stdout
                )
                pipeline.prefix.extend(["-f", "compose.images.yaml"])
                override = Path(folder) / "images-qa.yaml"
                override.write_text(
                    json.dumps(
                        {
                            "services": {
                                "image-worker": {
                                    "image": baseline["services"]["backend"]["image"]
                                }
                            }
                        }
                    )
                )
                pipeline.prefix.extend(["-f", str(override)])
                pipeline.env["S3_CONFIG_FILE"] = str(storage_config)
                image_config = json.loads(
                    pipeline.compose("config", "--format", "json", capture=True).stdout
                )
                for name, service in baseline["services"].items():
                    if name != "backend" and image_config["services"][name] != service:
                        raise RuntimeError("Unexpected image QA service change")
                backend = image_config["services"]["backend"]
                if (
                    set(backend["networks"]) != {"private", "api", "image_storage"}
                    or backend.get("ports")
                    or image_config["services"]["db"].get("volumes")
                    or image_config["services"]["image-worker"].get("ports")
                    or image_config["services"]["image-worker"]["image"]
                    != backend["image"]
                    or image_config["secrets"]["product_images"]["file"]
                    != str(storage_config)
                ):
                    raise RuntimeError("Image QA isolation verification failed")
            pipeline.compose("build", "backend", "tests", "frontend", timeout=1200)
            pipeline.compose("up", "-d", "--wait", "db")
            pipeline.compose("run", "--rm", "migrate")
            pipeline.compose("up", "-d", "--wait", "proxy")
            if storage_config is not None:
                pipeline.compose("up", "-d", "image-worker")
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
