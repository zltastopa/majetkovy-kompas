#!/usr/bin/env python3
"""Recreate a derived data state from raw evidence packages."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import evidence
import extract_from_evidence
import generate_content_hashes
import validate_evidence


def data_files(root: Path) -> dict[str, str]:
    files = {}
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        files[rel_path] = evidence.sha256_file(path)
    return files


def compare_data_dirs(actual: Path, expected: Path) -> dict[str, list[str]]:
    actual_files = data_files(actual)
    expected_files = data_files(expected)
    actual_names = set(actual_files)
    expected_names = set(expected_files)
    common = actual_names & expected_names
    different = sorted(
        name for name in common if actual_files[name] != expected_files[name]
    )
    return {
        "added": sorted(actual_names - expected_names),
        "removed": sorted(expected_names - actual_names),
        "different": different,
    }


def empty_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def copy_base_state(base_data_dir: Path, output_data_dir: Path) -> None:
    for path in sorted(base_data_dir.rglob("*")):
        if not path.is_file():
            continue
        target = output_data_dir / path.relative_to(base_data_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def reproduce_state(
    evidence_root: Path,
    output_data_dir: Path,
    *,
    base_data_dir: Path | None = None,
    expected_data_dir: Path | None = None,
    report_json: Path | None = None,
    require_timestamp: bool = False,
    require_signature: bool = False,
) -> dict[str, Any]:
    package_dirs = validate_evidence.package_dirs(evidence_root)
    if not package_dirs:
        raise ValueError(f"no evidence packages found under {evidence_root}")

    empty_dir(output_data_dir)
    if base_data_dir:
        copy_base_state(base_data_dir, output_data_dir)
    extract_reports = []
    for package_dir in package_dirs:
        validate_evidence.validate_package(
            package_dir,
            require_timestamp=require_timestamp,
            require_signature=require_signature,
        )
        extract_reports.append(
            extract_from_evidence.extract_package(package_dir, output_data_dir)
        )

    checks_path = output_data_dir / "_checks" / "content-hashes.json"
    content_manifest = generate_content_hashes.build_manifest(output_data_dir)
    checks_path.parent.mkdir(parents=True, exist_ok=True)
    checks_path.write_text(
        json.dumps(content_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    extracted = {
        "total": sum(report["total"] for report in extract_reports),
        "scraped": sum(report["scraped"] for report in extract_reports),
        "skipped": sum(report["skipped"] for report in extract_reports),
        "errors": sum(report["errors"] for report in extract_reports),
    }
    comparison = None
    matches_expected = None
    if expected_data_dir:
        comparison = compare_data_dirs(output_data_dir, expected_data_dir)
        matches_expected = not any(comparison.values())

    report = {
        "evidence_root": str(evidence_root),
        "base_data_dir": str(base_data_dir) if base_data_dir else None,
        "output_data_dir": str(output_data_dir),
        "packages": len(package_dirs),
        "package_paths": [str(path) for path in package_dirs],
        "extracted": extracted,
        "dataset_sha256": content_manifest["dataset_sha256"],
        "matches_expected": matches_expected,
        "comparison": comparison,
    }
    if report_json:
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Recreate data state from evidence packages")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-data-dir", type=Path, required=True)
    parser.add_argument("--base-data-dir", type=Path)
    parser.add_argument("--expected-data-dir", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--require-timestamp", action="store_true")
    parser.add_argument("--require-signature", action="store_true")
    args = parser.parse_args()

    report = reproduce_state(
        args.evidence_root,
        args.output_data_dir,
        base_data_dir=args.base_data_dir,
        expected_data_dir=args.expected_data_dir,
        report_json=args.report_json,
        require_timestamp=args.require_timestamp,
        require_signature=args.require_signature,
    )
    print(
        f"Recreated {report['extracted']['scraped']} declarations "
        f"from {report['packages']} evidence package(s); "
        f"dataset_sha256={report['dataset_sha256']}"
    )
    if report["matches_expected"] is not None:
        print(f"matches_expected={str(report['matches_expected']).lower()}")
        if not report["matches_expected"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
