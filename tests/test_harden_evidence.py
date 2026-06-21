from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import evidence
import harden_evidence


class HardenEvidenceTests(unittest.TestCase):
    def make_package(self, root: Path) -> None:
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

    def test_sign_package_writes_detached_signature_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "evidence"
            key = Path(tmp) / "key.pem"
            key.write_text("private-key", encoding="utf-8")
            self.make_package(root)
            commands = []

            def fake_run(command):
                commands.append(command)
                Path(command[command.index("-out") + 1]).write_bytes(b"signature")

            harden_evidence.sign_package(root, key, run=fake_run)

            self.assertTrue((root / "manifest.sha256.sig").exists())
            self.assertTrue((root / "manifest.signature.json").exists())
            self.assertIn("openssl", commands[0][0])

    def test_timestamp_package_writes_rfc3161_response_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "evidence"
            self.make_package(root)

            def fake_run(command):
                Path(command[command.index("-out") + 1]).write_bytes(b"timestamp-query")

            def fake_post(url, body, content_type):
                self.assertEqual(url, "https://tsa.example.test")
                self.assertEqual(body, b"timestamp-query")
                self.assertEqual(content_type, "application/timestamp-query")
                return b"timestamp-response"

            harden_evidence.timestamp_package(
                root,
                "https://tsa.example.test",
                run=fake_run,
                post=fake_post,
            )

            self.assertEqual((root / "manifest.sha256.tsr").read_bytes(), b"timestamp-response")
            self.assertTrue((root / "manifest.timestamp.json").exists())


if __name__ == "__main__":
    unittest.main()
