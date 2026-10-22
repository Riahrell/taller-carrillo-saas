"""Quiesced inventory backup, public-key encryption and verified off-VPS upload."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

WRITERS = ["backend", "image-worker", "identity-worker"]


def run(command, *, output=None, timeout=1800):
    p = subprocess.run(
        command,
        stdout=output if output is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if p.returncode:
        raise RuntimeError("Backup operation failed; details withheld")
    return p.stdout


def backup(config):
    directory = Path(config["directory"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        directory.is_symlink()
        or directory.stat().st_mode & 0o077
        or directory.stat().st_uid != os.getuid()
    ):
        raise ValueError("Private backup directory required")
    compose = [config["compose_wrapper"]]
    recipient = config["recipient"]
    if not re.fullmatch("[0-9A-F]{40}", recipient):
        raise ValueError("Explicit public-key fingerprint required")
    lockfd = os.open(
        directory / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(lockfd, "wb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
        archive = directory / (identity + ".tar.gpg")
        with tempfile.TemporaryDirectory(prefix="work-", dir=directory) as name:
            work = Path(name)
            stopped = False
            try:
                # A failing stop may have stopped only some services: always resume.
                stopped = True
                run([*compose, "stop", "--timeout", "60", *WRITERS])
                dump = work / "database.dump"
                with os.fdopen(
                    os.open(dump, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb"
                ) as output:
                    run(
                        [
                            *compose,
                            "exec",
                            "-T",
                            "db",
                            "pg_dump",
                            "-U",
                            "carrillo_bootstrap",
                            "-d",
                            config["database"],
                            "--format=custom",
                            "--no-owner",
                        ],
                        output=output,
                    )
                    output.flush()
                    os.fsync(output.fileno())
                common = [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--user",
                    "0:0",
                    "--volume",
                    str(work) + ":/backup",
                    "image-worker",
                    "python",
                    "-m",
                    "app.tools.image_archive",
                ]
                run(
                    [
                        *common,
                        "export",
                        "--directory",
                        "/backup/images",
                        "--storage-config",
                        "/run/secrets/product_images",
                        "--maintenance-confirmed",
                    ]
                )
                run([*common, "check-database", "--directory", "/backup/images"])
                recovery = {
                    "identity_keys": json.loads(
                        Path(config["identity_keys"]).read_text()
                    ),
                    "csrf_signing_key": Path(config["csrf_key"]).read_text().strip(),
                    "release": Path(config["release_path"]).resolve().name,
                }
                (work / "recovery.json").write_text(json.dumps(recovery))
                (work / "recovery.json").chmod(0o600)
                checks = {}
                for path in work.rglob("*"):
                    if path.is_file():
                        with path.open("rb") as stream:
                            checks[str(path.relative_to(work))] = hashlib.file_digest(
                                stream, "sha256"
                            ).hexdigest()
                (work / "checksums.json").write_text(json.dumps(checks))
                (work / "checksums.json").chmod(0o600)
            finally:
                if stopped:
                    run(
                        [
                            *compose,
                            "up",
                            "-d",
                            "--wait",
                            "--wait-timeout",
                            "120",
                            *WRITERS,
                        ]
                    )
            # Resume app before compression/encryption/provider transfer.
            plain = work / "snapshot.tar"
            run(
                [
                    "tar",
                    "-cf",
                    str(plain),
                    "-C",
                    str(work),
                    "database.dump",
                    "images",
                    "checksums.json",
                    "recovery.json",
                ]
            )
            plain.chmod(0o600)
            run(
                [
                    "gpg",
                    "--homedir",
                    config["gnupg_home"],
                    "--batch",
                    "--quiet",
                    "--trust-model",
                    "always",
                    "--encrypt",
                    "--recipient",
                    recipient,
                    "--output",
                    str(archive),
                    "--",
                    str(plain),
                ]
            )
            archive.chmod(0o600)
        key = "respaldos/" + archive.name
        run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "--user",
                "0:0",
                "--volume",
                str(directory) + ":/backup:ro",
                "--volume",
                config["storage_config"] + ":/run/secrets/archive_storage:ro",
                "image-worker",
                "python",
                "-m",
                "app.tools.backup_archive_store",
                "--storage-config",
                "/run/secrets/archive_storage",
                "--expected-bucket",
                config["backup_bucket"],
                "--forbidden-bucket",
                config["images_bucket"],
                "--file",
                "/backup/" + archive.name,
                "--key",
                key,
            ]
        )
        receipt = {
            "archive": archive.name,
            "key": key,
            "recipient": recipient,
            "offsite_verified": True,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with os.fdopen(
            os.open(
                directory / (identity + ".json"),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            ),
            "w",
        ) as output:
            json.dump(receipt, output)
        # Only generated encrypted local copies; never remove provider objects.
        copies = sorted(directory.glob("????????T??????Z-*.tar.gpg"))
        for old in copies[:-7]:
            old.unlink()
            old.with_name(old.name.removesuffix(".tar.gpg") + ".json").unlink(
                missing_ok=True
            )
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        info = args.config.lstat()
        if (
            args.config.is_symlink()
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise ValueError("Private operator configuration required")
        config = json.loads(args.config.read_text())
        receipt = backup(config)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print("Scheduled backup failed; inspect service status. No secret details.")
        return 1
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
