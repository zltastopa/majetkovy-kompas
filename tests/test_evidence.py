from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

import evidence
import extract_from_evidence


DECLARATION_HTML = """
<html>
  <body>
    <div id="_sectionLayoutContainer_ctl01_OutPut">
      <table class="oznamenie_table">
        <tr>
          <td class="label">Interné číslo:</td>
          <td class="value">Test.User</td>
        </tr>
        <tr>
          <td class="label">titul, meno, priezvisko:</td>
          <td class="value">Test User</td>
        </tr>
        <tr>
          <td class="label">oznámenie za rok:</td>
          <td class="value">2025</td>
        </tr>
        <tr>
          <td class="label">oznámenie bolo podané:</td>
          <td class="value">2026-03-01</td>
        </tr>
      </table>
    </div>
  </body>
</html>
"""


class EvidencePackageTests(unittest.TestCase):
    def test_capture_response_stores_raw_body_and_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = evidence.EvidencePackage(Path(tmp), command=["test-command"])

            capture = package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={"Content-Type": "text/html; charset=utf-8"},
                body=b"raw response body",
                metadata={"user_id": "Test.User"},
            )
            manifest = package.finalize()

            body_path = Path(tmp) / capture["body_path"]
            manifest_path = Path(tmp) / "manifest.json"
            manifest_hash_path = Path(tmp) / "manifest.sha256"

            self.assertEqual(body_path.read_bytes(), b"raw response body")
            self.assertEqual(capture["body_sha256"], evidence.sha256_bytes(b"raw response body"))
            self.assertTrue(manifest_path.exists())
            self.assertTrue(manifest_hash_path.exists())
            self.assertEqual(manifest["captures"][0]["url"], "https://example.test/declaration?UserId=Test.User")
            self.assertIn(evidence.sha256_file(manifest_path), manifest_hash_path.read_text(encoding="utf-8"))

    def test_extract_from_evidence_writes_yaml_without_network_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = evidence.EvidencePackage(root / "evidence", command=["test-command"])
            package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={"Content-Type": "text/html; charset=utf-8"},
                body=DECLARATION_HTML.encode("utf-8"),
                metadata={"user_id": "Test.User"},
            )
            package.finalize()

            data_dir = root / "data"
            report = extract_from_evidence.extract_package(root / "evidence", data_dir)

            output = yaml.safe_load((data_dir / "Test.User.yaml").read_text(encoding="utf-8"))
            self.assertEqual(output["id"], "Test.User")
            self.assertEqual(output["name"], "Test User")
            self.assertEqual(output["year"], 2025)
            self.assertEqual(report["scraped"], 1)
            self.assertEqual(report["errors"], 0)

            manifest = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["captures"][0]["purpose"], "declaration")

    def test_extract_from_evidence_rejects_manifest_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = evidence.EvidencePackage(root / "evidence", command=["test-command"])
            package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={"Content-Type": "text/html; charset=utf-8"},
                body=DECLARATION_HTML.encode("utf-8"),
                metadata={"user_id": "Test.User"},
            )
            package.finalize()
            manifest_path = root / "evidence" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["captures"][0]["url"] = "https://tampered.example"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                extract_from_evidence.extract_package(root / "evidence", root / "data")

    def test_explicit_year_extraction_ignores_default_page_with_wrong_year(self):
        latest_html = DECLARATION_HTML.replace("<td class=\"value\">2025</td>", "<td class=\"value\">2025</td>")
        requested_html = DECLARATION_HTML.replace("<td class=\"value\">2025</td>", "<td class=\"value\">2024</td>")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = evidence.EvidencePackage(root / "evidence", command=["test-command"])
            package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={},
                body=latest_html.encode("utf-8"),
                metadata={"user_id": "Test.User", "requested_year": 2024, "explicit_requested_year": True},
            )
            package.capture_response(
                purpose="declaration",
                method="POST",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={},
                body=requested_html.encode("utf-8"),
                metadata={
                    "user_id": "Test.User",
                    "requested_year": 2024,
                    "explicit_requested_year": True,
                    "postback": True,
                },
            )
            package.finalize()

            extract_from_evidence.extract_package(root / "evidence", root / "data")

            output = yaml.safe_load((root / "data" / "Test.User.yaml").read_text(encoding="utf-8"))
            self.assertEqual(output["year"], 2024)

    def test_fallback_year_extraction_uses_first_parseable_capture(self):
        unavailable_html = """
        <html><body>
          <div id="_sectionLayoutContainer_ctl01_OutPut">v štádiu spracovania</div>
        </body></html>
        """
        valid_html = DECLARATION_HTML.replace("<td class=\"value\">2025</td>", "<td class=\"value\">2024</td>")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = evidence.EvidencePackage(root / "evidence", command=["test-command"])
            package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={},
                body=unavailable_html.encode("utf-8"),
                metadata={"user_id": "Test.User"},
            )
            package.capture_response(
                purpose="declaration",
                method="POST",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={},
                body=unavailable_html.encode("utf-8"),
                metadata={"user_id": "Test.User", "requested_year": 2025, "postback": True},
            )
            package.capture_response(
                purpose="declaration",
                method="POST",
                url="https://example.test/declaration?UserId=Test.User",
                status_code=200,
                response_headers={},
                body=valid_html.encode("utf-8"),
                metadata={"user_id": "Test.User", "requested_year": 2024, "postback": True},
            )
            package.finalize()

            extract_from_evidence.extract_package(root / "evidence", root / "data")

            output = yaml.safe_load((root / "data" / "Test.User.yaml").read_text(encoding="utf-8"))
            self.assertEqual(output["year"], 2024)


if __name__ == "__main__":
    unittest.main()
