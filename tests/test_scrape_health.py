from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


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
