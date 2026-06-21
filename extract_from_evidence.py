#!/usr/bin/env python3
"""Extract declaration YAML from a raw evidence package."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import evidence
import scrape


def load_manifest(evidence_dir: Path) -> dict[str, Any]:
    evidence.validate_manifest_hash(evidence_dir)
    return json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))


def verified_body(evidence_dir: Path, capture: dict[str, Any]) -> str:
    body_path = evidence_dir / capture["body_path"]
    body = body_path.read_bytes()
    actual_hash = evidence.sha256_bytes(body)
    if actual_hash != capture["body_sha256"]:
        raise ValueError(
            f"{capture['body_path']} hash mismatch: "
            f"expected {capture['body_sha256']}, got {actual_hash}"
        )
    return body.decode("utf-8", errors="replace")


def declaration_captures(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    captures = [
        capture
        for capture in manifest.get("captures", [])
        if capture.get("purpose") == "declaration" and capture.get("status_code") == 200
    ]
    return sorted(captures, key=lambda item: item.get("id", ""))


def target_year_for(captures: list[dict[str, Any]]) -> int | None:
    for capture in captures:
        metadata = capture.get("metadata") or {}
        if not metadata.get("explicit_requested_year"):
            continue
        year = metadata.get("requested_year")
        if isinstance(year, int):
            return year
    return None


def parse_capture(evidence_dir: Path, capture: dict[str, Any]) -> dict[str, Any] | None:
    html = verified_body(evidence_dir, capture)
    return scrape.parse_declaration(html)


def extract_package(evidence_dir: Path, data_dir: Path, *, report_json: Path | None = None) -> dict[str, Any]:
    manifest = load_manifest(evidence_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    by_user: dict[str, list[dict[str, Any]]] = {}
    for capture in declaration_captures(manifest):
        metadata = capture.get("metadata") or {}
        user_id = metadata.get("user_id")
        if user_id:
            by_user.setdefault(user_id, []).append(capture)

    for user_id in sorted(by_user):
        captures = by_user[user_id]
        target_year = target_year_for(captures)
        parsed_any = False
        wrote_data = False
        last_error: Exception | None = None
        for capture in captures:
            try:
                data = parse_capture(evidence_dir, capture)
            except Exception as exc:
                last_error = exc
                continue
            if not data:
                continue
            parsed_any = True
            if target_year is not None and data.get("year") != target_year:
                continue
            (data_dir / f"{user_id}.yaml").write_text(
                scrape.dump_yaml(data),
                encoding="utf-8",
            )
            results.append({"user_id": user_id, "status": "scraped", "year": data.get("year")})
            wrote_data = True
            break
        if wrote_data:
            continue
        if last_error:
            results.append(
                {
                    "user_id": user_id,
                    "status": "error",
                    "error_type": type(last_error).__name__,
                    "error_message": str(last_error),
                }
            )
        else:
            reason = "requested year not captured" if target_year is not None and parsed_any else "no data"
            results.append({"user_id": user_id, "status": "skipped", "reason": reason})

    counts = Counter(result["status"] for result in results)
    report = {
        "evidence_manifest_sha256": evidence.sha256_file(evidence_dir / "manifest.json"),
        "total": len(results),
        "scraped": counts["scraped"],
        "skipped": counts["skipped"],
        "errors": counts["error"],
        "results": results,
    }
    if report_json:
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract declaration YAML from an evidence package")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()

    report = extract_package(args.evidence_dir, args.data_dir, report_json=args.report_json)
    print(
        f"Extracted: {report['scraped']} scraped, "
        f"{report['skipped']} skipped, {report['errors']} errors"
    )


if __name__ == "__main__":
    main()
