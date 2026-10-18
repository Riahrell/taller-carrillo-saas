"""Create a new keyring document retaining old keys and the stable quota key."""

import argparse
import json
import os
import secrets
import uuid
from pathlib import Path


def rotate(source: Path, destination: Path):
    data = json.loads(source.read_text())
    if set(data) != {
        "seed_keys",
        "active_seed_key",
        "mail_keys",
        "active_mail_key",
        "quota_key",
    }:
        raise ValueError("Invalid key document")
    if any(
        not isinstance(data[key], dict) or not data[key]
        for key in ("seed_keys", "mail_keys")
    ):
        raise ValueError("Invalid key document")
    for name, active in (
        ("seed_keys", "active_seed_key"),
        ("mail_keys", "active_mail_key"),
    ):
        identity = ("seed-" if name == "seed_keys" else "mail-") + uuid.uuid4().hex[:12]
        data[name][identity] = secrets.token_hex(32)
        data[active] = identity
    raw = json.dumps(data) + "\n"
    if len(raw) > 16384:
        raise ValueError("Retire old keys only after backup review")
    with os.fdopen(
        os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444), "w"
    ) as stream:
        stream.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        rotate(args.input, args.output)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(
            1, "Rotation document refused; existing files were not overwritten.\n"
        )
    print(
        "New keyring document created; old keys and quota identity "
        "retained. Values hidden."
    )


if __name__ == "__main__":
    main()
