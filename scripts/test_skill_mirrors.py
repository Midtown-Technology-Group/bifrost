"""Repository discovery contracts without Docker or model calls."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_skill_mirrors as guard


class DiscoveryTests(unittest.TestCase):
    def test_sync_preflight_preserves_local_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            relative = ".agents/skills/debug/SKILL.md"
            expected = {".agents/skills": {"debug/SKILL.md": (b"canonical", False)}}
            listing = f"100644 blob abc123\t{relative}\n"
            with patch.object(guard, "REPO", repo), patch.object(guard, "_expected_mirrors", return_value=expected):
                with patch.object(guard.subprocess, "check_output", return_value=listing):
                    errors = guard._preflight_sync()
            self.assertEqual(errors, [f"locally deleted mirror file; preserve and reconcile: {relative}"])
            self.assertFalse((repo / relative).exists())

    def test_drift_check_preserves_custom_and_edited_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            mirror = repo / ".agents/skills"
            edited = self.write_skill(mirror, "debug", "debug", "Human edit.")
            custom = self.write_skill(mirror, "custom", "custom", "Keep this skill.")
            before = {p: p.read_bytes() for p in (edited, custom)}
            expected = {".agents/skills": {"debug/SKILL.md": (b"canonical", False)}}
            with patch.object(guard, "REPO", repo), patch.object(guard, "_expected_mirrors", return_value=expected):
                self.assertTrue(guard._check_mirror_sync())
                self.assertEqual({p: p.read_bytes() for p in before}, before)
                with patch.object(guard.subprocess, "check_output", return_value=""):
                    errors = guard._preflight_sync()
            self.assertEqual(len(errors), 2)
            self.assertEqual({p: p.read_bytes() for p in before}, before)
    @unittest.skipIf(os.name == "nt", "POSIX executable-bit contract")
    def test_mirror_digest_detects_executable_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "pack.py"
            path.write_text("print('pack')\n")
            path.chmod(0o644)
            before = guard._tree_digest(root)
            path.chmod(0o755)
            self.assertNotEqual(before, guard._tree_digest(root))
    def write_skill(self, root: Path, directory: str, name: str, description: str) -> Path:
        path = root / directory / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\ndescription: {description}\n---\nBody\n", encoding="utf-8")
        return path

    def test_duplicate_and_retired_discovery_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.write_skill(repo / ".agents/skills", "debug", "debug", "Start debugging.")
            self.write_skill(repo / ".codex/skills", "debug", "debug", "Start debugging.")
            with patch.object(guard, "REPO", repo):
                errors = guard._check_local_discovery()
            self.assertTrue(any("duplicate repository skill" in error for error in errors))
            self.assertTrue(any("retired .codex" in error for error in errors))

    def test_unique_concise_skill_and_word_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = self.write_skill(repo / ".agents/skills", "debug", "debug", "word " * 35)
            with patch.object(guard, "REPO", repo):
                self.assertEqual(guard._check_local_discovery(), [])
                path.write_text(path.read_text().replace("description: ", "description: extra "))
                self.assertTrue(any("exceeds 35 words" in error for error in guard._check_local_discovery()))

    def test_folded_description_namespace_and_missing_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = self.write_skill(repo / ".agents/skills", "debug", "bifrost:debug", ">\n  Start debugging.")
            with patch.object(guard, "REPO", repo):
                errors = guard._check_local_discovery()
                self.assertTrue(any("repeats the plugin namespace" in error for error in errors))
                path.write_text("---\nname: debug\ndescription:\nmetadata: value\n---\nBody\n")
                self.assertTrue(any("no description" in error for error in guard._check_local_discovery()))


if __name__ == "__main__":
    unittest.main()
