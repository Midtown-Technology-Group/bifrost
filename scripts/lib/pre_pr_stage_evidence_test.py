#!/usr/bin/env python3
"""Focused tests for resumable pre-PR evidence; no Docker or project deps."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).with_name("pre_pr_stage_evidence.py")


class StageEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        (self.repo / "docker-compose.test.yml").write_text("services: {}\n", encoding="utf-8")
        (self.repo / "client").mkdir()
        (self.repo / "client" / "playwright.config.ts").write_text("export default {};\n", encoding="utf-8")
        (self.bin / "docker").write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = version ]; then cat docker-version; exit 0; fi\n"
            "if [ \"$1\" = compose ] && [ \"$2\" = version ]; then cat compose-version; exit 0; fi\n"
            "if [ \"$1\" = image ]; then cat image-id; exit $?; fi\n"
            "if [ \"$1\" = compose ] && [ \"$2\" = -f ]; then\n"
            "  if [ \"$5\" = --images ]; then echo test-image; exit 0; fi\n"
            "  if [ \"$4\" = config ]; then cat compose-output; exit $?; fi\n"
            "fi\nexit 1\n",
            encoding="utf-8",
        )
        (self.bin / "docker").chmod(0o755)
        (self.bin / "node").write_text("#!/bin/sh\necho v22\n", encoding="utf-8")
        (self.bin / "node").chmod(0o755)
        (self.repo / "docker-version").write_text("27\n", encoding="utf-8")
        (self.repo / "compose-version").write_text("2.40\n", encoding="utf-8")
        (self.repo / ".env.test").write_text("EXAMPLE=one\n", encoding="utf-8")
        (self.repo / "image-id").write_text("image-one\n", encoding="utf-8")
        (self.repo / "compose-output").write_text("resolved config\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("docker-version\ncompose-version\n.env.test\nimage-id\ncompose-output\nclient/playwright-results/\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / "tracked.txt").write_text("candidate\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"], cwd=self.repo, check=True)
        self.state = Path(self.tmp.name) / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def invoke(self, action: str, stage: str | None = None, context: str = "") -> int:
        command = ["python3", str(HELPER), action, "--repo", str(self.repo), "--state", str(self.state)]
        if stage:
            command += ["--stage", stage]
        if context:
            command += ["--context", context]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PATH"] = str(self.bin) + os.pathsep + environment["PATH"]
        return subprocess.run(command, cwd=self.repo, check=False, env=environment).returncode

    def test_only_completed_unchanged_clean_stage_is_reusable(self) -> None:
        self.assertNotEqual(self.invoke("reuse", "unit"), 0)
        self.assertEqual(self.invoke("start", "unit"), 0)
        self.assertNotEqual(self.invoke("reuse", "unit"), 0)
        self.assertEqual(self.invoke("success", "unit"), 0)
        self.assertEqual(self.invoke("reuse", "unit"), 0)
        for name in ["docker-version", "compose-version", ".env.test", "compose-output", "image-id"]:
            path = self.repo / name
            original = path.read_bytes()
            path.write_text("changed\n", encoding="utf-8")
            self.assertEqual(subprocess.check_output(["git", "status", "--porcelain"], cwd=self.repo), b"")
            self.assertNotEqual(self.invoke("reuse", "unit"), 0, name)
            path.write_bytes(original)
            self.assertEqual(self.invoke("reuse", "unit"), 0, name)
        self.assertNotEqual(self.invoke("reuse", "unit", "full"), 0)
        (self.repo / "compose-output").unlink()
        self.assertNotEqual(self.invoke("reuse", "unit"), 0)

        (self.repo / "tracked.txt").write_text("source changed\n", encoding="utf-8")
        self.assertNotEqual(self.invoke("reuse", "unit"), 0)

        (self.repo / "untracked.txt").write_text("change\n", encoding="utf-8")
        self.assertNotEqual(self.invoke("reuse", "unit"), 0)

    def test_failed_stage_is_never_credited_and_fresh_clears_state(self) -> None:
        self.assertEqual(self.invoke("start", "browser"), 0)
        self.assertEqual(self.invoke("failed", "browser"), 0)
        self.assertNotEqual(self.invoke("reuse", "browser"), 0)
        self.assertEqual(self.invoke("fresh"), 0)
        self.assertFalse(self.state.exists())

    def test_state_is_json_and_records_stage_status(self) -> None:
        self.assertEqual(self.invoke("start", "repository"), 0)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["stages"]["repository"]["status"], "running")

    def test_stage_shell_preserves_early_failure(self) -> None:
        root = HELPER.parents[2]
        shutil.copytree(root / "scripts/lib", self.repo / "scripts/lib", ignore=shutil.ignore_patterns("__pycache__"))
        runner = (root / "test.sh").read_text(encoding="utf-8")
        runner += '\nprobe_stage() { echo stage-start; false; echo later-success; }\nrun_pytest() { printf "arg=%s\\n" "$@"; }\ndocker() { printf "docker-arg=%s\\n" "$@"; }\n'
        (self.repo / "test.sh").write_text(runner, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "harness"], cwd=self.repo, check=True)
        script = 'source ./test.sh help >/dev/null; run_pre_pr_stage unit probe_stage'
        result = subprocess.run(["bash", "-e", "-c", script, "./test.sh"], cwd=self.repo, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stage-start", result.stdout)
        self.assertNotIn("later-success", result.stdout)
        script = 'source ./test.sh help >/dev/null; cmd_unit_targets tests/unit/selected.py; cmd_e2e_targets tests/e2e/selected.py; client_unit_targets src/selected.test.ts'
        result = subprocess.run(["bash", "-e", "-c", script, "./test.sh"], cwd=self.repo, capture_output=True, text=True, check=True)
        self.assertIn("arg=tests/unit/selected.py", result.stdout)
        self.assertIn("arg=tests/e2e/selected.py", result.stdout)
        self.assertNotIn("arg=tests/\n", result.stdout)
        self.assertIn("docker-arg=src/selected.test.ts", result.stdout)


if __name__ == "__main__":
    unittest.main()
