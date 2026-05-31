#!/usr/bin/env python3
"""Summarize scrape health and optionally notify Discord."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


USER_AGENT = "majetkovy-kompas-github-actions/1.0"
MAX_FAILED_IDS = 10


def load_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"total": 0, "scraped": 0, "skipped": 0, "errors": 0, "results": []}
    return value if isinstance(value, dict) else {}


def combine_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    by_user: dict[str, dict[str, Any]] = {}
    for report in reports:
        for result in report.get("results", []):
            user_id = result.get("user_id")
            if isinstance(user_id, str) and user_id:
                by_user[user_id] = result

    results = list(by_user.values())
    counts = Counter(result.get("status") for result in results)
    error_groups = Counter(
        error_group(result)
        for result in results
        if result.get("status") == "error"
    )
    failed_ids = [
        result["user_id"]
        for result in results
        if result.get("status") == "error" and result.get("user_id")
    ]
    total = len(results)
    return {
        "total": total,
        "scraped": counts["scraped"],
        "skipped": counts["skipped"],
        "errors": counts["error"],
        "error_rate": (counts["error"] / total) if total else 0,
        "error_groups": dict(sorted(error_groups.items())),
        "failed_user_ids": sorted(failed_ids),
        "results": sorted(results, key=lambda item: item.get("user_id", "")),
    }


def error_group(result: dict[str, Any]) -> str:
    status = result.get("error_status")
    if status:
        return f"HTTP {status}"
    return str(result.get("error_type") or "Unknown")


def evaluate_quality(
    report: dict[str, Any],
    *,
    warn_threshold: float = 0.10,
    alert_threshold: float = 0.25,
    suppress_threshold: float = 0.25,
    fail_threshold: float = 0.50,
) -> dict[str, Any]:
    total = int(report.get("total") or 0)
    errors = int(report.get("errors") or 0)
    rate = (errors / total) if total else 0

    hard_fail = total > 0 and rate >= fail_threshold
    if hard_fail:
        level = "error"
    elif total > 0 and rate >= alert_threshold:
        level = "alert"
    elif total > 0 and rate >= warn_threshold:
        level = "warning"
    else:
        level = "ok"

    return {
        "level": level,
        "error_rate": rate,
        "commit_ok": not (total > 0 and rate >= suppress_threshold),
        "hard_fail": hard_fail,
        "notify": level != "ok",
    }


def markdown_summary(report: dict[str, Any], quality: dict[str, Any]) -> str:
    groups = report.get("error_groups") or {}
    top_groups = sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:5]
    group_text = ", ".join(f"{name}: {count}" for name, count in top_groups) or "none"
    failed_ids = report.get("failed_user_ids") or []
    failed_text = ", ".join(failed_ids[:MAX_FAILED_IDS]) or "none"
    if len(failed_ids) > MAX_FAILED_IDS:
        failed_text += f", ... +{len(failed_ids) - MAX_FAILED_IDS} more"

    return "\n".join(
        [
            "## Scrape health",
            "",
            f"- Level: `{quality['level']}`",
            f"- Targets: {report.get('total', 0)}",
            f"- Scraped: {report.get('scraped', 0)}",
            f"- Skipped/no data: {report.get('skipped', 0)}",
            f"- Errors: {report.get('errors', 0)} ({quality['error_rate']:.1%})",
            f"- Error groups: {group_text}",
            f"- Commit allowed: `{str(quality['commit_ok']).lower()}`",
            f"- Failed IDs: {failed_text}",
            "",
        ]
    )


def clean_discord_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace("@", "@\u200b")
    for char in "\\`*_~|<>[]":
        text = text.replace(char, "\\" + char)
    return text


def build_discord_payload(
    report: dict[str, Any],
    quality: dict[str, Any],
    *,
    repo_full_name: str,
    run_url: str,
) -> dict[str, Any]:
    level_label = {
        "warning": "Varovanie",
        "alert": "Varovanie",
        "error": "Chyba",
        "ok": "OK",
    }.get(quality["level"], "Varovanie")
    groups = report.get("error_groups") or {}
    top_groups = sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:5]
    group_lines = [
        f"- {clean_discord_text(name)}: {count}"
        for name, count in top_groups
    ] or ["- žiadne"]
    failed_ids = [
        clean_discord_text(user_id)
        for user_id in (report.get("failed_user_ids") or [])[:MAX_FAILED_IDS]
    ]
    if len(report.get("failed_user_ids") or []) > MAX_FAILED_IDS:
        failed_ids.append(
            f"... +{len(report['failed_user_ids']) - MAX_FAILED_IDS} ďalších"
        )
    failed_text = ", ".join(failed_ids) if failed_ids else "žiadne"
    description = (
        f"Chybovosť scrape: {quality['error_rate']:.1%} "
        f"({report.get('errors', 0)}/{report.get('total', 0)} cieľov)\n"
        f"Spracované: {report.get('scraped', 0)}, "
        f"bez dát: {report.get('skipped', 0)}\n"
        f"Commit povolený: {'áno' if quality['commit_ok'] else 'nie'}"
    )
    if run_url:
        description += f"\n[Beh workflow](<{run_url}>)"
    if repo_full_name:
        description += f"\nRepozitár: `{clean_discord_text(repo_full_name)}`"

    return {
        "content": f"{level_label}: denný scrape Majetkového kompasu má chyby",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": "Majetkový kompas - zdravie scrape",
                "description": description,
                "color": 0xC53030 if quality["level"] == "error" else 0xD69E2E,
                "fields": [
                    {
                        "name": "Najčastejšie chyby",
                        "value": "\n".join(group_lines)[:1024],
                        "inline": False,
                    },
                    {
                        "name": "Ukážka zlyhaných UserId",
                        "value": failed_text[:1024],
                        "inline": False,
                    },
                ],
            }
        ],
    }


def post_payload(webhook_url: str, payload: dict[str, Any], attempts: int = 3) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status < 300:
                    return
                status = response.status
                retry_after = None
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = exc.headers.get("Retry-After")
            if status in {401, 403}:
                raise RuntimeError(
                    "Discord webhook returned HTTP "
                    f"{status}. Check the DISCORD_WEBHOOK_URL webhook secret."
                ) from exc
            if status < 500 and status != 429:
                raise RuntimeError(f"Discord webhook returned HTTP {status}") from exc
        except urllib.error.URLError:
            if attempt == attempts:
                raise
            status = 0
            retry_after = None

        if attempt == attempts:
            raise RuntimeError(f"Discord webhook returned HTTP {status}")

        if status == 429 and retry_after:
            try:
                delay = min(float(retry_after), 10.0)
            except ValueError:
                delay = 2.0
        else:
            delay = min(2 ** (attempt - 1), 5)
        time.sleep(delay)


def write_github_outputs(path: Path, quality: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(f"level={quality['level']}\n")
        f.write(f"commit_ok={str(quality['commit_ok']).lower()}\n")
        f.write(f"hard_fail={str(quality['hard_fail']).lower()}\n")
        f.write(f"notify={str(quality['notify']).lower()}\n")


def emit_annotation(report: dict[str, Any], quality: dict[str, Any]) -> None:
    if quality["level"] == "ok":
        return
    command = "error" if quality["level"] == "error" else "warning"
    print(
        f"::{command} title=High scraper error rate::"
        f"{report.get('errors', 0)}/{report.get('total', 0)} targets failed "
        f"({quality['error_rate']:.1%})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize scrape health")
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--combined-report", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--step-summary", type=Path)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-url", default="")
    parser.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL", ""))
    parser.add_argument("--discord", action="store_true")
    parser.add_argument("--emit-annotation", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = combine_reports([load_report(path) for path in args.report if path.exists()])
    quality = evaluate_quality(report)

    if args.combined_report:
        args.combined_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.github_output:
        write_github_outputs(args.github_output, quality)
    if args.step_summary:
        with args.step_summary.open("a", encoding="utf-8") as f:
            f.write(markdown_summary(report, quality))
    if args.emit_annotation:
        emit_annotation(report, quality)

    if args.discord and quality["notify"]:
        payload = build_discord_payload(
            report,
            quality,
            repo_full_name=args.repo,
            run_url=args.run_url,
        )
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.webhook_url:
            post_payload(args.webhook_url, payload)
            print("Discord scrape health notification sent.")
        else:
            print("DISCORD_WEBHOOK_URL is not configured; skipping Discord notification.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
