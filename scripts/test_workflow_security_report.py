"""Self-contained report contracts; no platform services or host pytest."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.workflow_security_report import main, report

HEAD = "a" * 40
VERSION = "1.30.1"


def audit(results=None):
    return {
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "zizmor", "version": VERSION}},
            "results": [] if results is None else results,
        }],
    }


class ReportTests(unittest.TestCase):
    def test_clean_audit_is_explicitly_advisory(self):
        metadata, summary = report(audit(), HEAD, VERSION)
        self.assertEqual(metadata["findings"], 0)
        self.assertTrue(metadata["advisory"])
        self.assertIn("not a security certification", summary)

    def test_findings_do_not_fail_and_all_levels_are_counted(self):
        metadata, summary = report(audit([
            {"level": "error"}, {"level": "warning"},
            {"level": "note"}, {"level": "none"}, {"level": "error"},
        ]), HEAD, VERSION)
        self.assertEqual(metadata["findings"], 5)
        self.assertEqual(metadata["levels"]["error"], 2)
        self.assertIn("Findings: **5**", summary)

    def test_omitted_level_defaults_to_sarif_warning(self):
        metadata, _ = report(audit([{}]), HEAD, VERSION)
        self.assertEqual(metadata["levels"], {"warning": 1})

    def test_untrusted_finding_text_is_not_rendered(self):
        _, summary = report(audit([{
            "level": "error", "message": {"text": "::error::<script>injected</script>"},
            "ruleId": "[click](https://invalid.example)",
        }]), HEAD, VERSION)
        self.assertNotIn("injected", summary)
        self.assertNotIn("invalid.example", summary)

    def test_metadata_binds_source_version_and_offline_limits(self):
        metadata, summary = report(audit(), HEAD, VERSION)
        self.assertEqual(metadata["head_sha"], HEAD)
        self.assertEqual(metadata["scanner_version"], VERSION)
        self.assertEqual(metadata["mode"], "offline")
        self.assertEqual(metadata["collection"], ["workflows", "actions"])
        self.assertIn("Online audits were not run", summary)

    def test_invalid_heads_cannot_enter_markdown(self):
        for head in ["a" * 7, "g" * 40, HEAD + "\n::error::", ""]:
            with self.subTest(head=head), self.assertRaises(ValueError):
                report(audit(), head, VERSION)

    def test_invalid_versions_cannot_enter_markdown(self):
        for version in ["latest", "v1.30.1", "1.30.1\n", "1.30.1-rc1"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                report(audit(), HEAD, version)

    def test_wrong_sarif_version_is_rejected(self):
        data = audit()
        data["version"] = "2.0.0"
        with self.assertRaises(ValueError):
            report(data, HEAD, VERSION)

    def test_missing_or_multiple_runs_are_rejected(self):
        for runs in [None, [], [audit()["runs"][0]] * 2]:
            with self.subTest(runs=runs), self.assertRaises(ValueError):
                report({"version": "2.1.0", "runs": runs}, HEAD, VERSION)

    def test_wrong_scanner_identity_is_rejected(self):
        for field, value in [("name", "other"), ("version", "1.30.0")]:
            data = audit()
            data["runs"][0]["tool"]["driver"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                report(data, HEAD, VERSION)

    def test_failed_scanner_invocation_is_rejected(self):
        data = audit()
        data["runs"][0]["invocations"] = [{"executionSuccessful": False}]
        with self.assertRaises(ValueError):
            report(data, HEAD, VERSION)

    def test_missing_or_invalid_results_are_rejected(self):
        for results in [None, {}]:
            data = audit()
            data["runs"][0]["results"] = results
            with self.subTest(results=results), self.assertRaises(ValueError):
                report(data, HEAD, VERSION)

    def test_unknown_result_level_is_rejected(self):
        with self.assertRaises(ValueError):
            report(audit([{"level": "critical"}]), HEAD, VERSION)

    def test_report_does_not_mutate_sarif(self):
        data = audit([{}])
        original = copy.deepcopy(data)
        report(data, HEAD, VERSION)
        self.assertEqual(data, original)

    def test_cli_writes_source_bound_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sarif = root / "audit.sarif"
            sarif.write_text(json.dumps(audit([{"level": "error"}])), encoding="utf-8")
            with patch.object(sys, "argv", [
                "workflow_security_report.py",
                "--sarif", str(sarif), "--head-sha", HEAD,
                "--scanner-version", VERSION, "--output-dir", str(root / "out"),
            ]):
                main()
            metadata = json.loads((root / "out" / "metadata.json").read_text())
            self.assertEqual(metadata["head_sha"], HEAD)
            self.assertEqual(metadata["findings"], 1)
            self.assertIn(HEAD, (root / "out" / "summary.md").read_text())

    def test_invalid_cli_evidence_does_not_create_success_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sarif = root / "audit.sarif"
            sarif.write_text("{invalid", encoding="utf-8")
            with patch.object(sys, "argv", [
                "workflow_security_report.py",
                "--sarif", str(sarif), "--head-sha", HEAD,
                "--scanner-version", VERSION, "--output-dir", str(root / "out"),
            ]), self.assertRaises(json.JSONDecodeError):
                main()
            self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
