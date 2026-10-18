from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import yaml

import scrape


class ScrapeStartupTests(unittest.TestCase):
    def test_successful_scrape_writes_yaml_and_passes_health_check(self):
        listing = '<a href="Default.aspx?sid=vnf/oznamenie&UserId=Test.User">Test User</a>'
        declaration = '''
        <div id="_sectionLayoutContainer_ctl01_OutPut">
          <table class="oznamenie_table">
            <tr><td class="label">Interné číslo:</td><td class="value">Test.User</td></tr>
            <tr><td class="label">oznámenie za rok:</td><td class="value">2025</td></tr>
          </table>
        </div>
        '''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.json"
            data_dir = root / "data"
            responses = []
            for text in (listing, declaration):
                response = requests.Response()
                response.status_code = 200
                response._content = text.encode("utf-8")
                responses.append(response)
            argv = [
                "scrape.py", "--data-dir", str(data_dir),
                "--workers", "1", "--report-json", str(report_path),
            ]
            with (
                patch("sys.argv", argv),
                patch.object(scrape.requests, "request", side_effect=responses),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                scrape.main()
            self.assertEqual(
                yaml.safe_load((data_dir / "Test.User.yaml").read_text(encoding="utf-8")),
                {"id": "Test.User", "year": 2025},
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual((report["total"], report["scraped"], report["errors"]), (1, 1, 0))
            health_path = Path(__file__).resolve().parents[1] / ".github/scripts/scrape_health.py"
            outputs = root / "health.txt"
            result = subprocess.run(
                [sys.executable, str(health_path), "--report", str(report_path),
                 "--github-output", str(outputs)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("commit_ok=true", outputs.read_text(encoding="utf-8"))

    def test_listing_failure_writes_fatal_report_without_changing_data(self):
        for failure in (requests.exceptions.SSLError("record layer failure"), None):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data_dir = root / "data"
                data_dir.mkdir()
                baseline = data_dir / "Test.User.yaml"
                baseline.write_text("id: Test.User\nyear: 2025\n", encoding="utf-8")
                report_path = root / "report.json"
                failed_path = root / "failed.txt"
                # An earlier run's report must not survive a failed startup.
                report_path.write_text('{"total": 100, "errors": 0}', encoding="utf-8")
                argv = [
                    "scrape.py", "--data-dir", str(data_dir),
                    "--report-json", str(report_path),
                    "--failed-ids-output", str(failed_path),
                ]
                with (
                    patch("sys.argv", argv),
                    patch.object(scrape, "fetch_politician_list", side_effect=failure, return_value=[]),
                    patch.object(scrape, "scrape_one") as scrape_one,
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as exited,
                ):
                    scrape.main()

                self.assertEqual(exited.exception.code, 1)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["total"], 0)
                self.assertEqual(report["results"], [])
                self.assertEqual(report["fatal_error"]["stage"], "politician_list")
                self.assertEqual(report["fatal_error"]["error_type"], "SSLError" if failure else "ValueError")
                self.assertEqual(failed_path.read_text(encoding="utf-8"), "")
                self.assertEqual(baseline.read_text(encoding="utf-8"), "id: Test.User\nyear: 2025\n")
                scrape_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
