"""Pinned, isolated secret/dependency audits; never execute application packages."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GITLEAKS = (
    "ghcr.io/gitleaks/gitleaks@sha256:"
    "c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
)
NODE = (
    "node:24.22.1-bookworm-slim@sha256:"
    "55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205"
)
LOCKS = (
    "backend/requirements.lock",
    "backend/requirements-dev.lock",
    "ops/security/requirements.lock",
)
SCANNERS = (
    "detector-self-test",
    "secrets-worktree",
    "secrets-history",
    "python-locks",
    "npm-lock",
)


class AuditError(Exception):
    """Sanitized operational failure; not a clean audit or an accepted finding."""


def secret_report(value, code):
    if code not in (0, 41) or not isinstance(value, list):
        raise AuditError("Secret scan incomplete")
    findings = []
    for item in value:
        if not isinstance(item, dict) or not all(
            key in item for key in ("RuleID", "File", "StartLine", "Commit")
        ):
            raise AuditError("Invalid secret report")
        # Never retain Secret, Match, author, email or surrounding source text.
        findings.append(
            {key: item[key] for key in ("RuleID", "File", "StartLine", "Commit")}
        )
    if bool(findings) != (code == 41):
        raise AuditError("Inconsistent secret scan result")
    return findings


def python_report(value, code):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("dependencies"), list)
        or not value["dependencies"]
    ):
        raise AuditError("Python audit incomplete")
    findings = []
    for package in value["dependencies"]:
        if (
            not isinstance(package, dict)
            or "skip_reason" in package
            or not isinstance(package.get("name"), str)
            or not package["name"]
            or not isinstance(package.get("version"), str)
            or not package["version"]
            or not isinstance(package.get("vulns"), list)
        ):
            raise AuditError("Python dependency was not audited")
        for issue in package["vulns"]:
            findings.append(
                {
                    "package": package["name"],
                    "version": package["version"],
                    "id": issue["id"],
                    "fix_versions": issue["fix_versions"],
                }
            )
    if code != (1 if findings else 0):
        raise AuditError("Python audit failed")
    return findings


def npm_report(value, code):
    if (
        not isinstance(value, dict)
        or "error" in value
        or value.get("auditReportVersion") != 2
        or not isinstance(value.get("vulnerabilities"), dict)
    ):
        raise AuditError("npm audit incomplete")

    def advisories(name, visited):
        if name in visited:
            raise AuditError("Cyclic npm advisory chain")
        result = set()
        for via in value["vulnerabilities"][name]["via"]:
            if isinstance(via, str):
                result.update(advisories(via, visited | {name}))
            elif isinstance(via, dict) and isinstance(via.get("url"), str):
                result.add(via["url"])
            else:
                raise AuditError("Invalid npm advisory chain")
        if not result:
            raise AuditError("Missing npm advisory")
        return sorted(result)

    findings = []
    for name, issue in value["vulnerabilities"].items():
        if not isinstance(issue, dict) or issue.get("severity") not in (
            "info",
            "low",
            "moderate",
            "high",
            "critical",
        ):
            raise AuditError("Invalid npm severity")
        findings.append(
            {
                "package": name,
                "severity": issue["severity"],
                "range": issue["range"],
                "fix_available": issue["fixAvailable"],
                "nodes": issue["nodes"],
                "advisories": advisories(name, set()),
            }
        )
    if code != (1 if findings else 0) or value["metadata"]["vulnerabilities"][
        "total"
    ] != len(findings):
        raise AuditError("npm audit failed")
    return findings


def npm_exceptions(findings, lock, policy, *, today=None):
    """Expiring advisory/version/dev-only exceptions keep findings visible."""
    today = today or datetime.now(UTC).date()
    if policy.get("schema") != 1 or not isinstance(policy.get("npm"), list):
        raise AuditError("Invalid exception policy")
    for entry in policy["npm"]:
        created, expires = (
            date.fromisoformat(entry["created"]),
            date.fromisoformat(entry["expires"]),
        )
        if (
            not created <= today < expires
            or expires - created > timedelta(days=14)
            or not all(
                entry.get(key)
                for key in ("id", "owner", "reason", "advisory", "packages")
            )
        ):
            raise AuditError("Exception expired or requires review")
        expected = entry["packages"]
        valid = all(
            lock["packages"].get("node_modules/" + name, {}).get("version") == version
            and lock["packages"].get("node_modules/" + name, {}).get("dev") is True
            for name, version in expected.items()
        )
        if not valid:
            raise AuditError("Exception package versions or runtime scope changed")
        for item in findings:
            if (
                item["package"] in expected
                and item["severity"] in ("info", "low", "moderate", "high")
                and item["advisories"] == [entry["advisory"]]
                and item["nodes"] == ["node_modules/" + item["package"]]
            ):
                item["exception"] = entry["id"]
    return findings


def snapshot(root: Path, target: Path, run):
    """Tracked and non-ignored new files; no .local, credentials or Git config."""
    if run(["git", "rev-parse", "--is-shallow-repository"]).stdout.strip() != "false":
        raise AuditError("Full Git history required")
    names = run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    ).stdout.split("\0")
    count = 0
    for name in sorted(set(names) - {""}):
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parts[0] in (".git", ".local", ".venv", "node_modules")
        ):
            raise AuditError("Unsafe or local-only file selected")
        source = root / relative
        # Reject links even if their target is absent; never copy outside the repo.
        if any(
            parent.is_symlink()
            for parent in (source, *source.parents)
            if parent != root.parent
        ):
            raise AuditError("Symlinks are not supported in security input")
        if not source.exists():
            continue
        if not source.is_file():
            raise AuditError("Submodules or non-regular security input")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        count += 1
    return count


class SecurityScan:
    def __init__(self, root: Path, workspace: Path):
        self.root, self.workspace = root.resolve(), workspace.resolve()
        if not self.workspace.is_relative_to(self.root / ".local"):
            raise ValueError("Security workspace must be private and ignored")
        self.key = uuid.uuid4().hex[:12]
        self.image = f"carrillo-security-{self.key}:tools"
        self.names = []
        self.results = []
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.workspace),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        }
        # Preserve explicit Docker connection selection, never application settings.
        for key in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "XDG_RUNTIME_DIR",
        ):
            if key in os.environ:
                self.env[key] = os.environ[key]

    def run(self, args, *, cwd=None, timeout=180, check=True):
        result = subprocess.run(
            args,
            cwd=cwd or self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode:
            raise AuditError("Security command failed; raw output withheld")
        return result

    def container(self, label, image, args, mounts, *, network="none"):
        name = f"carrillo-scan-{self.key}-{label}"
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
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--memory",
            "512m",
            "--cpus",
            "2",
            "--pids-limit",
            "128",
            "--env",
            "HOME=/tmp",
        ]
        for source, destination, writable in mounts:
            command.extend(
                [
                    "--mount",
                    f"type=bind,src={source},dst={destination}"
                    + ("" if writable else ",readonly"),
                ]
            )
        return self.run([*command, image, *args], check=False)

    def secrets(self, label, mode, source):
        reports = self.workspace / "raw-reports"
        reports.mkdir(mode=0o700, exist_ok=True)
        report = reports / (label + ".json")
        args = [
            mode,
            "/source",
            "--config",
            "/policy/gitleaks.toml",
            "--gitleaks-ignore-path",
            "/dev/null",
            "--ignore-gitleaks-allow",
            "--redact=100",
            "--no-banner",
            "--no-color",
            "--log-level=error",
            "--exit-code=41",
            "--timeout=120",
            "--report-format=json",
            "--report-path",
            "/reports/" + report.name,
        ]
        if mode == "git":
            args.extend(["--log-opts=--all --full-history"])
        result = self.container(
            label,
            GITLEAKS,
            args,
            [
                (source, "/source", False),
                (self.workspace / "source/ops/security", "/policy", False),
                (reports, "/reports", True),
            ],
        )
        if not report.is_file():
            raise AuditError("Secret scan produced no report")
        return secret_report(json.loads(report.read_text()), result.returncode)

    def canary(self):
        """Prove detection of a synthetic secret, including after deletion."""
        source = self.workspace / "canary"
        source.mkdir()
        # Deliberately assembled fake token, not an account or usable credential.
        canary = "ghp_" + "Ab3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5"
        (source / "canary.txt").write_text("token=" + canary + "\n")
        if not self.secrets("canary-dir", "dir", source):
            raise AuditError("Secret detector canary failed")
        self.run(["git", "-c", "init.templateDir=", "init", "--quiet", str(source)])
        for args in (
            ["add", "canary.txt"],
            [
                "-c",
                "user.name=Security fixture",
                "-c",
                "user.email=security@example.com",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--quiet",
                "-m",
                "synthetic scanner canary",
            ],
            ["rm", "--quiet", "canary.txt"],
            [
                "-c",
                "user.name=Security fixture",
                "-c",
                "user.email=security@example.com",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--quiet",
                "-m",
                "remove synthetic canary",
            ],
        ):
            self.run(["git", *args], cwd=source)
        if not self.secrets("canary-history", "git", source):
            raise AuditError("History detector canary failed")
        for path in (self.workspace / "raw-reports").glob("canary-*.json"):
            if canary in path.read_text():
                raise AuditError("Secret detector failed to redact")

    def record(self, name, operation):
        try:
            findings = operation()
            self.results.append(
                {
                    "scanner": name,
                    "status": (
                        "accepted"
                        if all(item.get("exception") for item in findings)
                        else "findings"
                    )
                    if findings
                    else "passed",
                    "findings": findings,
                }
            )
        except (
            AuditError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ) as error:
            self.results.append(
                {
                    "scanner": name,
                    "status": "error",
                    "message": str(error)
                    if isinstance(error, AuditError)
                    else "Incomplete audit: tool, connectivity or report error.",
                }
            )
        result = self.results[-1]
        count = len(result.get("findings", []))
        print(
            f"Security {name}: {result['status']} ({count} findings)",
            flush=True,
        )
        for finding in result.get("findings", []):
            print(json.dumps(finding, ensure_ascii=True), flush=True)
        if result.get("message"):
            print(result["message"], flush=True)

    def execute(self):
        source = self.workspace / "source"
        source.mkdir()
        try:
            files = snapshot(self.root, source, self.run)
            history = self.workspace / "history.git"
            self.run(
                [
                    "git",
                    "-c",
                    "init.templateDir=",
                    "clone",
                    "--mirror",
                    "--no-local",
                    "--no-hardlinks",
                    str(self.root),
                    str(history),
                ]
            )
            # Context contains only tool Dockerfile and hash-pinned requirements.
            self.run(
                ["docker", "build", "--tag", self.image, str(source / "ops/security")],
                timeout=600,
            )
            self.record("detector-self-test", lambda: self.canary() or [])
            self.record(
                "secrets-worktree", lambda: self.secrets("worktree", "dir", source)
            )
            self.record(
                "secrets-history", lambda: self.secrets("history", "git", history)
            )
            # Networked tools receive lockfiles/manifests only, never application
            # source, Git history, local credentials or the secret scanner's reports.
            dependencies = self.workspace / "dependencies"
            dependencies.mkdir()
            for name in (*LOCKS, "frontend/package.json", "frontend/package-lock.json"):
                target = dependencies / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, target)

            def require_secret_scans():
                if any(item["status"] != "passed" for item in self.results[:3]):
                    raise AuditError(
                        "Dependency queries blocked by incomplete secret scans"
                    )

            def python_audit():
                require_secret_scans()
                args = [
                    "--disable-pip",
                    "--no-deps",
                    "--require-hashes",
                    "--strict",
                    "--progress-spinner=off",
                    "--desc=off",
                    "--format=json",
                    "--timeout=15",
                ]
                for lock in LOCKS:
                    args.extend(["-r", "/source/" + lock])
                result = self.container(
                    "python",
                    self.image,
                    args,
                    [(dependencies, "/source", False)],
                    network="bridge",
                )
                return python_report(json.loads(result.stdout), result.returncode)

            def npm_audit():
                require_secret_scans()
                result = self.container(
                    "npm",
                    NODE,
                    [
                        "npm",
                        "--prefix",
                        "/source/frontend",
                        "audit",
                        "--json",
                        "--package-lock-only",
                        "--include=dev",
                        "--audit-level=info",
                        "--ignore-scripts",
                        "--registry=https://registry.npmjs.org",
                        "--fetch-retries=0",
                        "--fetch-timeout=15000",
                    ],
                    [(dependencies, "/source", False)],
                    network="bridge",
                )
                findings = npm_report(json.loads(result.stdout), result.returncode)
                return npm_exceptions(
                    findings,
                    json.loads((source / "frontend/package-lock.json").read_text()),
                    json.loads((source / "ops/security/exceptions.json").read_text()),
                )

            self.record("python-locks", python_audit)
            self.record("npm-lock", npm_audit)
            return {
                "schema": 1,
                "at": datetime.now(UTC).isoformat(),
                "commit": self.run(["git", "rev-parse", "HEAD"]).stdout.strip(),
                "worktree_files": files,
                "inputs_sha256": {
                    name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                    for name in (
                        *LOCKS,
                        "frontend/package-lock.json",
                        "ops/security/gitleaks.toml",
                        "ops/security/exceptions.json",
                    )
                },
                "tools": {
                    "gitleaks": GITLEAKS,
                    "npm_image": NODE,
                    "python_tool_lock": "ops/security/requirements.lock",
                },
                "results": self.results,
            }
        finally:
            # A timed-out Docker client may leave its container alive. Only our names.
            for name in self.names:
                self.run(["docker", "rm", "--force", name], check=False, timeout=30)
            self.run(["docker", "image", "rm", self.image], check=False, timeout=30)


def exit_code(report):
    states = [item["status"] for item in report["results"]]
    if (
        len(states) != 5
        or {item["scanner"] for item in report["results"]} != set(SCANNERS)
        or "error" in states
        or any(state not in ("passed", "accepted", "findings") for state in states)
    ):
        return 2
    return 1 if "findings" in states else 0


def main():
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="security-", dir=local) as directory:
            report = SecurityScan(ROOT, Path(directory)).execute()
        reports = local / "security-reports"
        reports.mkdir(mode=0o700, exist_ok=True)
        target = reports / (uuid.uuid4().hex + ".json")
        with target.open("x") as stream:
            os.chmod(target, 0o600)
            json.dump(report, stream, indent=2)
        print(f"Sanitized security report: {target.relative_to(ROOT)}", flush=True)
        return exit_code(report)
    except (
        AuditError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ):
        print(
            "Security audit incomplete; raw tool output withheld. No clean result.",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
