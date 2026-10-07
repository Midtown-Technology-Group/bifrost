"""Ownership and read-only reconciliation tests using isolated real Git trees."""
from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sonar_test_skill_mirrors", ROOT / "scripts/check_skill_mirrors.py")
assert spec and spec.loader
mirrors = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirrors)


class SkillMirrorOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "--quiet")
        self.canonical = self.root / ".claude/skills"
        for name in ("public", "private"):
            path = self.canonical / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: A bounded fixture skill.\n---\nSource\n")
        skills = self.root / "skills"
        skills.mkdir()
        (skills / "public").symlink_to("../.claude/skills/public", target_is_directory=True)
        shutil.copytree(self.canonical, self.root / ".agents/skills")
        shutil.copytree(self.canonical / "public", self.root / "plugins/bifrost/skills/public")
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "--quiet", "-m", "fixture")
        for name, value in (("REPO", self.root), ("PUBLIC_ROOT", self.root / "plugins/bifrost/skills")):
            patcher = patch.object(mirrors, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.STDOUT)

    def run_main(self, *args):
        out, error = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["check_skill_mirrors.py", *args]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(error):
            status = mirrors.main()
        return status, out.getvalue(), error.getvalue()

    def test_current_sources_have_exact_public_and_local_ownership_without_writes(self):
        before = self.git("status", "--porcelain")
        expected = mirrors._expected_mirrors()
        self.assertEqual(set(expected["plugins/bifrost/skills"]), {"public/SKILL.md"})
        self.assertEqual(set(expected[".agents/skills"]), {"public/SKILL.md", "private/SKILL.md"})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(before, self.git("status", "--porcelain"))

    def test_source_successor_allows_only_unchanged_owned_mirror_replacement(self):
        source = self.canonical / "public/SKILL.md"
        source.write_text(source.read_text() + "Reviewed successor\n")
        before = self.git("diff")
        status, _, error = self.run_main()
        self.assertEqual(status, 1)
        self.assertIn("out of sync", error)
        self.assertEqual(self.run_main("--preflight-sync")[0], 0)
        self.assertEqual(before, self.git("diff"))

    def test_locally_modified_and_unmanaged_mirrors_are_preserved(self):
        managed = self.root / ".agents/skills/public/SKILL.md"
        managed.write_text("Local edit\n")
        unmanaged = self.root / "plugins/bifrost/skills/unmanaged.txt"
        unmanaged.write_text("Retain this\n")
        status, _, error = self.run_main("--preflight-sync")
        self.assertEqual(status, 1)
        self.assertIn(".agents/skills/public/SKILL.md", error)
        self.assertIn("plugins/bifrost/skills/unmanaged.txt", error)
        self.assertEqual(managed.read_text(), "Local edit\n")
        self.assertEqual(unmanaged.read_text(), "Retain this\n")

    def test_locally_deleted_owned_file_is_not_silently_restored(self):
        missing = self.root / ".agents/skills/public/SKILL.md"
        missing.unlink()
        status, _, error = self.run_main("--preflight-sync")
        self.assertEqual(status, 1)
        self.assertIn("locally deleted mirror file", error)
        self.assertFalse(missing.exists())

    def test_linked_source_or_mirror_entries_fail_closed(self):
        linked = self.root / ".agents/skills/external"
        linked.symlink_to(self.canonical / "private", target_is_directory=True)
        self.assertIn("linked mirror/source entry", self.run_main("--preflight-sync")[2])
        linked.unlink()
        mirror = self.root / "plugins/bifrost/skills"
        shutil.rmtree(mirror)
        mirror.symlink_to(self.canonical, target_is_directory=True)
        self.assertIn("linked mirror/source root", self.run_main("--preflight-sync")[2])

    def test_windows_materialized_public_link_retains_exact_canonical_target(self):
        link = self.root / "skills/public"
        link.unlink()
        link.write_text("../.claude/skills/public\n")
        expected = mirrors._expected_mirrors()
        self.assertEqual(set(expected["plugins/bifrost/skills"]), {"public/SKILL.md"})
        link.write_text("../outside\n")
        self.assertIn("public skill target escapes or is missing", self.run_main("--preflight-sync")[2])

    def test_duplicate_retired_namespaced_and_overlong_discovery_are_reported(self):
        local = self.root / ".codex/skills/duplicate/SKILL.md"
        local.parent.mkdir(parents=True)
        local.write_text("---\nname: public\ndescription: " + "word " * 36 + "\n---\n")
        malformed = self.root / ".agents/skills/malformed/SKILL.md"
        malformed.parent.mkdir(parents=True)
        malformed.write_text("No frontmatter\n")
        namespaced = self.root / ".agents/skills/namespaced/SKILL.md"
        namespaced.parent.mkdir(parents=True)
        namespaced.write_text("---\nname: bifrost:owned\n---\n")
        unnamed = self.root / ".agents/skills/unnamed/SKILL.md"
        unnamed.parent.mkdir(parents=True)
        unnamed.write_text("---\ndescription: No identifier.\n---\n")
        errors = "\n".join(mirrors._check_local_discovery())
        for message in ("duplicate repository skill", "retired .codex/skills", "exceeds 35 words", "no YAML frontmatter", "repeats the plugin namespace", "has no description", "has no skill name"):
            self.assertIn(message, errors)

    def test_public_frontmatter_cannot_repeat_plugin_namespace_or_omit_name(self):
        path = self.root / "plugins/bifrost/skills/public/SKILL.md"
        path.write_text("---\nname: bifrost:public\n---\n")
        self.assertIn("repeats the plugin namespace", "\n".join(mirrors._check_public_skill_names()))
        path.write_text("---\ndescription: No identifier.\n---\n")
        self.assertIn("no name frontmatter", "\n".join(mirrors._check_public_skill_names()))
        path.unlink()
        self.assertIn("no public plugin skills found", "\n".join(mirrors._check_public_skill_names()))

    def test_tree_digest_includes_paths_contents_and_executable_ownership(self):
        tree = self.root / "digest"
        tree.mkdir()
        file = tree / "a.txt"
        file.write_bytes(b"original")
        original = mirrors._tree_digest(tree)
        file.chmod(0o755)
        self.assertNotEqual(original, mirrors._tree_digest(tree))
        executable = mirrors._tree_digest(tree)
        file.rename(tree / "b.txt")
        self.assertNotEqual(executable, mirrors._tree_digest(tree))
        (tree / "b.txt").write_bytes(b"reviewed")
        self.assertEqual(mirrors._tree_manifest(tree), {"b.txt": (b"reviewed", True)})
        self.assertEqual(mirrors._tree_digest(self.root / "missing"), mirrors._tree_digest(self.root / "also-missing"))

    def test_unavailable_git_inventory_cannot_certify_safe_replacement(self):
        with patch.object(mirrors.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "git")):
            self.assertNotEqual(mirrors._check_mirror_sync(), [])
            self.assertNotEqual(mirrors._preflight_sync(), [])
        shutil.rmtree(self.canonical)
        self.assertIn("public skill target escapes or is missing", self.run_main("--preflight-sync")[2])
