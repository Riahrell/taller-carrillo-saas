"""Scan runtime image archives offline and retain private JSON/CycloneDX evidence."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Official v0.76.0 OCI index, resolved 2026-10-18; never run a mutable tag.
TRIVY = (
    "ghcr.io/aquasecurity/trivy@sha256:"
    "2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222"
)
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,255}")
SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


class ImageAuditError(Exception):
    """Incomplete scan is never a clean image."""


def findings(report):
    if not isinstance(report, dict):
        raise ImageAuditError("Invalid image report")
    if (
        report.get("SchemaVersion") != 2
        or report.get("ArtifactType") != "container_image"
        or not isinstance(report.get("Results"), list)
        or not report["Results"]
        or report.get("Metadata", {}).get("OS", {}).get("Family")
        not in {"alpine", "debian"}
        or not report["Metadata"]["OS"].get("Name")
        or report["Metadata"]["OS"].get("EOSL") is True
    ):
        raise ImageAuditError("Missing image coverage or unsupported OS")
    issues, packages, os_packages = [], 0, 0
    for result in report["Results"]:
        if not isinstance(result, dict) or not isinstance(
            result.get("Vulnerabilities") or [], list
        ):
            raise ImageAuditError("Invalid package result")
        packages += len(result.get("Packages") or [])
        if result.get("Class") == "os-pkgs":
            os_packages += len(result.get("Packages") or [])
        for issue in result.get("Vulnerabilities") or []:
            if (
                not all(issue.get(key) for key in ("VulnerabilityID", "PkgName"))
                or issue.get("Severity") not in SEVERITIES
            ):
                raise ImageAuditError("Invalid vulnerability report")
            issues.append(
                {
                    key: issue.get(key, "")
                    for key in (
                        "VulnerabilityID",
                        "PkgName",
                        "InstalledVersion",
                        "FixedVersion",
                        "Severity",
                        "PrimaryURL",
                    )
                }
            )
    if not packages or not os_packages:
        raise ImageAuditError("Image package inventory is empty")
    return issues


def database_metadata(path, *, now=None):
    value = json.loads(path.read_text())
    updated = datetime.fromisoformat(value["UpdatedAt"].replace("Z", "+00:00"))
    now = now or datetime.now(UTC)
    if value.get("Version") != 2 or not (
        now - timedelta(hours=48) <= updated <= now + timedelta(minutes=5)
    ):
        raise ImageAuditError("Vulnerability database is stale or invalid")
    return value


class ImageScan:
    def __init__(self, output: Path):
        self.output = output
        self.names = []
        self.key = uuid.uuid4().hex[:12]

    def run(self, args, *, timeout=600):
        result = subprocess.run(
            args, check=False, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode:
            (self.output / "tool-error.log").write_text(result.stderr[-100_000:])
            raise ImageAuditError("Image tool failed; no clean scan")
        return result.stdout

    def container(self, cache, reports, args, *, archive=None, network="none"):
        name = "carrillo-image-scan-" + self.key + "-" + str(len(self.names))
        self.names.append(name)
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            network,
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=1536m,mode=1777",
            "--env",
            "HOME=/tmp",
            "--env",
            "TRIVY_DISABLE_TELEMETRY=true",
            "--mount",
            f"type=bind,src={cache},dst=/cache"
            + ("" if network != "none" else ",readonly"),
            "--mount",
            f"type=bind,src={reports},dst=/reports",
        ]
        if archive:
            command.extend(
                ["--mount", f"type=bind,src={archive},dst=/image.tar,readonly"]
            )
        return self.run([*command, TRIVY, "--cache-dir", "/cache", *args])

    def execute(self, images):
        if (
            not images
            or len(set(images)) != len(images)
            or any(not IMAGE.fullmatch(item) for item in images)
        ):
            raise ValueError("Explicit distinct runtime images required")
        self.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            with tempfile.TemporaryDirectory(
                prefix="image-scan-", dir=ROOT / ".local"
            ) as tmp:
                temporary = Path(tmp)
                cache = temporary / "cache"
                cache.mkdir()
                self.run(["docker", "pull", TRIVY])
                # Network receives no image, application source or credentials.
                self.container(
                    cache,
                    self.output,
                    [
                        "image",
                        "--download-db-only",
                        "--no-progress",
                        "--skip-version-check",
                        "--db-repository",
                        "ghcr.io/aquasecurity/trivy-db:2",
                    ],
                    network="bridge",
                )
                database = database_metadata(cache / "db/metadata.json")
                with (cache / "db/trivy.db").open("rb") as stream:
                    db_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                results = []
                for index, image in enumerate(images):
                    image_id = self.run(
                        ["docker", "image", "inspect", "--format", "{{.Id}}", image]
                    ).strip()
                    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                        raise ImageAuditError("Missing immutable image identity")
                    archive = temporary / "image.tar"
                    self.run(["docker", "save", "--output", str(archive), image_id])
                    self.container(
                        cache,
                        self.output,
                        [
                            "image",
                            "--input",
                            "/image.tar",
                            "--skip-db-update",
                            "--skip-java-db-update",
                            "--skip-version-check",
                            "--offline-scan",
                            "--cache-backend",
                            "memory",
                            "--scanners",
                            "vuln",
                            "--list-all-pkgs",
                            "--no-progress",
                            "--format",
                            "json",
                            "--output",
                            f"/reports/{index}.json",
                            "--ignorefile",
                            "/dev/null",
                            "--exit-code",
                            "0",
                        ],
                        archive=archive,
                    )
                    report = json.loads((self.output / f"{index}.json").read_text())
                    issues = findings(report)
                    self.container(
                        cache,
                        self.output,
                        [
                            "convert",
                            "--format",
                            "cyclonedx",
                            "--output",
                            f"/reports/{index}.cdx.json",
                            f"/reports/{index}.json",
                            "--ignorefile",
                            "/dev/null",
                        ],
                    )
                    sbom = json.loads((self.output / f"{index}.cdx.json").read_text())
                    if sbom.get("bomFormat") != "CycloneDX" or not sbom.get(
                        "components"
                    ):
                        raise ImageAuditError("Missing CycloneDX package inventory")
                    blocking = [
                        x for x in issues if x["Severity"] in {"HIGH", "CRITICAL"}
                    ]
                    results.append(
                        {
                            "image": image,
                            "image_id": image_id,
                            "findings": issues,
                            "blocking": len(blocking),
                            "components": len(sbom["components"]),
                        }
                    )
                    print(
                        f"Image {image}: {len(issues)} findings; "
                        f"{len(blocking)} HIGH/CRITICAL",
                        flush=True,
                    )
                    archive.unlink()
                summary = {
                    "schema": 1,
                    "at": datetime.now(UTC).isoformat(),
                    "scanner": TRIVY,
                    "database": database,
                    "database_sha256": db_hash,
                    "results": results,
                }
                (self.output / "summary.json").write_text(json.dumps(summary, indent=2))
                return 1 if any(x["blocking"] for x in results) else 0
        finally:
            for name in self.names:
                subprocess.run(
                    ["docker", "rm", "--force", name],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", action="append", required=True)
    args = parser.parse_args()
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    output = local / "image-reports" / uuid.uuid4().hex
    try:
        code = ImageScan(output).execute(args.image)
        print(f"Private image/SBOM evidence: {output.relative_to(ROOT)}", flush=True)
        return code
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        ImageAuditError,
    ):
        print(
            "Image scan incomplete; not approved. Evidence: "
            f"{output.relative_to(ROOT)}",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
