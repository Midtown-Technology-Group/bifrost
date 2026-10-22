"""Exact-main transport probes using the existing real report/preflight fixture."""

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from test_preflight import Fixture

SPEC = importlib.util.spec_from_file_location("main_receipt_under_test", Path(__file__).with_name("main_receipt.py"))
receipt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt)


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return stream.getvalue()


class MainReceiptTests(Fixture):
    def setUp(self):
        super().setUp()
        self.run_preflight()
        self.environment = {
            "EXPECTED_HEAD": self.head, "GITHUB_REPOSITORY": receipt.REPOSITORY,
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_SHA": self.head, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
            "COMPOSE_FILE": "fixture-compose.yml",
        }
        self.command_patch = patch.object(receipt, "command", side_effect=self.command)
        self.command_patch.start()
        self.addCleanup(self.command_patch.stop)
        with patch.dict(os.environ, self.environment):
            receipt.record(self.root, self.output)
        self.files = {name: (self.output / name).read_bytes() for name in
                      ("producer.json", "manifest.json", "python.xml", "client.lcov", "node.lcov")}
        for name in self.files:
            (self.output / name).unlink()
        self.run = {"id": 123, "run_attempt": 2, "head_sha": self.head, "head_branch": "main",
                    "event": "push", "path": receipt.WORKFLOW,
                    "head_repository": {"full_name": receipt.REPOSITORY},
                    "status": "completed", "conclusion": "success"}
        self.jobs = [{"id": number, "name": name, "status": "completed", "conclusion": "success",
                      "started_at": "2026-10-22T01:00:00Z", "completed_at": "2026-10-22T02:00:00Z"}
                     for number, name in ((11, receipt.EVIDENCE_JOB), (12, receipt.SCAN_JOB))]
        self.jobs[1]["steps"] = [{"name": name, "conclusion": "success"} for name in
                                ("Analyze exact source and wait for its quality gate", "Verify evidence after analysis")]
        self.task = (f"projectKey={receipt.PROJECT}\nserverUrl=https://sonarcloud.io\n"
                     "ceTaskId=original-main-task\nceTaskUrl=https://sonarcloud.io/api/ce/task?id=original-main-task\n").encode()
        self.refresh_archives()
        self.environment.update(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/tags/v3.2.0",
                                GITHUB_RUN_ID="456", GITHUB_RUN_ATTEMPT="1")
        self.env_patch = patch.dict(os.environ, self.environment)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.api_patch = patch.object(receipt, "api", side_effect=self.api)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def command(self, args, **kwargs):
        if args[0] == "git":
            return self.run_git(*args[1:])
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": "sha256:" + c * 64, "RepoDigests": []} for c in ("a", "b")])
        if args[:2] == ["docker", "inspect"]:
            return "sha256:" + "c" * 64
        if args[-2:] == ["ps", "-aq"]:
            return "d" * 64
        return "observed fixture toolchain version"

    def refresh_archives(self):
        self.archives = {1: archive(self.files), 2: archive({"report-task.txt": self.task})}
        self.artifacts = [{"id": number, "name": f"{prefix}-{self.head}-2", "expired": False,
                           "workflow_run": {"id": 123}, "created_at": "2026-10-22T01:30:00Z",
                           "size_in_bytes": len(self.archives[number]),
                           "digest": "sha256:" + hashlib.sha256(self.archives[number]).hexdigest()}
                          for number, prefix in ((1, "sonar-evidence"), (2, "sonar-task"))]

    def api(self, path, *, archive=False):
        if path.startswith("actions/workflows/ci.yml/runs?"):
            return {"workflow_runs": [self.run]}
        if path.startswith("actions/runs/123/attempts/2/jobs?"):
            return {"jobs": self.jobs}
        if path.startswith("actions/runs/123/artifacts?"):
            return {"artifacts": self.artifacts}
        if path == "actions/runs/123":
            return self.run
        if archive:
            return self.archives[int(path.split("/")[2])]
        raise AssertionError(path)

    def test_valid_reuse_preserves_original_reports_base_and_analysis_identity(self):
        original = json.loads(self.files["producer.json"])
        result = receipt.reuse(self.root, self.output)
        self.assertEqual(result, original)
        for name, data in self.files.items():
            self.assertEqual((self.output / name).read_bytes(), data)
        self.assertEqual((self.output / "original-report-task.txt").read_bytes(), self.task)
        reuse = json.loads((self.output / "reuse.json").read_bytes())
        self.assertEqual(reuse["consumer_run"], "456")
        self.assertEqual(reuse["producer"]["run_attempt"], 2)
        self.assertIn("not_submitted", reuse["tag_analysis"])
        self.assertEqual(self.verify(original["manifest_sha256"])["sonar_quality_gate"], "not_observed")

    def test_non_tag_and_foreign_repository_cannot_reuse(self):
        for change in ({"GITHUB_REF": "refs/heads/main"}, {"GITHUB_REPOSITORY": "attacker/bifrost"}):
            with self.subTest(change=change), patch.dict(os.environ, change), self.assertRaises(ValueError):
                receipt.reuse(self.root, self.output)

    def test_failed_running_wrong_source_and_wrong_workflow_producers_refuse(self):
        for key, value in (("conclusion", "failure"), ("status", "in_progress"), ("head_sha", "f" * 40),
                           ("event", "pull_request"), ("head_branch", "feature"),
                           ("path", ".github/workflows/untrusted.yml"), ("run_attempt", 0)):
            with self.subTest(key=key), patch.dict(self.run, {key: value}), self.assertRaises(ValueError):
                receipt.reuse(self.root, self.output)

    def test_skipped_missing_duplicate_or_failed_analysis_cannot_be_reused(self):
        for jobs in (self.jobs[:1], self.jobs + [copy.deepcopy(self.jobs[1])],
                     [self.jobs[0], {**self.jobs[1], "conclusion": "skipped"}],
                     [self.jobs[0], {**self.jobs[1], "steps": []}]):
            with self.subTest(jobs=jobs), patch.object(self, "jobs", jobs), self.assertRaises(ValueError):
                receipt.reuse(self.root, self.output)

    def test_missing_expired_wrong_attempt_and_corrupt_archives_refuse(self):
        for change in ({"expired": True}, {"name": "sonar-evidence-wrong-attempt"},
                       {"created_at": "2026-10-21T01:30:00Z"}, {"digest": "sha256:" + "0" * 64},
                       {"workflow_run": {"id": 999}}):
            with self.subTest(change=change), patch.dict(self.artifacts[0], change), self.assertRaises(ValueError):
                receipt.reuse(self.root, self.output)

    def test_altered_producer_context_toolchain_and_base_refuse(self):
        original = self.files["producer.json"]
        for key, value in (("head", "f" * 40), ("tree", "e" * 40), ("workflow_sha", "d" * 40),
                           ("run_id", 999), ("run_attempt", 1), ("base", "c" * 40),
                           ("config_sha256", {}), ("toolchain", {}), ("manifest_sha256", "0" * 64)):
            with self.subTest(key=key):
                producer = json.loads(original); producer[key] = value
                self.files["producer.json"] = json.dumps(producer).encode(); self.refresh_archives()
                with self.assertRaises(ValueError):
                    receipt.reuse(self.root, self.output)
                self.assertFalse((self.output / "reuse.json").exists())
        self.files["producer.json"] = original

    def test_real_preflight_rejects_altered_reports_and_cleans_before_fallback(self):
        self.files["python.xml"] += b"altered"
        self.refresh_archives()
        with self.assertRaises(ValueError):
            receipt.reuse(self.root, self.output)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertFalse((self.output / "python.xml").exists())

    def test_task_from_another_project_or_server_refuses(self):
        self.task = self.task.replace(receipt.PROJECT.encode(), b"another-project")
        self.refresh_archives()
        with self.assertRaises(ValueError):
            receipt.reuse(self.root, self.output)

    def test_duplicate_artifact_and_missing_original_toolchain_cannot_pass(self):
        self.artifacts.append(copy.deepcopy(self.artifacts[0]))
        with self.assertRaises(ValueError):
            receipt.reuse(self.root, self.output)
        self.artifacts.pop()
        del self.files["producer.json"]
        self.refresh_archives()
        with self.assertRaises(ValueError):
            receipt.reuse(self.root, self.output)

    def test_duplicate_zip_members_and_symlink_payloads_are_not_extracted(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            for name, data in self.files.items():
                entry = zipfile.ZipInfo(name)
                entry.external_attr = (0o120777 << 16) if name == "python.xml" else (0o100644 << 16)
                bundle.writestr(entry, data)
        self.archives[1] = stream.getvalue()
        self.artifacts[0].update(size_in_bytes=len(self.archives[1]),
                                 digest="sha256:" + hashlib.sha256(self.archives[1]).hexdigest())
        with self.assertRaises(ValueError):
            receipt.reuse(self.root, self.output)
        self.assertFalse((self.output / "python.xml").exists())

    def test_pagination_consumes_all_pages_and_rejects_a_later_failed_run(self):
        older = [{**self.run, "id": number} for number in range(1, 101)]
        def paginated(path, **kwargs):
            return {"workflow_runs": older if path.endswith("page=1") else [{**self.run, "id": 200, "conclusion": "failure"}]}
        with patch.object(receipt, "api", side_effect=paginated) as api, self.assertRaises(ValueError):
            receipt.choose_run(self.head)
        self.assertEqual(api.call_count, 2)

    def test_rerun_started_during_download_cannot_pass(self):
        real = self.api
        def racing(path, **kwargs):
            if path == "actions/runs/123":
                return {**self.run, "run_attempt": 3, "status": "in_progress", "conclusion": None}
            return real(path, **kwargs)
        with patch.object(receipt, "api", side_effect=racing), self.assertRaises(ValueError):
            receipt.reuse(self.root, self.output)
        self.assertFalse((self.output / "manifest.json").exists())

    def test_unavailable_reuse_leaves_collection_enabled_without_success_output(self):
        output, summary = self.root / "github-output", self.root / "summary"
        with patch.object(receipt, "reuse", side_effect=ValueError("missing evidence")), \
                patch("sys.argv", ["main_receipt.py", "reuse"]), \
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}):
            self.assertEqual(receipt.main(), 0)
        self.assertFalse(output.exists())
        self.assertFalse(summary.exists())


if __name__ == "__main__":
    unittest.main()
