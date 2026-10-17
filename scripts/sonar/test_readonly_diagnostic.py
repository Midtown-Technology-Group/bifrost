"""Offline, standard-library-only probes for the exact diagnostic workflow code."""

import contextlib
import copy
import http.client
import io
import json
import os
from pathlib import Path
import socket
import ssl
import textwrap
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/sonar-readonly-diagnostic.yml"
PROJECT = "Midtown-Technology-Group_bifrost"
TOKEN = "fake-secret-not-real"
OMITTED = "unselected-response-field-must-not-be-printed"
WORKFLOW_TEXT = WORKFLOW.read_text()


def inline_scripts():
    """Extract only the two expected isolated-Python heredocs, without PyYAML."""
    blocks = WORKFLOW_TEXT.split("        run: |\n")
    if len(blocks) != 3:
        raise AssertionError("Expected exactly two inline workflow run blocks")
    scripts = []
    prefix = "          python3 -I -B - <<'PY'\n"
    for block in blocks[1:]:
        if not block.startswith(prefix):
            raise AssertionError("Expected an isolated standard-library Python step")
        code, separator, _ = block[len(prefix):].partition("\n          PY")
        if not separator:
            raise AssertionError("Missing Python heredoc terminator")
        scripts.append(textwrap.dedent(code))
    return scripts


SCRIPTS = inline_scripts()
# These are workflow heredocs, not standalone Python files. A synthetic filename
# keeps coverage.py from parsing the YAML workflow while exporting Python XML.
CODE_OBJECTS = tuple(compile(script, "<sonar-readonly-diagnostic>", "exec") for script in SCRIPTS)
PARAMETERS = {
    "components/show": ["component"],
    "components/tree": ["component", "qualifiers", "strategy", "ps", "p"],
    "project_branches/list": ["project"],
    "project_analyses/search": ["project", "ps"],
    "qualitygates/get_by_project": ["project", "organization"],
    "qualitygates/show": ["id", "organization"],
    "qualityprofiles/search": ["project", "organization"],
    "settings/values": ["component", "keys"],
}


def responses():
    services = []
    for path, parameters in PARAMETERS.items():
        service, action = path.split("/")
        services.append({
            "path": "api/" + service,
            "actions": [{
                "key": action,
                "post": False,
                "params": [{"key": key, "required": False} for key in parameters],
            }],
        })
    return {
        "webservices/list": {"webServices": services},
        "components/show": {"component": {
            "key": PROJECT, "qualifier": "TRK", "organization": "mtg",
            "visibility": "public", "unselected": OMITTED,
        }},
        "qualitygates/get_by_project": {
            "qualityGate": {"id": "gate1", "name": "Gate", "default": True},
        },
        "qualitygates/show": {
            "id": "gate1", "name": "Gate",
            "conditions": [{"metric": "new_coverage", "op": "LT", "error": "80", "unselected": OMITTED}],
        },
        "qualityprofiles/search": {"profiles": [{
            "key": "profile1", "name": "Sonar way", "language": "py", "unselected": OMITTED,
        }]},
        "settings/values": {"settings": [
            {"key": "sonar.exclusions", "values": ["**/node_modules/**"], "inherited": True},
            {"key": "sonar.secret", "value": OMITTED},
            {"key": "sonar.qualitygate.ignoreSmallChanges", "value": "true"},
        ]},
        "project_branches/list": {"branches": [
            {"name": "main", "isMain": True, "type": "LONG"},
            {"name": "private-branch-name", "isMain": False},
        ]},
        "project_analyses/search": {"analyses": [{
            "key": "analysis1", "date": "2026-10-05", "revision": "a" * 40, "unselected": OMITTED,
        }]},
        "components/tree": {
            "paging": {"total": 2, "pageIndex": 1, "pageSize": 500},
            "components": [
                {"key": PROJECT + ":api/a.py", "path": "api/a.py", "language": "py"},
                {"key": PROJECT + ":client/a.ts", "path": "client/a.ts", "language": "ts"},
            ],
        },
    }


class ReadonlyDiagnosticTests(unittest.TestCase):
    def execute(self, change=None):
        fixtures = responses()
        if change:
            change(fixtures)
        calls = []
        connections = []

        class Connection:
            def __init__(self, host, timeout, context):
                connections.append((host, timeout, context.check_hostname, context.verify_mode))

            def request(self, method, uri, headers):
                self.path = urlsplit(uri).path.removeprefix("/api/")
                calls.append((method, uri, copy.deepcopy(headers)))

            def getresponse(self):
                value = fixtures[self.path]
                self.status = value if isinstance(value, int) else 200
                return self

            def getheader(self, key, default=""):
                return "application/json"

            def read(self, limit):
                return json.dumps(fixtures[self.path]).encode()[:limit]

            def close(self):
                pass

        output = io.StringIO()
        status = 0
        with (
            patch.object(http.client, "HTTPSConnection", Connection),
            patch.object(
                socket, "create_connection", side_effect=AssertionError("No real network in offline tests"),
            ) as network,
            patch.dict(os.environ, {"SONAR_TOKEN": TOKEN, "GITHUB_SHA": "a" * 40}),
            contextlib.redirect_stdout(output),
        ):
            try:
                exec(CODE_OBJECTS[1], {})
            except SystemExit as error:
                status = error.code

        # Check invariants outside the workflow's exception handler so a swallowed
        # assertion cannot make a negative probe pass for an unrelated failure.
        network.assert_not_called()
        for connection in connections:
            self.assertEqual(connection, ("sonarcloud.io", 20, True, ssl.CERT_REQUIRED))
        parsed_calls = []
        for method, uri, headers in calls:
            self.assertEqual(method, "GET")
            self.assertTrue(uri.startswith("/api/"))
            self.assertNotIn(TOKEN, uri)
            parsed = urlsplit(uri)
            path = parsed.path.removeprefix("/api/")
            self.assertIn(path, fixtures)
            authorization = None if path == "webservices/list" else "Bearer " + TOKEN
            self.assertEqual(headers.get("Authorization"), authorization)
            parsed_calls.append((path, parse_qs(parsed.query)))
        text = output.getvalue()
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(OMITTED, text)
        self.assertNotIn("private-branch-name", text)
        return status, json.loads(text), parsed_calls

    def test_happy_path_exact_scope_and_selected_output(self):
        status, result, calls = self.execute()
        self.assertEqual(status, 0)
        self.assertTrue(result["analysis_stable_during_read"])
        self.assertEqual(result["source_inventory"]["source_files"], 2)
        self.assertEqual(result["source_inventory"]["by_known_root"], {"api": 1, "client": 1})
        self.assertEqual(result["project"]["organization"], "mtg")
        self.assertEqual(result["default_branch"]["name"], "main")
        self.assertEqual(len(calls), 10)
        for _, parameters in calls:
            for key in ("project", "component"):
                if key in parameters:
                    self.assertEqual(parameters[key], [PROJECT])
        settings = result["server_settings"]
        self.assertIn("sonar.coverage.exclusions", settings["not_returned"])
        self.assertTrue(settings["values"][0]["inherited"])

    def test_redirect_never_followed_or_retried(self):
        status, result, calls = self.execute(lambda values: values.__setitem__("components/show", 302))
        self.assertNotEqual(status, 0)
        self.assertEqual([path for path, _ in calls], ["webservices/list", "components/show"])
        self.assertEqual(result["inspection"]["unavailable"], "HTTP 302; no redirect or retry.")

    def test_metadata_403_never_authenticates_or_retries(self):
        status, result, calls = self.execute(lambda values: values.__setitem__("webservices/list", 403))
        self.assertNotEqual(status, 0)
        self.assertEqual([path for path, _ in calls], ["webservices/list"])
        self.assertEqual(result["inspection"]["unavailable"], "HTTP 403; no redirect or retry.")

    def test_post_descriptor_is_blocked_before_authentication(self):
        def change(values):
            values["webservices/list"]["webServices"][0]["actions"][0]["post"] = True

        status, result, calls = self.execute(change)
        self.assertNotEqual(status, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("does not confirm a GET action", result["inspection"]["unavailable"])

    def test_internal_descriptor_is_blocked_before_authentication(self):
        def change(values):
            values["webservices/list"]["webServices"][0]["actions"][0]["internal"] = True

        status, result, calls = self.execute(change)
        self.assertNotEqual(status, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("does not confirm a GET action", result["inspection"]["unavailable"])

    def test_partial_403_is_unavailable_without_write_or_retry(self):
        status, result, calls = self.execute(lambda values: values.__setitem__("settings/values", 403))
        self.assertNotEqual(status, 0)
        self.assertEqual(result["source_inventory"]["source_files"], 2)
        self.assertEqual(result["unverified_sections"], ["server_settings"])
        self.assertEqual(result["server_settings"], {"unavailable": "HTTP 403; no redirect or retry."})
        self.assertEqual(sum(path == "settings/values" for path, _ in calls), 1)

    def test_component_identity_mismatch_stops_project_reads(self):
        def change(values):
            values["components/show"]["component"]["key"] = "other-project"

        status, result, calls = self.execute(change)
        self.assertNotEqual(status, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["inspection"]["unavailable"], "Returned project identity does not match.")

    def test_duplicate_inventory_fails_closed(self):
        def change(values):
            components = values["components/tree"]["components"]
            components[1] = components[0]

        status, result, _ = self.execute(change)
        self.assertNotEqual(status, 0)
        self.assertIn("source_inventory", result["unverified_sections"])
        self.assertIn("duplicate", result["source_inventory"]["unavailable"])

    def test_duplicate_component_key_with_a_different_path_fails_closed(self):
        def change(values):
            components = values["components/tree"]["components"]
            components[1]["key"] = components[0]["key"]

        status, result, _ = self.execute(change)
        self.assertNotEqual(status, 0)
        self.assertIn("source_inventory", result["unverified_sections"])
        self.assertIn("duplicate component key", result["source_inventory"]["unavailable"])

    def test_inventory_totals_require_bounded_nonnegative_integers(self):
        for total in (-1, True, False, 2.0, "2", None, {}, 10_001):
            with self.subTest(total=total):
                def change(values):
                    values["components/tree"]["paging"]["total"] = total

                status, result, _ = self.execute(change)
                self.assertNotEqual(status, 0)
                self.assertEqual(result["source_inventory"]["unavailable"],
                                 "Metadata count must be a bounded nonnegative integer.")

    def test_inventory_pagination_fields_require_matching_integer_counts(self):
        for field, value in (("pageIndex", True), ("pageIndex", 1.0), ("pageIndex", 2),
                             ("pageSize", "500"), ("pageSize", -1), ("pageSize", 499)):
            with self.subTest(field=field, value=value):
                def change(values):
                    values["components/tree"]["paging"][field] = value

                status, result, _ = self.execute(change)
                self.assertNotEqual(status, 0)
                self.assertIn("source_inventory", result["unverified_sections"])

    def test_inventory_page_counts_must_match_the_declared_total(self):
        for total in (1, 3):
            with self.subTest(total=total):
                def change(values):
                    values["components/tree"]["paging"]["total"] = total

                status, result, _ = self.execute(change)
                self.assertNotEqual(status, 0)
                self.assertEqual(result["source_inventory"]["unavailable"],
                                 "Inventory page count does not match its declared total.")

    def test_empty_inventory_is_valid_without_fabricating_files(self):
        def change(values):
            values["components/tree"]["paging"]["total"] = 0
            values["components/tree"]["components"] = []

        status, result, _ = self.execute(change)
        self.assertEqual(status, 0)
        self.assertEqual(result["source_inventory"]["source_files"], 0)
        self.assertEqual(result["source_inventory"]["by_language"], {})

    def test_selected_metadata_rejects_nested_values_and_wrong_scalar_types(self):
        cases = (
            ("components/show", ("component", "visibility"), {"nested": OMITTED}, "inspection"),
            ("qualitygates/get_by_project", ("qualityGate", "id"), {"nested": OMITTED}, "quality_gate"),
            ("qualitygates/show", ("conditions", 0, "error"), [OMITTED], "quality_gate"),
            ("qualityprofiles/search", ("profiles", 0, "activeRuleCount"), True, "quality_profiles"),
            ("settings/values", ("settings", 0, "values"), [[OMITTED]], "server_settings"),
            ("settings/values", ("settings", 0, "value"), {"nested": OMITTED}, "server_settings"),
            ("settings/values", ("settings", 0, "parentValues"), [{"nested": OMITTED}], "server_settings"),
            ("settings/values", ("settings", 0, "inherited"), "true", "server_settings"),
            ("project_branches/list", ("branches", 0, "name"), {"nested": OMITTED}, "default_branch"),
            ("project_analyses/search", ("analyses", 0, "revision"), [OMITTED], "latest_default_analysis"),
            ("components/tree", ("components", 0, "language"), {"nested": OMITTED}, "source_inventory"),
            ("components/tree", ("components", 0, "path"), [OMITTED], "source_inventory"),
            ("components/tree", ("components", 0, "key"), {"nested": OMITTED}, "source_inventory"),
        )
        for endpoint, path, value, section in cases:
            with self.subTest(endpoint=endpoint, field=path[-1]):
                def change(values):
                    target = values[endpoint]
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = value

                status, result, _ = self.execute(change)
                self.assertNotEqual(status, 0)
                self.assertIn(section, result["unverified_sections"])
                self.assertIn("unavailable", result[section])

    def test_latest_analysis_rejects_more_than_the_requested_one_result(self):
        def change(values):
            analyses = values["project_analyses/search"]["analyses"]
            analyses.append(dict(analyses[0], key="unexpected-extra-analysis"))

        status, result, _ = self.execute(change)
        self.assertNotEqual(status, 0)
        self.assertIn("latest_default_analysis", result["unverified_sections"])
        self.assertIn("latest_default_analysis_recheck", result["unverified_sections"])
        self.assertFalse(result["analysis_stable_during_read"])
        self.assertNotIn("unexpected-extra-analysis", json.dumps(result))

    def test_selected_metadata_strings_and_arrays_are_bounded(self):
        def oversized_string(values):
            values["components/show"]["component"]["visibility"] = "x" * 4097

        status, result, _ = self.execute(oversized_string)
        self.assertNotEqual(status, 0)
        self.assertNotIn("x" * 4097, json.dumps(result))
        self.assertEqual(result["inspection"]["unavailable"], "Metadata must contain bounded strings.")

        def oversized_array(values):
            values["settings/values"]["settings"][0]["values"] = ["pattern"] * 257

        status, result, _ = self.execute(oversized_array)
        self.assertNotEqual(status, 0)
        self.assertEqual(result["server_settings"]["unavailable"],
                         "Metadata array has an unexpected type or size.")

    def test_valid_array_settings_and_scalar_metadata_are_preserved(self):
        def change(values):
            setting = values["settings/values"]["settings"][0]
            setting["values"] = ["**/node_modules/**", "client/dist/**"]
            setting["parentValues"] = ["generated/**"]
            values["qualitygates/get_by_project"]["qualityGate"]["id"] = 123
            values["qualitygates/show"]["id"] = 123
            values["qualitygates/show"]["conditions"][0].update({"error": 80.5, "period": 1})
            values["qualityprofiles/search"]["profiles"][0]["activeRuleCount"] = 100

        status, result, _ = self.execute(change)
        self.assertEqual(status, 0)
        setting = result["server_settings"]["values"][0]
        self.assertEqual(setting["values"], ["**/node_modules/**", "client/dist/**"])
        self.assertEqual(setting["parentValues"], ["generated/**"])
        self.assertEqual(result["quality_gate"]["assignment"]["id"], 123)
        self.assertEqual(result["quality_gate"]["conditions"][0]["error"], 80.5)

    def test_nonfinite_gate_thresholds_are_not_printed(self):
        for threshold in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(threshold=threshold):
                def change(values):
                    values["qualitygates/show"]["conditions"][0]["error"] = threshold

                status, result, _ = self.execute(change)
                self.assertNotEqual(status, 0)
                self.assertEqual(result["quality_gate"]["unavailable"],
                                 "Metadata threshold must be finite and bounded.")

    def test_echoed_secret_is_redacted_and_never_becomes_a_query_parameter(self):
        def change(values):
            values["components/show"]["component"]["organization"] = TOKEN

        status, result, calls = self.execute(change)
        self.assertNotEqual(status, 0)
        self.assertEqual(result["project"]["organization"], "[REDACTED]")
        self.assertFalse(any(path.startswith("qualitygates/") for path, _ in calls))
        self.assertEqual(result["quality_gate"]["unavailable"],
                         "A response-derived parameter matched the credential; request blocked.")

    def test_exact_caller_callee_and_reviewed_identity_guard(self):
        environment = {
            "REVIEWED_SHA": "a" * 40,
            "WORKFLOW_SHA": "a" * 40,
            "GITHUB_SHA": "a" * 40,
            "CALLEE_WORKFLOW_SHA": "a" * 40,
            "CALLEE_REPOSITORY": "Midtown-Technology-Group/bifrost",
            "CALLEE_PATH": ".github/workflows/sonar-readonly-diagnostic.yml",
        }
        with patch.dict(os.environ, environment):
            exec(CODE_OBJECTS[0], {})
        for key in environment:
            with self.subTest(identity=key), patch.dict(os.environ, dict(environment, **{key: "b" * 40})):
                with self.assertRaises(SystemExit):
                    exec(CODE_OBJECTS[0], {})

    def test_inline_code_uses_only_synthetic_coverage_filenames(self):
        pending = list(CODE_OBJECTS)
        while pending:
            code = pending.pop()
            self.assertEqual(code.co_filename, "<sonar-readonly-diagnostic>")
            self.assertFalse(code.co_filename.endswith((".py", ".yml", ".yaml")))
            pending.extend(value for value in code.co_consts if isinstance(value, types.CodeType))

    def test_no_extra_steps_actions_or_broader_event_trigger(self):
        self.assertEqual(len(SCRIPTS), 2)
        self.assertEqual(WORKFLOW_TEXT.count("      - name:"), 2)
        self.assertNotIn("uses:", WORKFLOW_TEXT)
        self.assertIn("permissions: {}", WORKFLOW_TEXT)
        triggers = WORKFLOW_TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(
            [line.strip() for line in triggers.splitlines() if line.startswith("  ") and not line.startswith("   ")],
            ["workflow_call:", "workflow_dispatch:"],
        )
        self.assertIn("github.event_name == 'workflow_dispatch'", WORKFLOW_TEXT)
        self.assertIn("github.repository == 'Midtown-Technology-Group/bifrost'", WORKFLOW_TEXT)
        self.assertIn("inputs.allow_visible_metadata", WORKFLOW_TEXT)
        self.assertEqual(WORKFLOW_TEXT.count("SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}"), 1)


if __name__ == "__main__":
    unittest.main()
