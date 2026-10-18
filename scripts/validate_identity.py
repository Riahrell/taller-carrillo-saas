"""Focused identity validation in disposable PostgreSQL; never uses development."""

import argparse
import tempfile
import uuid
from pathlib import Path

from scripts.ci import ROOT, Pipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--full-backend", action="store_true")
    selection.add_argument("--unit-only", action="store_true")
    selection.add_argument("--test-path", action="append")
    parser.add_argument("--maxfail", type=int, choices=range(1, 11), default=1)
    args = parser.parse_args()
    if args.test_path:
        for value in args.test_path:
            target = (ROOT / "backend" / value).resolve()
            if (
                not target.is_relative_to(ROOT / "backend" / "tests")
                or not target.exists()
            ):
                parser.error("Test paths must exist inside backend/tests")
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ci-identity-", dir=local) as directory:
        pipeline = Pipeline(
            ROOT, Path(directory), "carrillo-ci-" + uuid.uuid4().hex[:12]
        )
        try:
            pipeline.validate_isolation()
            pipeline.compose("build", "backend", "tests", timeout=1200)
            pipeline.compose("up", "-d", "--wait", "db")
            pipeline.compose("run", "--rm", "migrate")
            pipeline.compose(
                "run", "--rm", "migrate", "python", "-m", "alembic", "check"
            )
            pipeline.compose(
                "run",
                "--rm",
                "tests",
                "python",
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "--maxfail=" + str(args.maxfail),
                "-q",
                *(
                    args.test_path
                    if args.test_path
                    else ["tests", "--ignore=tests/integration"]
                    if args.unit_only
                    else ["tests"]
                    if args.full_backend
                    else [
                        "tests/integration/test_identity_completion.py",
                        "tests/integration/test_identity_http.py",
                        "tests/integration/test_identity_delivery.py",
                        "tests/integration/test_authentication.py",
                        "tests/integration/test_bootstrap_postgres.py",
                        "tests/integration/test_enrollment_email.py",
                        "tests/test_identity_transport.py",
                        "tests/test_password_policy.py",
                    ]
                ),
                timeout=600,
            )
        finally:
            pipeline.cleanup()


if __name__ == "__main__":
    main()
