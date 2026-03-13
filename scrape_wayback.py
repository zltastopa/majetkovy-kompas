#!/usr/bin/env python3
"""
Scrape archived declaration pages from the Wayback Machine.

For officials whose data has been wiped from the live NRSR site,
this script fetches archived snapshots and parses them with the
same parser used by scrape.py.

Results are saved progressively so the script can be interrupted
and resumed.

Usage:
    uv run python scrape_wayback.py --input wayback_to_fetch.json --data-dir /tmp/wb_out
    # Resume after interruption (skips already-scraped users):
    uv run python scrape_wayback.py --input wayback_to_fetch.json --data-dir /tmp/wb_out
"""

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from scrape import parse_declaration, dump_yaml


WAYBACK_RAW_URL = "https://web.archive.org/web/{timestamp}id_/{url}"


def fetch_wayback(timestamp, url, retries=2):
    """Fetch archived page from Wayback Machine. Returns HTML or None."""
    wb_url = WAYBACK_RAW_URL.format(timestamp=timestamp, url=url)
    for attempt in range(retries):
        try:
            resp = requests.get(wb_url, timeout=60)
            if resp.status_code in (429, 503):
                return None
            resp.encoding = "utf-8"
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.ConnectionError:
            if attempt < retries - 1:
                continue
            return None
        except Exception:
            return None


def strip_wayback_toolbar(html):
    """Remove Wayback Machine toolbar/rewrite JS from archived HTML."""
    html = re.sub(
        r'<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->',
        '', html, flags=re.DOTALL
    )
    html = re.sub(
        r'https?://web\.archive\.org/web/\d+(?:id_)?/',
        '', html
    )
    return html


def process_user(uid, snapshots, data_dir):
    """Fetch and parse all snapshots for one user. Save YAMLs immediately."""
    saved = []
    for snap in snapshots:
        ts = snap["timestamp"]
        url = snap["url"]
        try:
            html = fetch_wayback(ts, url)
            if not html:
                continue
            html = strip_wayback_toolbar(html)
            data = parse_declaration(html)
            if data and data.get("year"):
                year = data["year"]
                if year in [d["year"] for d in saved]:
                    continue
                saved.append(data)
                year_dir = data_dir / str(year)
                year_dir.mkdir(parents=True, exist_ok=True)
                out_path = year_dir / f"{uid}.yaml"
                out_path.write_text(dump_yaml(data), encoding="utf-8")
        except Exception:
            pass
    return uid, saved


def main():
    parser = argparse.ArgumentParser(
        description="Scrape archived declarations from Wayback Machine"
    )
    parser.add_argument(
        "--input", type=Path, required=True,
        help="JSON file mapping UserId -> list of {timestamp, url}",
    )
    parser.add_argument(
        "--data-dir", type=Path, required=True,
        help="Output directory for YAML files (organized by year subdir)",
    )
    parser.add_argument(
        "--workers", type=int, default=3,
        help="Parallel workers (default: 3)",
    )
    parser.add_argument("--limit", type=int, help="Limit number of users")
    args = parser.parse_args()

    with open(args.input) as f:
        to_fetch = json.load(f)

    args.data_dir.mkdir(parents=True, exist_ok=True)

    # Find already-scraped users (for resume)
    already_done = set()
    for year_dir in args.data_dir.iterdir():
        if year_dir.is_dir() and year_dir.name.isdigit():
            for yaml_file in year_dir.glob("*.yaml"):
                already_done.add(yaml_file.stem)

    remaining = {uid: snaps for uid, snaps in to_fetch.items() if uid not in already_done}
    if already_done:
        print(f"Resuming: {len(already_done)} already done, {len(remaining)} remaining", file=sys.stderr)

    if args.limit:
        remaining = dict(list(remaining.items())[:args.limit])

    total = len(remaining)
    total_snaps = sum(len(s) for s in remaining.values())
    print(f"Fetching {total_snaps} snapshots for {total} users ({args.workers} workers)...", file=sys.stderr)

    users_ok = 0
    users_empty = 0
    declarations = 0
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_user, uid, snaps, args.data_dir): uid
            for uid, snaps in remaining.items()
        }
        for future in as_completed(futures):
            uid = futures[future]
            done += 1
            try:
                uid, saved = future.result()
                if saved:
                    users_ok += 1
                    declarations += len(saved)
                    years = sorted(d["year"] for d in saved)
                    print(f"[{done}/{total}] {uid}: {len(saved)} ({years})", file=sys.stderr)
                else:
                    users_empty += 1
            except Exception as e:
                users_empty += 1

            if done % 100 == 0:
                print(
                    f"  --- {done}/{total}: {users_ok} ok, {declarations} decls, "
                    f"{users_empty} empty ---",
                    file=sys.stderr,
                )

    print(
        f"\nDone: {users_ok} users, {declarations} declarations, "
        f"{users_empty} empty",
        file=sys.stderr,
    )

    year_counts = {}
    for year_dir in args.data_dir.iterdir():
        if year_dir.is_dir() and year_dir.name.isdigit():
            count = len(list(year_dir.glob("*.yaml")))
            if count:
                year_counts[int(year_dir.name)] = count
    for year in sorted(year_counts):
        print(f"  {year}: {year_counts[year]}", file=sys.stderr)


if __name__ == "__main__":
    main()
