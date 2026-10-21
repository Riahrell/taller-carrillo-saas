"""Full PostgreSQL dump/restore with synthetic MFA and post-restore quarantine."""

import json
import os
import secrets
import tempfile
import uuid
from pathlib import Path

from scripts.ci import ROOT, Pipeline


def main():
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
        pipeline.prefix.extend(["-f", str(override)])
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
            run("quarantine")
            run("verify-quarantine")
            print(
                "Full restore drill passed: snapshot/code replay risk "
                "demonstrated; factor decrypts, quarantine retains MFA and "
                "inventory/catalog/minimum/reversal evidence reconciles."
            )
        finally:
            pipeline.cleanup()


if __name__ == "__main__":
    main()
