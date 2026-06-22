#!/usr/bin/env python3
"""Externally anchor evidence manifests with signatures and RFC3161 timestamps."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

import evidence


Runner = Callable[[list[str]], None]
Poster = Callable[[str, bytes, str], bytes]


def default_run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def default_post(url: str, body: bytes, content_type: str) -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sanitized_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def sign_package(root: Path, private_key: Path, *, run: Runner = default_run) -> None:
    manifest_hash = evidence.validate_manifest_hash(root)
    signature_path = root / "manifest.sha256.sig"
    run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(signature_path),
            str(root / "manifest.sha256"),
        ]
    )
    write_json(
        root / "manifest.signature.json",
        {
            "manifest_sha256": manifest_hash,
            "signature_path": "manifest.sha256.sig",
            "signature_sha256": evidence.sha256_file(signature_path),
            "signature_algorithm": "openssl-dgst-sha256",
        },
    )


def timestamp_package(
    root: Path,
    tsa_url: str,
    *,
    run: Runner = default_run,
    post: Poster = default_post,
) -> None:
    manifest_hash = evidence.validate_manifest_hash(root)
    query_path = root / "manifest.sha256.tsq"
    response_path = root / "manifest.sha256.tsr"
    run(
        [
            "openssl",
            "ts",
            "-query",
            "-data",
            str(root / "manifest.json"),
            "-sha256",
            "-cert",
            "-out",
            str(query_path),
        ]
    )
    response = post(
        tsa_url,
        query_path.read_bytes(),
        "application/timestamp-query",
    )
    response_path.write_bytes(response)
    write_json(
        root / "manifest.timestamp.json",
        {
            "manifest_sha256": manifest_hash,
            "query_path": "manifest.sha256.tsq",
            "query_sha256": evidence.sha256_file(query_path),
            "response_path": "manifest.sha256.tsr",
            "response_sha256": evidence.sha256_file(response_path),
            "tsa_url": sanitized_url(tsa_url),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Externally anchor an evidence package")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--timestamp-url")
    parser.add_argument("--signing-key", type=Path)
    args = parser.parse_args()

    if args.timestamp_url:
        timestamp_package(args.evidence_dir, args.timestamp_url)
        print(f"Timestamped {args.evidence_dir}")
    if args.signing_key:
        sign_package(args.evidence_dir, args.signing_key)
        print(f"Signed {args.evidence_dir}")
    if not args.timestamp_url and not args.signing_key:
        parser.error("provide --timestamp-url, --signing-key, or both")


if __name__ == "__main__":
    main()
