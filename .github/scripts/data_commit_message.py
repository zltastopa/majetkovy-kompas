#!/usr/bin/env python3
"""Build a descriptive commit message for daily data snapshots."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataDiffSummary:
    added: int
    modified: int
    removed: int
    total: int
    samples: tuple[str, ...]

    @property
    def changed(self) -> int:
        return self.added + self.modified + self.removed


def run_git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.quotePath=false", *args], cwd=repo, text=True
    ).strip()


def summarize_staged_data(repo: Path) -> DataDiffSummary:
    output = run_git(repo, "diff", "--cached", "--name-status", "--", "data")
    added = 0
    modified = 0
    removed = 0
    samples = []

    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        if not path.startswith("data/") or not path.endswith(".yaml"):
            continue

        user_id = Path(path).stem
        if status.startswith("A"):
            added += 1
        elif status.startswith("D"):
            removed += 1
        else:
            modified += 1

        if len(samples) < 8:
            samples.append(user_id)

    total = sum(1 for _ in (repo / "data").glob("*.yaml"))
    return DataDiffSummary(
        added=added,
        modified=modified,
        removed=removed,
        total=total,
        samples=tuple(samples),
    )


def subject_for(summary: DataDiffSummary) -> str:
    if not summary.changed:
        return "data: refresh daily data checks"
    if summary.added and not summary.modified and not summary.removed:
        return "data: add daily declarations"
    if summary.modified and not summary.added and not summary.removed:
        return "data: update daily declarations"
    if summary.removed and not summary.added and not summary.modified:
        return "data: remove daily declarations"
    return "data: update daily baseline with mixed declaration changes"


def build_message(summary: DataDiffSummary, latest_year: str) -> str:
    year = latest_year.strip() or "unknown"
    message = [
        subject_for(summary),
        "",
        "Previously daily data commits used a generic baseline message; this",
        "commit records the shape of the scrape delta while keeping the",
        "subject free of a reporting year for site generation.",
        "",
        "- Re-scrape latest NR SR declarations into data/",
        (
            "- Declaration files changed: "
            f"+{summary.added} new, ~{summary.modified} updated, "
            f"-{summary.removed} removed"
        ),
        f"- Total declaration files after scrape: {summary.total}",
        f"- Latest reporting year detected: {year}",
        "- Updated content hashes in data/_checks/content-hashes.json",
    ]
    if summary.samples:
        message.append("- Sample changed UserIds: " + ", ".join(summary.samples))
    return "\n".join(message) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the daily data commit message"
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--latest-year", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary = summarize_staged_data(args.repo)
    args.output.write_text(
        build_message(summary, args.latest_year),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
