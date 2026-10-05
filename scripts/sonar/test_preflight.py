"""Self-contained contract probes; run with Python unittest, not host pytest."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE_SPEC = importlib.util.spec_from_file_location(
    "sonar_preflight_under_test", Path(__file__).with_name("preflight.py"))
guard = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(guard)


def xml_report(filename="/app/src/service.py", sources="/app", branch=False):
    branches = ' branch="true" condition-coverage="50% (1/2)"' if branch else ""
    return (f'<coverage lines-valid="2" lines-covered="2" branches-valid="{2 if branch else 0}" '
            f'branches-covered="{1 if branch else 0}"><sources><source>{sources}</source></sources>'
            f'<packages><package name="api"><classes><class filename="{filename}">'
            f'<lines><line number="1" hits="1"{branches}/><line number="2" hits="1"/>'
            '</lines></class></classes></package></packages></coverage>').encode()


def lcov_report(filename="src/app.ts", hit=1, branch=False):
    records = ["TN:", "SF:" + filename, "FN:1,run", f"FNDA:{hit},run", "FNF:1", f"FNH:{int(hit > 0)}",
               f"DA:1,{hit}", f"DA:2,{hit}", "LF:2", f"LH:{2 if hit else 0}"]
    if branch:
        records.extend(["BRDA:1,0,0,1", "BRDA:1,0,1,-", "BRF:2", "BRH:1"])
    else:
        records.extend(["BRF:0", "BRH:0"])
    return ("\n".join(records + ["end_of_record"]) + "\n").encode()


def properties():
    return ("sonar.projectKey=Midtown-Technology-Group_bifrost\nsonar.host.url=https://sonarcloud.io\n"
            + "sonar.sources=.\nsonar.tests=.\nsonar.scm.exclusions.disabled=true\n"
            + "sonar.test.inclusions=" + ",".join(guard.TEST_PATTERNS) + "\n"
            + "sonar.exclusions=" + ",".join(guard.TEST_PATTERNS + guard.GENERATED_PATTERNS) + "\n"
            + "sonar.python.coverage.reportPaths=.sonar-evidence/python.xml\n"
            + "sonar.javascript.lcov.reportPaths=.sonar-evidence/client.lcov,.sonar-evidence/node.lcov\n")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("api/src/service.py", "def answer():\n    return 42\n\n")
        self.write("client/src/app.ts", "function run() {\n  return 42;\n}\n")
        self.write(".github/scripts/check.mjs", "function run() {\n  return 42;\n}\n")
        self.write("scripts/release/unreported.py", "print('not measured')\n")
        self.write("api/tests/test_service.py", "def test_answer():\n    assert True\n")
        self.write("sonar-project.properties", properties())
        self.run_git("init", "-q")
        self.commit()
        self.base = self.head
        self.output = self.root / ".sonar-evidence"
        self.reports = {kind: self.root / f"input-{kind}.report" for kind in ("python", "client", "node")}
        self.reports["python"].write_bytes(xml_report())
        self.reports["client"].write_bytes(lcov_report())
        self.reports["node"].write_bytes(lcov_report(".github/scripts/check.mjs"))

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)

    def run_git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True).stdout.decode().strip()

    def commit(self):
        self.run_git("add", "api", "client", ".github", "scripts", "sonar-project.properties")
        self.run_git("-c", "user.name=Coverage Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        self.head = self.run_git("rev-parse", "HEAD")

    def tracked(self):
        return {item["path"]: item for item in guard.inventory(self.root, self.base, self.head)}

    def stamp(self):
        for report in self.reports.values():
            guard.stamp_report(self.root, self.head, self.base, report, "sonar-project.properties")

    def run_preflight(self):
        self.stamp()
        return guard.preflight(self.root, self.head, self.base, self.reports,
                               self.output, "sonar-project.properties")

    def verify(self, digest=None):
        if digest is None:
            digest = guard.sha256((self.output / "manifest.json").read_bytes())
        return guard.verify(self.root, self.head, self.base, self.output, digest)


class EvidenceIntegrationTests(Fixture):
    def test_normalized_manifest_is_deterministic_and_unreported_is_explicit(self):
        result = self.run_preflight()
        first = (self.output / "manifest.json").read_bytes()
        self.run_preflight()
        self.assertEqual(first, (self.output / "manifest.json").read_bytes())
        self.assertEqual(result["status"], "validated_for_scan")
        self.assertEqual(result["sonar_quality_gate"], "not_observed")
        self.assertEqual(result["coverage_completeness"], "incomplete")
        self.assertEqual(result["unreported_authored_files"], ["scripts/release/unreported.py"])
        self.assertIn(b'filename="api/src/service.py"', (self.output / "python.xml").read_bytes())
        self.assertIn(b"SF:client/src/app.ts", (self.output / "client.lcov").read_bytes())
        self.assertEqual(self.verify(), result)

    def test_every_tracked_path_is_in_inventory_including_dotgithub(self):
        result = self.run_preflight()
        self.assertEqual({item["path"] for item in result["inventory"]},
                         set(self.run_git("ls-files").splitlines()))
        self.assertIn("sonar-project.properties", result["config_sha256"])

    def test_changed_authored_source_omitted_from_reports_rejected(self):
        self.write("scripts/release/unreported.py", "print('changed and unmeasured')\n")
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "changed authored executable source omitted"):
            self.run_preflight()

    def test_changed_comment_only_python_is_not_fabricated_coverage(self):
        self.write("scripts/release/unreported.py", "# Documentation only\n")
        self.commit()
        result = self.run_preflight()
        item = next(item for item in result["inventory"] if item["path"] == "scripts/release/unreported.py")
        self.assertEqual(item["coverage_state"], "no_executable_lines")

    def test_changed_unsupported_config_and_declaration_require_review(self):
        self.write("scripts/release/check.sh", "#!/bin/sh\nexit 0\n")
        self.write("api/src/services/templates/sdk.py.j2", "{{ generated_python }}\n")
        self.write("client/src/types.d.ts", "declare const name: string;\n")
        self.write(".github/config.json", '{"example": true}\n')
        self.commit()
        result = self.run_preflight()
        self.assertEqual(result["changed_paths_requiring_human_review"],
                         [".github/config.json", "api/src/services/templates/sdk.py.j2",
                          "client/src/types.d.ts", "scripts/release/check.sh"])

    def test_changed_generated_vendor_and_directory_alias_require_review(self):
        self.write(".agents/skills/generated/helper.py", "print('generated mirror')\n")
        self.write("client/dist/new-script.js", "console.log('requires review');\n")
        self.write("client/node_modules/example/index.js", "console.log('vendor');\n")
        self.write("client/src/lib/v1.d.ts", "declare const generated: string;\n")
        self.write(".agents/plugins/marketplace.json", '{"plugins": []}\n')
        (self.root / "skills").mkdir()
        (self.root / "skills/build").symlink_to(guard.SYMLINK_ALIASES["skills/build"])
        self.run_git("add", ".agents", "skills")
        self.commit()
        result = self.run_preflight()
        self.assertEqual(result["changed_paths_requiring_human_review"], [
            ".agents/plugins/marketplace.json", ".agents/skills/generated/helper.py",
            "client/dist/new-script.js", "client/node_modules/example/index.js",
            "client/src/lib/v1.d.ts", "skills/build",
        ])
        alias = next(item for item in result["inventory"] if item["path"] == "skills/build")
        self.assertEqual(alias["language"], "directory_alias")
        self.assertEqual(alias["coverage_state"], "not_measured_by_this_coverage_gate")

    def test_authored_agents_file_cannot_be_hidden_by_blanket_exclusion(self):
        self.write(".agents/custom.py", "print('authored tooling')\n")
        self.write("sonar-project.properties", properties().replace(
            "sonar.exclusions=", "sonar.exclusions=.agents/**,"))
        self.run_git("add", ".agents")
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, r"authored source excluded.*\.agents/custom\.py"):
            self.run_preflight()

    def test_missing_report_rejected(self):
        self.reports["python"].unlink()
        with self.assertRaisesRegex(guard.EvidenceError, "missing/unreadable report"):
            self.run_preflight()

    def test_empty_reports_rejected(self):
        for content in (b"", b" \n"):
            with self.subTest(content=content):
                self.reports["client"].write_bytes(content)
                with self.assertRaisesRegex(guard.EvidenceError, "empty"):
                    self.run_preflight()

    def test_unstamped_report_rejected(self):
        with self.assertRaisesRegex(guard.EvidenceError, "missing/malformed evidence"):
            guard.preflight(self.root, self.head, self.base, self.reports, self.output, "sonar-project.properties")

    def test_changed_report_bytes_after_stamp_rejected(self):
        self.stamp()
        self.reports["client"].write_bytes(lcov_report(hit=0))
        with self.assertRaisesRegex(guard.EvidenceError, "provenance/SHA/config mismatch"):
            guard.preflight(self.root, self.head, self.base, self.reports, self.output, "sonar-project.properties")

    def test_stale_report_head_rejected(self):
        self.stamp()
        target = Path(str(self.reports["python"]) + ".provenance.json")
        data = guard.read_json(target)
        data["head"] = "0" * 40
        target.write_bytes(guard.json_bytes(data))
        with self.assertRaisesRegex(guard.EvidenceError, "provenance/SHA/config mismatch"):
            guard.preflight(self.root, self.head, self.base, self.reports, self.output, "sonar-project.properties")

    def test_stale_config_stamp_rejected(self):
        self.stamp()
        self.write("sonar-project.properties", properties() + "# configuration changed\n")
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "provenance/SHA/config mismatch"):
            guard.preflight(self.root, self.head, self.base, self.reports, self.output, "sonar-project.properties")

    def test_exact_head_and_clean_checkout_required(self):
        with self.assertRaisesRegex(guard.EvidenceError, "exact, lowercase"):
            guard.validate_checkout(self.root, self.head[:8], self.base)
        self.write("api/src/service.py", "print('dirty')\n")
        with self.assertRaisesRegex(guard.EvidenceError, "working tree or index"):
            self.run_preflight()

    def test_mismatched_checkout_sha_rejected(self):
        original = self.head
        self.write("api/src/service.py", "print('new')\n")
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "head SHA mismatch"):
            guard.validate_checkout(self.root, original, self.base)

    def test_advanced_event_base_preserves_identity_and_compares_merge_base(self):
        common = self.head
        self.write("api/src/only_on_base.py", "print('upstream only')\n")
        self.commit()
        event_base = self.head
        self.run_git("checkout", "--detach", common)
        self.write("api/src/service.py", "def answer():\n    return 43\n\n")
        self.commit()
        self.base = event_base
        result = self.run_preflight()
        self.assertEqual(result["base"], event_base)
        self.assertEqual(result["head"], self.head)
        self.assertEqual(result["merge_base"], common)
        self.assertEqual([item["path"] for item in result["inventory"] if item["changed"]], ["api/src/service.py"])
        self.verify()

    def test_unrelated_commit_histories_rejected(self):
        self.run_git("checkout", "--orphan", "unrelated")
        self.write("api/src/service.py", "print('unrelated history')\n")
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "git merge-base"):
            guard.validate_checkout(self.root, self.head, self.base)

    def test_verify_detects_report_tampering(self):
        self.run_preflight()
        (self.output / "node.lcov").write_bytes(lcov_report(".github/scripts/check.mjs", hit=0))
        with self.assertRaisesRegex(guard.EvidenceError, "changed since preflight"):
            self.verify()

    def test_output_cannot_overwrite_source_via_symlink(self):
        self.output.mkdir()
        (self.output / "python.xml").symlink_to(self.root / "api/src/service.py")
        with self.assertRaisesRegex(guard.EvidenceError, "overwrite a symlink"):
            self.run_preflight()
        self.assertEqual((self.root / "api/src/service.py").read_text(), "def answer():\n    return 42\n\n")

    def test_overlapping_reports_are_rejected_instead_of_double_counting(self):
        self.reports["node"].write_bytes(lcov_report("client/src/app.ts"))
        with self.assertRaisesRegex(guard.EvidenceError, "multiple report inputs"):
            self.run_preflight()

    def test_verify_detects_manifest_tampering_from_job_digest(self):
        result = self.run_preflight()
        digest = guard.sha256((self.output / "manifest.json").read_bytes())
        result["inventory"] = []
        (self.output / "manifest.json").write_bytes(guard.json_bytes(result))
        with self.assertRaisesRegex(guard.EvidenceError, "manifest hash"):
            self.verify(digest)

    def test_verify_rejects_unverified_status(self):
        result = self.run_preflight()
        result["status"] = "tests_passed"
        (self.output / "manifest.json").write_bytes(guard.json_bytes(result))
        with self.assertRaisesRegex(guard.EvidenceError, "status/schema"):
            self.verify()

    def test_verify_rejects_changed_source_or_config(self):
        self.run_preflight()
        self.write("sonar-project.properties", properties() + "sonar.coverage.exclusions=**\n")
        with self.assertRaisesRegex(guard.EvidenceError, "working tree or index"):
            self.verify()

    def test_authored_exclusion_rejected(self):
        self.write("sonar-project.properties", properties().replace("sonar.exclusions=", "sonar.exclusions=api/src/**,"))
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "authored source excluded"):
            self.run_preflight()

    def test_coverage_exclusion_rejected(self):
        self.write("sonar-project.properties", properties() + "sonar.coverage.exclusions=.github/scripts/**\n")
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "authored source excluded"):
            self.run_preflight()

    def test_source_misclassified_as_test_rejected(self):
        self.write("sonar-project.properties", properties().replace("sonar.test.inclusions=", "sonar.test.inclusions=api/src/**,"))
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "authored source excluded or misclassified"):
            self.run_preflight()

    def test_unvalidated_report_property_rejected(self):
        self.write("sonar-project.properties", properties().replace(".sonar-evidence/python.xml", "old-coverage.xml"))
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "only to validated outputs"):
            self.run_preflight()


class ReportParserTests(Fixture):
    def test_container_paths_and_document_renderer_map_to_repo(self):
        self.write("doc_renderer_service/main.py", "print('renderer')\nprint('done')\n")
        self.run_git("add", "doc_renderer_service")
        self.commit()
        tracked = self.tracked()
        for filename, sources, expected in (
            ("src/service.py", "/app", "api/src/service.py"),
            ("api/src/service.py", "/repo", "api/src/service.py"),
            ("/repo/api/src/service.py", "/repo", "api/src/service.py"),
            ("/app/doc_renderer_service/main.py", "/app", "doc_renderer_service/main.py"),
        ):
            with self.subTest(filename=filename):
                _, result = guard.parse_python(xml_report(filename, sources), self.root, tracked)
                self.assertEqual(list(result), [expected])

    def test_ambiguous_cobertura_source_rejected(self):
        self.write("scripts/shared.py", "print(1)\nprint(2)\n")
        self.write("api/scripts/shared.py", "print(1)\nprint(2)\n")
        self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "ambiguous"):
            guard.parse_python(xml_report("scripts/shared.py"), self.root, self.tracked())

    def test_checkout_absolute_node_lcov_normalized(self):
        output, result = guard.parse_lcov(lcov_report(str(self.root / ".github/scripts/check.mjs")), self.root, self.tracked(), "node")
        self.assertIn(b"SF:.github/scripts/check.mjs", output)
        self.assertEqual(list(result), [".github/scripts/check.mjs"])

    def test_native_node_can_measure_client_tooling(self):
        output, result = guard.parse_lcov(lcov_report("client/src/app.ts"), self.root, self.tracked(), "node")
        self.assertIn(b"SF:client/src/app.ts", output)
        self.assertEqual(list(result), ["client/src/app.ts"])

    def test_native_node_allows_same_function_name_on_different_lines(self):
        data = lcov_report().replace(b"FN:1,run", b"FN:1,run\nFN:2,run")
        data = data.replace(b"FNDA:1,run", b"FNDA:1,run\nFNDA:0,run").replace(b"FNF:1", b"FNF:2")
        guard.parse_lcov(data, self.root, self.tracked(), "client")
        with self.assertRaisesRegex(guard.EvidenceError, "definitions/hits disagree"):
            guard.parse_lcov(data.replace(b"FNDA:0,run", b"FNDA:0,different"), self.root, self.tracked(), "client")

    def test_sourcemapped_branches_can_differ_from_da_lines(self):
        data = lcov_report(branch=True).replace(b"BRDA:1,0,1,-", b"BRDA:3,0,1,-")
        _, result = guard.parse_lcov(data, self.root, self.tracked(), "client")
        self.assertEqual(result["client/src/app.ts"]["branches"], 2)

    def test_unsafe_and_unknown_paths_rejected(self):
        for path in ("../api/src/service.py", "/app/../src/service.py", "missing.py", "/etc/passwd", "C:\\source.py"):
            with self.subTest(path=path), self.assertRaises(guard.EvidenceError):
                guard.parse_python(xml_report(path), self.root, self.tracked())

    def test_source_symlink_rejected(self):
        (self.root / "api/src/service.py").unlink()
        (self.root / "api/src/service.py").symlink_to(self.root / "scripts/release/unreported.py")
        with self.assertRaisesRegex(guard.EvidenceError, "symlink"):
            guard.inventory(self.root, self.base, self.head)

    def test_malformed_or_empty_xml_rejected(self):
        for data in (b"<coverage>", b"<notcoverage/>", b"<coverage/>",
                     b'<!DOCTYPE coverage [<!ENTITY x "secret">]><coverage/>'):
            with self.subTest(data=data), self.assertRaises(guard.EvidenceError):
                guard.parse_python(data, self.root, self.tracked())

    def test_python_out_of_bounds_lines_and_invalid_hits_rejected(self):
        for data in (xml_report().replace(b'number="2"', b'number="99"'),
                     xml_report().replace(b'number="2"', b'number="1"'),
                     xml_report().replace(b'hits="1"', b'hits="-1"'),
                     xml_report().replace(b'lines-valid="2"', b'lines-valid="3"')):
            with self.subTest(data=data), self.assertRaises(guard.EvidenceError):
                guard.parse_python(data, self.root, self.tracked())

    def test_python_branches_validate(self):
        _, result = guard.parse_python(xml_report(branch=True), self.root, self.tracked())
        self.assertEqual(result["api/src/service.py"]["branches"], 2)
        for old, new in ((b"50% (1/2)", b"50% (3/2)"), (b"50% (1/2)", b"100% (1/2)"),
                         (b'branch="true"', b'branch="maybe"'), (b'branch="true"', b'branch="false"')):
            with self.subTest(new=new), self.assertRaises(guard.EvidenceError):
                guard.parse_python(xml_report(branch=True).replace(old, new), self.root, self.tracked())

    def test_python_rates_and_nested_condition_structure_are_checked(self):
        for rate in (b"nan", b"1.5", b"-1", b"0.25"):
            data = xml_report().replace(b"<coverage ", b'<coverage line-rate="' + rate + b'" ')
            with self.subTest(rate=rate), self.assertRaises(guard.EvidenceError):
                guard.parse_python(data, self.root, self.tracked())
        data = xml_report(branch=True).replace(
            b'condition-coverage="50% (1/2)"/>',
            b'condition-coverage="50% (1/2)"><conditions><condition number="0" type="jump" coverage="500%"/></conditions></line>')
        with self.assertRaisesRegex(guard.EvidenceError, "condition percentage"):
            guard.parse_python(data, self.root, self.tracked())

    def test_python_method_lines_cannot_escape_source(self):
        data = xml_report().replace(b"</class>", b'<methods><method><lines><line number="99" hits="1"/></lines></method></methods></class>')
        with self.assertRaisesRegex(guard.EvidenceError, "outside source"):
            guard.parse_python(data, self.root, self.tracked())

    def test_lcov_out_of_bounds_lines_and_invalid_hits_rejected(self):
        for old, new in ((b"DA:2,1", b"DA:99,1"), (b"DA:2,1", b"DA:1,1"),
                         (b"DA:2,1", b"DA:2,-1"), (b"LF:2", b"LF:3"),
                         (b"FNH:1", b"FNH:0"), (b"FN:1,run", b"FN:9,run"),
                         (b"end_of_record", b"")):
            with self.subTest(new=new), self.assertRaises(guard.EvidenceError):
                guard.parse_lcov(lcov_report().replace(old, new), self.root, self.tracked(), "client")

    def test_lcov_branch_structure_rejected(self):
        _, result = guard.parse_lcov(lcov_report(branch=True), self.root, self.tracked(), "client")
        self.assertEqual(result["client/src/app.ts"]["branches_hit"], 1)
        for old, new in ((b"BRDA:1,0,1,-", b"BRDA:1,0,0,-"),
                         (b"BRDA:1,0,1,-", b"BRDA:99,0,1,-"),
                         (b"BRDA:1,0,1,-", b"BRDA:1,x,1,-"),
                         (b"BRDA:1,0,1,-", b"BRDA:1,0,1,-2"), (b"BRH:1", b"BRH:2")):
            with self.subTest(new=new), self.assertRaises(guard.EvidenceError):
                guard.parse_lcov(lcov_report(branch=True).replace(old, new), self.root, self.tracked(), "client")

    def test_empty_or_duplicate_lcov_records_rejected(self):
        for data in (b"TN:\n", b"LF:1\n", lcov_report() + lcov_report()):
            with self.subTest(data=data), self.assertRaises(guard.EvidenceError):
                guard.parse_lcov(data, self.root, self.tracked(), "client")

    def test_cross_language_or_report_scope_rejected(self):
        for data, kind in ((lcov_report("api/src/service.py"), "node"),
                           (lcov_report(".github/scripts/check.mjs"), "client")):
            with self.subTest(kind=kind), self.assertRaises(guard.EvidenceError):
                guard.parse_lcov(data, self.root, self.tracked(), kind)

    def test_zero_hit_record_never_becomes_covered(self):
        self.reports["client"].write_bytes(lcov_report(hit=0))
        result = self.run_preflight()
        item = next(item for item in result["inventory"] if item["path"] == "client/src/app.ts")
        self.assertEqual(item["coverage_state"], "reported_zero_hits")
        self.assertEqual(item["coverage"]["lines_hit"], 0)


class ScopeUnitTests(unittest.TestCase):
    def test_scanner_destination_cannot_redirect_the_project_or_credential(self):
        for key, value in (("sonar.projectKey", None), ("sonar.projectKey", "other_project"),
                           ("sonar.host.url", None), ("sonar.host.url", "http://sonarcloud.io"),
                           ("sonar.host.url", "https://unapproved.invalid")):
            props = guard.read_properties(properties().encode())
            if value is None:
                del props[key]
            else:
                props[key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(guard.EvidenceError, "approved"):
                guard.validate_scope([], props)

    def test_globs_cover_root_and_nested_paths(self):
        self.assertTrue(guard.glob_match("test_a.py", "**/test_*.py"))
        self.assertTrue(guard.glob_match("api/tests/test_a.py", "**/tests/**"))
        self.assertFalse(guard.glob_match("api/test_a.py", "*.py"))
        self.assertFalse(guard.glob_match("scripts/release/automatic-release.py", "api/**"))

    def test_duplicate_and_malformed_properties_fail(self):
        for data in (b"sonar.sources=.\nsonar.sources=api\n", b"sonar.sources .\n", b"sonar.sources=\\"):
            with self.subTest(data=data), self.assertRaises(guard.EvidenceError):
                guard.read_properties(data)

    def test_scanner_cannot_hide_tracked_source_with_scm_ignore(self):
        props = guard.read_properties(properties().encode())
        props["sonar.scm.exclusions.disabled"] = "false"
        with self.assertRaisesRegex(guard.EvidenceError, "scm.exclusions"):
            guard.validate_scope([], props)

    def test_inventory_classifications_do_not_hide_critical_surfaces(self):
        for path in ("api/bifrost/cli.py", "api/bifrost/solution_dev/proxy.py", ".github/scripts/authorize-merge-queue.mjs",
                     "scripts/release/automatic-release.py", ".claude/skills/pack.py", "doc_renderer_service/app.py",
                     ".agents/custom.py"):
            with self.subTest(path=path):
                self.assertEqual(guard.classification(path)[0], "authored")

    def test_only_known_agents_mirrors_are_generated(self):
        self.assertEqual(guard.classification(".agents/skills/example/helper.py")[0], "generated_or_vendor")
        self.assertEqual(guard.classification(".agents/plugins/marketplace.json")[0], "configuration_requires_review")


if __name__ == "__main__":
    unittest.main()
