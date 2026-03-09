#!/usr/bin/env python3
"""
Generate canonical content hashes for scraped declaration YAML files.

The hashes are derived from the parsed declaration content itself, not the
source HTML. This makes the manifest stable across presentation-only changes
on the source website while still surfacing any change that affects the
extracted dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_manifest(data_dir: Path):
    declarations = {}
    for path in sorted(data_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        declarations[path.stem] = {
            "content_sha256": sha256_text(canonical_json(data)),
            "year": data.get("year"),
        }

    dataset_payload = {
        user_id: declarations[user_id]["content_sha256"]
        for user_id in sorted(declarations)
    }
    return {
        "count": len(declarations),
        "dataset_sha256": sha256_text(canonical_json(dataset_payload)),
        "declarations": declarations,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate content hashes for scraped YAML data")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_manifest(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
