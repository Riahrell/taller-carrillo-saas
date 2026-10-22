"""Verify and atomically select a TLS certificate/key pair for Docker mounts."""

import argparse
import fcntl
import os
import stat
import subprocess
import uuid
from pathlib import Path


def openssl(args, *, data=None):
    p = subprocess.run(
        ["openssl", *args], input=data, capture_output=True, timeout=15, check=False
    )
    if p.returncode:
        raise ValueError("TLS certificate verification failed")
    return p.stdout


def publish(directory, fullchain, private_key, hostname, *, ca_file=None):
    from scripts.render_tls_proxy import render

    render(hostname)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("TLS publication directory must be private")
    fd = os.open(directory / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = directory / "current"
        if current.exists() and not current.is_symlink():
            raise ValueError("Current TLS path must be an owned symlink")
        version = directory / ("version-" + uuid.uuid4().hex)
        version.mkdir(mode=0o700)
        for source, name in [
            (fullchain, "fullchain.pem"),
            (private_key, "private-key.pem"),
        ]:
            payload = source.read_bytes()
            if not 0 < len(payload) <= 65536:
                raise ValueError("TLS file size invalid")
            with os.fdopen(
                os.open(version / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444),
                "wb",
            ) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o444)
        trust = ["-CAfile", str(ca_file)] if ca_file else ["-CApath", "/etc/ssl/certs"]
        openssl(
            [
                "verify",
                *trust,
                "-untrusted",
                str(version / "fullchain.pem"),
                "-purpose",
                "sslserver",
                "-verify_hostname",
                hostname,
                str(version / "fullchain.pem"),
            ]
        )
        openssl(
            [
                "x509",
                "-in",
                str(version / "fullchain.pem"),
                "-noout",
                "-checkend",
                "604800",
            ]
        )
        public = openssl(
            ["x509", "-in", str(version / "fullchain.pem"), "-pubkey", "-noout"]
        )
        certificate_key = openssl(["pkey", "-pubin", "-outform", "DER"], data=public)
        signing_key = openssl(
            [
                "pkey",
                "-in",
                str(version / "private-key.pem"),
                "-pubout",
                "-outform",
                "DER",
            ]
        )
        if certificate_key != signing_key:
            raise ValueError("Certificate and key differ")
        link = directory / ("next-" + uuid.uuid4().hex)
        link.symlink_to(version.name, target_is_directory=True)
        os.replace(link, current)
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--fullchain", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--hostname", required=True)
    args = parser.parse_args()
    try:
        publish(args.directory, args.fullchain, args.private_key, args.hostname)
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, "TLS publication failed; no key material displayed.\n")
    print("Verified TLS pair published; recreate the proxy to load new mounts.")


if __name__ == "__main__":
    main()
