"""Add HTTP security to an existing local environment without changing DB secrets."""

import argparse
import re
import secrets
from pathlib import Path

from scripts.private_files import write_mount_secret
from scripts.setup_local import ROOT


def upgrade(environment: str, *, base: Path | None = None) -> Path:
    if environment not in {"development", "test"}:
        raise ValueError("Only local development/test environments are supported")
    base = base if base is not None else ROOT / ".local"
    destination = base / environment
    config, secret_dir = destination / "compose.env", destination / "secrets"
    values = dict(
        line.split("=", 1)
        for line in config.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    if Path(
        values.get("DB_SECRETS_DIR", "")
    ).resolve() != secret_dir.resolve() or not secret_dir.resolve().is_relative_to(
        base.resolve()
    ):
        raise ValueError("Refusing an unexpected local secret directory")
    secret = secret_dir / "csrf_signing_key"
    try:
        write_mount_secret(secret, secrets.token_hex(32) + "\n")
    except FileExistsError:
        if not re.fullmatch(r"[0-9a-f]{64}", secret.read_text().strip()):
            raise ValueError(
                "Existing HTTP key is invalid; nothing was overwritten"
            ) from None
    if "PUBLIC_ORIGIN" not in values:
        with config.open("a") as stream:
            stream.write("\nPUBLIC_ORIGIN=http://localhost:8080\n")
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["development", "test"])
    args = parser.parse_args()
    try:
        upgrade(args.environment)
    except (OSError, ValueError):
        print("Cannot upgrade HTTP configuration; DB credentials preserved.")
        return 1
    print("Local HTTP configuration ready; credentials preserved (values hidden).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
