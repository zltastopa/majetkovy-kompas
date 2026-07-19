#!/usr/bin/env python3
"""Run the daily evidence-first scrape from a local machine."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent


def run(command: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=cwd, check=check, text=True)


def output(command: list[str], *, cwd: Path = ROOT) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def helper(name: str) -> str:
    return str(ROOT / name)


def github_helper(name: str) -> str:
    return str(ROOT / ".github" / "scripts" / name)


def branch_exists(branch: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def local_branch_exists(branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=ROOT,
    )
    return result.returncode == 0


def prepare_worktrees(tmp: Path) -> tuple[Path, Path]:
    run(["git", "fetch", "origin", "data"])
    evidence_exists = branch_exists("evidence")
    if evidence_exists:
        run(["git", "fetch", "origin", "evidence:evidence"], check=False)

    data_dir = tmp / "data-branch"
    evidence_dir = tmp / "evidence-branch"
    run(["git", "worktree", "add", str(data_dir), "origin/data"])

    if local_branch_exists("evidence"):
        run(["git", "worktree", "add", str(evidence_dir), "evidence"])
    else:
        run(["git", "worktree", "add", "--detach", str(evidence_dir)])
        run(["git", "switch", "--orphan", "evidence"], cwd=evidence_dir)
        run(["git", "rm", "-rf", "."], cwd=evidence_dir, check=False)
        (evidence_dir / "evidence").mkdir(parents=True, exist_ok=True)

    return data_dir, evidence_dir


def cleanup_worktrees(data_dir: Path, evidence_dir: Path) -> None:
    for path in [data_dir, evidence_dir]:
        run(["git", "worktree", "remove", str(path), "--force"], check=False)


def acquisition_args(args: argparse.Namespace, *, retries: int, workers: int) -> list[str]:
    return [
        "--request-retries",
        str(retries),
        "--request-timeout",
        str(args.request_timeout),
        "--request-delay",
        str(args.request_delay),
        "--request-jitter",
        str(args.request_jitter),
        "--workers",
        str(workers),
    ]


def combine_health(report_paths: list[Path], output_path: Path) -> dict[str, Any]:
    run(
        [
            sys.executable,
            github_helper("scrape_health.py"),
            *sum((["--report", str(path)] for path in report_paths), []),
            "--combined-report",
            str(output_path),
        ]
    )
    return json.loads(output_path.read_text(encoding="utf-8"))


def failed_ids(report: dict[str, Any]) -> list[str]:
    return [
        result["user_id"]
        for result in report.get("results", [])
        if result.get("status") == "error" and result.get("user_id")
    ]


def write_failed_ids(path: Path, ids: list[str]) -> None:
    path.write_text("".join(f"{user_id}\n" for user_id in sorted(set(ids))), encoding="utf-8")


def latest_year(data_path: Path) -> str:
    latest = 0
    for path in data_path.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        year = data.get("year")
        if isinstance(year, int):
            latest = max(latest, year)
    if not latest:
        raise RuntimeError("No scraped declarations found.")
    return str(latest)


def commit_if_changed(
    repo: Path,
    message: str,
    *,
    push_ref: str | None,
    paths: list[Path] | None = None,
) -> bool:
    run(["git", "config", "user.name", "github-actions[bot]"], cwd=repo)
    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=repo)
    if paths:
        run(["git", "add", "--", *(str(path) for path in paths)], cwd=repo)
    else:
        run(["git", "add", "."], cwd=repo)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo).returncode == 0:
        print(f"No changes to commit in {repo}")
        return False
    message_path = repo / ".commit-message.txt"
    message_path.write_text(message, encoding="utf-8")
    try:
        run(["git", "commit", "-F", str(message_path)], cwd=repo)
    finally:
        message_path.unlink(missing_ok=True)
    if push_ref:
        run(["git", "push", "origin", f"HEAD:{push_ref}"], cwd=repo)
    return True


def evidence_commit_message(run_id: str) -> str:
    return (
        "chore: add raw scrape evidence\n\n"
        "Previously this evidence existed only in the local acquisition run; this "
        "commit stores the raw package on the evidence branch for later review.\n"
        "Keep derived declaration YAML on the data branch.\n\n"
        f"- Add raw HTTP captures and manifests for `{run_id}`\n"
        "- Include RFC3161 timestamp artifacts when configured\n"
        "- Preserve extraction reports as package artifacts\n"
    )


def data_commit_message(data_repo: Path, latest: str, output_path: Path) -> str:
    run(
        [
            sys.executable,
            github_helper("data_commit_message.py"),
            "--repo",
            str(data_repo),
            "--latest-year",
            latest,
            "--output",
            str(output_path),
        ]
    )
    return output_path.read_text(encoding="utf-8")


def harden_args(args: argparse.Namespace) -> list[str]:
    result: list[str] = []
    if args.timestamp_url:
        result.extend(["--timestamp-url", args.timestamp_url])
    if args.signing_key:
        result.extend(["--signing-key", str(args.signing_key)])
    return result


def validation_args(args: argparse.Namespace) -> list[str]:
    result: list[str] = []
    if args.timestamp_url:
        result.append("--require-timestamp")
    if args.signing_key:
        result.append("--require-signature")
    return result


def package_dirs(evidence_root: Path) -> list[Path]:
    return sorted(path for path in evidence_root.iterdir() if (path / "manifest.json").exists())


def prepare_run_root(run_root: Path) -> Path:
    if run_root.exists():
        raise RuntimeError(f"refusing to overwrite existing evidence run: {run_root}")
    run_root.mkdir(parents=True)
    return run_root


def release_tag_exists(tag: str) -> bool:
    result = subprocess.run(
        ["gh", "release", "view", tag],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def evidence_release_tag(run_id: str, package_name: str) -> str:
    return f"evidence-{run_id}-{package_name}"


def publish_releases(
    evidence_root: Path,
    assets_dir: Path,
    run_id: str,
    args: argparse.Namespace,
    *,
    release_exists=release_tag_exists,
) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    for package_dir in package_dirs(evidence_root):
        name = package_dir.name
        release_args = []
        if args.publish_releases:
            tag = evidence_release_tag(run_id, name)
            if release_exists(tag):
                print(f"Release {tag} already exists; skipping publish.")
                continue
            release_args = [
                "--publish",
                "--release-tag",
                tag,
                "--release-title",
                f"Evidence package {run_id}/{name}",
                "--release-notes",
                f"Raw local scrape evidence package {run_id}, segment {name}.",
            ]
        run(
            [
                sys.executable,
                helper("publish_evidence.py"),
                "--evidence-dir",
                str(package_dir),
                "--output-dir",
                str(assets_dir),
                *validation_args(args),
                *release_args,
            ]
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("local-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--timestamp-url", default=os.environ.get("EVIDENCE_TSA_URL", ""))
    parser.add_argument("--signing-key", type=Path)
    parser.add_argument("--include-supplementary", action="store_true")
    parser.add_argument("--supplementary-ids", type=Path, default=ROOT / "supplementary_user_ids.txt")
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--request-delay", type=float, default=0.4)
    parser.add_argument("--request-jitter", type=float, default=0.6)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--retry-workers", type=int, default=1)
    parser.add_argument("--max-error-rate", type=float, default=0.25)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--publish-releases", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    tmp_context = tempfile.TemporaryDirectory(prefix=f"kompas-{args.run_id}-")
    tmp = Path(tmp_context.name)
    data_repo = tmp / "data-branch"
    evidence_repo = tmp / "evidence-branch"
    try:
        data_repo, evidence_repo = prepare_worktrees(tmp)
        base_data = tmp / "base-data"
        shutil.copytree(data_repo / "data", base_data)

        run_root = prepare_run_root(evidence_repo / "evidence" / args.run_id)
        live_dir = run_root / "live"
        retry_dir = run_root / "retry"
        supplementary_dir = run_root / "supplementary"
        reports = tmp / "reports"
        reports.mkdir(parents=True, exist_ok=True)

        run(
            [
                sys.executable,
                helper("acquire_evidence.py"),
                "--evidence-dir",
                str(live_dir),
                *acquisition_args(args, retries=0, workers=args.workers),
                "--report-json",
                str(live_dir / "acquire-report.json"),
            ]
        )
        run(
            [
                sys.executable,
                helper("extract_from_evidence.py"),
                "--evidence-dir",
                str(live_dir),
                "--data-dir",
                str(data_repo / "data"),
                "--report-json",
                str(reports / "scrape-live-report.json"),
            ]
        )
        live_report = combine_health(
            [live_dir / "acquire-report.json", reports / "scrape-live-report.json"],
            reports / "scrape-live-combined-report.json",
        )

        report_paths = [
            live_dir / "acquire-report.json",
            reports / "scrape-live-report.json",
        ]
        if args.include_supplementary:
            run(
                [
                    sys.executable,
                    helper("acquire_evidence.py"),
                    "--evidence-dir",
                    str(supplementary_dir),
                    "--only-supplementary",
                    "--supplementary-ids",
                    str(args.supplementary_ids),
                    *acquisition_args(args, retries=1, workers=1),
                    "--report-json",
                    str(supplementary_dir / "acquire-report.json"),
                ]
            )
            run(
                [
                    sys.executable,
                    helper("extract_from_evidence.py"),
                    "--evidence-dir",
                    str(supplementary_dir),
                    "--data-dir",
                    str(data_repo / "data"),
                    "--report-json",
                    str(reports / "scrape-supplementary-report.json"),
                ]
            )
            combine_health(
                [supplementary_dir / "acquire-report.json", reports / "scrape-supplementary-report.json"],
                reports / "scrape-supplementary-combined-report.json",
            )
            report_paths.extend(
                [
                    supplementary_dir / "acquire-report.json",
                    reports / "scrape-supplementary-report.json",
                ]
            )

        retry_ids = failed_ids(live_report)
        failed_ids_path = reports / "failed-ids.txt"
        write_failed_ids(failed_ids_path, retry_ids)
        if retry_ids:
            run(
                [
                    sys.executable,
                    helper("acquire_evidence.py"),
                    "--evidence-dir",
                    str(retry_dir),
                    "--user-ids-file",
                    str(failed_ids_path),
                    *acquisition_args(args, retries=1, workers=args.retry_workers),
                    "--report-json",
                    str(retry_dir / "acquire-report.json"),
                ]
            )
            run(
                [
                    sys.executable,
                    helper("extract_from_evidence.py"),
                    "--evidence-dir",
                    str(retry_dir),
                    "--data-dir",
                    str(data_repo / "data"),
                    "--report-json",
                    str(reports / "scrape-second-pass-report.json"),
                ]
            )
            report_paths.extend([retry_dir / "acquire-report.json", reports / "scrape-second-pass-report.json"])

        combined = combine_health(report_paths, reports / "scrape-combined-report.json")
        error_rate = float(combined.get("error_rate") or 0)
        if error_rate >= args.max_error_rate:
            raise RuntimeError(f"refusing to continue: scrape error rate {error_rate:.1%}")

        harden = harden_args(args)
        for package_dir in package_dirs(run_root):
            if harden:
                run([sys.executable, helper("harden_evidence.py"), "--evidence-dir", str(package_dir), *harden])

        run(
            [
                sys.executable,
                helper("validate_evidence.py"),
                "--evidence-root",
                str(run_root),
                *validation_args(args),
            ]
        )

        run(
            [
                sys.executable,
                helper("generate_content_hashes.py"),
                "--data-dir",
                str(data_repo / "data"),
                "--output",
                str(data_repo / "data" / "_checks" / "content-hashes.json"),
            ]
        )
        run(
            [
                sys.executable,
                helper("reproduce_from_evidence.py"),
                "--evidence-root",
                str(run_root),
                "--base-data-dir",
                str(base_data),
                "--output-data-dir",
                str(tmp / "recreated-data"),
                "--expected-data-dir",
                str(data_repo / "data"),
                "--report-json",
                str(reports / "reproduction-report.json"),
                *validation_args(args),
            ]
        )

        publish_releases(run_root, tmp / "evidence-release-assets", args.run_id, args)

        commit_if_changed(
            evidence_repo,
            evidence_commit_message(args.run_id),
            push_ref="evidence" if args.push else None,
            paths=[Path("evidence") / args.run_id],
        )

        run(["git", "add", "data"], cwd=data_repo)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=data_repo).returncode == 0:
            print("No upstream data changes detected.")
        else:
            msg = data_commit_message(data_repo, latest_year(data_repo / "data"), reports / "data-commit-message.txt")
            message_path = data_repo / ".commit-message.txt"
            message_path.write_text(msg, encoding="utf-8")
            try:
                run(["git", "commit", "-F", str(message_path)], cwd=data_repo)
            finally:
                message_path.unlink(missing_ok=True)
            if args.push:
                run(["git", "push", "origin", "HEAD:data"], cwd=data_repo)

        print(f"Reports: {reports}")
        return 0
    finally:
        if args.keep_temp:
            print(f"Kept temp directory: {tmp}")
        else:
            cleanup_worktrees(data_repo, evidence_repo)
            tmp_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
