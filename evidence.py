#!/usr/bin/env python3
"""Evidence package helpers for raw scrape acquisition."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_manifest_hash(root: Path) -> str:
    hash_path = root / "manifest.sha256"
    first_line = hash_path.read_text(encoding="utf-8").splitlines()[0]
    return first_line.split()[0]


def validate_manifest_hash(root: Path) -> str:
    manifest_path = root / "manifest.json"
    expected = expected_manifest_hash(root)
    actual = sha256_file(manifest_path)
    if actual != expected:
        raise ValueError(f"manifest hash mismatch: expected {expected}, got {actual}")
    return actual


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def file_entry(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


class EvidencePackage:
    """Append raw HTTP captures and write a self-contained manifest."""

    def __init__(
        self,
        root: Path,
        *,
        command: list[str] | None = None,
        created_at: str | None = None,
    ) -> None:
        self.root = root
        self.raw_dir = root / "raw"
        self.command = command or sys.argv
        self.created_at = created_at or utc_now_iso()
        self._captures: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._counter = 0
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def capture_response(
        self,
        *,
        purpose: str,
        method: str,
        url: str,
        status_code: int,
        response_headers: dict[str, str],
        body: bytes,
        request_headers: dict[str, str] | None = None,
        request_body: bytes | str | None = None,
        actual_request: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._counter += 1
            ordinal = self._counter

        capture_id = f"{ordinal:06d}-{sha256_bytes(body)[:16]}"
        body_path = self.raw_dir / f"{capture_id}.body"
        body_path.write_bytes(body)

        request_body_value, request_body_bytes = request_body_record(request_body)
        capture = {
            "id": capture_id,
            "captured_at": utc_now_iso(),
            "purpose": purpose,
            "method": method.upper(),
            "url": url,
            "request_headers": dict(sorted((request_headers or {}).items())),
            "request_body": request_body_value,
            "request_body_sha256": sha256_bytes(request_body_bytes) if request_body_bytes else None,
            "actual_request": actual_request,
            "status_code": status_code,
            "response_headers": dict(sorted(response_headers.items())),
            "body_path": body_path.relative_to(self.root).as_posix(),
            "body_size": len(body),
            "body_sha256": sha256_bytes(body),
            "metadata": metadata or {},
        }
        self._captures.append(capture)
        return capture

    def finalize(self, *, artifacts: list[Path] | None = None) -> dict[str, Any]:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_at": self.created_at,
            "finalized_at": utc_now_iso(),
            "command": self.command,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "cwd": os.getcwd(),
            },
            "git": {
                "commit": git_commit(),
            },
            "captures": sorted(self._captures, key=lambda item: item["id"]),
            "artifacts": [
                file_entry(self.root, path)
                for path in sorted(artifacts or [], key=lambda item: item.as_posix())
                if path.exists()
            ],
        }
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_hash = sha256_file(manifest_path)
        (self.root / "manifest.sha256").write_text(
            f"{manifest_hash}  manifest.json\n",
            encoding="utf-8",
        )
        return manifest


def request_body_record(value: Any) -> tuple[Any, bytes]:
    if value is None:
        return None, b""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace"), value
    if isinstance(value, str):
        return value, value.encode("utf-8")
    normalized = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return normalized, encoded
