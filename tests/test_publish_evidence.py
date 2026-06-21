from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import evidence
import publish_evidence


class PublishEvidenceTests(unittest.TestCase):
    def test_package_for_github_release_creates_validated_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "evidence"
            package = evidence.EvidencePackage(root, command=["test"])
            package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test",
                status_code=200,
                response_headers={},
                body=b"body",
                metadata={"user_id": "Test.User"},
            )
            package.finalize()

            archive = publish_evidence.package_for_github_release(
                root,
                output_dir=Path(tmp) / "release-assets",
            )

            self.assertEqual(archive.name, "evidence.tar.gz")
            self.assertTrue(archive.exists())
            self.assertTrue((Path(tmp) / "release-assets" / "evidence.tar.gz.sha256").exists())

    def test_publish_release_asset_uses_gh_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "evidence.tar.gz"
            archive.write_bytes(b"archive")
            commands = []

            publish_evidence.publish_release_asset(
                archive,
                tag="evidence-123",
                title="Evidence 123",
                notes="Forensic evidence package",
                run=commands.append,
            )

            self.assertEqual(commands[0][:4], ["gh", "release", "create", "evidence-123"])
            self.assertIn(str(archive), commands[0])
            self.assertIn("--latest=false", commands[0])


if __name__ == "__main__":
    unittest.main()
