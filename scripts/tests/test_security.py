"""Fail-closed audit parsing, private inputs and pipeline enforcement."""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.ci import Pipeline
from scripts.security import (
    SCANNERS,
    AuditError,
    SecurityScan,
    exit_code,
    npm_exceptions,
    npm_report,
    python_report,
    secret_report,
    snapshot,
)


class ReportTests(unittest.TestCase):
    def test_secret_report_drops_values_source_and_personal_metadata(self):
        source = {
            "RuleID": "test-rule",
            "File": "example.py",
            "StartLine": 3,
            "Commit": "a" * 40,
            "Secret": "private-canary",
            "Match": "private-canary",
            "Author": "person",
            "Email": "person@example.com",
        }
        result = secret_report([source], 41)
        self.assertEqual(set(result[0]), {"RuleID", "File", "StartLine", "Commit"})
        self.assertNotIn("private-canary", json.dumps(result))
        self.assertNotIn("person", json.dumps(result))

    def test_secret_failure_cannot_be_a_clean_scan(self):
        for code, value in (
            (1, []),
            (41, []),
            (0, [{}]),
            (0, {}),
            (0, [{"RuleID": "test"}]),
        ):
            with self.subTest(code=code, value=value), self.assertRaises(AuditError):
                secret_report(value, code)
        self.assertEqual(secret_report([], 0), [])

    def test_python_clean_and_vulnerable_are_distinct(self):
        package = {"name": "example", "version": "1.0", "vulns": []}
        self.assertEqual(python_report({"dependencies": [package]}, 0), [])
        package["vulns"] = [
            {
                "id": "CVE-2099-1234",
                "fix_versions": ["1.1"],
                "description": "private-canary",
            }
        ]
        found = python_report({"dependencies": [package]}, 1)
        self.assertEqual(found[0]["id"], "CVE-2099-1234")
        self.assertNotIn("private-canary", json.dumps(found))
        with self.assertRaises(AuditError):
            python_report({"dependencies": [package]}, 0)

    def test_python_skipped_empty_error_and_unexpected_exit_fail(self):
        for value, code in (
            ({}, 0),
            ({"dependencies": []}, 0),
            ({"dependencies": [{"skip_reason": "not found"}]}, 0),
            ({"dependencies": [{"vulns": []}]}, 1),
        ):
            with self.subTest(value=value, code=code), self.assertRaises(AuditError):
                python_report(value, code)

    def test_npm_clean_and_vulnerable_are_distinct(self):
        clean = {
            "auditReportVersion": 2,
            "vulnerabilities": {},
            "metadata": {"vulnerabilities": {"total": 0}},
        }
        self.assertEqual(npm_report(clean, 0), [])
        dirty = clean | {
            "vulnerabilities": {
                "example": {
                    "severity": "high",
                    "range": "<1.1",
                    "fixAvailable": True,
                    "nodes": ["node_modules/example"],
                    "via": [{"url": "https://github.com/advisories/GHSA-test"}],
                }
            },
            "metadata": {"vulnerabilities": {"total": 1}},
        }
        self.assertEqual(npm_report(dirty, 1)[0]["severity"], "high")
        with self.assertRaises(AuditError):
            npm_report(dirty, 0)

    def test_npm_error_and_unsupported_report_do_not_pass(self):
        for value in (
            {},
            {
                "error": {"code": "ECONNRESET"},
                "auditReportVersion": 2,
                "vulnerabilities": {},
            },
            {"auditReportVersion": 1, "vulnerabilities": {}},
        ):
            with self.subTest(value=value), self.assertRaises(AuditError):
                npm_report(value, 0)

    def test_missing_or_error_stage_takes_precedence_over_findings(self):
        def report(*states):
            return {
                "results": [
                    {"scanner": name, "status": state}
                    for name, state in zip(SCANNERS, states, strict=False)
                ]
            }

        self.assertEqual(exit_code(report(*(["passed"] * 5))), 0)
        self.assertEqual(exit_code(report("findings", *(["passed"] * 4))), 1)
        self.assertEqual(exit_code(report("findings", "error", *(["passed"] * 3))), 2)
        self.assertEqual(exit_code(report(*(["passed"] * 4))), 2)
        self.assertEqual(exit_code(report("unknown", *(["passed"] * 4))), 2)
        self.assertEqual(exit_code(report("accepted", *(["passed"] * 4))), 0)
        duplicate = report(*(["passed"] * 5))
        duplicate["results"][0]["scanner"] = "npm-lock"
        self.assertEqual(exit_code(duplicate), 2)


class ExceptionTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 17)
        self.url = "https://github.com/advisories/GHSA-test"
        self.policy = {
            "schema": 1,
            "npm": [
                {
                    "id": "TEST",
                    "created": "2026-10-17",
                    "expires": "2026-10-31",
                    "owner": "test",
                    "reason": "test only",
                    "advisory": self.url,
                    "packages": {"example": "1.0"},
                }
            ],
        }
        self.lock = {
            "packages": {"node_modules/example": {"version": "1.0", "dev": True}}
        }
        self.findings = [
            {
                "package": "example",
                "severity": "high",
                "nodes": ["node_modules/example"],
                "advisories": [self.url],
            }
        ]

    def apply(self):
        return npm_exceptions(self.findings, self.lock, self.policy, today=self.today)

    def test_exact_dev_only_exception_is_visible(self):
        self.assertEqual(self.apply()[0]["exception"], "TEST")

    def test_expiry_boundary_blocks(self):
        self.today = date(2026, 10, 31)
        with self.assertRaises(AuditError):
            self.apply()

    def test_future_or_overlong_exception_blocks(self):
        for created, expires in (
            ("2026-10-18", "2026-10-31"),
            ("2026-10-17", "2026-11-01"),
        ):
            with self.subTest(created=created, expires=expires):
                self.policy["npm"][0].update(created=created, expires=expires)
                with self.assertRaises(AuditError):
                    self.apply()

    def test_runtime_dependency_cannot_use_exception(self):
        self.lock["packages"]["node_modules/example"].pop("dev")
        with self.assertRaises(AuditError):
            self.apply()

    def test_changed_version_requires_review(self):
        self.lock["packages"]["node_modules/example"]["version"] = "1.1"
        with self.assertRaises(AuditError):
            self.apply()

    def test_new_advisory_is_not_covered(self):
        self.findings[0]["advisories"].append("https://github.com/advisories/another")
        self.assertNotIn("exception", self.apply()[0])

    def test_critical_or_nested_occurrence_is_not_covered(self):
        self.findings[0]["severity"] = "critical"
        self.assertNotIn("exception", self.apply()[0])
        self.findings[0]["severity"] = "high"
        self.findings[0]["nodes"] = ["node_modules/nested/node_modules/example"]
        self.assertNotIn("exception", self.apply()[0])


class InputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.output = self.base / "snapshot"
        self.output.mkdir()
        subprocess.run(
            ["git", "-c", "init.templateDir=", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
        )

    def run_git(self, args):
        return subprocess.run(
            args, cwd=self.root, check=True, text=True, capture_output=True
        )

    def test_snapshot_includes_changed_tracked_and_new_but_excludes_local_secrets(self):
        (self.root / ".gitignore").write_text(".local/\n.env\nnode_modules/\n")
        (self.root / "tracked.py").write_text("original")
        self.run_git(["git", "add", "."])
        (self.root / "tracked.py").write_text("changed")
        (self.root / "new.py").write_text("new")
        (self.root / ".env").write_text("private-canary")
        (self.root / ".local").mkdir()
        (self.root / ".local" / "secret").write_text("private-canary")
        self.assertEqual(snapshot(self.root, self.output, self.run_git), 3)
        self.assertEqual((self.output / "tracked.py").read_text(), "changed")
        self.assertTrue((self.output / "new.py").is_file())
        self.assertFalse((self.output / ".local").exists())
        self.assertFalse((self.output / ".env").exists())
        self.assertFalse((self.output / ".git").exists())

    def test_symlink_to_private_file_is_rejected_without_reading(self):
        outside = self.base / "secret"
        outside.write_text("private-canary")
        (self.root / "link").symlink_to(outside)
        with self.assertRaises(AuditError):
            snapshot(self.root, self.output, self.run_git)
        self.assertFalse((self.output / "link").exists())

    def test_shallow_history_cannot_silently_reduce_scope(self):
        run = Mock(return_value=Mock(stdout="true\n"))
        with self.assertRaises(AuditError):
            snapshot(self.root, self.output, run)
        run.assert_called_once()

    def test_forced_tracking_of_local_directory_fails_closed(self):
        (self.root / ".local").mkdir()
        (self.root / ".local/secret").write_text("private-canary")
        with self.assertRaises(AuditError):
            snapshot(self.root, self.output, self.run_git)

    def test_operational_environment_is_not_given_to_tools(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "private-canary",
                "CSRF_SIGNING_KEY": "private-canary",
                "NPM_TOKEN": "private-canary",
                "PIP_INDEX_URL": "https://private-canary.example",
            },
        ):
            scan = SecurityScan(self.root, self.root / ".local/test")
        self.assertNotIn("private-canary", json.dumps(scan.env))
        self.assertEqual(scan.env["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_non_private_workspace_is_rejected(self):
        with self.assertRaises(ValueError):
            SecurityScan(self.root, self.base / "public")

    def test_record_does_not_echo_exception_or_claim_success(self):
        scan = SecurityScan(self.root, self.root / ".local/test")
        with patch("builtins.print") as output:
            scan.record("test", Mock(side_effect=ValueError("private-canary")))
        self.assertEqual(scan.results[0]["status"], "error")
        self.assertNotIn("private-canary", str(output.call_args_list))

    def test_failed_security_gate_prevents_build_and_database_start(self):
        workspace = self.root / ".local/ci-run"
        workspace.mkdir(parents=True)
        pipeline = Pipeline(self.root, workspace, "carrillo-ci-012345abcdef")

        def run(args, **_):
            if "scripts.security" in args:
                raise subprocess.CalledProcessError(2, "security")

        with (
            patch.object(pipeline, "validate_isolation"),
            patch.object(pipeline, "run", side_effect=run),
            patch.object(pipeline, "compose") as compose,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            pipeline.steps()
        compose.assert_not_called()

    def test_container_restricts_network_mounts_and_privileges(self):
        scan = SecurityScan(self.root, self.root / ".local/test")
        with patch.object(scan, "run") as run:
            scan.container(
                "test", "pinned-image", ["scan"], [(self.root, "/input", False)]
            )
        args = run.call_args.args[0]
        self.assertIn("type=bind,src=" + str(self.root) + ",dst=/input,readonly", args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop=ALL", args)
        self.assertNotIn("/var/run/docker.sock", " ".join(args))


if __name__ == "__main__":
    unittest.main()
