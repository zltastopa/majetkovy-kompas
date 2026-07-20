#!/usr/bin/env python3
"""Package validated evidence for GitHub Releases."""

from __future__ import annotations

import argparse
import subprocess
import tarfile
from pathlib import Path
from typing import Callable

import evidence
import validate_evidence


Runner = Callable[[list[str]], None]


def default_run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def package_for_github_release(
    root: Path,
    *,
    output_dir: Path,
    require_timestamp: bool = False,
    require_signature: bool = False,
) -> Path:
    validate_evidence.validate_package(
        root,
        require_timestamp=require_timestamp,
        require_signature=require_signature,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{root.name}.tar.gz"
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(root, arcname=root.name)
    (output_dir / f"{archive_path.name}.sha256").write_text(
        f"{evidence.sha256_file(archive_path)}  {archive_path.name}\n",
        encoding="utf-8",
    )
    return archive_path


def publish_release_asset(
    archive_path: Path,
    *,
    tag: str,
    title: str,
    notes: str,
    run: Runner = default_run,
) -> None:
    checksum_path = archive_path.with_name(f"{archive_path.name}.sha256")
    assets = [str(archive_path)]
    if checksum_path.exists():
        assets.append(str(checksum_path))
    run(
        [
            "gh",
            "release",
            "create",
            tag,
            "--title",
            title,
            "--notes",
            notes,
            "--latest=false",
        ]
    )
    run(["gh", "release", "upload", tag, *assets, "--clobber"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Package and publish evidence to GitHub Releases")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--release-tag")
    parser.add_argument("--release-title")
    parser.add_argument("--release-notes", default="")
    parser.add_argument("--require-timestamp", action="store_true")
    parser.add_argument("--require-signature", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    archive = package_for_github_release(
        args.evidence_dir,
        output_dir=args.output_dir,
        require_timestamp=args.require_timestamp,
        require_signature=args.require_signature,
    )
    print(f"Packaged {archive}")
    if args.publish:
        if not args.release_tag or not args.release_title:
            parser.error("--publish requires --release-tag and --release-title")
        publish_release_asset(
            archive,
            tag=args.release_tag,
            title=args.release_title,
            notes=args.release_notes,
        )
        print(f"Published {archive} to release {args.release_tag}")


if __name__ == "__main__":
    main()
