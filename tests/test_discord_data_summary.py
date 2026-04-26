from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "discord_data_summary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("discord_data_summary", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


summary = load_module()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def write_yaml(path: Path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def write_manifest(repo: Path):
    declarations = {}
    for path in sorted((repo / "data").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        declarations[path.stem] = {
            "content_sha256": digest(data),
            "year": data.get("year"),
        }
    dataset = {
        user_id: declarations[user_id]["content_sha256"]
        for user_id in sorted(declarations)
    }
    manifest = {
        "count": len(declarations),
        "dataset_sha256": digest(dataset),
        "declarations": declarations,
    }
    checks = repo / "data" / "_checks"
    checks.mkdir(parents=True, exist_ok=True)
    (checks / "content-hashes.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class DiscordDataSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.test")
        (self.repo / "data").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def commit_all(self, subject: str):
        git(self.repo, "add", "data")
        git(self.repo, "commit", "-m", subject)

    def test_added_modified_removed_summary_uses_manifests_and_escapes_mentions(self):
        write_yaml(
            self.repo / "data" / "Alice.yaml",
            {
                "name": "ALICE TEST",
                "year": 2024,
                "income": {"public_function": 100, "other": 0},
            },
        )
        write_yaml(
            self.repo / "data" / "Removed.yaml",
            {"name": "REMOVED USER", "year": 2024, "public_function": "role"},
        )
        write_manifest(self.repo)
        self.commit_all("initial")

        write_yaml(
            self.repo / "data" / "Alice.yaml",
            {
                "name": "ALICE TEST",
                "year": 2025,
                "income": {"public_function": 250, "other": 50},
                "real_estate": [{"type": "byt"}],
            },
        )
        (self.repo / "data" / "Removed.yaml").unlink()
        write_yaml(
            self.repo / "data" / "New.User.yaml",
            {
                "name": "@everyone NEW [USER]",
                "year": 2025,
                "obligations": [{"type": "úver"}],
            },
        )
        write_manifest(self.repo)
        self.commit_all("daily")

        payload = summary.build_payload(
            self.repo,
            "owner/repo",
            "https://github.example/run",
            "2025",
        )
        field = payload["embeds"][0]["fields"][1]

        self.assertEqual(
            payload["content"],
            "Denná kontrola majetkových priznaní: +1 nové, 1 upravené, 1 odstránené",
        )
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(field["name"], "Ukážka zmien")
        self.assertIn("odstránené", field["value"])
        self.assertIn("upravené", field["value"])
        self.assertIn("nové", field["value"])
        self.assertIn("@\u200beveryone", field["value"])
        self.assertIn("\\[user\\]", field["value"])
        self.assertLessEqual(len(field["value"]), 1024)

    def test_missing_manifest_on_one_side_falls_back_to_yaml_diff(self):
        write_yaml(self.repo / "data" / "Alice.yaml", {"name": "Alice"})
        self.commit_all("initial without manifest")

        write_yaml(self.repo / "data" / "Bob.yaml", {"name": "Bob"})
        write_manifest(self.repo)
        self.commit_all("add manifest and bob")

        stats, items = summary.classify_changes(
            self.repo,
            git(self.repo, "rev-parse", "HEAD^"),
            git(self.repo, "rev-parse", "HEAD"),
        )

        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["modified"], 0)
        self.assertEqual(stats["removed"], 0)
        self.assertEqual(items[0]["user_id"], "Bob")
        self.assertEqual(stats["old_count"], 1)
        self.assertEqual(stats["new_count"], 2)

    def test_initial_commit_is_reported_as_added_without_compare_link(self):
        write_yaml(self.repo / "data" / "Only.yaml", {"name": "Only", "year": 2025})
        write_manifest(self.repo)
        self.commit_all("initial")

        payload = summary.build_payload(self.repo, "owner/repo", "", "2025")

        self.assertEqual(
            payload["content"],
            "Denná kontrola majetkových priznaní: +1 nové, 0 upravené, 0 odstránené",
        )
        self.assertNotIn("/compare/", payload["embeds"][0]["description"])

    def test_bad_yaml_does_not_crash_summary(self):
        write_yaml(self.repo / "data" / "Bad.yaml", {"name": "Bad", "year": 2024})
        write_manifest(self.repo)
        self.commit_all("initial")

        (self.repo / "data" / "Bad.yaml").write_text("name: [unterminated\n", encoding="utf-8")
        (self.repo / "data" / "_checks" / "content-hashes.json").write_text(
            json.dumps(
                {
                    "count": 1,
                    "dataset_sha256": "changed",
                    "declarations": {
                        "Bad": {"content_sha256": "changed", "year": 2025}
                    },
                }
            ),
            encoding="utf-8",
        )
        self.commit_all("bad yaml")

        payload = summary.build_payload(self.repo, "owner/repo", "", "2025")

        self.assertIn("1 upravené", payload["content"])
        self.assertIn("Bad", payload["embeds"][0]["fields"][1]["value"])

    def test_overflow_line_is_preserved_when_many_items_are_long(self):
        lines = ["x" * 500 for _ in range(8)]
        overflow = "- ...a ďalších 4 zmien v porovnaní."

        value = summary.fit_lines(lines, overflow, 1024)

        self.assertLessEqual(len(value), 1024)
        self.assertTrue(value.endswith(overflow))


if __name__ == "__main__":
    unittest.main()
