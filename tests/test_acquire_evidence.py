from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path

import acquire_evidence
import scrape


class PreparedRequest:
    def __init__(self, method: str, url: str, body: str | None = None):
        self.method = method
        self.url = url
        self.body = body
        self.headers = {"User-Agent": "test-agent"}


class Response:
    def __init__(self, status_code: int, text: str, request: PreparedRequest | None = None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = ""
        self.request = request

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class AcquireEvidenceTests(unittest.TestCase):
    def test_acquire_single_user_writes_raw_capture_without_yaml(self):
        calls = []
        original_request = scrape.requests.request

        def fake_request(method, url, timeout, **kwargs):
            calls.append((method, url, kwargs))
            return Response(
                200,
                "<html>raw declaration</html>",
                request=PreparedRequest(method, url),
            )

        scrape.requests.request = fake_request
        try:
            with tempfile.TemporaryDirectory() as tmp:
                evidence_dir = Path(tmp) / "evidence"

                report = acquire_evidence.acquire_package(
                    evidence_dir,
                    user_id="Test.User",
                    request_retries=0,
                    request_timeout=2,
                )

                manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
                body_paths = [evidence_dir / capture["body_path"] for capture in manifest["captures"]]

                self.assertEqual(report["captured"], 1)
                self.assertEqual(report["errors"], 0)
                self.assertEqual(manifest["captures"][0]["purpose"], "declaration")
                self.assertEqual(manifest["captures"][0]["metadata"]["user_id"], "Test.User")
                self.assertEqual(body_paths[0].read_text(encoding="utf-8"), "<html>raw declaration</html>")
                self.assertEqual(list(evidence_dir.glob("*.yaml")), [])
        finally:
            scrape.requests.request = original_request

        self.assertEqual(calls[0][0], "GET")

    def test_acquire_package_uses_configured_worker_count(self):
        original_executor = acquire_evidence.ThreadPoolExecutor
        seen_workers = []

        class SynchronousExecutor:
            def __init__(self, max_workers):
                seen_workers.append(max_workers)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def submit(self, fn, *args, **kwargs):
                future = Future()
                try:
                    future.set_result(fn(*args, **kwargs))
                except Exception as exc:
                    future.set_exception(exc)
                return future

        acquire_evidence.ThreadPoolExecutor = SynchronousExecutor
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ids_file = Path(tmp) / "ids.txt"
                ids_file.write_text("A\nB\n", encoding="utf-8")
                acquire_evidence.acquire_package(
                    Path(tmp) / "evidence",
                    user_ids_file=ids_file,
                    workers=3,
                    acquire_one=lambda _package, _user_id, *, year=None: None,
                    request_retries=0,
                    request_timeout=2,
                )
        finally:
            acquire_evidence.ThreadPoolExecutor = original_executor

        self.assertEqual(seen_workers, [3])

    def test_acquire_default_captures_alternate_year_when_initial_page_has_no_data(self):
        initial_html = """
        <html>
          <body>
            <input id="__VIEWSTATE" value="view" />
            <input id="__EVENTVALIDATION" value="valid" />
            <input id="__VIEWSTATEGENERATOR" value="gen" />
            <select id="_sectionLayoutContainer_ctl01_OznameniaList">
              <option selected value="new">2025</option>
              <option value="old">2024</option>
            </select>
            <div id="_sectionLayoutContainer_ctl01_OutPut">v štádiu spracovania</div>
          </body>
        </html>
        """
        declaration_html = """
        <html>
          <body>
            <div id="_sectionLayoutContainer_ctl01_OutPut">
              <table class="oznamenie_table">
                <tr><td class="label">Interné číslo:</td><td class="value">Test.User</td></tr>
                <tr><td class="label">oznámenie za rok:</td><td class="value">2024</td></tr>
              </table>
            </div>
          </body>
        </html>
        """
        responses = [Response(200, initial_html), Response(200, declaration_html)]
        original_request = scrape.requests.request

        def fake_request(method, url, timeout, **kwargs):
            response = responses.pop(0)
            response.request = PreparedRequest(
                method,
                url,
                body="_sectionLayoutContainer%24ctl01%24OznameniaList=old" if method == "POST" else None,
            )
            return response

        scrape.requests.request = fake_request
        try:
            with tempfile.TemporaryDirectory() as tmp:
                evidence_dir = Path(tmp) / "evidence"

                acquire_evidence.acquire_package(
                    evidence_dir,
                    user_id="Test.User",
                    request_retries=0,
                    request_timeout=2,
                )

                manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))

                self.assertEqual([capture["method"] for capture in manifest["captures"]], ["GET", "POST"])
                self.assertEqual(manifest["captures"][1]["metadata"]["requested_year"], 2024)
                self.assertEqual(
                    manifest["captures"][1]["request_body"]["_sectionLayoutContainer$ctl01$OznameniaList"],
                    "old",
                )
                self.assertEqual(manifest["captures"][1]["actual_request"]["method"], "POST")
                self.assertIn(
                    "_sectionLayoutContainer%24ctl01%24OznameniaList=old",
                    manifest["captures"][1]["actual_request"]["body"],
                )
        finally:
            scrape.requests.request = original_request


if __name__ == "__main__":
    unittest.main()
