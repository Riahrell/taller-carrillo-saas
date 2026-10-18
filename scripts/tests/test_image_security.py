import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.image_security import ImageAuditError, database_metadata, findings


def report():
    return {
        "SchemaVersion": 2,
        "ArtifactType": "container_image",
        "Metadata": {"OS": {"Family": "alpine", "Name": "3.24.2", "EOSL": False}},
        "Results": [
            {
                "Class": "os-pkgs",
                "Packages": [{"Name": "runtime"}],
                "Vulnerabilities": [],
            }
        ],
    }


class ImageReportTests(unittest.TestCase):
    def test_empty_or_unsupported_image_is_not_clean(self):
        for value in (
            {},
            {**report(), "Results": []},
            {**report(), "Metadata": {"OS": {"Family": "alpine", "EOSL": True}}},
        ):
            with self.subTest(value=value), self.assertRaises(ImageAuditError):
                findings(value)
        value = report()
        value["Results"][0]["Packages"] = []
        with self.assertRaises(ImageAuditError):
            findings(value)

    def test_unfixed_high_issue_is_preserved_without_an_exception(self):
        value = report()
        value["Results"][0]["Vulnerabilities"] = [
            {
                "VulnerabilityID": "CVE-synthetic",
                "PkgName": "runtime",
                "InstalledVersion": "1",
                "Severity": "HIGH",
            }
        ]
        issues = findings(value)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["FixedVersion"], "")
        self.assertNotIn("exception", issues[0])

    def test_malformed_vulnerability_cannot_appear_clean(self):
        value = report()
        value["Results"][0]["Vulnerabilities"] = [{"Severity": "HIGH"}]
        with self.assertRaises(ImageAuditError):
            findings(value)

    def test_stale_future_or_wrong_database_version_is_rejected(self):
        now = datetime(2026, 10, 18, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "metadata.json"
            for version, age in (
                (2, timedelta(hours=-49)),
                (2, timedelta(minutes=6)),
                (1, timedelta()),
            ):
                path.write_text(
                    json.dumps(
                        {"Version": version, "UpdatedAt": (now + age).isoformat()}
                    )
                )
                with (
                    self.subTest(version=version, age=age),
                    self.assertRaises(ImageAuditError),
                ):
                    database_metadata(path, now=now)
            path.write_text(json.dumps({"Version": 2, "UpdatedAt": now.isoformat()}))
            self.assertEqual(database_metadata(path, now=now)["Version"], 2)


if __name__ == "__main__":
    unittest.main()
