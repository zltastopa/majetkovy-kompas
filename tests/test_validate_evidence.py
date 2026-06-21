from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import evidence
import validate_evidence


class ValidateEvidenceTests(unittest.TestCase):
    def test_validate_package_accepts_complete_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = evidence.EvidencePackage(Path(tmp) / "evidence", command=["test"])
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

            summary = validate_evidence.validate_package(Path(tmp) / "evidence")

            self.assertEqual(summary["captures"], 1)

    def test_validate_package_rejects_body_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = evidence.EvidencePackage(Path(tmp) / "evidence", command=["test"])
            capture = package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test",
                status_code=200,
                response_headers={},
                body=b"body",
                metadata={"user_id": "Test.User"},
            )
            package.finalize()
            (Path(tmp) / "evidence" / capture["body_path"]).write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "body hash mismatch"):
                validate_evidence.validate_package(Path(tmp) / "evidence")

    def test_validate_package_rejects_unmanifested_raw_files(self):
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
            (root / "raw" / "extra.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unmanifested raw file"):
                validate_evidence.validate_package(root)

    def test_validate_package_can_require_external_anchors(self):
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
            manifest_hash = evidence.sha256_file(root / "manifest.json")

            with self.assertRaisesRegex(ValueError, "timestamp response missing"):
                validate_evidence.validate_package(root, require_timestamp=True)
            with self.assertRaisesRegex(ValueError, "signature response missing"):
                validate_evidence.validate_package(root, require_signature=True)

            (root / "manifest.sha256.tsr").write_bytes(b"timestamp-response")
            (root / "manifest.timestamp.json").write_text(
                (
                    '{"manifest_sha256":"%s",'
                    '"response_path":"manifest.sha256.tsr",'
                    '"response_sha256":"%s"}\n'
                )
                % (
                    manifest_hash,
                    evidence.sha256_bytes(b"timestamp-response"),
                ),
                encoding="utf-8",
            )
            (root / "manifest.sha256.sig").write_bytes(b"signature")
            (root / "manifest.signature.json").write_text(
                (
                    '{"manifest_sha256":"%s",'
                    '"signature_path":"manifest.sha256.sig",'
                    '"signature_sha256":"%s"}\n'
                )
                % (
                    manifest_hash,
                    evidence.sha256_bytes(b"signature"),
                ),
                encoding="utf-8",
            )

            summary = validate_evidence.validate_package(
                root,
                require_timestamp=True,
                require_signature=True,
            )

            self.assertTrue(summary["timestamped"])
            self.assertTrue(summary["signed"])

            (root / "manifest.sha256.sig").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "signature hash mismatch"):
                validate_evidence.validate_package(root, require_signature=True)

            (root / "manifest.sha256.sig").write_bytes(b"signature")
            (root / "manifest.sha256.tsr").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "timestamp hash mismatch"):
                validate_evidence.validate_package(root, require_timestamp=True)


if __name__ == "__main__":
    unittest.main()
