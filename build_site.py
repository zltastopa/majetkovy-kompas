#!/usr/bin/env python3
"""
Build static site from git history of the data branch.

Walks each commit on the 'data' branch, reads all YAML files,
and produces a JSON dataset that the frontend can use.
Computes journalist-useful signals: income jumps, new properties, red flags.
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
        for word in msg.split():
            if word.isdigit() and len(word) == 4:
                commits.append((h, int(word)))
                break
    return commits


def read_yaml_at_commit(commit, path):
    try:
        content = git("show", f"{commit}:{path}")
        return yaml.safe_load(content)
    except subprocess.CalledProcessError:
        return None


def total_income(data):
    inc = data.get("income")
    if not inc or not isinstance(inc, dict):
        return 0
    return (inc.get("public_function") or 0) + (inc.get("other") or 0)


def count_items(data, key):
    val = data.get(key)
    if isinstance(val, list):
        return len(val)
    return 0


def compute_diff(old, new):
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
            change = {"field": key, "old": old_val, "new": new_val}

            # Compute delta for income
            if key == "income" and isinstance(old_val, dict) and isinstance(new_val, dict):
                old_total = (old_val.get("public_function") or 0) + (old_val.get("other") or 0)
                new_total = (new_val.get("public_function") or 0) + (new_val.get("other") or 0)
                change["old_total"] = old_total
                change["new_total"] = new_total
                change["delta"] = new_total - old_total
                if old_total > 0:
                    change["delta_pct"] = round((new_total - old_total) / old_total * 100, 1)

            # Count added/removed for list fields
            if isinstance(old_val, list) and isinstance(new_val, list):
                change["old_count"] = len(old_val)
                change["new_count"] = len(new_val)

            changes.append(change)

    return {"type": "changed", "changes": changes} if changes else {"type": "unchanged"}


def title_case_name(name):
    """Convert 'JUDr. TOMÁŠ ABEL, PhD.' to 'JUDr. Tomáš Abel, PhD.'"""
    parts = []
    for part in name.split():
        # Keep academic titles as-is
        if part.endswith(".") or part.endswith(","):
            # Check if it's a title like JUDr. PhD. Ing. etc.
            base = part.rstrip(".,")
            if base and any(c.islower() for c in base):
                parts.append(part)
            elif base and base.isupper() and len(base) <= 5:
                parts.append(part)  # Keep short titles like PhD, MBA
            else:
                parts.append(part.capitalize())
        elif part.isupper() and len(part) > 3:
            parts.append(part.capitalize())
        else:
            parts.append(part)
    return " ".join(parts)


def build():
    commits = get_commits()
    years = [y for _, y in commits]
    print(f"Found {len(commits)} commits: {years}", file=sys.stderr)

    all_ids = set()
    year_data = {}

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

    # Build per-politician timeline with diffs and signals
    politicians = {}
    highlights = {
        "income_jumps": [],       # biggest year-over-year income increases
        "new_properties": [],     # newly added real estate
        "new_obligations": [],    # newly added loans/mortgages
        "top_earners": [],        # highest income in latest year
        "most_properties": [],    # most real estate in latest year
        "most_obligations": [],   # most obligations in latest year
    }

    for user_id in sorted(all_ids):
        timeline = []
        prev_data = None
        total_changes = 0

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

            # Collect highlights from diffs
            if diff["type"] == "changed":
                total_changes += len(diff["changes"])
                for c in diff["changes"]:
                    if c["field"] == "income" and "delta" in c:
                        highlights["income_jumps"].append({
                            "user_id": user_id,
                            "name": title_case_name(current_data.get("name", user_id)),
                            "function": current_data.get("public_function"),
                            "year": year,
                            "old_total": c["old_total"],
                            "new_total": c["new_total"],
                            "delta": c["delta"],
                            "delta_pct": c.get("delta_pct"),
                        })
                    if c["field"] == "real_estate" and isinstance(c.get("new"), list):
                        old_count = c.get("old_count", 0)
                        new_count = c.get("new_count", 0)
                        if new_count > old_count:
                            highlights["new_properties"].append({
                                "user_id": user_id,
                                "name": title_case_name(current_data.get("name", user_id)),
                                "function": current_data.get("public_function"),
                                "year": year,
                                "added": new_count - old_count,
                                "total": new_count,
                            })
                    if c["field"] == "obligations" and isinstance(c.get("new"), list):
                        old_count = c.get("old_count", 0)
                        new_count = c.get("new_count", 0)
                        if new_count > old_count:
                            highlights["new_obligations"].append({
                                "user_id": user_id,
                                "name": title_case_name(current_data.get("name", user_id)),
                                "function": current_data.get("public_function"),
                                "year": year,
                                "added": new_count - old_count,
                                "total": new_count,
                            })

            prev_data = current_data

        if not timeline:
            continue

        latest = timeline[-1]["data"]
        name = title_case_name(latest.get("name", user_id))

        politicians[user_id] = {
            "user_id": user_id,
            "name": name,
            "public_function": latest.get("public_function"),
            "years": [t["year"] for t in timeline],
            "timeline": timeline,
            "total_changes": total_changes,
        }

        # Collect latest-year signals
        latest_income = total_income(latest)
        latest_properties = count_items(latest, "real_estate")
        latest_obligations = count_items(latest, "obligations")

        if latest_income > 0:
            highlights["top_earners"].append({
                "user_id": user_id, "name": name,
                "function": latest.get("public_function"),
                "income": latest_income,
            })
        if latest_properties > 0:
            highlights["most_properties"].append({
                "user_id": user_id, "name": name,
                "function": latest.get("public_function"),
                "count": latest_properties,
            })
        if latest_obligations > 0:
            highlights["most_obligations"].append({
                "user_id": user_id, "name": name,
                "function": latest.get("public_function"),
                "count": latest_obligations,
            })

    # Sort and trim highlights
    highlights["income_jumps"].sort(key=lambda x: abs(x["delta"]), reverse=True)
    highlights["income_jumps"] = highlights["income_jumps"][:30]
    highlights["new_properties"].sort(key=lambda x: x["added"], reverse=True)
    highlights["new_properties"] = highlights["new_properties"][:30]
    highlights["new_obligations"].sort(key=lambda x: x["added"], reverse=True)
    highlights["new_obligations"] = highlights["new_obligations"][:30]
    highlights["top_earners"].sort(key=lambda x: x["income"], reverse=True)
    highlights["top_earners"] = highlights["top_earners"][:30]
    highlights["most_properties"].sort(key=lambda x: x["count"], reverse=True)
    highlights["most_properties"] = highlights["most_properties"][:30]
    highlights["most_obligations"].sort(key=lambda x: x["count"], reverse=True)
    highlights["most_obligations"] = highlights["most_obligations"][:30]

    # Write output
    SITE_DIR.mkdir(exist_ok=True)

    # Index
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
            "n_properties": count_items(latest, "real_estate"),
            "n_obligations": count_items(latest, "obligations"),
            "total_changes": p["total_changes"],
        })

    (SITE_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )

    # Per-politician detail
    detail_dir = SITE_DIR / "politicians"
    detail_dir.mkdir(exist_ok=True)
    for uid, data in politicians.items():
        (detail_dir / f"{uid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Highlights
    (SITE_DIR / "highlights.json").write_text(
        json.dumps(highlights, ensure_ascii=False), encoding="utf-8"
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
