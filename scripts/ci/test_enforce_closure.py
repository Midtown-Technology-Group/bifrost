#!/usr/bin/env python3
"""Unit tests for the enforcement dependency boundary audit.

Run directly: ``python3 scripts/ci/test_enforce_closure.py``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enforce_closure import (  # noqa: E402
    audit,
    changed_in_scope,
    load_manifest,
    load_patterns,
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
        owners = [".github/**", "scripts/**", "test.sh"]
        report = audit(MANIFEST, owners)
        self.assertFalse(report["owns_enforcement_chain"])
        self.assertGreater(len(report["manifest_uncovered"]), 0)
        self.assertNotIn(".github/workflows/**", report["manifest_uncovered"])

    def test_comprehensive_owners_satisfy_the_boundary(self) -> None:
        owners = [
            ".github/**",
            "scripts/**",
            "test.sh",
            "sonar-project.properties",
            "api/**",
            "docker-compose*.yml",
            "client/package.json",
        ]
        report = audit(MANIFEST, owners)
        self.assertEqual(report["manifest_uncovered"], [])
        self.assertTrue(report["owns_enforcement_chain"])

    def test_partial_owners_surface_the_gap(self) -> None:
        owners = [".github/**"]
        report = audit(MANIFEST, owners)
        self.assertFalse(report["owns_enforcement_chain"])
        self.assertGreater(len(report["manifest_uncovered"]), 0)

    def test_changed_path_in_scope_without_owner_is_flagged(self) -> None:
        owners = [".github/**"]
        report = audit(MANIFEST, owners, changed=["test.sh", "src/main.py"])
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
        path = Path(__file__).with_name("_tmp_codeowners")
        path.write_text("# comment\n\n.github/workflows/**  @owner\n", encoding="utf-8")
        try:
            self.assertEqual(load_patterns(path), [".github/workflows/**"])
        finally:
            path.unlink()

    def test_load_manifest_reads_the_declared_boundary(self) -> None:
        manifest = load_manifest(Path(__file__).with_name("enforcement-deps.txt"))
        self.assertIn(".github/workflows/**", manifest)
        self.assertGreater(len(manifest), 10)


if __name__ == "__main__":
    unittest.main()
