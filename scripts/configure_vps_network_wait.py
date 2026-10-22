"""One Ubuntu static-network RA correction; dry-run unless --apply is explicit."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

SOURCE = Path("/etc/netplan/50-cloud-init.yaml")
TARGET = Path("/etc/systemd/network/10-netplan-eth0.network.d/50-static-no-ra.conf")
CONTENT = b"[Network]\nIPv6AcceptRA=no\n"
UNIT = "taller-carrillo-network-rollback"


def validate_static(data):
    interface = data.get("network", {}).get("ethernets", {}).get("eth0", {})
    addresses = interface.get("addresses", [])
    defaults = {
        ("::/0" if ":" in str(row.get("via", "")) else "0.0.0.0/0")
        if row.get("to") == "default"
        else str(row.get("to"))
        for row in interface.get("routes", [])
    }
    if (
        interface.get("dhcp4", False)
        or interface.get("dhcp6", False)
        or not any(":" not in str(x) for x in addresses)
        or not any(":" in str(x) for x in addresses)
        or not {"0.0.0.0/0", "::/0"}.issubset(defaults)
    ):
        raise ValueError("Expected complete static dual-stack configuration")


def run(args, timeout=45):
    p = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError("Network operation failed; rollback remains scheduled")
    return p.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        import yaml  # Ubuntu cloud-init dependency; no application dependency added.

        original = SOURCE.read_bytes()
        validate_static(yaml.safe_load(original))
        if not args.apply:
            print("Static dual-stack network confirmed; no network changes.")
            return 0
        if os.geteuid() != 0 or TARGET.exists() or TARGET.is_symlink():
            raise ValueError("Root and a new drop-in path required")
        TARGET.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if TARGET.parent.resolve() != TARGET.parent:
            raise ValueError("Canonical drop-in directory required")
        rollback = (
            "import pathlib,subprocess; "
            f"p=pathlib.Path({str(TARGET)!r}); "
            f"expected={CONTENT!r}; "
            "assert p.read_bytes()==expected; p.unlink(); "
            'subprocess.run(["networkctl","reload"],check=True); '
            'subprocess.run(["networkctl","reconfigure","eth0"],check=True)'
        )
        with os.fdopen(
            os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), "wb"
        ) as stream:
            stream.write(CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            run(
                [
                    "systemd-run",
                    "--unit=" + UNIT,
                    "--on-active=300s",
                    "--timer-property=AccuracySec=1s",
                    "/usr/bin/python3",
                    "-c",
                    rollback,
                ]
            )
        except RuntimeError:
            TARGET.unlink()
            raise
        run(["networkctl", "reload"])
        run(["networkctl", "reconfigure", "eth0"])
        run(
            ["systemctl", "restart", "systemd-networkd-wait-online.service"],
            timeout=130,
        )
        if SOURCE.read_bytes() != original:
            raise ValueError("Netplan source changed unexpectedly")
        print(
            json.dumps(
                {
                    "network_wait": "passed",
                    "netplan_unchanged": True,
                    "drop_in_sha256": hashlib.sha256(CONTENT).hexdigest(),
                    "rollback_timer": UNIT + ".timer",
                    "rollback_pending": True,
                }
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("Network adjustment stopped; check rollback before retrying.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
