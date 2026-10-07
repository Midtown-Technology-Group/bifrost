"""Adversarial inputs fail before file access, child commands or local writes."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load("sonar_input_guard", "scripts/sonar/preflight.py")
stage = load("stage_input_guard", "scripts/lib/pre_pr_stage_evidence.py")
version = load("version_input_guard", "scripts/next-version.py")
release = load("release_input_guard", "scripts/release/automatic-release.py")
sampler = load("memory_sampler", "scripts/memory_sampler.py")
sys.modules["memory_sampler"] = sampler
benchmark = load("benchmark_input_guard", "scripts/benchmark_large_table_responses.py")
spike = load("spike_input_guard", "scripts/kubernetes/spike.py")
local = load("local_secret_guard", "scripts/kubernetes/local_secrets.py")


class InputBoundaries(unittest.TestCase):
    def test_git_evidence_rejects_unmodeled_options_before_command(self):
        with patch.object(guard.subprocess, "run") as command:
            for value in ("--exec-path=/tmp", "--config-env=core.sshCommand=ATTACK", "HEAD; id"):
                with self.subTest(value=value), self.assertRaises(guard.EvidenceError):
                    guard.git(ROOT, value)
            command.assert_not_called()

    def test_repository_environment_cannot_replace_the_selected_checkout(self):
        clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        expected = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                           env=clean, text=True).strip()
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory)
            subprocess.run(["git", "init", "-q", str(foreign)], env=clean, check=True)
            subprocess.run(["git", "-C", str(foreign), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                            "commit", "--allow-empty", "-qm", "foreign"], env=clean, check=True)
            with patch.dict(os.environ, {"GIT_DIR": str(foreign / ".git"),
                                        "GIT_WORK_TREE": str(foreign),
                                        "GIT_INDEX_FILE": str(foreign / ".git/index")}):
                self.assertEqual(guard.git(ROOT, "rev-parse", "HEAD").decode().strip(), expected)
                self.assertEqual(stage.run(["git", "rev-parse", "HEAD"], ROOT), expected)

    def test_report_escape_and_symlinks_rejected_without_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside-report"
            link = root / "report"
            link.symlink_to(outside)
            for value in (outside, link, root / ".." / "outside-report"):
                with self.subTest(value=value), self.assertRaises(guard.EvidenceError):
                    guard.report_bytes(value, root)

    def test_report_inside_checkout_keeps_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.xml"
            report.write_bytes(b"real report")
            self.assertEqual(guard.report_bytes(report, root), b"real report")

    def test_release_contract_ref_is_not_a_git_option(self):
        with patch.object(version.subprocess, "run") as command:
            for value in ("--exec-path=/tmp", "HEAD\nforged", "../other", "--help"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    version.contract_bump(value)
            command.assert_not_called()

    def test_release_api_rejects_path_escape_before_get_or_post(self):
        with patch.object(release.subprocess, "run") as command:
            for value in ("../other/pulls", "pulls/1\n--hostname=other", "pulls/1#fragment"):
                for fields in (None, {"title": "test"}):
                    with self.subTest(value=value, fields=fields), self.assertRaises(release.ReleaseError):
                        release.api(value, fields=fields)
            command.assert_not_called()

    def test_unknown_stage_executable_is_never_run(self):
        with patch.object(stage.subprocess, "check_output") as command:
            with self.assertRaises(ValueError):
                stage.run(["unexpected-executable"], ROOT)
            command.assert_not_called()

    def test_stage_state_escape_and_symlink_fail_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "keep.json"
            target.write_text('{"keep":true}')
            link = Path(directory) / "symlink.json"
            link.symlink_to(target)
            for path in (Path("/etc/stage.json"), link, Path(directory) / ".." / "escape"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    stage.atomic_write(path, {"replace": True})
            self.assertEqual(json.loads(target.read_text()), {"keep": True})

    def test_primary_environment_digest_keeps_exact_git_derived_read_boundary(self):
        with patch.object(stage, "run", return_value="/outside-primary/.git"):
            self.assertEqual(stage.environment_path(Path("/outside-primary/.env.test"), ROOT),
                             Path("/outside-primary/.env.test"))
            with self.assertRaises(ValueError):
                stage.environment_path(Path("/outside-primary/other.env"), ROOT)
            with self.assertRaises(ValueError):
                stage.atomic_write(Path("/outside-primary/.env.test"), {"replace": True})

    def test_local_output_and_trace_cannot_escape_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("preserve")
            link = Path(directory) / "link"
            link.symlink_to(target)
            for value in (Path("/etc/output"), link, Path(directory) / ".." / "escape"):
                for function in (spike.checked_output, benchmark.checked_trace_path):
                    with self.subTest(value=value, function=function), self.assertRaises(ValueError):
                        function(value)
            self.assertEqual(target.read_text(), "preserve")

    def test_container_option_fails_before_docker(self):
        with patch.object(sampler, "_parse_args", return_value=SimpleNamespace(container="--privileged")), \
                patch.object(sampler.subprocess, "run") as command:
            with self.assertRaises(ValueError):
                sampler.main()
            command.assert_not_called()

    def test_generated_local_credentials_are_unique_and_urls_agree(self):
        first, second = local.document(), local.document()
        data = first["stringData"]
        self.assertNotEqual(data["POSTGRES_PASSWORD"], second["stringData"]["POSTGRES_PASSWORD"])
        self.assertNotEqual(data["POSTGRES_PASSWORD"], data["RABBITMQ_PASSWORD"])
        self.assertIn(data["POSTGRES_PASSWORD"], data["BIFROST_DATABASE_URL"])
        self.assertIn(data["RABBITMQ_PASSWORD"], data["BIFROST_RABBITMQ_URL"])
        self.assertEqual(data["SEAWEEDFS_SECRET_KEY"], data["BIFROST_S3_SECRET_KEY"])

    def test_existing_local_secret_is_preserved_without_create(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.write_text("fixture")
            document = local.document()
            existing = {"metadata": document["metadata"], "data": dict.fromkeys(document["stringData"], "Zml4dHVyZQ==")}
            with patch.object(local.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(existing))) as command:
                local.ensure(config, "kind-test-cluster")
                self.assertEqual(command.call_count, 1)

    def test_non_kind_context_never_invokes_kubectl(self):
        with patch.object(local.subprocess, "run") as command:
            with self.assertRaises(ValueError):
                local.ensure(Path("/fixture/config"), "production")
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
