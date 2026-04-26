#!/usr/bin/env python3
"""Post a concise daily data-change summary to Discord.

The workflow calls this after the new data commit has been pushed.  The
summary is based on canonical declaration hashes first, then YAML data for
human-readable details.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


NRSR_DECL_URL = "https://www.nrsr.sk/web/Default.aspx?sid=vnf/oznamenie&UserId="
MANIFEST_PATH = "data/_checks/content-hashes.json"
MAX_ITEMS = 8


FIELD_LABELS = {
    "year": "rok",
    "filed": "typ podania",
    "declaration_id": "ID priznania",
    "name": "meno",
    "income": "príjmy",
    "employment": "zamestnanie",
    "business_activity": "podnikanie",
    "positions": "funkcie",
    "real_estate": "nehnuteľnosti",
    "movable_property": "hnuteľný majetok",
    "obligations": "záväzky",
    "vehicles": "vozidlá",
    "gifts": "dary",
    "property_rights": "majetkové práva",
    "public_function": "verejná funkcia",
    "public_functions": "verejné funkcie",
    "incompatibility": "nezlučiteľnosť",
    "use_of_others_real_estate": "užívanie nehnuteľností",
}

LIST_FIELDS = {
    "positions",
    "real_estate",
    "movable_property",
    "obligations",
    "vehicles",
}


def run_git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo,
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()


def read_json_at(repo: Path, commit: str, path: str) -> dict[str, Any]:
    if not commit:
        return {}
    try:
        content = run_git(repo, "show", f"{commit}:{path}")
    except subprocess.CalledProcessError:
        return {}
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def read_yaml_at(repo: Path, commit: str, user_id: str) -> dict[str, Any] | None:
    if not commit:
        return None
    try:
        content = run_git(repo, "show", f"{commit}:data/{user_id}.yaml")
    except subprocess.CalledProcessError:
        return None
    try:
        value = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return {"name": user_id, "_load_error": "YAML parse error"}
    return value if isinstance(value, dict) else {"name": user_id}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def total_income(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    return parse_int(value.get("public_function")) + parse_int(value.get("other"))


def parse_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def fmt_currency(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " EUR"


def fmt_count_delta(old_value: Any, new_value: Any) -> str:
    old_count = len(old_value) if isinstance(old_value, list) else 0
    new_count = len(new_value) if isinstance(new_value, list) else 0
    delta = new_count - old_count
    sign = "+" if delta > 0 else ""
    return f"{old_count} -> {new_count} ({sign}{delta})"


def list_item_delta(old_value: Any, new_value: Any) -> tuple[int, int]:
    old_items = (
        {canonical(item) for item in old_value}
        if isinstance(old_value, list)
        else set()
    )
    new_items = (
        {canonical(item) for item in new_value}
        if isinstance(new_value, list)
        else set()
    )
    return len(new_items - old_items), len(old_items - new_items)


def clean_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text.replace("@", "@\u200b")


def discord_text(value: Any) -> str:
    text = clean_text(value)
    for char in "\\`*_~|<>[]":
        text = text.replace(char, "\\" + char)
    return text


def title_case_name(value: str) -> str:
    parts = []
    for part in value.split():
        if part.endswith(".") or part.endswith(","):
            base = part.rstrip(".,")
            if base and any(char.islower() for char in base):
                parts.append(part)
            elif base and base.isupper() and len(base) <= 5:
                parts.append(part)
            else:
                parts.append(part.capitalize())
        elif part.isupper() and len(part) > 3:
            parts.append(part.capitalize())
        else:
            parts.append(part)
    return " ".join(parts)


def person_name(
    user_id: str,
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any] | None,
) -> str:
    data = new_data or old_data or {}
    return title_case_name(discord_text(data.get("name") or user_id))


def role_text(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    role = data.get("public_functions") or data.get("public_function") or ""
    if isinstance(role, list):
        role = " · ".join(discord_text(item) for item in role[:2] if item)
    return discord_text(role)


def declaration_counts(data: dict[str, Any] | None) -> list[str]:
    if not data:
        return []
    parts = []
    if data.get("year"):
        parts.append(f"rok {data['year']}")
    for key, labels in [
        ("real_estate", ("nehnuteľnosť", "nehnuteľnosti", "nehnuteľností")),
        ("obligations", ("záväzok", "záväzky", "záväzkov")),
        ("vehicles", ("vozidlo", "vozidlá", "vozidiel")),
        ("movable_property", ("hnuteľná vec", "hnuteľné veci", "hnuteľných vecí")),
    ]:
        value = data.get(key)
        if isinstance(value, list) and value:
            parts.append(sk_count(len(value), labels))
    return parts


def sk_count(count: int, labels: tuple[str, str, str]) -> str:
    if count == 1:
        label = labels[0]
    elif 2 <= count <= 4:
        label = labels[1]
    else:
        label = labels[2]
    return f"{count} {label}"


def changed_fields(
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any] | None,
) -> list[str]:
    if not old_data or not new_data:
        return []

    fields = []
    keys = list(dict.fromkeys([*old_data.keys(), *new_data.keys()]))
    for key in keys:
        if (
            key == "public_function"
            and old_data.get("public_functions") != new_data.get("public_functions")
        ):
            continue
        old_value = old_data.get(key)
        new_value = new_data.get(key)
        if old_value == new_value:
            continue

        label = FIELD_LABELS.get(key, key)
        if key == "income":
            old_total = total_income(old_value)
            new_total = total_income(new_value)
            delta = new_total - old_total
            sign = "+" if delta > 0 else ""
            fields.append(
                f"{label}: {fmt_currency(old_total)} -> "
                f"{fmt_currency(new_total)} ({sign}{fmt_currency(delta)})"
            )
        elif key in LIST_FIELDS:
            added, removed = list_item_delta(old_value, new_value)
            detail = fmt_count_delta(old_value, new_value)
            if added or removed:
                detail += f", +{added}/-{removed} položiek"
            fields.append(f"{label}: {detail}")
        elif key in {"year", "filed", "declaration_id", "name"}:
            fields.append(
                f"{label}: {discord_text(old_value)} -> {discord_text(new_value)}"
            )
        else:
            fields.append(label)

    return fields


def classify_changes(
    repo: Path,
    previous: str,
    current: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    old_manifest = read_json_at(repo, previous, MANIFEST_PATH)
    new_manifest = read_json_at(repo, current, MANIFEST_PATH)
    old_declarations = old_manifest.get("declarations", {})
    new_declarations = new_manifest.get("declarations", {})

    old_ids = set(old_declarations)
    new_ids = set(new_declarations)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    modified = sorted(
        user_id
        for user_id in old_ids & new_ids
        if old_declarations[user_id].get("content_sha256")
        != new_declarations[user_id].get("content_sha256")
    )

    if not previous:
        removed = []
        modified = []
        if new_declarations:
            added = sorted(new_ids)
        else:
            output = run_git(repo, "ls-tree", "-r", "--name-only", current, "--", "data")
            added = sorted(
                Path(line).stem
                for line in output.splitlines()
                if line.startswith("data/") and line.endswith(".yaml")
            )
    elif not old_declarations or not new_declarations:
        added = []
        modified = []
        removed = []
        changed_files = run_git(
            repo,
            "diff",
            "--name-status",
            previous,
            current,
            "--",
            "data/*.yaml",
        )
        for line in changed_files.splitlines():
            status, path = line.split(maxsplit=1)
            user_id = Path(path).stem
            if status.startswith("A"):
                added.append(user_id)
            elif status.startswith("D"):
                removed.append(user_id)
            else:
                modified.append(user_id)

    items = []
    for kind, ids in [("added", added), ("modified", modified), ("removed", removed)]:
        for user_id in ids:
            old_data = (
                read_yaml_at(repo, previous, user_id) if kind != "added" else None
            )
            new_data = (
                read_yaml_at(repo, current, user_id) if kind != "removed" else None
            )
            fields = changed_fields(old_data, new_data)
            items.append(
                {
                    "kind": kind,
                    "user_id": user_id,
                    "name": person_name(user_id, old_data, new_data),
                    "role": role_text(new_data or old_data),
                    "details": fields,
                    "counts": declaration_counts(new_data or old_data),
                }
            )

    old_count = old_manifest.get("count")
    new_count = new_manifest.get("count")
    if old_count is None:
        old_count = count_yaml_at(repo, previous)
    if new_count is None:
        new_count = count_yaml_at(repo, current)

    stats = {
        "old_count": old_count,
        "new_count": new_count,
        "added": len(added),
        "modified": len(modified),
        "removed": len(removed),
        "dataset_changed": old_manifest.get("dataset_sha256")
        != new_manifest.get("dataset_sha256"),
    }
    return stats, sorted(items, key=rank_item)


def count_yaml_at(repo: Path, commit: str) -> int:
    if not commit:
        return 0
    try:
        output = run_git(repo, "ls-tree", "-r", "--name-only", commit, "--", "data")
    except subprocess.CalledProcessError:
        return 0
    return sum(
        1
        for line in output.splitlines()
        if line.startswith("data/") and line.endswith(".yaml")
    )


def rank_item(item: dict[str, Any]) -> tuple[int, int, str]:
    kind_rank = {"removed": 0, "modified": 1, "added": 2}
    detail_text = " ".join(item.get("details") or [])
    priority = 0
    markers = ["rok:", "príjmy:", "nehnuteľnosti:", "záväzky:", "verejné funkcie:"]
    for marker in markers:
        if marker in detail_text:
            priority -= 1
    return (kind_rank.get(item["kind"], 9), priority, item["name"].casefold())


def github_links(repo_full_name: str, previous: str, current: str) -> dict[str, str]:
    base = f"https://github.com/{repo_full_name}" if repo_full_name else ""
    if not base:
        return {"commit": "", "compare": ""}
    return {
        "commit": f"{base}/commit/{current}",
        "compare": f"{base}/compare/{previous}...{current}" if previous else "",
    }


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def fit_lines(lines: list[str], overflow_line: str | None, limit: int) -> str:
    selected = []
    reserved = len(overflow_line) + 1 if overflow_line else 0
    used = 0
    for line in lines:
        line_length = len(line) + (1 if selected else 0)
        if used + line_length + reserved > limit:
            break
        selected.append(line)
        used += line_length
    if overflow_line:
        selected.append(overflow_line)
    return "\n".join(selected) if selected else (overflow_line or "")


def item_line(item: dict[str, Any]) -> str:
    icon = {"added": "nové", "modified": "upravené", "removed": "odstránené"}[
        item["kind"]
    ]
    name = truncate(item["name"], 120)
    url = f"{NRSR_DECL_URL}{item['user_id']}"
    detail_parts = []
    if item.get("details"):
        detail_parts.extend(truncate(detail, 180) for detail in item["details"][:2])
    elif item.get("counts"):
        detail_parts.extend(item["counts"][:4])
    if item.get("role"):
        detail_parts.append(truncate(item["role"], 220))
    detail = " · ".join(detail_parts)
    return truncate(
        f"- **{icon}** [{name}](<{url}>)"
        + (f" - {detail}" if detail else ""),
        500,
    )


def build_payload(
    repo: Path,
    repo_full_name: str,
    run_url: str,
    latest_year: str,
) -> dict[str, Any]:
    current = run_git(repo, "rev-parse", "HEAD")
    try:
        previous = run_git(repo, "rev-parse", "HEAD^")
    except subprocess.CalledProcessError:
        previous = ""
    stats, items = classify_changes(repo, previous, current)
    links = github_links(repo_full_name, previous, current)

    summary = (
        f"+{stats['added']} nové, {stats['modified']} upravené, "
        f"{stats['removed']} odstránené"
    )
    count_line = (
        f"Dataset: {stats['old_count']} -> {stats['new_count']} priznaní"
        f" · najvyšší zistený rok: {latest_year or 'neznámy'}"
    )
    headline = f"Denná kontrola majetkových priznaní: {summary}"

    lines = [item_line(item) for item in items[:MAX_ITEMS]]
    overflow_count = max(0, len(items) - MAX_ITEMS)
    overflow_line = (
        f"- ...a ďalších {overflow_count} zmien v porovnaní."
        if overflow_count
        else None
    )
    if not lines:
        lines.append("- Zmenil sa len technický manifest, bez zmien v YAML priznaniach.")
    changes_value = fit_lines(lines, overflow_line, 1024)

    description_parts = [count_line]
    if links["compare"]:
        description_parts.append(f"[Otvoriť GitHub porovnanie](<{links['compare']}>)")
    if run_url:
        description_parts.append(f"[Beh workflow](<{run_url}>)")

    return {
        "content": headline,
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": "Majetkový kompas - denná zmena dát",
                "description": "\n".join(description_parts),
                "color": 0x2F855A if stats["removed"] == 0 else 0xC53030,
                "fields": [
                    {"name": "Súhrn", "value": summary, "inline": False},
                    {
                        "name": "Ukážka zmien",
                        "value": changes_value,
                        "inline": False,
                    },
                ],
                "footer": {"text": f"Commit {current[:12]}"},
            }
        ],
    }


def post_payload(webhook_url: str, payload: dict[str, Any], attempts: int = 3) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send Discord summary for a data commit"
    )
    parser.add_argument("--data-repo", type=Path, default=Path("."))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-url", default="")
    parser.add_argument("--latest-year", default="")
    parser.add_argument(
        "--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL", "")
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = build_payload(args.data_repo, args.repo, args.run_url, args.latest_year)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not args.webhook_url:
        print("DISCORD_WEBHOOK_URL is not configured; skipping Discord notification.")
        return 0

    try:
        post_payload(args.webhook_url, payload)
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"Failed to send Discord notification: {exc}", file=sys.stderr)
        return 1

    print("Discord notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
