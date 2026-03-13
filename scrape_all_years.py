#!/usr/bin/env python3
"""
Scrape ALL available years for every known official from the live NRSR site.

For each person, discovers available years from the dropdown, then
fetches each year's declaration.  Output is organized by year subdirectory
so it can be integrated into the data branch.

Resumable: skips (user, year) pairs that already have a YAML file.

Usage:
    uv run python scrape_all_years.py --data-dir /tmp/all_years_output --workers 8
    # Resume after interruption:
    uv run python scrape_all_years.py --data-dir /tmp/all_years_output --workers 8
"""

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scrape import (
    DECL_URL,
    dump_yaml,
    fetch,
    fetch_declaration_html,
    fetch_politician_list,
    load_supplementary_ids,
    parse_available_years,
    parse_declaration,
)

lock = threading.Lock()


def process_user(uid, data_dir, already_have):
    """Discover all years for a user, scrape missing ones."""
    try:
        html = fetch(DECL_URL + uid)
    except Exception:
        return uid, []

    years, selected = parse_available_years(html)
    if not years:
        return uid, []

    saved = []
    for year in years:
        if (uid, year) in already_have:
            continue

        try:
            if year == selected:
                decl_html = html
            else:
                decl_html = fetch_declaration_html(uid, year=year)

            data = parse_declaration(decl_html)
            if data and data.get("year"):
                year_dir = data_dir / str(data["year"])
                year_dir.mkdir(parents=True, exist_ok=True)
                out_path = year_dir / f"{uid}.yaml"
                out_path.write_text(dump_yaml(data), encoding="utf-8")
                saved.append(data["year"])
        except Exception:
            pass

    return uid, saved


def main():
    parser = argparse.ArgumentParser(
        description="Scrape all available years for every official"
    )
    parser.add_argument(
        "--data-dir", type=Path, required=True,
        help="Output directory (year subdirs with YAML files)",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, help="Limit number of users")
    parser.add_argument(
        "--supplementary-ids", type=Path,
        default=Path("supplementary_user_ids.txt"),
    )
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    # Build full user list: live NRSR + supplementary
    print("Fetching politician list from NRSR...", file=sys.stderr)
    politicians = fetch_politician_list()
    existing_ids = {p["user_id"] for p in politicians}
    print(f"  Live NRSR: {len(politicians)}", file=sys.stderr)

    if args.supplementary_ids.exists():
        extra = load_supplementary_ids(args.supplementary_ids)
        added = 0
        for uid in extra:
            if uid not in existing_ids:
                politicians.append({"user_id": uid, "display_name": uid})
                existing_ids.add(uid)
                added += 1
        print(f"  Supplementary: +{added}", file=sys.stderr)

    print(f"  Total: {len(politicians)}", file=sys.stderr)

    if args.limit:
        politicians = politicians[:args.limit]

    # Find already-scraped (user, year) pairs for resume
    already_have = set()
    for year_dir in args.data_dir.iterdir():
        if year_dir.is_dir() and year_dir.name.isdigit():
            for yaml_file in year_dir.glob("*.yaml"):
                already_have.add((yaml_file.stem, int(year_dir.name)))

    if already_have:
        print(
            f"Resuming: {len(already_have)} (user, year) pairs already scraped",
            file=sys.stderr,
        )

    total = len(politicians)
    users_ok = 0
    users_empty = 0
    declarations = 0
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                process_user, p["user_id"], args.data_dir, already_have
            ): p["user_id"]
            for p in politicians
        }
        for future in as_completed(futures):
            uid = futures[future]
            done += 1
            try:
                uid, saved = future.result()
                if saved:
                    users_ok += 1
                    declarations += len(saved)
                    with lock:
                        print(
                            f"[{done}/{total}] {uid}: {len(saved)} years ({sorted(saved)})",
                            file=sys.stderr,
                        )
                else:
                    users_empty += 1
            except Exception:
                users_empty += 1

            if done % 200 == 0:
                with lock:
                    print(
                        f"  --- {done}/{total}: {users_ok} ok, "
                        f"{declarations} decls, {users_empty} empty ---",
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
