from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import run_local_evidence_daily


class LocalEvidenceDailyTests(unittest.TestCase):
    def test_commit_if_changed_stages_only_requested_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            commands: list[tuple[list[str], Path]] = []

            def fake_run(command: list[str], *, cwd: Path = repo, check: bool = True):
                commands.append((command, cwd))

            def fake_subprocess_run(command: list[str], cwd: Path, **_kwargs):
                class Result:
                    returncode = 1 if command[:4] == ["git", "diff", "--cached", "--quiet"] else 0

                commands.append((command, cwd))
                return Result()

            original_run = run_local_evidence_daily.run
            original_subprocess_run = run_local_evidence_daily.subprocess.run
            try:
                run_local_evidence_daily.run = fake_run
                run_local_evidence_daily.subprocess.run = fake_subprocess_run

                changed = run_local_evidence_daily.commit_if_changed(
                    repo,
                    "message",
                    push_ref=None,
                    paths=[Path("evidence/local-1")],
                )
            finally:
                run_local_evidence_daily.run = original_run
                run_local_evidence_daily.subprocess.run = original_subprocess_run

            self.assertTrue(changed)
            self.assertIn((["git", "add", "--", "evidence/local-1"], repo), commands)
            self.assertNotIn((["git", "add", "."], repo), commands)

    def test_publish_releases_skips_existing_release_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_root = root / "evidence" / "local-1"
            (evidence_root / "live").mkdir(parents=True)
            (evidence_root / "live" / "manifest.json").write_text("{}", encoding="utf-8")
            (evidence_root / "retry").mkdir()
            (evidence_root / "retry" / "manifest.json").write_text("{}", encoding="utf-8")
            commands: list[list[str]] = []

            class Args:
                publish_releases = True
                timestamp_url = ""
                signing_key = None

            def fake_run(command: list[str], **_kwargs):
                commands.append(command)

            original_run = run_local_evidence_daily.run
            try:
                run_local_evidence_daily.run = fake_run
                run_local_evidence_daily.publish_releases(
                    evidence_root,
                    root / "assets",
                    "local-1",
                    Args(),
                    release_exists=lambda tag: tag == "evidence-local-1-live",
                )
            finally:
                run_local_evidence_daily.run = original_run

            published_tags = [
                command[command.index("--release-tag") + 1]
                for command in commands
                if "--release-tag" in command
            ]
            self.assertEqual(published_tags, ["evidence-local-1-retry"])

    def test_prepare_run_root_rejects_existing_saved_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "evidence" / "local-1"
            run_root.mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                run_local_evidence_daily.prepare_run_root(run_root)


if __name__ == "__main__":
    unittest.main()
