"""Prepare private Resend SMTP files without printing keys or sending email."""

import argparse
import json
import os
import re
import shlex
from pathlib import Path


def prepare(source: Path, destination: Path, sender: str) -> None:
    if destination.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("SMTP credentials must remain outside the repository")
    if source.stat().st_mode & 0o077 or source.stat().st_size > 16384:
        raise ValueError("The source must be private and bounded")
    values = []
    for line in source.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        name, separator, value = line.partition("=")
        if separator and name.strip() == "RESEND_API_KEY":
            parts = shlex.split(value, comments=True)
            if len(parts) != 1:
                raise ValueError("Invalid private configuration")
            values.append(parts[0])
    if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", values[0]):
        raise ValueError("Invalid private configuration")
    if any(c in sender for c in "\r\n"):
        raise ValueError("Invalid sender")
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    password = destination / "smtp_password"
    config = destination / "smtp.json"
    # Host directory is traversable only by its owner; mounted secret files are
    # readable by the unprivileged container UID. Never overwrite existing files.
    for path, data in (
        (password, values[0] + "\n"),
        (
            config,
            json.dumps(
                {
                    "host": "smtp.resend.com",
                    "port": 465,
                    "sender": sender,
                    "username": "resend",
                    "password_file": "/run/secrets/smtp_password",
                }
            )
            + "\n",
        ),
    ):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resend-env", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--sender", required=True)
    args = parser.parse_args()
    try:
        prepare(args.resend_env, args.directory, args.sender)
    except (OSError, ValueError):
        parser.exit(
            1, "Private SMTP configuration could not be prepared; no email sent.\n"
        )
    print("Private SMTP files prepared; no secrets displayed and no email sent.")


if __name__ == "__main__":
    main()
