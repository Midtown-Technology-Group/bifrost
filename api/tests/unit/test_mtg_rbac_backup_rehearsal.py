"""Safety and independent restore-parity checks for the synthetic backup lane."""

from __future__ import annotations

import importlib.util
import os
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
