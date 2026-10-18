"""Explicit operator key generation. Never overwrite, rotate or print secrets."""

import argparse
import json
import os
import secrets
from pathlib import Path


def key_document():
    return {
        "seed_keys": {"seed-1": secrets.token_hex(32)},
        "active_seed_key": "seed-1",
        "mail_keys": {"mail-1": secrets.token_hex(32)},
        "active_mail_key": "mail-1",
        "quota_key": secrets.token_hex(32),
    }


def create(path: Path):
    if not path.parent.is_dir():
        raise ValueError("Create the private secret directory first")
    with os.fdopen(
        os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444), "w"
    ) as output:
        json.dump(key_document(), output)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        create(args.output)
    except (OSError, ValueError):
        parser.exit(1, "Key creation refused; existing keys were not overwritten.\n")
    print("Identity keys created. Back up this file separately; values hidden.")


if __name__ == "__main__":
    main()
