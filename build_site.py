#!/usr/bin/env python3
"""
Build static site from git history of the data branch.

Walks each commit on the 'data' branch, reads all YAML files,
and produces a JSON dataset that the frontend can use.
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml


SITE_DIR = Path("site")
DATA_BRANCH = "data"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def get_commits():
    """Get ordered list of (commit_hash, year) from data branch."""
    hashes = git("rev-list", "--reverse", DATA_BRANCH).split("\n")
    commits = []
    for h in hashes:
        msg = git("log", "--format=%s", "-1", h)
        # Extract year from "data: declarations for year YYYY"
        for word in msg.split():
            if word.isdigit() and len(word) == 4:
                commits.append((h, int(word)))
                break
    return commits


def read_yaml_at_commit(commit, path):
    """Read and parse a YAML file from a specific commit."""
    try:
        content = git("show", f"{commit}:{path}")
        return yaml.safe_load(content)
    except subprocess.CalledProcessError:
        return None


def compute_diff(old, new):
    """Compute a human-readable diff between two declaration dicts."""
    if old is None:
        return {"type": "new"}
    if new is None:
        return {"type": "removed"}

    changes = []
    all_keys = list(dict.fromkeys(list(old.keys()) + list(new.keys())))

    skip_keys = {"id", "declaration_id", "year", "filed", "name"}

    for key in all_keys:
        if key in skip_keys:
            continue
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            changes.append({
                "field": key,
                "old": old_val,
                "new": new_val,
            })

    return {"type": "changed", "changes": changes} if changes else {"type": "unchanged"}


def build():
    commits = get_commits()
    years = [y for _, y in commits]
    print(f"Found {len(commits)} commits: {years}", file=sys.stderr)

    # Collect all politician IDs across all years
    all_ids = set()
    year_data = {}  # {year: {user_id: data}}

    for commit_hash, year in commits:
        print(f"Reading year {year}...", file=sys.stderr)
        files = git("ls-tree", "--name-only", commit_hash, "data/").split("\n")
        year_data[year] = {}
        for filepath in files:
            if not filepath.endswith(".yaml"):
                continue
            user_id = filepath.replace("data/", "").replace(".yaml", "")
            all_ids.add(user_id)
            data = read_yaml_at_commit(commit_hash, filepath)
            if data:
                year_data[year][user_id] = data

    print(f"Total politicians: {len(all_ids)}", file=sys.stderr)

    # Build per-politician timeline with diffs
    politicians = {}
    for user_id in sorted(all_ids):
        timeline = []
        prev_data = None
        for year in years:
            current_data = year_data.get(year, {}).get(user_id)
            if current_data is None:
                prev_data = None
                continue
            diff = compute_diff(prev_data, current_data)
            timeline.append({
                "year": year,
                "data": current_data,
                "diff": diff,
            })
            prev_data = current_data

        if timeline:
            latest = timeline[-1]["data"]
            politicians[user_id] = {
                "user_id": user_id,
                "name": latest.get("name", user_id),
                "public_function": latest.get("public_function"),
                "years": [t["year"] for t in timeline],
                "timeline": timeline,
            }

    # Write output
    SITE_DIR.mkdir(exist_ok=True)

    # Index: lightweight list for the main page
    index = []
    for uid in sorted(politicians, key=lambda k: politicians[k]["name"]):
        p = politicians[uid]
        latest = p["timeline"][-1]["data"]
        index.append({
            "user_id": uid,
            "name": p["name"],
            "public_function": p["public_function"],
            "years": p["years"],
            "income": latest.get("income"),
        })

    (SITE_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=None), encoding="utf-8"
    )

    # Per-politician detail files
    detail_dir = SITE_DIR / "politicians"
    detail_dir.mkdir(exist_ok=True)
    for uid, data in politicians.items():
        (detail_dir / f"{uid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Metadata
    (SITE_DIR / "meta.json").write_text(
        json.dumps({"years": years, "count": len(politicians)}, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Built site: {len(index)} politicians, {len(years)} years", file=sys.stderr)
    print(f"Output: {SITE_DIR}/", file=sys.stderr)


if __name__ == "__main__":
    build()
