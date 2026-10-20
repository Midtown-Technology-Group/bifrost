#!/usr/bin/env python3
"""Offline stdlib checks for the blocked-candidate diagnostic."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "automatic_release", ROOT / "scripts/release/automatic-release.py"
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


class BlockedCandidateDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.directory.name)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        # Never inherit or append to a runner's real step summary.
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        git("init", "-q")
        git("config", "user.name", "Release fixture")
        git("config", "user.email", "release@example.invalid")
        git("config", "commit.gpgsign", "false")
        git("config", "gc.auto", "0")
        git("config", "maintenance.auto", "false")
        Path("README.md").write_text("fixture\n")
        git("add", ".")
        git("commit", "-qm", "fixture baseline")
        base = git("rev-parse", "HEAD")
        Path("release").mkdir()
        Path("release/candidate.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "repository": release.REPOSITORY,
                    "version": "v1.1.0",
                    "source_commit": base,
                }
            )
        )
        git("add", ".")
        git("commit", "-qm", "chore(release): v1.1.0")
        self.merged = git("rev-parse", "HEAD")
        self.prs = [
            {
                "number": 12,
                "title": "chore(release): v1.1.0",
                "merged_at": "2026-10-20T00:00:00Z",
                "merge_commit_sha": self.merged,
                "labels": [],
                "base": {"ref": "main"},
                "head": {
                    "ref": release.BRANCH,
                    "sha": self.merged,
                    "repo": {"full_name": release.REPOSITORY},
                },
                "user": {"login": "github-actions[bot]"},
            }
        ]
        git("update-ref", "refs/remotes/origin/main", self.merged)

    def held(self) -> str:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertTrue(release.pending_release(self.merged, self.prs))
        return stderr.getvalue()

    def test_held_candidate_reports_identity_and_links(self):
        text = self.held()
        self.assertIn("v1.1.0", text)
        self.assertIn("#12", text)
        self.assertIn(f"https://github.com/{release.REPOSITORY}/pull/12", text)
        self.assertIn(self.merged, text)
        self.assertIn(
            f"https://github.com/{release.REPOSITORY}/commit/{self.merged}/checks",
            text,
        )
        self.assertIn(
            f"https://github.com/{release.REPOSITORY}/blob/main/docs/VERSIONING.md#recovery",
            text,
        )

    def test_report_never_prints_an_executable_dispatch(self):
        text = self.held()
        self.assertNotIn("main_ci_run=", text)
        self.assertIsNone(re.search(r"/actions/runs/\d+", text))
        for word in ("conclusion", "succeeded", "failed", "quality gate"):
            self.assertNotIn(word, text)

    def test_step_summary_receives_the_same_report(self):
        summary = Path("summary.md")
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            text = self.held()
        self.assertIn("v1.1.0", summary.read_text())
        self.assertIn(summary.read_text().strip(), text)

    def test_unwritable_step_summary_keeps_the_outcome(self):
        blocked = Path("summary-directory")
        blocked.mkdir()
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(blocked)}):
            text = self.held()
        self.assertIn("v1.1.0", text)

    def test_absent_step_summary_still_reports_to_the_log(self):
        self.assertNotIn("GITHUB_STEP_SUMMARY", os.environ)
        text = self.held()
        self.assertIn("v1.1.0", text)
        self.assertEqual(list(Path(".").glob("summary*")), [])

    def test_failing_stderr_write_keeps_the_outcome_and_the_summary(self):
        summary = Path("summary.md")

        class BrokenStream:
            def write(self, _text):
                raise OSError("stderr is unavailable")

            def flush(self):
                raise OSError("stderr is unavailable")

        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            with patch.object(sys, "stderr", BrokenStream()):
                self.assertTrue(release.pending_release(self.merged, self.prs))
        self.assertIn("v1.1.0", summary.read_text())

    def test_published_candidate_is_not_reported(self):
        git("tag", "v1.1.0", self.merged)
        published = [[{"tag_name": "v1.1.0", "draft": False, "immutable": True}]]
        stderr = io.StringIO()
        with patch.object(release, "api", lambda *args, **kwargs: published):
            with contextlib.redirect_stderr(stderr):
                self.assertFalse(release.pending_release(self.merged, self.prs))
        self.assertEqual(stderr.getvalue(), "")

    def test_tag_outside_the_candidate_still_fails_closed(self):
        Path("later.txt").write_text("later\n")
        git("add", ".")
        git("commit", "-qm", "later commit")
        git("tag", "v1.1.0", git("rev-parse", "HEAD"))
        with self.assertRaises(release.ReleaseError):
            release.pending_release(self.merged, self.prs)

    def test_preparation_stays_held_for_a_pending_candidate(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(release.prepare(self.merged, self.prs))
        self.assertIn("v1.1.0", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
