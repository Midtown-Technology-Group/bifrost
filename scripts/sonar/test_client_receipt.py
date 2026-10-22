"""Same-run transport probes using the existing real preflight fixture."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import zipfile

from test_preflight import Fixture, lcov_report

SPEC = importlib.util.spec_from_file_location("client_receipt", Path(__file__).with_name("client_receipt.py"))
receipt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt)


class ClientReceiptTests(Fixture):
    def setUp(self):
        super().setUp()
        self.write("client/src/app.test.ts", "test('fixture', () => {});\n")
        self.commit()
        self.report = lcov_report("client/src/app.ts")
        self.write("client/coverage/lcov.info", self.report.decode())
        self.environment = {"GITHUB_REPOSITORY": receipt.transport.REPOSITORY,
                            "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/7/merge",
                            "GITHUB_SHA": self.head, "GITHUB_WORKFLOW_SHA": self.head,
                            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "EXPECTED_HEAD": self.head,
                            "CLIENT_ARTIFACT_ID": "17", "GITHUB_OUTPUT": str(self.root / "outputs")}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.environment).start()
        patch.object(receipt.transport, "command", side_effect=self.command).start()
        receipt.record(self.root)
        self.files = {name: (self.root / ".client-coverage" / name).read_bytes()
                      for name in ("client.lcov", "producer.json")}
        self.run = {"id": 123, "run_attempt": 2, "event": "pull_request", "head_sha": self.head,
                    "path": receipt.transport.WORKFLOW,
                    "head_repository": {"full_name": receipt.transport.REPOSITORY}}
        self.job = {"id": 10, "name": "Client Unit Tests", "status": "completed", "conclusion": "success",
                    "started_at": "2026-10-22T01:00:00Z", "completed_at": "2026-10-22T01:01:00Z",
                    "steps": [{"name": name, "conclusion": "success"} for name in
                              ("Vitest", "Bind complete client coverage", "Retain complete client coverage")]}
        self.refresh()
        patch.object(receipt.transport, "api", side_effect=self.api).start()

    def command(self, args, **kwargs):
        if args[0] == "git":
            return self.run_git(*args[1:])
        return {"node": "v26.0.0", "npm": "11.0.0"}[args[0]]

    def refresh(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            for name, data in self.files.items():
                bundle.writestr(name, data)
        self.archive = stream.getvalue()
        self.artifact = {"id": 17, "name": "client-coverage-123-2", "expired": False,
                         "size_in_bytes": len(self.archive), "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest(),
                         "workflow_run": {"id": 123}, "created_at": "2026-10-22T01:00:30Z"}

    def api(self, path, **kwargs):
        if path == "actions/runs/123":
            return copy.deepcopy(self.run)
        if path.startswith("actions/runs/123/attempts/2/jobs?"):
            return {"jobs": [copy.deepcopy(self.job)]}
        if path == "actions/artifacts/17":
            return copy.deepcopy(self.artifact)
        if path == "actions/artifacts/17/zip":
            return self.archive
        raise AssertionError("Unexpected endpoint")

    def test_valid_transport_preserves_report_and_original_identity_then_real_preflight(self):
        receipt.reuse(self.root)
        actual = self.output / "raw/client.lcov"
        self.assertEqual(actual.read_bytes(), self.report)
        self.assertEqual((self.output / "client-producer.json").read_bytes(), self.files["producer.json"])
        self.reports["client"] = actual
        manifest = self.run_preflight()
        self.assertTrue(manifest)
        self.verify()

    def test_equal_tree_different_checkout_commit_is_supported_honestly(self):
        # PR merge/head commit identity may differ, but tested content must not.
        producer = json.loads(self.files["producer.json"])
        producer["checkout"] = "a" * 40
        producer["source"] = "a" * 40
        self.files["producer.json"] = json.dumps(producer).encode()
        self.refresh()
        with patch.dict(os.environ, GITHUB_SHA="a" * 40):
            receipt.reuse(self.root)
        self.assertEqual((self.output / "raw/client.lcov").read_bytes(), self.report)

    def test_all_identity_drift_and_incomplete_inventories_refuse(self):
        original = self.files["producer.json"]
        for key, value in {"schema": 2, "repository": "foreign/repo", "event": "push", "ref": "foreign",
                           "source": "a" * 40, "checkout": "b" * 40, "workflow_sha": "c" * 40,
                           "run_id": 9, "run_attempt": 1, "tree": "d" * 40, "tests": "",
                           "node": "v25.0.0", "npm": "old", "mode": "affected", "command": "npm test",
                           "report_sha256": "bad"}.items():
            with self.subTest(key=key):
                producer = json.loads(original); producer[key] = value
                self.files["producer.json"] = json.dumps(producer).encode(); self.refresh()
                with self.assertRaises(receipt.transport.ReuseRefused):
                    receipt.reuse(self.root)
                self.assertFalse((self.output / "raw/client.lcov").exists())

    def test_failed_skipped_cancelled_missing_or_duplicate_producer_refuse(self):
        for state in ("failure", "skipped", "cancelled"):
            with self.subTest(state=state):
                self.job["conclusion"] = state
                with self.assertRaises(receipt.transport.ReuseRefused): receipt.reuse(self.root)
        self.job["conclusion"] = "success"
        for jobs in ([], [self.job, self.job]):
            with patch.object(receipt.transport, "pages", return_value=jobs):
                with self.assertRaises(receipt.transport.ReuseRefused): receipt.reuse(self.root)
        self.job["steps"][0]["conclusion"] = "skipped"
        with self.assertRaises(receipt.transport.ReuseRefused): receipt.reuse(self.root)

    def test_corrupt_expired_wrong_attempt_and_foreign_artifacts_refuse(self):
        for key, value in {"expired": True, "name": "client-coverage-123-1", "id": 8,
                           "digest": "sha256:" + "0" * 64, "workflow_run": {"id": 9}}.items():
            original = copy.deepcopy(self.artifact); self.artifact[key] = value
            with self.subTest(key=key), self.assertRaises(receipt.transport.ReuseRefused): receipt.reuse(self.root)
            self.artifact = original
        self.files["client.lcov"] = b"corrupt"; self.refresh()
        with self.assertRaises(receipt.transport.ReuseRefused): receipt.reuse(self.root)

    def test_changed_attempt_and_wrong_run_source_refuse(self):
        for key, value in {"run_attempt": 3, "head_sha": "a" * 40, "path": "foreign", "event": "push",
                           "head_repository": {"full_name": "foreign/repo"}}.items():
            original = copy.deepcopy(self.run); self.run[key] = value
            with self.subTest(key=key), self.assertRaises(receipt.transport.ReuseRefused): receipt.reuse(self.root)
            self.run = original
        changed = dict(self.run, run_attempt=3)
        original_api = self.api
        count = 0
        def race(path, **kwargs):
            nonlocal count
            if path == "actions/runs/123":
                count += 1
                return self.run if count == 1 else changed
            return original_api(path, **kwargs)
        with patch.object(receipt.transport, "api", side_effect=race):
            with self.assertRaises(receipt.transport.ReuseRefused): receipt.reuse(self.root)

    def test_fallback_redacts_foreign_error_and_never_claims_reuse(self):
        with patch.object(receipt, "reuse", side_effect=ValueError("secret-value")), \
                patch.object(sys, "argv", ["client_receipt.py", "reuse"]), \
                patch.object(Path, "cwd", return_value=self.root), \
                patch.object(receipt.transport, "summary") as summary:
            self.assertEqual(receipt.main(), 0)
        self.assertNotIn("secret-value", str(summary.call_args_list))
        self.assertFalse((self.root / "outputs").exists())

    def test_unexpected_error_terminal_and_summary_failure_non_authoritative(self):
        with patch.object(receipt, "reuse", side_effect=RuntimeError("secret")), \
                patch.object(sys, "argv", ["client_receipt.py", "reuse"]):
            self.assertEqual(receipt.main(), 1)
        with patch.dict(os.environ, GITHUB_STEP_SUMMARY=str(self.root / "missing/summary")), \
                patch.object(sys, "argv", ["client_receipt.py", "reuse"]), \
                patch.object(Path, "cwd", return_value=self.root):
            self.assertEqual(receipt.main(), 0)
        self.assertEqual((self.root / "outputs").read_text(), "reused=true\n")

    def test_partial_write_is_removed_before_fallback(self):
        real_write = Path.write_bytes
        def fail(path, data):
            if path.name == "client.lcov" and path.parent.name == "raw":
                real_write(path, data[:10]); raise OSError("disk failure")
            return real_write(path, data)
        with patch.object(Path, "write_bytes", fail):
            with self.assertRaises(OSError): receipt.reuse(self.root)
        self.assertFalse((self.output / "raw/client.lcov").exists())
        self.assertFalse((self.output / "client-producer.json").exists())
