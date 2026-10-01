"""CI shard contracts: complete coverage, isolation and fail-closed aggregation."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from scripts import plan_affected_tests as affected


def _repo_file(name):
    for root in (Path("/repo"), Path("/app"), Path(__file__).resolve().parents[4]):
        path = root / name
        if path.exists():
            return path
    raise FileNotFoundError(name)


def _allocator():
    spec = importlib.util.spec_from_file_location(
        "e2e_allocator", _repo_file("scripts/e2e_shard.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("workflow", [
    "codspeed", "arm64-worker-compat", "snyk", "doc-renderer",
    "dependabot-lockfile-regen",
])
def test_expensive_workflows_cancel_only_superseded_pr_snapshots(workflow):
    config = yaml.safe_load(_repo_file(f".github/workflows/{workflow}.yml").read_text())
    concurrency = config["concurrency"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.event_name" in concurrency["group"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.ref" in concurrency["group"]
    if workflow == "codspeed":
        assert all(job["timeout-minutes"] == 20 for job in config["jobs"].values())


def test_backend_shards_cover_each_file_once_and_balance_known_and_new_files(
    monkeypatch,
):
    allocator = _allocator()
    monkeypatch.setattr(allocator, "WEIGHTS", {"a": 100, "b": 80, "c": 20})
    monkeypatch.setattr(allocator, "DEFAULT_WEIGHT", 10)
    files = ["new-1", "c", "b", "a", "new-2"]
    shards = allocator.split(files, 2)
    assert sorted(item for shard in shards for item in shard) == sorted(files)
    loads = [sum(allocator.WEIGHTS.get(item, 10) for item in shard) for shard in shards]
    assert loads == [110, 110]
    assert allocator.split(list(reversed(files)), 2) == shards
    with pytest.raises(ValueError, match="positive"):
        allocator.split(files, 0)
    with pytest.raises(ValueError, match="unique"):
        allocator.split(["a", "a"], 2)


def test_measured_inventory_covers_every_backend_file_exactly_once(monkeypatch):
    allocator = _allocator()
    api_root = (
        Path("/app")
        if Path("/app/tests/e2e").exists()
        else Path(__file__).resolve().parents[3]
    )
    monkeypatch.setattr(allocator, "API_ROOT", api_root)
    monkeypatch.setattr(allocator, "TESTS_ROOT", api_root / "tests/e2e")
    files = allocator.collect_test_files()
    assert files
    assert set(allocator.WEIGHTS) <= set(files)
    assert all(weight > 0 for weight in allocator.WEIGHTS.values())
    shards = allocator.split(files, 4)
    assert sorted(file for shard in shards for file in shard) == sorted(files)
    loads = [
        sum(allocator.WEIGHTS.get(file, allocator.DEFAULT_WEIGHT) for file in shard)
        for shard in shards
    ]
    assert max(loads) - min(loads) <= max(allocator.WEIGHTS.values())


@pytest.mark.parametrize(
    "mode,total", [("comprehensive", 2), ("affected", 1), ("skip", 0)]
)
def test_browser_matrix_preserves_focused_and_skip_lanes(tmp_path, mode, total):
    plan = affected.AffectedPlan(
        "affected", "test", (), lane_overrides={"client_e2e": mode}
    )
    output = tmp_path / "output"
    affected.write_github_output(output, plan)
    line = next(
        line
        for line in output.read_text().splitlines()
        if line.startswith("client_e2e_matrix=")
    )
    matrix = json.loads(line.split("=", 1)[1])["include"]
    assert matrix == (
        [{"shard": 0, "total": 0}]
        if not total
        else [{"shard": shard, "total": total} for shard in range(1, total + 1)]
    )


def test_browser_jobs_keep_isolated_stacks_diagnostics_and_required_aggregate():
    workflow_path = _repo_file(".github/workflows/ci.yml")
    jobs = yaml.safe_load(workflow_path.read_text())["jobs"]
    browser = jobs["test-client-e2e"]
    assert browser["strategy"]["fail-fast"] is False
    assert "client_e2e_matrix" in browser["strategy"]["matrix"]
    run = next(
        step["run"]
        for step in browser["steps"]
        if step["name"] == "Run Playwright tests"
    )
    assert "./test.sh stack up" in run
    assert '--shard="$SHARD_ID/$SHARD_TOTAL"' in run
    for project in ("platform-admin", "org-user", "unauthenticated", "chromium"):
        assert f"--project={project}" in run
    uploads = [
        step for step in browser["steps"] if "upload-artifact@" in step.get("uses", "")
    ]
    assert all("matrix.shard" in step["with"]["name"] for step in uploads)
    assert "always()" in uploads[0]["if"]
    gate = jobs["test-e2e-gate"]
    assert "test-client-e2e" in gate["needs"]
    assert "needs.test-client-e2e.result" in gate["steps"][0]["run"]
