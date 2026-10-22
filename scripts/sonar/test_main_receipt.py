"""Exact-main transport probes using the existing real report/preflight fixture."""

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
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
        self.remote_api = receipt.api
        self.api_patch = patch.object(receipt, "api", side_effect=self.api)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def command(self, args, **kwargs):
        if args[:2] == ["git", "-C"]:
            return "e" * 40
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
        self.assertIn("Evidence retrieval or validation failed", summary.read_text())

    def test_remote_timeout_falls_back_but_local_timeout_remains_visible(self):
        with patch.object(receipt, "command", side_effect=subprocess.TimeoutExpired("gh", 120)), \
                self.assertRaisesRegex(ValueError, "Remote evidence retrieval timed out"):
            self.remote_api("actions/runs/123")
        with patch.object(receipt, "command", side_effect=subprocess.TimeoutExpired("git", 120)), \
                self.assertRaises(subprocess.TimeoutExpired):
            receipt.exact_head()

    def test_remote_api_objects_and_pages_reject_malformed_shapes(self):
        with patch.object(receipt, "command", return_value=b"[]"), self.assertRaises(ValueError):
            self.remote_api("actions/runs/123")
        with patch.object(receipt, "api", return_value={"jobs": [None]}), self.assertRaises(ValueError):
            receipt.pages("actions/runs/123/jobs", "jobs")

    def test_unreadable_zip_entry_is_unavailable(self):
        with patch.object(zipfile.ZipFile, "read", side_effect=RuntimeError("encrypted entry")), \
                self.assertRaisesRegex(ValueError, "Unreadable remote evidence entry"):
            receipt.reuse(self.root, self.output)
        self.assertFalse((self.output / "reuse.json").exists())

    def test_unexpected_failure_cleans_partial_files_and_remains_visible(self):
        with patch.object(receipt.preflight, "verify", side_effect=RuntimeError("unexpected failure")), \
                self.assertRaises(RuntimeError):
            receipt.reuse(self.root, self.output)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertFalse((self.output / "python.xml").exists())

    def manual_context(self):
        ref = "refs/heads/reviewed-rehearsal"
        return {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": ref,
                "GITHUB_SHA": "e" * 40, "GITHUB_WORKFLOW_SHA": "e" * 40,
                "GITHUB_WORKFLOW_REF": f"{receipt.REPOSITORY}/.github/workflows/sonar-coverage.yml@{ref}"}

    def test_rehearsal_uses_real_dispatch_identity_and_original_transport(self):
        target, summary = self.root / "github-output", self.root / "summary"
        with patch.dict(os.environ, {**self.manual_context(), "GITHUB_OUTPUT": str(target),
                                     "GITHUB_STEP_SUMMARY": str(summary)}), \
                patch("sys.argv", ["main_receipt.py", "rehearse"]), \
                patch.object(receipt.Path, "cwd", return_value=self.root):
            self.assertEqual(receipt.main(), 0)
        result = json.loads((self.output / "reuse.json").read_bytes())
        self.assertEqual(result["kind"], "exact-main-evidence-reuse-rehearsal")
        self.assertEqual(result["consumer_event"], "workflow_dispatch")
        self.assertEqual(result["consumer_ref"], "refs/heads/reviewed-rehearsal")
        self.assertEqual(result["consumer_sha"], "e" * 40)
        self.assertEqual(result["producer"]["head"], self.head)
        self.assertIn("reused=true", target.read_text())
        self.assertIn("not a tag-consumer proof", summary.read_text())
        for name, data in self.files.items():
            self.assertEqual((self.output / name).read_bytes(), data)

    def test_rehearsal_refuses_wrong_event_workflow_and_tooling_without_tag_spoof(self):
        with patch.dict(os.environ, self.manual_context()):
            for change in ({"GITHUB_EVENT_NAME": "push"}, {"GITHUB_REF": "refs/tags/v3.2.0"},
                           {"GITHUB_WORKFLOW_REF": "untrusted/ci.yml@refs/heads/main"},
                           {"GITHUB_SHA": "f" * 40}, {"GITHUB_WORKFLOW_SHA": "f" * 40}):
                with self.subTest(change=change), patch.dict(os.environ, change), self.assertRaises(ValueError):
                    receipt.reuse(self.root, self.output, rehearsal=True)
            with self.assertRaises(ValueError):
                receipt.reuse(self.root, self.output)

    def test_rehearsal_cli_records_fallback_without_collection_or_success_output(self):
        self.artifacts[0]["digest"] = "sha256:" + "0" * 64
        target = self.root / "github-output"
        with patch.dict(os.environ, {**self.manual_context(), "GITHUB_OUTPUT": str(target)}), \
                patch("sys.argv", ["main_receipt.py", "rehearse"]), \
                patch.object(receipt.Path, "cwd", return_value=self.root):
            self.assertEqual(receipt.main(), 2)
        decision = json.loads((self.output / "rehearsal.json").read_bytes())
        self.assertEqual(decision["decision"], "fresh_collection_required")
        self.assertEqual(decision["reason"], "Artifact archive digest/size mismatch")
        self.assertFalse(decision["collection_executed"])
        self.assertFalse(decision["tag_consumer_proven"])
        self.assertFalse(target.exists())

    def test_rehearsal_retains_attempt_race_and_config_rejection(self):
        with patch.dict(os.environ, self.manual_context()):
            original = self.files["producer.json"]
            producer = json.loads(original)
            producer["config_sha256"] = {}
            self.files["producer.json"] = json.dumps(producer).encode()
            self.refresh_archives()
            with self.assertRaises(ValueError):
                receipt.reuse(self.root, self.output, rehearsal=True)
            self.files["producer.json"] = original
            self.refresh_archives()
            real = self.api
            def racing(path, **kwargs):
                return {**self.run, "run_attempt": 3} if path == "actions/runs/123" else real(path, **kwargs)
            with patch.object(receipt, "api", side_effect=racing), self.assertRaises(ValueError):
                receipt.reuse(self.root, self.output, rehearsal=True)
        self.assertFalse((self.output / "manifest.json").exists())


    def cli(self, *, action="reuse", summary_path=None):
        target = self.root / "github-output"
        summary_path = summary_path or self.root / "summary"
        console = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(target), "GITHUB_STEP_SUMMARY": str(summary_path)}), \
                patch("sys.argv", ["main_receipt.py", action]), \
                patch.object(receipt.Path, "cwd", return_value=self.root), \
                patch("sys.stdout", console):
            status = receipt.main()
        return status, target, console.getvalue()

    def test_pending_ineligible_and_missing_producers_have_controlled_reasons(self):
        for status, conclusion in (("queued", None), ("in_progress", None), ("completed", "failure")):
            with self.subTest(status=status), patch.dict(self.run, status=status, conclusion=conclusion):
                result, target, console = self.cli()
                self.assertEqual(result, 0)
                self.assertFalse(target.exists())
                self.assertIn("Latest exact-main producer is not complete/successful", console)
                self.assertIn("collecting fresh evidence", (self.root / "summary").read_text())
        with patch.object(receipt, "pages", return_value=[]):
            result, target, console = self.cli()
        self.assertEqual(result, 0)
        self.assertIn("No exact-main producer", console)
        self.assertFalse(target.exists())

    def test_corrupt_archive_reason_is_visible_without_success_output(self):
        self.artifacts[0]["digest"] = "sha256:" + "0" * 64
        result, target, console = self.cli()
        self.assertEqual(result, 0)
        self.assertFalse(target.exists())
        self.assertIn("Artifact archive digest/size mismatch", console)
        self.assertIn("Artifact archive digest/size mismatch", (self.root / "summary").read_text())

    def test_foreign_exception_text_is_redacted_without_changing_fallback(self):
        secret = "credential=do-not-print; subprocess-output; PRIVATE_ENV=value"
        for error in (ValueError(secret), OSError(secret), KeyError(secret), TypeError(secret), zipfile.BadZipFile(secret)):
            with self.subTest(error=type(error).__name__), patch.object(receipt, "reuse", side_effect=error):
                result, target, console = self.cli()
                self.assertEqual(result, 0)
                self.assertFalse(target.exists())
                self.assertIn("Evidence retrieval or validation failed", console)
                self.assertNotIn(secret, console + (self.root / "summary").read_text())

    def test_unexpected_exception_is_terminal_redacted_and_not_collection_fallback(self):
        secret = "unexpected arbitrary exception credential=private"
        with patch.object(receipt.preflight, "verify", side_effect=RuntimeError(secret)):
            result, target, console = self.cli()
        self.assertEqual(result, 1)
        self.assertFalse(target.exists())
        self.assertFalse((self.output / "python.xml").exists())
        self.assertNotIn(secret, console + (self.root / "summary").read_text())
        self.assertIn("stopped without enabling reuse or fresh collection", console)

    def test_summary_unavailable_cannot_change_success_or_fallback(self):
        # A directory cannot be opened for append; summary is not a qualification gate.
        result, target, console = self.cli(summary_path=self.root)
        self.assertEqual(result, 0)
        self.assertIn("reused=true", target.read_text())
        self.assertTrue((self.output / "reuse.json").exists())
        self.assertIn("Step summary unavailable", console)
        target.unlink()
        with patch.object(receipt, "reuse", side_effect=receipt.ReuseRefused("No exact-main producer")):
            result, target, console = self.cli(summary_path=self.root)
        self.assertEqual(result, 0)
        self.assertFalse(target.exists())
        with patch.dict(os.environ, {}, clear=True):
            receipt.summary("Missing summary is non-authoritative")

    def test_rehearsal_pending_reason_and_unavailable_summary_preserve_exit_two(self):
        with patch.dict(os.environ, self.manual_context()), patch.dict(self.run, status="queued", conclusion=None):
            result, target, console = self.cli(action="rehearse", summary_path=self.root)
        self.assertEqual(result, 2)
        self.assertFalse(target.exists())
        decision = json.loads((self.output / "rehearsal.json").read_bytes())
        self.assertEqual(decision["reason"], "Latest exact-main producer is not complete/successful")
        self.assertFalse(decision["collection_executed"])
        self.assertIn("Step summary unavailable", console)


if __name__ == "__main__":
    unittest.main()
