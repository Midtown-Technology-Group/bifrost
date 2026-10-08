#!/usr/bin/env python3
"""Unit tests for the enforcement dependency boundary audit.

Run directly: ``python3 scripts/ci/test_enforce_closure.py``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enforce_closure import (  # noqa: E402
    audit,
    changed_in_scope,
    is_owned,
    load_manifest,
    load_patterns,
    load_rules,
    main,
    matches_any,
)

MANIFEST = [
    ".github/workflows/**",
    ".github/CODEOWNERS",
    "test.sh",
    "scripts/ci/**",
    "api/Dockerfile*",
    "docker-compose*.yml",
    "client/package.json",
]


class MatchingTests(unittest.TestCase):
    def test_bare_pattern_matches_at_any_depth(self) -> None:
        self.assertTrue(matches_any(["test.sh"], "test.sh"))
        self.assertTrue(matches_any(["test.sh"], "nested/dir/test.sh"))

    def test_slash_pattern_is_root_anchored(self) -> None:
        self.assertTrue(matches_any(["api/Dockerfile*"], "api/Dockerfile.dev"))
        self.assertFalse(matches_any(["api/Dockerfile*"], "other/api/Dockerfile.dev"))

    def test_double_star_spans_directories(self) -> None:
        self.assertTrue(matches_any(["scripts/ci/**"], "scripts/ci/deep/nested/file.py"))
        self.assertFalse(matches_any(["scripts/ci/**"], "scripts/sonar/file.py"))

    def test_single_star_does_not_cross_a_slash(self) -> None:
        self.assertTrue(matches_any(["docker-compose*.yml"], "docker-compose.test.yml"))
        self.assertFalse(matches_any(["docker-compose*.yml"], "docker-compose/sub/test.yml"))

    def test_directory_pattern_covers_children(self) -> None:
        self.assertTrue(matches_any(["scripts/lib/"], "scripts/lib/helpers.py"))

    def test_unowned_path_is_not_matched(self) -> None:
        self.assertFalse(matches_any([".github/workflows/**"], "src/main.py"))

    def test_single_character_wildcard_and_root_pattern(self) -> None:
        # Covers the '?' branch and the '/'-means-everything normalisation.
        self.assertTrue(matches_any(["dir/?ile.py"], "dir/file.py"))
        self.assertFalse(matches_any(["dir/?ile.py"], "dir/sub/file.py"))
        self.assertTrue(matches_any(["/"], "scripts/ci/x.py"))


class LastMatchTests(unittest.TestCase):
    """CODEOWNERS is last-match: a later ownerless rule un-owns the path."""

    def test_last_matching_rule_decides_ownership(self) -> None:
        rules = [(".github/**", True), (".github/CODEOWNERS", False)]
        self.assertTrue(is_owned(rules, ".github/workflows/ci.yml"))
        self.assertFalse(is_owned(rules, ".github/CODEOWNERS"))

    def test_trailing_ownerless_match_unowns_everything(self) -> None:
        rules = [(".github/**", True), (".github/**", False)]
        self.assertFalse(is_owned(rules, ".github/workflows/ci.yml"))

    def test_no_matching_rule_is_unowned(self) -> None:
        self.assertFalse(is_owned([("docs/**", True)], "src/main.py"))

    def test_load_rules_keeps_ownerless_rules_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CODEOWNERS"
            path.write_text(
                "# comment\n/scripts/ @team\n.github/\n/api/ @team # trailing comment\n",
                encoding="utf-8",
            )
            self.assertEqual(
                load_rules(path),
                [("/scripts/", True), (".github/", False), ("/api/", True)],
            )


class AuditTests(unittest.TestCase):
    def test_empty_codeowners_reports_everything_uncovered(self) -> None:
        report = audit(MANIFEST, [], changed=["src/main.py"])
        self.assertEqual(report["manifest_covered"], 0)
        self.assertFalse(report["owns_enforcement_chain"])
        self.assertEqual(report["manifest_uncovered"], MANIFEST)
        self.assertEqual(report["changed_total"], 1)
        self.assertEqual(report["changed_in_scope"], 0)
        self.assertEqual(report["changed_in_scope_unowned"], [])

    def test_owners_cover_a_subset(self) -> None:
        # The manifest spans more than .github/ and scripts/ (scanner config,
        # compose files, Dockerfiles, package manifests), so partial ownership
        # must stay visible instead of passing vacuously.
        rules = [(pattern, True) for pattern in (".github/**", "scripts/**", "test.sh")]
        report = audit(MANIFEST, rules)
        self.assertFalse(report["owns_enforcement_chain"])
        self.assertGreater(len(report["manifest_uncovered"]), 0)
        self.assertNotIn(".github/workflows/**", report["manifest_uncovered"])

    def test_comprehensive_owners_satisfy_the_boundary(self) -> None:
        rules = [
            (pattern, True)
            for pattern in (
                ".github/**",
                "scripts/**",
                "test.sh",
                "sonar-project.properties",
                "api/**",
                "docker-compose*.yml",
                "client/package.json",
            )
        ]
        report = audit(MANIFEST, rules)
        self.assertEqual(report["manifest_uncovered"], [])
        self.assertTrue(report["owns_enforcement_chain"])

    def test_partial_owners_surface_the_gap(self) -> None:
        report = audit(MANIFEST, [(".github/**", True)])
        self.assertFalse(report["owns_enforcement_chain"])
        self.assertGreater(len(report["manifest_uncovered"]), 0)

    def test_changed_path_in_scope_without_owner_is_flagged(self) -> None:
        rules = [(".github/**", True)]
        report = audit(MANIFEST, rules, changed=["test.sh", "src/main.py"])
        self.assertEqual(report["changed_in_scope"], 1)
        self.assertEqual(report["changed_in_scope_unowned"], ["test.sh"])

    def test_changed_in_scope_helper_filters_out_of_scope_paths(self) -> None:
        changed = ["test.sh", "docs/readme.md", "scripts/ci/report.py"]
        self.assertEqual(
            changed_in_scope(MANIFEST, changed),
            ["test.sh", "scripts/ci/report.py"],
        )


class LoaderTests(unittest.TestCase):
    def test_load_patterns_skips_comments_and_blank_lines(self) -> None:
        # The Sonar evidence job mounts the repository read-only, so the fixture
        # must be written outside the checkout rather than next to this file.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CODEOWNERS"
            path.write_text(
                "# comment\n\n.github/workflows/**  @owner\n", encoding="utf-8"
            )
            self.assertEqual(load_patterns(path), [".github/workflows/**"])

    def test_load_manifest_reads_the_declared_boundary(self) -> None:
        manifest = load_manifest(Path(__file__).with_name("enforcement-deps.txt"))
        self.assertIn(".github/workflows/**", manifest)
        self.assertGreater(len(manifest), 10)


class CliTests(unittest.TestCase):
    """Drive main() end-to-end against a throwaway repo-shaped directory."""

    def setUp(self) -> None:
        import os

        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        # addCleanup runs LIFO: restore the cwd first, then delete the tree.
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._old_cwd)

        Path("scripts/ci").mkdir(parents=True)
        Path(".github").mkdir(parents=True)
        Path("scripts/ci/enforcement-deps.txt").write_text(
            "scripts/**\ntest.sh\n", encoding="utf-8"
        )
        Path(".github/CODEOWNERS").write_text(
            "scripts/**  @MTG-Thomas\ntest.sh  @MTG-Thomas\n", encoding="utf-8"
        )

    def test_report_mode_succeeds_on_an_owned_boundary(self) -> None:
        self.assertEqual(main(["--mode", "report"]), 0)

    def test_enforce_mode_passes_when_everything_is_owned(self) -> None:
        self.assertEqual(main(["--mode", "enforce"]), 0)

    def test_enforce_mode_fails_when_the_boundary_is_unowned(self) -> None:
        Path(".github/CODEOWNERS").write_text("# intentionally empty\n", encoding="utf-8")
        self.assertEqual(main(["--mode", "enforce"]), 1)

    def test_report_prints_uncovered_entries_and_unowned_changes(self) -> None:
        # Manifest covers scripts/** (owned) and test.sh (owned), but CODEOWNERS
        # then re-matches test.sh with an ownerless rule: last-match leaves that
        # changed path unowned while the manifest pattern itself stays covered.
        Path(".github/CODEOWNERS").write_text(
            "scripts/**  @MTG-Thomas\nscripts/**  @MTG-Thomas\ntest.sh\n",
            encoding="utf-8",
        )
        Path("changed.txt").write_text("test.sh\n", encoding="utf-8")

        self.assertEqual(main(["--mode", "report", "--changed-files", "changed.txt"]), 0)

    def test_enforce_fails_for_a_last_match_unowned_change(self) -> None:
        # Manifest stays fully owned (its own pattern string is owned), while a
        # trailing ownerless rule leaves the changed *path* unowned: the changed
        # branch of enforce, not the boundary branch, must fire.
        Path("scripts/ci/enforcement-deps.txt").write_text("*.sh\n", encoding="utf-8")
        Path(".github/CODEOWNERS").write_text(
            "*.sh  @MTG-Thomas\ntest.sh\n", encoding="utf-8"
        )
        Path("changed.txt").write_text("test.sh\n", encoding="utf-8")

        from contextlib import redirect_stderr
        from io import StringIO

        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main(["--mode", "enforce", "--changed-files", "changed.txt"])

        self.assertEqual(code, 1)
        self.assertIn("changed enforcement paths are not owned", stderr.getvalue())

    def test_missing_manifest_is_an_input_error(self) -> None:
        Path("scripts/ci/enforcement-deps.txt").unlink()
        self.assertEqual(main([]), 2)

    def test_missing_changed_files_is_an_input_error(self) -> None:
        self.assertEqual(main(["--changed-files", "nope.txt"]), 2)

    def test_traversal_in_supplied_paths_is_rejected(self) -> None:
        self.assertEqual(main(["--manifest", "scripts/../secrets.txt"]), 2)
        self.assertEqual(main(["--codeowners", "../../etc/passwd"]), 2)

    def test_absolute_path_outside_the_repo_is_rejected(self) -> None:
        self.assertEqual(main(["--codeowners", "/etc/passwd"]), 2)

    def test_out_writes_the_json_report(self) -> None:
        out = Path("report.json")
        self.assertEqual(main(["--out", str(out)]), 0)
        self.assertTrue(out.exists())
        self.assertIn('"manifest_patterns"', out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
