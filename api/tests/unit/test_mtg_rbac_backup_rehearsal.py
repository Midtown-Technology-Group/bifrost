"""Safety and independent restore-parity checks for the synthetic backup lane."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _load_helper():
    paths = (Path("/repo/scripts/rehearse-mtg-rbac-backup.py"),
             Path(__file__).resolve().parents[3] / "scripts/rehearse-mtg-rbac-backup.py")
    path = next(path for path in paths if path.exists())
    spec = importlib.util.spec_from_file_location("mtg_rbac_backup_rehearsal", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", [
    "postgres", "bifrost_test", "bifrost", "bifrost_test_template",
    "bifrost_r2b_rehearsal_abcdef123456;DROP DATABASE postgres",
    "bifrost_r2b_rehearsal_ABCDEF123456", "bifrost_r2b_rehearsal_1234",
])
def test_database_guard_rejects_shared_or_untrusted_targets(name):
    with pytest.raises(ValueError, match="synthetic rehearsal namespace"):
        _load_helper().validate_database_name(name)


def test_restore_parity_detects_changed_rows_even_with_equal_counts():
    helper = _load_helper()
    original = {"roles": {"rows": 5, "sha256": "original grants"}}
    altered = {"roles": {"rows": 5, "sha256": "altered grants"}}
    with pytest.raises(AssertionError, match="Restored rows differ"):
        helper.assert_restore_parity({"indexes": []}, {"indexes": []}, original, altered)


def test_restore_parity_detects_lost_nulls_not_distinct_semantics():
    helper = _load_helper()
    expected = {"indexes": [{"indnullsnotdistinct": True}]}
    altered = {"indexes": [{"indnullsnotdistinct": False}]}
    with pytest.raises(AssertionError, match="schema differs.*indexes"):
        helper.assert_restore_parity(expected, altered, {}, {})


def test_public_receipt_omits_raw_rows_and_original_json():
    receipt = _load_helper()._receipt({
        "source_legacy": {"roles": [{"permissions": {"can_execute_devices": True}}]},
        "source_catalog": {"functions": ["synthetic SQL"]},
        "source_data": {"synthetic": "rows"}, "owned_databases": ["private ledger"],
        "fixture_ids": {"user_a": "synthetic identity"}, "fixture_documents": {"home": "synthetic content"},
        "dump_sha256": "digest", "source_head": "20261012_work_delivery_started",
    })
    assert receipt == {"dump_sha256": "digest", "source_head": "20261012_work_delivery_started"}


@pytest.mark.parametrize("broken_contract", ["wrong_source", "writable", "missing_cleanup", "wrong_dependencies"])
def test_old_reader_acceptance_refuses_wrong_source_writes_or_incomplete_cleanup(broken_contract):
    helper = _load_helper()
    result = {"status": "passed", "source_commit": helper.OLD_COMMIT, "restored_head": helper.FORK_HEAD,
              "transaction_read_only": True, "old_application_modules_hash_verified": 20,
              "knowledge_documents_read": 2, "foreign_knowledge_document_denied": True,
              "http_auth_startup_ui_and_deployment_proven": False}
    provenance = {"status": "passed", "source_sha": helper.OLD_COMMIT,
                  "image_id": "sha256:" + "a" * 64, "source_archive_sha256": "b" * 64,
                  "source_bind_read_only": True, "synthetic_data_only": True, "host_ports_published": False,
                  "dependency_inputs_sha256": {key: "c" * 64 for key in (
                      "api/Dockerfile.dev", "pyproject.toml", "requirements.lock", "requirements-pyright.lock")}}
    cleanup = {"status": "passed", "container_removed": True, "image_removed": True,
               "temporary_source_removed": True, "envfile_removed": True}
    if broken_contract == "wrong_source":
        result["source_commit"] = "not-the-frozen-fork"
    elif broken_contract == "writable":
        result["transaction_read_only"] = False
    elif broken_contract == "missing_cleanup":
        cleanup["envfile_removed"] = False
    else:
        provenance["dependency_inputs_sha256"] = {}
    with pytest.raises(AssertionError):
        helper.assert_old_reader_proof(result, provenance, cleanup)


def test_cleanup_refuses_database_with_a_different_ownership_marker(monkeypatch, tmp_path):
    helper = _load_helper()
    drop = AsyncMock()

    async def inspect(_operation):
        return {"marker": "belongs to a different operation"}

    legacy = SimpleNamespace(_with_admin_connection=inspect, _drop_database=drop)
    monkeypatch.setattr(helper, "_helpers", lambda: (None, legacy))
    state = {"run_id": "a" * 32, "variant": "clean",
             "owned_databases": ["bifrost_r2b_rehearsal_abcdef123456"]}
    with pytest.raises(RuntimeError, match="cleanup could not be verified"):
        helper.cleanup(None, tmp_path / "state.json", state)
    drop.assert_not_awaited()
    assert state["database_cleanup_verified"] is False


def test_command_rejects_wrong_stack_before_stack_up(tmp_path):
    candidates = (Path("/app/test.sh"), Path(__file__).resolve().parents[3] / "test.sh")
    script = next(path for path in candidates if path.exists()).read_text()
    start = script.index("cmd_rbac_backup_rehearsal() {")
    end = script.index("\ncmd_ci() {", start)
    marker = tmp_path / "mutated-stack"
    wrong_compose = tmp_path / "docker-compose.production.yml"
    wrong_compose.write_text("services: {}\n")
    shell = f"""set -euo pipefail
stack_up() {{ touch '{marker}'; }}
compute_project_name() {{ echo bifrost-test-12345678; }}
""" + script[start:end] + "\ncmd_rbac_backup_rehearsal\n"
    result = subprocess.run(["bash", "-c", shell], env={
        **os.environ, "SCRIPT_DIR": str(tmp_path), "COMPOSE_FILE": str(wrong_compose),
        "COMPOSE_PROJECT_NAME": "bifrost-test-12345678",
    }, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "canonical isolated test stack" in result.stderr
    assert not marker.exists()


def _repo_file(relative):
    candidates = (Path("/repo") / relative, Path("/app") / relative,
                  Path(__file__).resolve().parents[3] / relative)
    return next(path for path in candidates if path.exists())


@pytest.mark.parametrize("diagnostic, expected", [
    ('pg_restore: error: option -1/--single-transaction requires a database to be specified', 'missing_database_target'),
    ('pg_restore: error: connection to server failed: password authentication failed for user "sensitive"', 'connection_failure'),
    ('pg_restore: error: unsupported version (1.17) in file header', 'archive_version_unsupported'),
    ('pg_restore: error: unexpected end of file', 'archive_read_failure'),
    ('pg_restore: error: could not execute query: ERROR: permission denied for schema sensitive', 'insufficient_privilege'),
    ('pg_restore: error: could not execute query: ERROR: relation "sensitive" already exists', 'schema_conflict'),
    ('pg_restore: error: could not execute query: ERROR: syntax error at sensitive', 'restore_sql_failure'),
    ('Error response from daemon: sensitive container is not running', 'container_execution_failure'),
    ('sensitive unknown driver output INSERT INTO sensitive VALUES (\'credential\')', 'unclassified_restore_failure'),
])
def test_restore_failure_receipt_exposes_only_closed_diagnostics(tmp_path, diagnostic, expected):
    log = tmp_path / "pg_restore.log"
    log.write_text(diagnostic)
    receipt = _load_helper().classify_restore_failure(log, 1)
    assert receipt == {
        "tool": "pg_restore", "exit_code": 1, "category": expected,
        "raw_diagnostic_exposed": False, "diagnostic_bytes": len(diagnostic.encode()),
    }
    assert "sensitive" not in json.dumps(receipt)
    assert "credential" not in json.dumps(receipt)


def test_missing_restore_diagnostic_does_not_guess_root_cause(tmp_path):
    result = _load_helper().classify_restore_failure(tmp_path / "absent", 1)
    assert result["category"] == "restore_diagnostic_unavailable"
    assert "diagnostic_bytes" not in result


def test_cleanup_records_closed_restore_diagnostic_before_removing_databases(tmp_path):
    helper = _load_helper()
    (tmp_path / "pg_restore.log").write_text("pg_restore: error: unexpected end of file; sensitive SQL")
    state = {"owned_databases": []}
    helper.cleanup(SimpleNamespace(failed_host_step="pg_restore", failed_host_exit_code=1),
                   tmp_path / "private-state.json", state)
    assert state["database_cleanup_verified"] is True
    assert state["status"] == "failed"
    assert state["restore_failure"]["category"] == "archive_read_failure"
    assert "sensitive" not in json.dumps(helper._receipt(state))


def test_both_actual_restore_shell_commands_select_validated_database(tmp_path):
    script = _repo_file("scripts/rehearse-mtg-rbac-backup.sh").read_text()
    bodies = re.findall(
        r'"\$\{compose\[@\]\}" exec -T postgres sh -eu -c \'([^\']*)\' sh "\$restored_database"',
        script,
    )
    assert len(bodies) == 2, "Both initial and interrupted-upgrade recovery restores are required"
    fake = tmp_path / "pg_restore"
    fake.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                    "print(json.dumps({'args': sys.argv[1:], 'database': os.environ['PGDATABASE']}))\n")
    fake.chmod(0o755)
    database = "bifrost_r2b_rehearsal_abcdef123456"
    for body in bodies:
        result = subprocess.run(["sh", "-eu", "-c", body, "sh", database], env={
            **os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "POSTGRES_USER": "synthetic", "POSTGRES_PASSWORD": "private-fixture-password",
        }, capture_output=True, text=True, check=True)
        observed = json.loads(result.stdout)
        arguments = observed["args"]
        assert arguments[arguments.index("--dbname") + 1] == observed["database"] == database
        assert "--single-transaction" in arguments and "--exit-on-error" in arguments
        assert "private-fixture-password" not in result.stdout


def test_phase_uses_nonroot_host_identity_without_entrypoint_chown():
    script = _repo_file("scripts/rehearse-mtg-rbac-backup.sh").read_text()
    start = script.index("phase() {")
    end = script.index("\nfinish() {", start)
    shell = """set -euo pipefail
capture() { printf '%s\\n' "$@"; }
compose=(capture)
rehearsal_uid=12345
rehearsal_gid=23456
rehearsal_run=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
active_variant=clean
""" + script[start:end] + "\nphase cleanup\n"
    result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, check=True)
    arguments = result.stdout.splitlines()
    assert arguments[arguments.index("--user") + 1] == "12345:23456"
    assert arguments.index("--user") < arguments.index("test-runner")


def test_export_logs_atomically_replaces_readonly_public_log_preserving_private_files(tmp_path):
    assert os.geteuid() != 0, "Repository Linux Docker tests must exercise non-root filesystem permissions"
    previous = tmp_path / "api.log"
    previous.write_text("previous container-owned log\n")
    previous.chmod(0o444)
    with pytest.raises(PermissionError):
        previous.open("w")
    private_artifacts = [tmp_path / "private-state.json", tmp_path / "fork-head.dump"]
    private_stats = {}
    for private in private_artifacts:
        private.write_text("private fixture material")
        private.chmod(0o600)
        private_stats[private] = private.stat()
    helper = _repo_file("scripts/lib/test_helpers.sh")
    shell = """set -euo pipefail
source "$1"
docker() {
    if [[ "$*" == *' ps --services' ]]; then printf 'api\\n';
    else printf 'fresh synthetic service log\\n'; fi
}
export_logs synthetic-project docker-compose.test.yml
"""
    with previous.open() as old_reader:
        subprocess.run(["bash", "-c", shell, "bash", str(helper)], env={
            **os.environ, "LOG_DIR": str(tmp_path),
        }, capture_output=True, text=True, check=True)
        assert old_reader.read() == "previous container-owned log\n"
    assert previous.read_text() == "fresh synthetic service log\n"
    assert stat.S_IMODE(previous.stat().st_mode) == 0o644
    for private, private_stat in private_stats.items():
        assert private.read_text() == "private fixture material"
        assert (private.stat().st_ino, private.stat().st_uid, private.stat().st_gid,
                private.stat().st_mode, private.stat().st_mtime_ns) == (
            private_stat.st_ino, private_stat.st_uid, private_stat.st_gid,
            private_stat.st_mode, private_stat.st_mtime_ns)
    assert not list(tmp_path.glob(".api.log.*"))


def _teardown_shell():
    script = _repo_file("test.sh").read_text()
    start = script.index("stack_down() {")
    end = script.index("\nstack_reset() {", start)
    return """set -euo pipefail
print_project() { :; }
export_logs() { printf 'capture\\n' >> "$CALLS"; return "$CAPTURE_STATUS"; }
docker() { printf '%s\\n' "$*" >> "$CALLS"; return "$TEARDOWN_STATUS"; }
""" + script[start:end]


@pytest.mark.parametrize("capture_status,teardown_status,expected", [(7, 0, 7), (0, 13, 13), (7, 13, 13)])
def test_stack_down_always_removes_stack_after_log_capture_failure(tmp_path, capture_status, teardown_status, expected):
    calls = tmp_path / "calls.txt"
    result = subprocess.run(["bash", "-c", _teardown_shell() + "\nstack_down\n"], env={
        **os.environ, "CALLS": str(calls), "CAPTURE_STATUS": str(capture_status),
        "TEARDOWN_STATUS": str(teardown_status), "COMPOSE_FILE": "docker-compose.test.yml",
        "COMPOSE_PROJECT_NAME": "synthetic-test-stack",
    }, capture_output=True, text=True, check=False)
    assert result.returncode == expected
    assert calls.read_text().splitlines() == [
        "capture", "compose -f docker-compose.test.yml --profile e2e --profile test --profile client down -v",
    ]
    if capture_status:
        assert "Service log capture failed" in result.stderr
    if teardown_status:
        assert "Test stack teardown failed" in result.stderr


@pytest.mark.parametrize("original_status,capture_status,teardown_status,expected", [
    (47, 7, 13, 47), (47, 0, 0, 47), (0, 7, 0, 7), (0, 0, 13, 13), (0, 0, 0, 0),
])
def test_actual_exit_trap_preserves_original_failure_and_reports_cleanup_failure(
    tmp_path, original_status, capture_status, teardown_status, expected,
):
    script = _repo_file("test.sh").read_text()
    traps = re.findall(r"^\s*(trap stack_down_on_exit EXIT)\s*$", script, re.M)
    assert len(traps) == 3, "Every task-owned rehearsal/CI/pre-pr stack must use the safe EXIT handler"
    assert "trap 'export_logs" not in script
    calls = tmp_path / "calls.txt"
    shell = _teardown_shell() + "\n" + traps[0] + '\nbash -c \'exit "$1"\' bash "$ORIGINAL_STATUS"\n'
    result = subprocess.run(["bash", "-c", shell], env={
        **os.environ, "CALLS": str(calls), "CAPTURE_STATUS": str(capture_status),
        "TEARDOWN_STATUS": str(teardown_status), "ORIGINAL_STATUS": str(original_status),
        "COMPOSE_FILE": "docker-compose.test.yml", "COMPOSE_PROJECT_NAME": "synthetic-test-stack",
    }, capture_output=True, text=True, check=False)
    assert result.returncode == expected
    observed = calls.read_text().splitlines()
    assert observed[0] == "capture" and len(observed) == 2
    assert observed[1].endswith("--profile client down -v")
    assert ("cleanup reported a failure" in result.stderr) == bool(capture_status or teardown_status)


# Public proof projection tests use synthetic XML only; no application imports.
def _proof_extractor_path():
    candidates = (
        Path("/app/scripts/extract_mtg_rbac_rehearsal_proof.py"),
        Path(__file__).resolve().parents[3] / "api/scripts/extract_mtg_rbac_rehearsal_proof.py",
    )
    return next(path for path in candidates if path.exists())


def _proof_extractor():
    path = _proof_extractor_path()
    spec = importlib.util.spec_from_file_location("mtg_rbac_public_proof", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _public_proof_records():
    rollback = {
        "head": "20260929_r2b_roles", "archive_retained": True, "rollback_window_open_before": True,
        "only_recorded_rows_removed": True, "r2b_rows_and_ids_unchanged": True,
        "unrelated_permission_rows_preserved": 50, "unrelated_boundary_ids_preserved": 8,
        "created_permission_rows_removed": 5, "created_boundary_rows_removed": 1,
    }
    records = {"mtg_rbac_bridge_only_rollback": rollback}
    for outcome in ("commit", "rollback"):
        records[f"mtg_rbac_maintenance_fence_{outcome}"] = {
            "outcome": outcome, "same_backend_and_transaction": True, "transaction_locks_released": True,
            "stages": [{"stage": stage, "locks": [{"table_name": table, "mode": "ShareRowExclusiveLock"}
                        for table in ("roles", "user_roles", "users")]}
                       for stage in ("preflight", "r2b", "bridge", "cutover")],
            "contenders": [{"table": table, "waiting_mode": "RowExclusiveLock", "blocked_by_migrator": True,
                            "write_attempts": 1, "cancel_sqlstate": "57014", "transaction_rolled_back": True,
                            "contender_session_closed": True,
                            "abort_method": "observer_cancel" if table == "roles" else "server_statement_deadline"}
                           for table in ("roles", "user_roles", "users")],
            "original_authority_preserved": True, "external_full_graph_release_fence_proven": False,
        }
    return records


def _proof_xml(tmp_path, records):
    from xml.etree import ElementTree as ET

    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", {"tests": "1", "errors": "0", "failures": "0"})
    properties = ET.SubElement(suite, "properties")
    for name, value in records:
        ET.SubElement(properties, "property", {"name": name, "value": value})
    ET.SubElement(suite, "system-out").text = "private SQL and credentials must never be copied"
    path = tmp_path / "results.xml"
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def test_public_junit_proof_round_trip_is_complete_but_not_a_release_verdict(tmp_path):
    expected = _public_proof_records()
    xml = _proof_xml(tmp_path, [(name, json.dumps(value)) for name, value in expected.items()])
    receipt = _proof_extractor().extract_proof(xml, source_sha="a" * 40, run_id=123, run_attempt=2,
                                               shard=3, lane="normal_e2e")
    assert receipt["status"] == "complete"
    assert receipt["proofs"] == expected
    assert receipt["missing_properties"] == receipt["invalid_properties"] == []
    assert receipt["provenance"] == {"source_sha": "a" * 40, "run_id": 123, "run_attempt": 2,
                                     "shard": 3, "lane": "normal_e2e"}
    assert receipt["evidence_scope"] == "synthetic_migration_tests_only"
    assert len(receipt["junit_sha256"]) == 64
    assert "private" not in json.dumps(receipt) and "credentials" not in json.dumps(receipt)
    assert all(not proof.get("external_full_graph_release_fence_proven", False)
               for proof in receipt["proofs"].values())


def test_public_junit_proof_ignores_unknown_properties_and_never_copies_them(tmp_path):
    helper = _proof_extractor()
    receipt = helper.extract_proof(_proof_xml(tmp_path, [("secret-property", "unrestricted private SQL credential")]))
    assert receipt["status"] == "not_present"
    assert receipt["missing_properties"] == list(helper.PROPERTIES)
    assert receipt["proofs"] == {}
    assert "secret-property" not in json.dumps(receipt)
    assert "private" not in json.dumps(receipt)


def test_public_junit_proof_reports_partial_or_missing_evidence(tmp_path):
    helper = _proof_extractor()
    expected = _public_proof_records()
    first = next(iter(expected))
    receipt = helper.extract_proof(_proof_xml(tmp_path, [(first, json.dumps(expected[first]))]))
    assert receipt["status"] == "incomplete" and set(receipt["proofs"]) == {first}
    assert receipt["missing_properties"] == list(helper.PROPERTIES[1:])
    missing = helper.extract_proof(tmp_path / "absent.xml")
    assert missing["status"] == "not_present" and missing["input_status"] == "missing"
    assert missing["missing_properties"] == list(helper.PROPERTIES)


@pytest.mark.parametrize("value", [True, "50", 50.0, -1, 1_000_001, None])
def test_public_junit_proof_counts_must_be_bounded_integers(tmp_path, value):
    records = _public_proof_records()
    name = "mtg_rbac_bridge_only_rollback"
    records[name]["unrelated_permission_rows_preserved"] = value
    receipt = _proof_extractor().extract_proof(_proof_xml(tmp_path, [(name, json.dumps(records[name]))]))
    assert receipt["status"] == "invalid"
    assert receipt["invalid_properties"] == [{"property": name, "category": "invalid_schema"}]
    assert receipt["proofs"] == {}


@pytest.mark.parametrize("mutation", ["unknown_field", "wrong_stage", "wrong_table", "wrong_mode", "wrong_outcome",
                                      "extra_attempt", "unclosed_session", "raw_abort", "claims_release_fence"])
def test_public_fence_proof_rejects_unreviewed_shapes_without_exposing_values(tmp_path, mutation):
    name = "mtg_rbac_maintenance_fence_commit"
    item = _public_proof_records()[name]
    if mutation == "unknown_field":
        item["private SQL"] = "sensitive customer credential"
    elif mutation == "wrong_stage":
        item["stages"][0]["stage"] = "sensitive customer credential"
    elif mutation == "wrong_table":
        item["contenders"][0]["table"] = "sensitive customer credential"
    elif mutation == "wrong_mode":
        item["stages"][0]["locks"][0]["mode"] = "AccessShareLock"
    elif mutation == "wrong_outcome":
        item["outcome"] = "rollback"
    elif mutation == "extra_attempt":
        item["contenders"][0]["write_attempts"] = 2
    elif mutation == "unclosed_session":
        item["contenders"][0]["contender_session_closed"] = False
    elif mutation == "raw_abort":
        item["contenders"][0]["abort_method"] = "sensitive customer credential"
    else:
        item["external_full_graph_release_fence_proven"] = True
    receipt = _proof_extractor().extract_proof(_proof_xml(tmp_path, [(name, json.dumps(item))]))
    assert receipt["status"] == "invalid" and receipt["proofs"] == {}
    assert "sensitive" not in json.dumps(receipt) and "private SQL" not in json.dumps(receipt)


@pytest.mark.parametrize("payload", ["private malformed JSON", "[]", "null", '"sensitive"',
                                    '{"head":"sensitive","head":"duplicate"}', '{"head":NaN}'])
def test_public_proof_rejects_malformed_json_and_duplicate_json_keys(tmp_path, payload):
    name = "mtg_rbac_bridge_only_rollback"
    receipt = _proof_extractor().extract_proof(_proof_xml(tmp_path, [(name, payload)]))
    assert receipt["status"] == "invalid" and receipt["proofs"] == {}
    assert "sensitive" not in json.dumps(receipt) and "private malformed" not in json.dumps(receipt)


def test_duplicate_known_xml_property_is_never_accepted_even_if_identical(tmp_path):
    name = "mtg_rbac_bridge_only_rollback"
    raw = json.dumps(_public_proof_records()[name])
    receipt = _proof_extractor().extract_proof(_proof_xml(tmp_path, [(name, raw), (name, raw)]))
    assert receipt["status"] == "invalid" and receipt["proofs"] == {}
    assert receipt["invalid_properties"] == [{"property": name, "category": "duplicate_property"}]


@pytest.mark.parametrize("argument,value", [("source_sha", "sensitive credential"), ("run_id", True),
                                            ("run_attempt", 0), ("shard", 1001), ("lane", "private SQL")])
def test_public_proof_provenance_is_bounded_without_echoing_invalid_inputs(tmp_path, argument, value):
    receipt = _proof_extractor().extract_proof(tmp_path / "absent.xml", **{argument: value})
    assert receipt["status"] == "invalid" and receipt["input_error"] == "invalid_provenance"
    assert receipt["provenance"] == {} and "sensitive" not in json.dumps(receipt)
    assert "private SQL" not in json.dumps(receipt)


def test_extractor_cli_writes_failure_receipt_and_returns_nonzero_without_raw_xml(tmp_path):
    source = tmp_path / "results.xml"
    source.write_text("private credential invalid XML")
    output = tmp_path / "public.json"
    result = subprocess.run([sys.executable, str(_proof_extractor_path()),
                             "--junit", str(source), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 1
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "invalid" and receipt["input_error"] == "invalid_xml"
    assert "private" not in result.stdout + result.stderr + output.read_text()
    assert "credential" not in result.stdout + result.stderr + output.read_text()


@pytest.mark.parametrize("also_valid_suite_property", [False, True])
def test_reserved_proof_names_in_testcase_metadata_cannot_bypass_validation(tmp_path, also_valid_suite_property):
    from xml.etree import ElementTree as ET

    name = "mtg_rbac_bridge_only_rollback"
    raw = json.dumps(_public_proof_records()[name])
    records = [(name, raw)] if also_valid_suite_property else []
    xml = _proof_xml(tmp_path, records)
    root = ET.parse(xml).getroot()
    suite = root.find("testsuite")
    assert suite is not None
    case = ET.SubElement(suite, "testcase")
    properties = ET.SubElement(case, "properties")
    ET.SubElement(properties, "property", {"name": name, "value": raw})
    ET.ElementTree(root).write(xml, encoding="utf-8")
    receipt = _proof_extractor().extract_proof(xml)
    assert receipt["status"] == "invalid" and receipt["proofs"] == {}
    category = "duplicate_property" if also_valid_suite_property else "invalid_property_location"
    assert receipt["invalid_properties"] == [{"property": name, "category": category}]


def test_complete_proof_with_one_invalid_record_is_not_partially_claimed_complete(tmp_path):
    records = _public_proof_records()
    name = "mtg_rbac_maintenance_fence_rollback"
    records[name]["original_authority_preserved"] = False
    receipt = _proof_extractor().extract_proof(_proof_xml(
        tmp_path, [(key, json.dumps(value)) for key, value in records.items()],
    ))
    assert receipt["status"] == "invalid"
    assert set(receipt["proofs"]) == set(records) - {name}
    assert receipt["missing_properties"] == [name]


@pytest.mark.parametrize("payload", [
    b'<?xml version="1.0" encoding="sensitive-customer-credential"?><testsuites/>',
    '<?xml version="1.0"?><!DOCTYPE testsuites [<!ENTITY secret "sensitive-customer-credential">]><testsuites/>'.encode("utf-16"),
    '<?xml version="1.0"?><!DOCTYPE testsuites [<!ENTITY secret "sensitive-customer-credential">]><testsuites/>'.encode("utf-16-le"),
    b'\xffsensitive-customer-credential',
])
def test_extractor_cli_rejects_unsupported_xml_encodings_without_raw_diagnostics(tmp_path, payload):
    source = tmp_path / "results.xml"
    source.write_bytes(payload)
    output = tmp_path / "public.json"
    result = subprocess.run([sys.executable, str(_proof_extractor_path()),
                             "--junit", str(source), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 1
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "invalid" and receipt["input_error"] == "invalid_xml"
    assert "sensitive" not in result.stdout + result.stderr + output.read_text()
    assert "credential" not in result.stdout + result.stderr + output.read_text()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_standard_utf8_junit_declaration_and_optional_bom_remain_supported(tmp_path, bom):
    records = _public_proof_records()
    xml = _proof_xml(tmp_path, [(name, json.dumps(value)) for name, value in records.items()])
    xml.write_bytes(bom + b'<?xml version="1.0" encoding="utf-8"?>' + xml.read_bytes())
    receipt = _proof_extractor().extract_proof(xml)
    assert receipt["status"] == "complete" and receipt["proofs"] == records
