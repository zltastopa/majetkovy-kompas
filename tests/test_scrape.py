from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import requests

import scrape


class Response:
    def __init__(self, status_code: int, text: str = "ok"):
        self.status_code = status_code
        self.text = text
        self.headers = {}
        self.encoding = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Server Error",
                response=self,
            )


class ScrapeTransportTests(unittest.TestCase):
    def test_request_with_retries_recovers_from_transient_http_error(self):
        responses = [Response(504), Response(200, "ok")]
        calls = []
        sleeps = []
        original_request = scrape.requests.request
        original_sleep = scrape.time.sleep
        original_random = scrape.random.uniform

        def fake_request(method, url, timeout, **kwargs):
            calls.append((method, url, timeout, kwargs))
            return responses.pop(0)

        scrape.requests.request = fake_request
        scrape.time.sleep = sleeps.append
        scrape.random.uniform = lambda _start, _end: 0
        try:
            response = scrape.request_with_retries(
                "GET",
                "https://example.test",
                retries=1,
            )
        finally:
            scrape.requests.request = original_request
            scrape.time.sleep = original_sleep
            scrape.random.uniform = original_random

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [1])

    def test_request_with_retries_uses_configured_defaults(self):
        calls = []
        original_request = scrape.requests.request
        original_retries = scrape.REQUEST_RETRIES
        original_timeout = scrape.REQUEST_TIMEOUT

        def fake_request(method, url, timeout, **kwargs):
            calls.append((method, url, timeout))
            return Response(200)

        scrape.requests.request = fake_request
        scrape.REQUEST_RETRIES = 0
        scrape.REQUEST_TIMEOUT = 12
        try:
            response = scrape.request_with_retries("GET", "https://example.test")
        finally:
            scrape.requests.request = original_request
            scrape.REQUEST_RETRIES = original_retries
            scrape.REQUEST_TIMEOUT = original_timeout

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [("GET", "https://example.test", 12)])

    def test_write_scrape_report_records_failed_ids_and_error_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            failed_path = Path(tmp) / "failed.txt"

            scrape.write_scrape_report(
                report_path,
                failed_path,
                [
                    scrape.ScrapeResult("Alice", "scraped", year=2025),
                    scrape.ScrapeResult("Bob", "skipped"),
                    scrape.ScrapeResult(
                        "Carol",
                        "error",
                        error_type="HTTPError",
                        error_status=504,
                        error_message="504 Server Error",
                    ),
                ],
            )

            report = json.loads(report_path.read_text(encoding="utf-8"))
            failed_text = failed_path.read_text(encoding="utf-8")

        self.assertEqual(report["total"], 3)
        self.assertEqual(report["scraped"], 1)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["error_rate"], 1 / 3)
        self.assertEqual(report["error_groups"]["HTTP 504"], 1)
        self.assertEqual(report["failed_user_ids"], ["Carol"])
        self.assertEqual(failed_text, "Carol\n")


if __name__ == "__main__":
    unittest.main()
