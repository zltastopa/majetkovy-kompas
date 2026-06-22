#!/usr/bin/env python3
"""Validate raw evidence packages before extraction or publication."""

from __future__ import annotations

import argparse
import json
import ssl
import subprocess
from pathlib import Path
from typing import Any, Callable

import evidence


Runner = Callable[[list[str]], None]


def default_run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def default_ca_args() -> list[str]:
    candidates = [
        ssl.get_default_verify_paths().openssl_cafile,
        "/opt/homebrew/etc/openssl@3/cert.pem",
        "/opt/homebrew/etc/ca-certificates/cert.pem",
        "/etc/ssl/cert.pem",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ["-CAfile", candidate]
    return []


def validate_file_hash(root: Path, entry: dict[str, Any], *, label: str) -> None:
    path = root / entry["path"]
    if not path.exists():
        raise ValueError(f"{label} missing: {entry['path']}")
    actual = evidence.sha256_file(path)
    if actual != entry["sha256"]:
        raise ValueError(
            f"{label} hash mismatch for {entry['path']}: "
            f"expected {entry['sha256']}, got {actual}"
        )


def validate_capture(root: Path, capture: dict[str, Any]) -> None:
    body_path = root / capture["body_path"]
    if not body_path.exists():
        raise ValueError(f"capture body missing: {capture['body_path']}")
    actual = evidence.sha256_file(body_path)
    if actual != capture["body_sha256"]:
        raise ValueError(
            f"body hash mismatch for {capture['body_path']}: "
            f"expected {capture['body_sha256']}, got {actual}"
        )


def validate_raw_directory(root: Path, captures: list[dict[str, Any]]) -> None:
    raw_dir = root / "raw"
    expected = {capture["body_path"] for capture in captures}
    if not raw_dir.exists():
        return
    for path in raw_dir.iterdir():
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        if rel_path not in expected:
            raise ValueError(f"unmanifested raw file: {rel_path}")


def validate_external_anchor(
    root: Path,
    metadata_name: str,
    payload_name: str,
    label: str,
    *,
    manifest_sha256: str,
    payload_hash_key: str,
) -> bool:
    metadata_path = root / metadata_name
    payload_path = root / payload_name
    if not payload_path.exists():
        raise ValueError(f"{label} response missing: {payload_name}")
    if not metadata_path.exists():
        raise ValueError(f"{label} metadata missing: {metadata_name}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    recorded_path = metadata.get("response_path") or metadata.get("signature_path")
    if recorded_path and recorded_path != payload_name:
        raise ValueError(f"{label} metadata points to unexpected file: {recorded_path}")
    if payload_path.stat().st_size == 0:
        raise ValueError(f"{label} payload is empty: {payload_name}")
    recorded_manifest_hash = metadata.get("manifest_sha256")
    if recorded_manifest_hash != manifest_sha256:
        raise ValueError(
            f"{label} manifest hash mismatch: "
            f"expected {manifest_sha256}, got {recorded_manifest_hash}"
        )
    recorded_payload_hash = metadata.get(payload_hash_key)
    actual_payload_hash = evidence.sha256_file(payload_path)
    if recorded_payload_hash != actual_payload_hash:
        raise ValueError(
            f"{label} hash mismatch for {payload_name}: "
            f"expected {recorded_payload_hash}, got {actual_payload_hash}"
        )
    return True


def validate_timestamp_response(root: Path, metadata: dict[str, Any], *, run: Runner) -> None:
    query_path = metadata.get("query_path")
    if not query_path:
        raise ValueError("timestamp query missing from metadata")
    query = root / query_path
    if not query.exists():
        raise ValueError(f"timestamp query missing: {query_path}")
    recorded_query_hash = metadata.get("query_sha256")
    actual_query_hash = evidence.sha256_file(query)
    if recorded_query_hash != actual_query_hash:
        raise ValueError(
            f"timestamp query hash mismatch for {query_path}: "
            f"expected {recorded_query_hash}, got {actual_query_hash}"
        )
    run(
        [
            "openssl",
            "ts",
            "-verify",
            "-queryfile",
            str(query),
            "-in",
            str(root / "manifest.sha256.tsr"),
            *default_ca_args(),
        ]
    )


def validate_package(
    root: Path,
    *,
    require_timestamp: bool = False,
    require_signature: bool = False,
    run: Runner = default_run,
) -> dict[str, int | bool]:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"manifest missing: {manifest_path}")
    manifest_sha256 = evidence.validate_manifest_hash(root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    captures = manifest.get("captures", [])
    if not isinstance(captures, list):
        raise ValueError("manifest captures must be a list")
    for capture in captures:
        validate_capture(root, capture)
    validate_raw_directory(root, captures)

    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("manifest artifacts must be a list")
    for artifact in artifacts:
        validate_file_hash(root, artifact, label="artifact")

    timestamped = False
    signed = False
    if require_timestamp:
        timestamped = validate_external_anchor(
            root,
            "manifest.timestamp.json",
            "manifest.sha256.tsr",
            "timestamp",
            manifest_sha256=manifest_sha256,
            payload_hash_key="response_sha256",
        )
        timestamp_metadata = json.loads(
            (root / "manifest.timestamp.json").read_text(encoding="utf-8")
        )
        validate_timestamp_response(root, timestamp_metadata, run=run)
    else:
        timestamped = (root / "manifest.sha256.tsr").exists()
    if require_signature:
        signed = validate_external_anchor(
            root,
            "manifest.signature.json",
            "manifest.sha256.sig",
            "signature",
            manifest_sha256=manifest_sha256,
            payload_hash_key="signature_sha256",
        )
    else:
        signed = (root / "manifest.sha256.sig").exists()

    return {
        "captures": len(captures),
        "artifacts": len(artifacts),
        "timestamped": timestamped,
        "signed": signed,
    }


def package_dirs(evidence_root: Path) -> list[Path]:
    if (evidence_root / "manifest.json").exists():
        return [evidence_root]
    return sorted(path for path in evidence_root.iterdir() if (path / "manifest.json").exists())


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate raw evidence packages")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--require-timestamp", action="store_true")
    parser.add_argument("--require-signature", action="store_true")
    args = parser.parse_args()

    roots = []
    if args.evidence_dir:
        roots.append(args.evidence_dir)
    if args.evidence_root:
        roots.extend(package_dirs(args.evidence_root))
    if not roots:
        parser.error("provide --evidence-dir or --evidence-root")

    for root in roots:
        summary = validate_package(
            root,
            require_timestamp=args.require_timestamp,
            require_signature=args.require_signature,
        )
        print(
            f"{root}: {summary['captures']} captures, "
            f"{summary['artifacts']} artifacts, "
            f"timestamped={str(summary['timestamped']).lower()}, "
            f"signed={str(summary['signed']).lower()}"
        )


if __name__ == "__main__":
    main()
