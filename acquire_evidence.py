#!/usr/bin/env python3
"""Acquire raw NR SR declaration evidence without extracting YAML."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import evidence
import scrape


def response_body(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    return str(getattr(response, "text", "")).encode("utf-8")


def prepared_request_record(response: Any) -> dict[str, Any] | None:
    request = getattr(response, "request", None)
    if request is None:
        return None
    body = getattr(request, "body", None)
    body_value, body_bytes = evidence.request_body_record(body)
    return {
        "method": getattr(request, "method", None),
        "url": getattr(request, "url", None),
        "headers": dict(getattr(request, "headers", {}) or {}),
        "body": body_value,
        "body_sha256": evidence.sha256_bytes(body_bytes) if body_bytes else None,
    }


def capture_request(
    package: evidence.EvidencePackage,
    *,
    purpose: str,
    method: str,
    url: str,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    response = scrape.request_with_retries(method, url, **kwargs)
    response.encoding = "utf-8"
    package.capture_response(
        purpose=purpose,
        method=method,
        url=url,
        status_code=response.status_code,
        response_headers=dict(response.headers),
        body=response_body(response),
        request_body=kwargs.get("data"),
        actual_request=prepared_request_record(response),
        metadata=metadata,
    )
    response.raise_for_status()
    return response


def load_target_politicians(
    package: evidence.EvidencePackage,
    *,
    user_id: str | None = None,
    user_ids_file: Path | None = None,
    only_supplementary: bool = False,
    supplementary_ids: Path | None = None,
) -> list[dict[str, str]]:
    if user_id:
        return [{"user_id": user_id, "display_name": user_id}]
    if user_ids_file:
        return [
            {"user_id": uid, "display_name": uid}
            for uid in scrape.load_supplementary_ids(user_ids_file)
        ]
    if only_supplementary:
        if not supplementary_ids or not supplementary_ids.exists():
            raise ValueError("--only-supplementary requires --supplementary-ids")
        return [
            {"user_id": uid, "display_name": uid}
            for uid in scrape.load_supplementary_ids(supplementary_ids)
        ]

    response = capture_request(
        package,
        purpose="politician-list",
        method="GET",
        url=scrape.LIST_URL,
    )
    politicians = scrape.parse_politician_list(response.text)
    if supplementary_ids and supplementary_ids.exists():
        existing_ids = {politician["user_id"] for politician in politicians}
        for uid in scrape.load_supplementary_ids(supplementary_ids):
            if uid not in existing_ids:
                politicians.append({"user_id": uid, "display_name": uid})
                existing_ids.add(uid)
    return politicians


def postback_data(html: str, year: int) -> dict[str, str] | None:
    soup = scrape.BeautifulSoup(html, "html.parser")
    dropdown = soup.select_one("#_sectionLayoutContainer_ctl01_OznameniaList")
    if not dropdown:
        return None
    year_map = {}
    for opt in dropdown.select("option"):
        try:
            candidate_year = int(opt.get_text(strip=True))
        except ValueError:
            continue
        year_map[candidate_year] = opt.get("value", "")

    if year not in year_map:
        return None

    selected = dropdown.select_one("option[selected]")
    if selected and selected.get_text(strip=True) == str(year):
        return None

    viewstate = soup.select_one("#__VIEWSTATE")
    validation = soup.select_one("#__EVENTVALIDATION")
    viewstate_gen = soup.select_one("#__VIEWSTATEGENERATOR")
    return {
        "__EVENTTARGET": "_sectionLayoutContainer$ctl01$OznameniaList",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": viewstate["value"] if viewstate else "",
        "__EVENTVALIDATION": validation["value"] if validation else "",
        "__VIEWSTATEGENERATOR": viewstate_gen["value"] if viewstate_gen else "",
        "_sectionLayoutContainer$ctl01$OznameniaList": year_map[year],
    }


def capture_declaration_year(
    package: evidence.EvidencePackage,
    *,
    user_id: str,
    url: str,
    initial_html: str,
    year: int,
) -> None:
    post_data = postback_data(initial_html, year)
    if not post_data:
        return
    capture_request(
        package,
        purpose="declaration",
        method="POST",
        url=url,
        data=post_data,
        metadata={"user_id": user_id, "requested_year": year, "postback": True},
    )


def acquire_declaration(package: evidence.EvidencePackage, user_id: str, *, year: int | None = None) -> None:
    url = f"{scrape.DECL_URL}{user_id}"
    response = capture_request(
        package,
        purpose="declaration",
        method="GET",
        url=url,
        metadata={"user_id": user_id, "requested_year": year},
    )
    if year is not None:
        capture_declaration_year(
            package,
            user_id=user_id,
            url=url,
            initial_html=response.text,
            year=year,
        )
        return

    if scrape.parse_declaration(response.text):
        return

    available_years, selected_year = scrape.parse_available_years(response.text)
    for candidate_year in available_years:
        if candidate_year == selected_year:
            continue
        capture_declaration_year(
            package,
            user_id=user_id,
            url=url,
            initial_html=response.text,
            year=candidate_year,
        )


def acquire_package(
    evidence_dir: Path,
    *,
    user_id: str | None = None,
    year: int | None = None,
    limit: int | None = None,
    user_ids_file: Path | None = None,
    only_supplementary: bool = False,
    supplementary_ids: Path | None = None,
    request_retries: int = scrape.REQUEST_RETRIES,
    request_timeout: float = scrape.REQUEST_TIMEOUT,
    request_delay: float = scrape.REQUEST_DELAY,
    request_jitter: float = scrape.REQUEST_JITTER,
    report_json: Path | None = None,
) -> dict[str, Any]:
    scrape.REQUEST_RETRIES = request_retries
    scrape.REQUEST_TIMEOUT = request_timeout
    scrape.REQUEST_DELAY = request_delay
    scrape.REQUEST_JITTER = request_jitter

    package = evidence.EvidencePackage(evidence_dir)
    results: list[dict[str, Any]] = []
    try:
        politicians = load_target_politicians(
            package,
            user_id=user_id,
            user_ids_file=user_ids_file,
            only_supplementary=only_supplementary,
            supplementary_ids=supplementary_ids,
        )
        if limit:
            politicians = politicians[:limit]

        total = len(politicians)
        for index, politician in enumerate(politicians, start=1):
            uid = politician["user_id"]
            try:
                acquire_declaration(package, uid, year=year)
                results.append({"user_id": uid, "status": "captured"})
                print(f"[{index}/{total}] {uid} captured", file=sys.stderr)
            except Exception as exc:
                results.append(
                    {
                        "user_id": uid,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
                print(f"[{index}/{total}] {uid} ERROR: {exc}", file=sys.stderr)
    finally:
        counts = Counter(result["status"] for result in results)
        report = {
            "total": len(results),
            "captured": counts["captured"],
            "errors": counts["error"],
            "results": results,
        }
        if report_json:
            report_json.parent.mkdir(parents=True, exist_ok=True)
            report_json.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            package.finalize(artifacts=[report_json])
        else:
            package.finalize()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Acquire raw declaration evidence")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--user-id")
    parser.add_argument("--year", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--user-ids-file", type=Path)
    parser.add_argument("--only-supplementary", action="store_true")
    parser.add_argument("--supplementary-ids", type=Path)
    parser.add_argument("--request-retries", type=int, default=scrape.REQUEST_RETRIES)
    parser.add_argument("--request-timeout", type=float, default=scrape.REQUEST_TIMEOUT)
    parser.add_argument("--request-delay", type=float, default=scrape.REQUEST_DELAY)
    parser.add_argument("--request-jitter", type=float, default=scrape.REQUEST_JITTER)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()

    report = acquire_package(
        args.evidence_dir,
        user_id=args.user_id,
        year=args.year,
        limit=args.limit,
        user_ids_file=args.user_ids_file,
        only_supplementary=args.only_supplementary,
        supplementary_ids=args.supplementary_ids,
        request_retries=args.request_retries,
        request_timeout=args.request_timeout,
        request_delay=args.request_delay,
        request_jitter=args.request_jitter,
        report_json=args.report_json,
    )
    print(f"Acquired: {report['captured']} captured, {report['errors']} errors")


if __name__ == "__main__":
    main()
