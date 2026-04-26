from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "data_commit_message.py"


def load_module():
    spec = importlib.util.spec_from_file_location("data_commit_message", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


message = load_module()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


class DataCommitMessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.test")
        (self.repo / "data").mkdir()
        (self.repo / "data" / "_checks").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def commit_all(self, subject: str):
        git(self.repo, "add", "data")
        git(self.repo, "commit", "-m", subject)

    def test_added_only_subject_and_body_are_descriptive(self):
        (self.repo / "data" / "_checks" / "content-hashes.json").write_text(
            "{}\n", encoding="utf-8"
        )
        self.commit_all("initial")

        (self.repo / "data" / "Ondrej.Uhrik.yaml").write_text(
            "name: Ing. ONDREJ UHRÍK\nyear: 2025\n", encoding="utf-8"
        )
        git(self.repo, "add", "data")

        summary = message.summarize_staged_data(self.repo)
        body = message.build_message(summary, "2025")

        self.assertEqual(summary.added, 1)
        self.assertEqual(summary.modified, 0)
        self.assertEqual(summary.removed, 0)
        self.assertEqual(body.splitlines()[0], "data: add daily declarations")
        self.assertIn("+1 new, ~0 updated, -0 removed", body)
        self.assertIn("Sample changed UserIds: Ondrej.Uhrik", body)

    def test_mixed_change_subject_uses_file_count_without_year(self):
        (self.repo / "data" / "A.yaml").write_text("name: A\n", encoding="utf-8")
        (self.repo / "data" / "B.yaml").write_text("name: B\n", encoding="utf-8")
        self.commit_all("initial")

        (self.repo / "data" / "A.yaml").write_text("name: A2\n", encoding="utf-8")
        (self.repo / "data" / "B.yaml").unlink()
        (self.repo / "data" / "C.yaml").write_text("name: C\n", encoding="utf-8")
        git(self.repo, "add", "data")

        summary = message.summarize_staged_data(self.repo)
        body = message.build_message(summary, "2025")
        subject = body.splitlines()[0]

        self.assertEqual((summary.added, summary.modified, summary.removed), (1, 1, 1))
        self.assertEqual(
            subject,
            "data: update daily baseline with mixed declaration changes",
        )
        self.assertNotRegex(subject, r"\d")
        self.assertIn("Sample changed UserIds: A, B, C", body)


if __name__ == "__main__":
    unittest.main()
