"""Retire only the inventoried disposable legacy VPS apps; defaults to dry-run."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

CONTAINERS = {
    "saas_frontend": (
        "d7e60714557c88107778800849e03a7f06c65c4202cd21d413eb5c4731dd08cc",
        "frontend-taller-carrillo",
    ),
    "saas_backend": (
        "b64478f581f6508654db38681e3190848c1abeb8aca9e7f8bf7567ada71563ab",
        "taller-carrillo",
    ),
    "saas_postgres": (
        "28c15e5d9afcf3b08c523cc6eea175b398fe532e9284f05fe4514e805e858ab6",
        "taller-carrillo",
    ),
}
VOLUMES = {"taller-carrillo_pgdata", "taller-carrillo_postgres_data"}
NETWORKS = {"taller-carrillo_default", "frontend-taller-carrillo_default"}
IMAGES = {
    "carrillo-legacy-snapshot:f14db42e6f10-saas_postgres": "b93a09f95cf0",
    "carrillo-legacy-snapshot:f14db42e6f10-saas_backend": "1fcdc72b9ac9",
    "carrillo-legacy-snapshot:f14db42e6f10-saas_frontend": "eac36ade0337",
    "frontend-taller-carrillo-saas_frontend:latest": "006dffc2f374",
    "taller-carrillo-saas_backend:latest": "ea5aac3215b8",
    "postgres:16-alpine": "721873c34ceb",
}
DIRECTORIES = (
    "/opt/taller-carrillo",
    "/opt/frontend-taller-carrillo",
    "/var/backups/taller-carrillo",
    "/var/lib/taller-carrillo-operations",
)


def run(args, *, timeout=120):
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError("Legacy retirement command failed; details withheld")
    return result.stdout


def validate_inventory(containers, volumes, networks, images):
    if {x["Name"].lstrip("/") for x in containers} != set(CONTAINERS):
        raise ValueError("Unexpected container set")
    for item in containers:
        name = item["Name"].lstrip("/")
        identity, project = CONTAINERS[name]
        if (
            item["Id"] != identity
            or item["Config"].get("Labels", {}).get("com.docker.compose.project")
            != project
        ):
            raise ValueError("Legacy container identity changed")
        mounts = item.get("Mounts", [])
        if name == "saas_postgres":
            if (
                len(mounts) != 1
                or mounts[0].get("Name") != "taller-carrillo_postgres_data"
            ):
                raise ValueError("Unexpected database mount")
        elif mounts:
            raise ValueError("Unexpected application mount")
    if {x["Name"] for x in volumes} != VOLUMES:
        raise ValueError("Unexpected volume set")
    for item in volumes:
        if (
            item.get("Options") not in (None, {})
            or item["Driver"] != "local"
            or item.get("Labels", {}).get("com.docker.compose.project")
            != "taller-carrillo"
        ):
            raise ValueError("Unexpected volume ownership")
    if set(networks) != NETWORKS | {"bridge", "host", "none"}:
        raise ValueError("Unexpected network set")
    if set(images) != set(IMAGES) or any(
        not images[name].removeprefix("sha256:").startswith(identity)
        for name, identity in IMAGES.items()
    ):
        raise ValueError("Unexpected image identity")


def validate_directory(path, mountpoints):
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise ValueError("Unexpected legacy directory")
    if any(
        point == str(path) or point.startswith(str(path) + "/") for point in mountpoints
    ):
        raise ValueError("Refusing a mounted filesystem inside legacy directories")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--discard-test-data", action="store_true")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0 or run(["docker", "context", "show"]).strip() != "default":
            raise ValueError("Root on the inventoried local Docker engine required")
        ids = run(["docker", "ps", "-aq"]).split()
        containers = json.loads(run(["docker", "inspect", *ids])) if ids else []
        names = run(["docker", "volume", "ls", "--format", "{{.Name}}"]).split()
        volumes = (
            json.loads(run(["docker", "volume", "inspect", *names])) if names else []
        )
        networks = run(["docker", "network", "ls", "--format", "{{.Name}}"]).split()
        images = {}
        for line in run(
            [
                "docker",
                "image",
                "ls",
                "--no-trunc",
                "--format",
                "{{.Repository}}:{{.Tag}} {{.ID}}",
            ]
        ).splitlines():
            tag, identity = line.split()
            images[tag] = identity
        validate_inventory(containers, volumes, networks, images)
        mountpoints = [
            line.split()[4]
            for line in Path("/proc/self/mountinfo").read_text().splitlines()
        ]
        for name in DIRECTORIES:
            validate_directory(Path(name), mountpoints)
        units = run(
            [
                "systemctl",
                "list-units",
                "--state=active,activating,deactivating",
                "--no-legend",
                "--no-pager",
            ]
        )
        if "carrillo-legacy-backup-" in units:
            raise ValueError("Legacy backup is active")
        plan = {
            "containers": list(CONTAINERS),
            "volumes": sorted(VOLUMES),
            "networks": sorted(NETWORKS),
            "images": list(IMAGES),
            "directories": list(DIRECTORIES),
            "builder_cache": "inventoried legacy engine",
            "preserved": ["operating system", "SSH", "Docker", "Object Storage"],
        }
        if not args.apply:
            print(json.dumps({"dry_run": True, "plan": plan}, indent=2))
            return 0
        if not args.discard_test_data:
            raise ValueError("Explicit disposal of test data required")
        run(["docker", "stop", "--time", "30", *ids], timeout=180)
        run(["docker", "rm", *ids])
        run(["docker", "volume", "rm", *sorted(VOLUMES)])
        run(["docker", "network", "rm", *sorted(NETWORKS)])
        run(["docker", "image", "rm", *IMAGES], timeout=300)
        if (
            run(["docker", "ps", "-aq"]).strip()
            or run(["docker", "image", "ls", "-q"]).strip()
        ):
            raise ValueError("New Docker resources appeared; cache was not removed")
        run(["docker", "builder", "prune", "--all", "--force"], timeout=600)
        for name in DIRECTORIES:
            path = Path(name)
            validate_directory(path, mountpoints)
            shutil.rmtree(path)
        remaining = {
            "containers": run(["docker", "ps", "-aq"]).split(),
            "volumes": run(["docker", "volume", "ls", "-q"]).split(),
            "images": run(["docker", "image", "ls", "-q"]).split(),
            "application_directories": [
                name for name in DIRECTORIES if Path(name).exists()
            ],
            "networks": run(
                ["docker", "network", "ls", "--format", "{{.Name}}"]
            ).split(),
        }
        if any(
            remaining[key]
            for key in ("containers", "volumes", "images", "application_directories")
        ) or set(remaining["networks"]) != {"bridge", "host", "none"}:
            raise ValueError("Legacy retirement verification failed")
        print(
            json.dumps({"removed": plan, "remaining": remaining, "ok": True}, indent=2)
        )
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print(
            "Legacy retirement stopped; inspect state before retrying. "
            "No secret details."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
