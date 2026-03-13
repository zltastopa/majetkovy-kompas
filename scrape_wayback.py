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
import time
from pathlib import Path

import requests

from scrape import parse_declaration, dump_yaml


WAYBACK_RAW_URL = "https://web.archive.org/web/{timestamp}id_/{url}"
REQUEST_DELAY = 1.5  # seconds between requests


def fetch_wayback(timestamp, url, retries=3):
    """Fetch archived page from Wayback Machine with conservative retries."""
    wb_url = WAYBACK_RAW_URL.format(timestamp=timestamp, url=url)
    for attempt in range(retries):
        try:
            resp = requests.get(wb_url, timeout=60)
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code == 503:
                wait = 20 * (attempt + 1)
                print(f"    503, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.encoding = "utf-8"
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.ConnectionError:
            wait = 30 * (attempt + 1)
            print(f"    Connection error, waiting {wait}s...", file=sys.stderr)
            time.sleep(wait)
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(10)
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
        "--delay", type=float, default=REQUEST_DELAY,
        help=f"Delay between requests in seconds (default: {REQUEST_DELAY})",
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

    # Filter out already-done users
    remaining = {uid: snaps for uid, snaps in to_fetch.items() if uid not in already_done}
    if already_done:
        print(f"Resuming: {len(already_done)} already done, {len(remaining)} remaining", file=sys.stderr)

    if args.limit:
        remaining = dict(list(remaining.items())[:args.limit])

    total = len(remaining)
    total_snaps = sum(len(s) for s in remaining.values())
    print(f"Fetching {total_snaps} snapshots for {total} users (delay={args.delay}s)...", file=sys.stderr)

    users_ok = 0
    users_empty = 0
    declarations = 0
    errors = 0

    for i, (uid, snapshots) in enumerate(remaining.items(), 1):
        user_decls = []

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
                    # Skip if we already have this year for this user
                    if any(d.get("year") == year for d in user_decls):
                        continue
                    user_decls.append(data)

                    # Save immediately
                    year_dir = args.data_dir / str(year)
                    year_dir.mkdir(parents=True, exist_ok=True)
                    out_path = year_dir / f"{uid}.yaml"
                    out_path.write_text(dump_yaml(data), encoding="utf-8")
                    declarations += 1

            except Exception as e:
                print(f"  {uid} ts={ts}: {e}", file=sys.stderr)
                errors += 1

            time.sleep(args.delay)

        if user_decls:
            users_ok += 1
            years = sorted(d["year"] for d in user_decls)
            print(f"[{i}/{total}] {uid}: {len(user_decls)} declarations ({years})", file=sys.stderr)
        else:
            users_empty += 1
            if i % 20 == 0:
                print(f"[{i}/{total}] ... {users_ok} ok, {users_empty} empty, {errors} errors", file=sys.stderr)

    print(
        f"\nDone: {users_ok} users with data, {declarations} declarations, "
        f"{users_empty} empty, {errors} errors",
        file=sys.stderr,
    )

    # Summary by year
    year_counts = {}
    for year_dir in args.data_dir.iterdir():
        if year_dir.is_dir() and year_dir.name.isdigit():
            count = len(list(year_dir.glob("*.yaml")))
            if count:
                year_counts[int(year_dir.name)] = count
    for year in sorted(year_counts):
        print(f"  {year}: {year_counts[year]} declarations", file=sys.stderr)


if __name__ == "__main__":
    main()
