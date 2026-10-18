from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "scrape_health.py"


def load_module():
    spec = importlib.util.spec_from_file_location("scrape_health", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


health = load_module()


class ScrapeHealthTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), *map(str, args)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_load_report_rejects_missing_unreadable_and_invalid_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            with self.assertRaisesRegex(ValueError, "report.json"):
                health.load_report(path)
            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(ValueError, "denied"):
                    health.load_report(path)
            path.write_bytes(b"\xff")
            with self.assertRaises(ValueError):
                health.load_report(path)

    def test_load_report_rejects_invalid_json_and_schema(self):
        invalid_reports = [
            "", "{", "null", "[]", "{}",
            '{"results":null}', '{"results":{}}',
            '{"results":[null]}', '{"results":[[]]}',
            '{"results":[{}]}',
            '{"results":[{"user_id":1,"status":"scraped"}]}',
            '{"results":[{"user_id":" ","status":"scraped"}]}',
            '{"results":[{"user_id":"u","status":"unknown"}]}',
            '{"results":[{"user_id":"u","status":[]}]}',
            '{"results":[],"total":1}',
            '{"results":[],"total":false}',
            '{"results":[],"total":"0"}',
            '{"results":[],"errors":-1}',
            '{"results":[],"scraped":1}',
            '{"results":[],"captured":1}',
            '{"results":[],"fatal_error":null}',
            '{"results":[],"fatal_errors":{}}',
            '{"results":[],"fatal_errors":[null]}',
            '{"results":[],"report_errors":["missing input"]}',
            json.dumps({"results": [{"user_id": "u", "status": "scraped"}] * 2}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            for raw in invalid_reports:
                with self.subTest(raw=raw):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        health.load_report(path)

    def test_load_report_accepts_producer_reports_and_empty_placeholders(self):
        reports = [
            {"results": []},
            {"total": 1, "captured": 1, "errors": 0,
             "results": [{"user_id": "u", "status": "captured"}]},
            {"total": 1, "scraped": 1, "skipped": 0, "errors": 0,
             "results": [{"user_id": "u", "status": "scraped", "year": 2025}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            for report in reports:
                with self.subTest(report=report):
                    path.write_text(json.dumps(report), encoding="utf-8")
                    self.assertEqual(health.load_report(path), report)

    def test_cli_bad_inputs_fail_closed_even_alongside_valid_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.json"
            valid.write_text(json.dumps({
                "results": [{"user_id": "Alice", "status": "scraped"}],
            }), encoding="utf-8")
            bad = root / "bad.json"
            for raw in [None, "", "{", "{}", '{"results":[null]}']:
                for include_valid in [False, True]:
                    with self.subTest(raw=raw, include_valid=include_valid):
                        if raw is None:
                            bad.unlink(missing_ok=True)
                        else:
                            bad.write_text(raw, encoding="utf-8")
                        outputs = root / "outputs.txt"
                        summary = root / "summary.md"
                        combined = root / "combined.json"
                        args = ["--report", bad]
                        if include_valid:
                            args += ["--report", valid]
                        completed = self.run_cli(
                            *args, "--combined-report", combined,
                            "--github-output", outputs, "--step-summary", summary,
                            "--emit-annotation",
                        )
                        self.assertEqual(completed.returncode, 1, completed.stderr)
                        self.assertIn("bad.json", completed.stderr)
                        self.assertNotIn("Traceback", completed.stderr)
                        self.assertIn("commit_ok=false", outputs.read_text())
                        self.assertIn("hard_fail=true", outputs.read_text())
                        self.assertIn("notify=true", outputs.read_text())
                        self.assertIn("bad.json", summary.read_text())
                        self.assertIn("::error title=Invalid scrape report::", completed.stdout)
                        report = json.loads(combined.read_text())
                        self.assertEqual(report["total"], int(include_valid))
                        self.assertEqual(report["errors"], 0)
                        self.assertTrue(report["report_errors"])

    def test_cli_empty_health_check_fails_but_optional_aggregation_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.json"
            empty.write_text('{"results":[]}', encoding="utf-8")
            combined = root / "combined.json"
            completed = self.run_cli("--report", empty)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("No scrape targets", completed.stderr)

            completed = self.run_cli(
                "--report", empty, "--report", empty, "--combined-report", combined,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(combined.read_text())["results"], [])

            for option in ["--github-output", "--step-summary", "--emit-annotation", "--discord"]:
                with self.subTest(option=option):
                    args = [option]
                    if option in {"--github-output", "--step-summary"}:
                        args.append(root / "health.txt")
                    if option == "--discord":
                        args.append("--dry-run")
                    completed = self.run_cli(
                        "--report", empty, "--combined-report", combined, *args,
                    )
                    self.assertEqual(completed.returncode, 1, completed.stderr)

    def test_cli_missing_optional_aggregation_input_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = self.run_cli(
                "--report", root / "missing.json",
                "--combined-report", root / "combined.json",
            )
            self.assertEqual(completed.returncode, 1)

    def test_cli_valid_reports_keep_retry_and_threshold_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            acquisition = root / "acquisition.json"
            acquisition.write_text(json.dumps({"results": [
                {"user_id": "Alice", "status": "captured"},
                {"user_id": "Bob", "status": "error", "error_type": "HTTPError"},
            ]}), encoding="utf-8")
            extraction = root / "extraction.json"
            extraction.write_text(json.dumps({"results": [
                {"user_id": "Alice", "status": "scraped"},
            ]}), encoding="utf-8")
            empty = root / "empty.json"
            empty.write_text('{"results":[]}', encoding="utf-8")
            combined = root / "combined.json"
            outputs = root / "outputs.txt"
            completed = self.run_cli(
                "--report", acquisition, "--report", extraction, "--report", empty,
                "--combined-report", combined, "--github-output", outputs,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("commit_ok=false", outputs.read_text())
            self.assertIn("hard_fail=true", outputs.read_text())
            self.assertEqual(json.loads(combined.read_text())["total"], 2)

            retry = root / "retry.json"
            retry.write_text(json.dumps({"results": [
                {"user_id": "Bob", "status": "skipped"},
            ]}), encoding="utf-8")
            completed = self.run_cli(
                "--report", acquisition, "--report", extraction, "--report", empty,
                "--report", retry, "--combined-report", combined,
                "--github-output", root / "retry-outputs.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(combined.read_text())
            self.assertEqual((report["total"], report["scraped"], report["skipped"], report["errors"]),
                             (2, 1, 1, 0))
            self.assertIn("commit_ok=true", (root / "retry-outputs.txt").read_text())

    def test_fatal_errors_survive_combining_without_inventing_targets(self):
        fatal = {
            "stage": "politician_list", "error_type": "HTTPError",
            "error_status": 403,
            "error_message": "Your system administrator has blocked your computer or device.",
        }
        failed = {"total": 0, "scraped": 0, "skipped": 0, "errors": 0,
                  "results": [], "fatal_error": fatal}
        good = {"results": [{"user_id": "Alice", "status": "scraped"}]}
        for reports in [[failed], [failed, good], [good, failed]]:
            with self.subTest(reports=reports):
                report = health.combine_reports(reports)
                self.assertEqual(report["fatal_errors"], [fatal])
                self.assertEqual(report["total"], len(reports) - 1)
                self.assertEqual(report["errors"], 0)
                quality = health.evaluate_quality(report, allow_empty=True)
                self.assertFalse(quality["commit_ok"])
                self.assertTrue(quality["hard_fail"])
                self.assertTrue(quality["notify"])
                self.assertIn("politician_list", health.markdown_summary(report, quality))
                payload = health.build_discord_payload(
                    report, quality, repo_full_name="owner/repo", run_url="",
                )
                self.assertIn("HTTP 403", json.dumps(payload))
                self.assertIn("blocked your computer", json.dumps(payload))
                self.assertEqual(health.combine_reports([report])["fatal_errors"], [fatal])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fatal.json"
            source.write_text(json.dumps(failed), encoding="utf-8")
            combined = root / "combined.json"
            good_path = root / "good.json"
            good_path.write_text(json.dumps(good), encoding="utf-8")
            for extra in [[], ["--report", good_path]]:
                with self.subTest(extra=extra):
                    completed = self.run_cli(
                        "--report", source, *extra, "--combined-report", combined,
                    )
                    self.assertEqual(completed.returncode, 1, completed.stderr)
                    self.assertIn("politician_list", completed.stderr)
                    self.assertIn("HTTP 403", completed.stderr)
                    self.assertEqual(health.load_report(combined)["fatal_errors"], [fatal])

            outputs = root / "outputs.txt"
            summary = root / "summary.md"
            completed = self.run_cli(
                "--report", source, "--report", good_path,
                "--combined-report", combined, "--github-output", outputs,
                "--step-summary", summary, "--emit-annotation", "--discord", "--dry-run",
            )
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertIn("commit_ok=false", outputs.read_text())
            self.assertIn("hard_fail=true", outputs.read_text())
            self.assertIn("politician_list", summary.read_text())
            self.assertIn("blocked your computer", summary.read_text())
            self.assertIn("::error title=Invalid scrape report::", completed.stdout)
            self.assertIn("HTTP 403", completed.stdout)
            self.assertIn("blocked your computer", completed.stdout)

    def test_evaluate_quality_fails_closed_for_no_targets(self):
        for report in [{}, {"total": 0, "errors": 0}, {"results": []}]:
            with self.subTest(report=report):
                quality = health.evaluate_quality(report)
                self.assertEqual(quality["level"], "error")
                self.assertFalse(quality["commit_ok"])
                self.assertTrue(quality["hard_fail"])
                self.assertTrue(quality["notify"])

    def test_captured_declarations_require_extraction_results_before_publication(self):
        acquisition = {"results": [
            {"user_id": "Alice", "status": "captured"},
            {"user_id": "Bob", "status": "captured"},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "acquisition.json"
            source.write_text(json.dumps(acquisition), encoding="utf-8")
            extraction = root / "extraction.json"
            for results in [[], [{"user_id": "Alice", "status": "scraped"}]]:
                with self.subTest(results=results):
                    report = health.combine_reports([acquisition, {"results": results}])
                    self.assertFalse(health.evaluate_quality(report)["commit_ok"])
                    extraction.write_text(json.dumps({"results": results}), encoding="utf-8")
                    completed = self.run_cli(
                        "--report", source, "--report", extraction,
                        "--combined-report", root / "combined.json",
                    )
                    self.assertEqual(completed.returncode, 1, completed.stderr)
                    self.assertIn("no extraction result", completed.stderr)

            # Skipped and errored extractions are explicit outcomes too.
            for status in ["scraped", "skipped", "error"]:
                report = health.combine_reports([acquisition, {"results": [
                    {"user_id": "Alice", "status": "scraped"},
                    {"user_id": "Bob", "status": status},
                ]}])
                self.assertEqual(health.report_problems(report), [])

    def test_combine_reports_uses_second_pass_result_for_failed_user(self):
        first = {
            "results": [
                {"user_id": "Alice", "status": "scraped", "year": 2025},
                {
                    "user_id": "Bob",
                    "status": "error",
                    "error_type": "HTTPError",
                    "error_status": 504,
                },
            ]
        }
        second = {
            "results": [
                {"user_id": "Bob", "status": "scraped", "year": 2025},
            ]
        }

        report = health.combine_reports([first, second])

        self.assertEqual(report["total"], 2)
        self.assertEqual(report["scraped"], 2)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["failed_user_ids"], [])

    def test_evaluate_quality_warns_alerts_and_suppresses_bad_scrapes(self):
        report = {
            "total": 100,
            "scraped": 60,
            "skipped": 10,
            "errors": 30,
            "error_groups": {"HTTP 504": 30},
            "failed_user_ids": ["u"] * 30,
        }

        quality = health.evaluate_quality(report)

        self.assertEqual(quality["level"], "alert")
        self.assertFalse(quality["commit_ok"])
        self.assertFalse(quality["hard_fail"])

    def test_discord_payload_describes_warning_without_mentions(self):
        report = {
            "total": 10,
            "scraped": 7,
            "skipped": 1,
            "errors": 2,
            "error_groups": {"HTTP 504": 2},
            "failed_user_ids": ["@everyone"],
        }
        quality = health.evaluate_quality(report)

        payload = health.build_discord_payload(
            report,
            quality,
            repo_full_name="owner/repo",
            run_url="https://github.example/run",
        )

        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn("Varovanie", payload["content"])
        self.assertIn("20.0%", payload["embeds"][0]["description"])
        self.assertNotIn("@everyone", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
