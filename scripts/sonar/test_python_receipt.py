"""Python-only reuse with real report verification and immutable archive probes."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from test_preflight import Fixture

SPEC = importlib.util.spec_from_file_location("python_receipt_under_test", Path(__file__).with_name("python_receipt.py"))
receipt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt)


class PythonReceiptTests(Fixture):
    def setUp(self):
        super().setUp()
        self.run_preflight()
        self.environment = {
            "EXPECTED_HEAD": self.head, "EXPECTED_BASE": self.base,
            "GITHUB_REPOSITORY": receipt.transport.REPOSITORY,
            "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/7/merge",
            "GITHUB_HEAD_REF": "feature", "GITHUB_WORKFLOW_SHA": self.head,
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "COMPOSE_FILE": "fixture.yml",
        }
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.environment).start()
        patch.object(receipt.transport, "command", side_effect=self.command).start()
        receipt.record(self.root, self.output)
        self.files = {name: (self.output / name).read_bytes() for name in receipt.FILES}
        for name in self.files:
            (self.output / name).unlink()
        self.run = {"id": 123, "run_attempt": 2, "head_sha": self.head, "head_branch": "feature",
                    "event": "pull_request", "path": receipt.transport.WORKFLOW,
                    "head_repository": {"full_name": receipt.transport.REPOSITORY},
                    "status": "completed", "conclusion": "success"}
        self.job = {"id": 11, "name": receipt.transport.EVIDENCE_JOB, "status": "completed", "conclusion": "success",
                    "started_at": "2026-10-22T01:00:00Z", "completed_at": "2026-10-22T02:00:00Z",
                    "steps": [{"name": name, "conclusion": "success"} for name in receipt.STEPS]}
        self.refresh()
        self.environment.update(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/feature",
                                GITHUB_HEAD_REF="", GITHUB_RUN_ID="456", GITHUB_RUN_ATTEMPT="1")
        os.environ.update(self.environment)
        patch.object(receipt.transport, "api", side_effect=self.api).start()

    def command(self, args, **kwargs):
        if args[:2] == ["git", "fetch"]:
            raise receipt.transport.ReuseRefused("Fixture object unavailable")
        if args[0] == "git":
            try:
                return self.run_git(*args[1:])
            except subprocess.CalledProcessError as exc:
                raise receipt.transport.ReuseRefused("Required read-only evidence command failed") from exc
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": "sha256:" + c * 64, "RepoDigests": []} for c in ("a", "b")])
        if args[:2] == ["docker", "inspect"]:
            return "sha256:" + "c" * 64
        if args[-2:] == ["ps", "-aq"]:
            return "d" * 64
        return "observed fixture toolchain version"

    def refresh(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            for name, data in self.files.items():
                bundle.writestr(name, data)
        self.archive = stream.getvalue()
        self.artifact = {"id": 17, "name": f"sonar-evidence-{self.head}-2", "expired": False,
                         "workflow_run": {"id": 123}, "created_at": "2026-10-22T01:30:00Z",
                         "size_in_bytes": len(self.archive),
                         "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest()}

    def api(self, path, *, archive=False):
        if path.startswith("actions/workflows/ci.yml/runs?"):
            return {"workflow_runs": [copy.deepcopy(self.run)]}
        if path.startswith("actions/runs/123/attempts/") and "/jobs?" in path:
            return {"jobs": [self.job]}
        if path.startswith("actions/runs/123/artifacts?"):
            return {"artifacts": [self.artifact]}
        if path == "actions/runs/123":
            return copy.deepcopy(self.run)
        if archive:
            return self.archive
        raise AssertionError(path)

    def refuse(self):
        with self.assertRaises((ValueError, KeyError)):
            receipt.reuse(self.root, self.output)
        self.assertFalse((self.output / "raw/python.xml").exists())
        self.assertFalse((self.output / "python-origin").exists())
        self.assertFalse((self.output / "manifest.json").exists())

    def test_python_bytes_and_original_producer_survive_current_preflight(self):
        receipt.reuse(self.root, self.output)
        raw = self.output / "raw/python.xml"
        self.assertEqual(raw.read_bytes(), self.files["python.xml"])
        origin = json.loads((self.output / "python-origin/reuse.json").read_bytes())
        self.assertEqual(origin["producer"]["run_attempt"], 2)
        self.assertEqual(origin["consumer_run"], "456")
        self.assertEqual((self.output / "python-origin/producer.json").read_bytes(), self.files["producer.json"])
        # Existing client/Node paths still collect and stamp their own reports.
        for kind in ("client", "node"):
            receipt.transport.preflight.stamp_report(self.root, self.head, self.base, self.reports[kind], "sonar-project.properties")
        reports = self.reports | {"python": raw}
        receipt.transport.preflight.preflight(self.root, self.head, self.base, reports, self.output, "sonar-project.properties")
        self.verify()
        self.assertFalse((self.output / "producer.json").exists())  # no laundering reuse into a fresh producer

    def test_pr_consumer_and_manual_producer_are_supported(self):
        producer = json.loads(self.files["producer.json"])
        producer.update(event="workflow_dispatch", ref="refs/heads/feature")
        self.files["producer.json"] = json.dumps(producer).encode()
        self.run["event"] = "workflow_dispatch"
        self.refresh()
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_HEAD_REF": "feature", "GITHUB_REF": "refs/pull/7/merge"}):
            receipt.reuse(self.root, self.output)
        self.assertTrue((self.output / "raw/python.xml").exists())

    def test_missing_synthetic_workflow_object_uses_only_fixed_repository_and_exact_sha(self):
        calls = []
        def command(args):
            calls.append(args)
            if len(calls) == 1:
                raise receipt.transport.ReuseRefused("Object unavailable")
            return "tree" if args[1] == "rev-parse" else ""
        with patch.object(receipt.transport, "command", side_effect=command):
            self.assertEqual(receipt.workflow_tree(self.head), "tree")
        self.assertEqual(calls[1], ["git", "fetch", "--no-tags", "--depth=1",
                                   "https://github.com/" + receipt.transport.REPOSITORY + ".git", self.head])

    def test_foreign_tag_wrong_base_workflow_and_current_run_refuse(self):
        for change in ({"GITHUB_REPOSITORY": "foreign/repo"}, {"GITHUB_REF": "refs/tags/v1"},
                       {"EXPECTED_BASE": "f" * 40}, {"GITHUB_WORKFLOW_SHA": "f" * 40}, {"GITHUB_RUN_ID": "123"}):
            with self.subTest(change=change), patch.dict(os.environ, change):
                self.refuse()

    def test_failed_running_foreign_source_workflow_branch_or_attempt_refuse(self):
        for change in ({"conclusion": "failure"}, {"status": "in_progress"}, {"run_attempt": 3},
                       {"head_sha": "f" * 40}, {"head_branch": "other"}, {"path": "foreign.yml"},
                       {"head_repository": {"full_name": "foreign/repo"}}):
            with self.subTest(change=change), patch.dict(self.run, change):
                self.refuse()

    def test_latest_failed_or_running_run_cannot_hide_behind_old_green(self):
        for change in ({"conclusion": "failure"}, {"status": "in_progress"}):
            runs = [self.run, self.run | {"id": 200} | change]
            with self.subTest(change=change), patch.object(receipt.transport, "pages", return_value=runs):
                self.refuse()

    def test_every_collection_export_and_teardown_step_must_succeed(self):
        for step in self.job["steps"]:
            with self.subTest(step=step["name"]), patch.dict(step, {"conclusion": "skipped"}):
                self.refuse()
        with patch.dict(self.job, {"conclusion": "failure"}):
            self.refuse()

    def test_missing_old_narrow_modified_config_toolchain_or_receipt_refuse(self):
        original = self.files["producer.json"]
        changes = [("python_collection", None), ("python_collection", {**receipt.CONTRACT, "branch": False}),
                   ("tree", "f" * 40), ("head", "e" * 40), ("workflow_sha", "d" * 40),
                   ("base", "c" * 40), ("event", "push"), ("ref", "refs/heads/other"),
                   ("toolchain", {}), ("config_sha256", {}), ("run_attempt", 1), ("run_id", 999)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                producer = json.loads(original); producer[key] = value
                self.files["producer.json"] = json.dumps(producer).encode()
                self.refresh()
                self.refuse()

    def test_corrupt_expired_wrong_attempt_and_cross_run_artifacts_refuse(self):
        for change in ({"digest": "sha256:" + "0" * 64}, {"expired": True},
                       {"name": f"sonar-evidence-{self.head}-1"}, {"workflow_run": {"id": 999}},
                       {"created_at": "2026-10-21T01:30:00Z"}):
            with self.subTest(change=change), patch.dict(self.artifact, change):
                self.refuse()

    def test_changed_report_fails_real_verification_and_cleans_fallback(self):
        self.files["python.xml"] += b"changed"
        self.refresh()
        self.refuse()

    def test_rerun_during_download_refuses_and_cleans_fallback(self):
        real = self.api
        def racing(path, **kwargs):
            if path == "actions/runs/123":
                return self.run | {"run_attempt": 3}
            return real(path, **kwargs)
        with patch.object(receipt.transport, "api", side_effect=racing):
            self.refuse()

    def test_normal_refusal_leaves_fresh_collection_enabled(self):
        target = self.root / "github-output"
        with patch.object(receipt, "reuse", side_effect=receipt.transport.ReuseRefused("No exact-source Python producer")), \
                patch("sys.argv", ["python_receipt.py", "reuse"]), \
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(target)}):
            self.assertEqual(receipt.main(), 0)
        self.assertFalse(target.exists())


class WorkflowWiringTests(unittest.TestCase):
    def test_only_python_collection_is_guarded_and_permissions_are_unchanged(self):
        source = (Path(__file__).resolve().parents[2] / ".github/workflows/sonar-coverage.yml").read_text()
        for name in ("Prepare exact-source test images", "Prepare isolated Sonar test stack", *receipt.STEPS[:2]):
            self.assertIn(f"- name: {name}\n        if: steps.reuse.outputs.reused != 'true' && steps.python-reuse.outputs.reused != 'true'", source)
        for name in ("Validate reports and source inventory", "Collect repository Node test coverage"):
            self.assertIn(f"- name: {name}\n        if: steps.reuse.outputs.reused != 'true'\n", source)
        self.assertIn("python3 scripts/sonar/python_receipt.py record", source)
        self.assertIn("actions: read", source)
        self.assertNotIn("actions: write", source)
