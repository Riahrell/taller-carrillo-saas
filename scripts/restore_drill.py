"""Full PostgreSQL dump/restore with synthetic MFA and post-restore quarantine."""

import argparse
import json
import os
import secrets
import stat
import tempfile
import uuid
from pathlib import Path

from scripts.ci import ROOT, Pipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-config", type=Path)
    parser.add_argument("--expected-bucket")
    parser.add_argument("--forbidden-bucket")
    parser.add_argument("--expected-endpoint")
    args = parser.parse_args()
    external = args.storage_config is not None
    targets = (
        args.storage_config,
        args.expected_bucket,
        args.forbidden_bucket,
        args.expected_endpoint,
    )
    if any(targets) and not all(targets):
        parser.error(
            "Storage config, expected/forbidden bucket and endpoint "
            "are required together"
        )
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="restore-", dir=local) as directory:
        folder = Path(directory)
        pipeline = Pipeline(ROOT, folder, "carrillo-ci-" + uuid.uuid4().hex[:12])
        probe = folder / "test" / "secrets" / "restore_probe"
        with os.fdopen(
            os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444), "w"
        ) as output:
            json.dump(
                {
                    "user": str(uuid.uuid4()),
                    "credential": str(uuid.uuid4()),
                    "set": str(uuid.uuid4()),
                    "key": secrets.token_hex(32),
                    "real_image_fixture": external,
                },
                output,
            )
        override = folder / "restore.yaml"
        override.write_text(
            json.dumps(
                {
                    "services": {
                        "migrate": {
                            "environment": {
                                "RESTORE_PROBE_FILE": "/run/secrets/restore_probe"
                            },
                            "secrets": ["restore_probe"],
                        }
                    },
                    "secrets": {"restore_probe": {"file": str(probe)}},
                }
            )
        )
        if external:
            descriptor = os.open(args.storage_config, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or info.st_size > 16384
                ):
                    raise ValueError(
                        "Private operator-owned storage configuration required"
                    )
                payload = source.read(16385)
            storage_secret = probe.parent / "restore_storage"
            descriptor = os.open(
                storage_secret, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                os.fchmod(output.fileno(), 0o444)
            artifacts = folder / "artifacts"
            artifacts.mkdir(mode=0o700)
            overlay = json.loads(override.read_text())
            service = overlay["services"]["migrate"]
            service["user"] = f"{os.getuid()}:{os.getgid()}"
            service["secrets"].append("restore_storage")
            service["networks"] = ["private", "restore_egress"]
            service["volumes"] = [f"{artifacts}:/restore-artifacts"]
            overlay["secrets"]["restore_storage"] = {"file": str(storage_secret)}
            overlay["networks"] = {"restore_egress": {}}
            override.write_text(json.dumps(overlay))
        pipeline.prefix.extend(["-f", str(override)])

        def objects(phase):
            pipeline.compose(
                "run",
                "--rm",
                "migrate",
                "python",
                "-m",
                "app.tools.storage_restore_probe",
                phase,
                "--storage-config",
                "/run/secrets/restore_storage",
                "--expected-bucket",
                args.expected_bucket,
                "--forbidden-bucket",
                args.forbidden_bucket,
                "--expected-endpoint",
                args.expected_endpoint,
                "--directory",
                "/restore-artifacts/images",
            )

        seeded = False
        try:
            pipeline.validate_isolation()
            pipeline.compose("build", "backend", timeout=1200)
            pipeline.compose("up", "-d", "--wait", "db")
            pipeline.compose("run", "--rm", "migrate")

            def run(phase):
                pipeline.compose(
                    "run",
                    "--rm",
                    "migrate",
                    "python",
                    "-m",
                    "app.tools.restore_probe",
                    phase,
                )

            run("seed")
            seeded = True
            if external:
                objects("upload")
                objects("archive")
            dump = folder / "snapshot.dump"
            with os.fdopen(
                os.open(dump, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb"
            ) as output:
                import subprocess

                subprocess.run(
                    [
                        *pipeline.prefix,
                        "exec",
                        "-T",
                        "db",
                        "pg_dump",
                        "--username=carrillo_owner",
                        "--dbname=carrillo_test",
                        "--format=custom",
                        "--no-owner",
                    ],
                    stdout=output,
                    check=True,
                    env=pipeline.env,
                    timeout=120,
                )
            if external:
                objects("lose")
            run("mutate")
            run("check-mutated")
            with dump.open("rb") as source:
                subprocess.run(
                    [
                        *pipeline.prefix,
                        "exec",
                        "-T",
                        "db",
                        "pg_restore",
                        "--username=carrillo_owner",
                        "--dbname=carrillo_test",
                        "--clean",
                        "--if-exists",
                        "--no-owner",
                        "--exit-on-error",
                    ],
                    stdin=source,
                    check=True,
                    env=pipeline.env,
                    timeout=120,
                )
            run("check")
            if external:
                objects("restore")
                objects("verify")
                objects("restore")  # conditional replay of existing identical objects
            run("quarantine")
            run("verify-quarantine")
            print(
                "Full restore drill passed: snapshot/code replay risk "
                "demonstrated; factor decrypts, quarantine retains MFA and "
                "inventory/catalog/minimum/reversal and private WebP/thumbnail "
                "metadata reconcile."
            )
        finally:
            try:
                if external and seeded:
                    objects("cleanup")
            finally:
                pipeline.cleanup()


if __name__ == "__main__":
    main()
