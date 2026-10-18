"""Provision read-only container secret mounts inside private host directories."""

import os
import stat
from pathlib import Path


def write_mount_secret(path: Path, value: str) -> None:
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise ValueError("Secret directory must be private and owned by the operator")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())
        # Explicit mode after writing: umask must not make a valid secret mount
        # unreadable by the container UID. The private parent protects the host.
        os.fchmod(output.fileno(), 0o444)
