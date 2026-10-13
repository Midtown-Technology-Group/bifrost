"""Producer evidence attributes shards without exporting process payloads."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

API_ROOT = Path(__file__).resolve().parents[2]


def _module():
    spec = importlib.util.spec_from_file_location(
        "sonar_producers_under_test", API_ROOT / "scripts/capture_sonar_producers.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("state", ["running", "exited"])
def test_producer_identity_and_exit_state_exclude_payloads(monkeypatch, state):
    module = _module()
    calls = []

    def docker(arguments):
        calls.append(arguments)
        if arguments[1] == "compose":
            return SimpleNamespace(returncode=0, stdout="\n".join(module.SERVICES))
        if arguments[1] == "inspect":
            service = arguments[-1]
            data = {key: 0 for key in module.FIELDS}
            data.update(service=service, hostname=f"host-{service}", container_id=service,
                        state=state, exit_code=137, started_at="start", finished_at="finish",
                        environment="sensitive environment", command="sensitive arguments")
            return SimpleNamespace(returncode=0, stdout=json.dumps(data))
        assert arguments[1] == "exec"
        process = {"pid": 19, "ppid": 1, "state": "S", "start_ticks": 120,
                   "cmdline": "sensitive arguments", "environ": "sensitive environment"}
        return SimpleNamespace(returncode=0, stdout=json.dumps([process]))

    monkeypatch.setattr(module, "_run", docker)
    evidence = module.snapshot("compose.yml", "before-stop")
    assert {item["service"] for item in evidence["containers"]} == set(module.SERVICES)
    assert "sensitive" not in json.dumps(evidence)
    assert ".Config.Env" not in module.INSPECT_FORMAT
    assert ".Config.Cmd" not in module.INSPECT_FORMAT
    assert "cmdline" not in module.PROCESS_QUERY
    assert "environ" not in module.PROCESS_QUERY
    for item in evidence["containers"]:
        assert item["hostname"] == f"host-{item['service']}"
        assert item["exit_code"] == 137
        assert item["finished_at"] == "finish"
        assert item["processes"] == (
            [{"pid": 19, "ppid": 1, "state": "S", "start_ticks": 120}]
            if state == "running" else []
        )
    assert len([args for args in calls if args[1] == "exec"]) == (
        len(module.SERVICES) if state == "running" else 0
    )


def test_incomplete_producer_inventory_is_not_reported_as_complete(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "_run", lambda _: SimpleNamespace(returncode=0, stdout=""))
    with pytest.raises(ValueError, match="Incomplete"):
        module.snapshot("compose.yml", "started")


def test_producer_artifact_is_private_and_cannot_overwrite_existing_evidence(tmp_path, monkeypatch):
    module = _module()
    evidence = {"schema": 1, "containers": []}
    monkeypatch.setattr(module, "snapshot", lambda *_: evidence)
    monkeypatch.setattr(sys, "argv", ["capture", "--compose-file", "compose.yml",
                                    "--output-dir", str(tmp_path), "--phase", "after-stop"])
    assert module.main() == 0
    artifact = tmp_path / "sonar-runtime-producers-after-stop.json"
    assert json.loads(artifact.read_text()) == evidence
    assert artifact.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        module.main()
    assert json.loads(artifact.read_text()) == evidence


def test_workflow_captures_producers_before_teardown_and_retains_them():
    workflow_path = next(
        root / ".github/workflows/sonar-coverage.yml"
        for root in (API_ROOT, *API_ROOT.parents)
        if (root / ".github/workflows/sonar-coverage.yml").is_file()
    )
    workflow = workflow_path.read_text()
    assert workflow.index("--phase started") < workflow.index('./test.sh unit -m ""')
    assert workflow.index("--phase before-stop") < workflow.index(' stop api api-replica worker')
    assert workflow.index(' stop api api-replica worker') < workflow.index("--phase after-stop")
    assert workflow.index("--phase after-stop") < workflow.index("python /app/scripts/combine_sonar_coverage.py")
    assert workflow.index("python /app/scripts/combine_sonar_coverage.py") < workflow.index(
        "      - name: Tear down task-owned stack"
    )
    copies = workflow[workflow.index("          files=(sonar-runtime-coverage.json"):]
    for phase in ("started", "before-stop", "after-stop"):
        assert f"sonar-runtime-producers-{phase}.json" in copies
