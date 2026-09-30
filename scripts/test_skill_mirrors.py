"""Repository discovery contracts without Docker or model calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_skill_mirrors as guard


class DiscoveryTests(unittest.TestCase):
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
