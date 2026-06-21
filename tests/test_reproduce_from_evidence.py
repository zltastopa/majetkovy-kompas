from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import evidence
import reproduce_from_evidence
import scrape


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
      </table>
    </div>
  </body>
</html>
"""


class ReproduceFromEvidenceTests(unittest.TestCase):
    def test_reproduce_root_extracts_clean_state_and_compares_expected_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_root = root / "evidence"
            package = evidence.EvidencePackage(evidence_root / "live", command=["test"])
            package.capture_response(
                purpose="declaration",
                method="GET",
                url="https://example.test/?UserId=Test.User",
                status_code=200,
                response_headers={},
                body=DECLARATION_HTML.encode("utf-8"),
                metadata={"user_id": "Test.User"},
            )
            package.finalize()

            expected_data = root / "expected"
            expected_data.mkdir()
            base_data = root / "base"
            base_data.mkdir()
            (base_data / "Old.User.yaml").write_text(
                scrape.dump_yaml({"id": "Old.User", "name": "Old User", "year": 2024}),
                encoding="utf-8",
            )
            (expected_data / "Old.User.yaml").write_text(
                scrape.dump_yaml({"id": "Old.User", "name": "Old User", "year": 2024}),
                encoding="utf-8",
            )
            (expected_data / "Test.User.yaml").write_text(
                scrape.dump_yaml(
                    {
                        "id": "Test.User",
                        "name": "Test User",
                        "year": 2025,
                    }
                ),
                encoding="utf-8",
            )
            expected_manifest = reproduce_from_evidence.generate_content_hashes.build_manifest(
                expected_data
            )
            (expected_data / "_checks").mkdir()
            (expected_data / "_checks" / "content-hashes.json").write_text(
                json.dumps(expected_manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            output_data = root / "recreated"
            report_path = root / "reproduction-report.json"
            report = reproduce_from_evidence.reproduce_state(
                evidence_root,
                output_data,
                base_data_dir=base_data,
                expected_data_dir=expected_data,
                report_json=report_path,
            )

            self.assertTrue(report["matches_expected"])
            self.assertEqual(report["packages"], 1)
            self.assertEqual(report["extracted"]["scraped"], 1)
            self.assertEqual(report["comparison"]["different"], [])
            self.assertTrue((output_data / "Old.User.yaml").exists())
            self.assertTrue((output_data / "_checks" / "content-hashes.json").exists())
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8"))["matches_expected"],
                True,
            )


if __name__ == "__main__":
    unittest.main()
