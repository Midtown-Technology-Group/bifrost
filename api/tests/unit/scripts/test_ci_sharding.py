"""CI shard contracts: complete coverage, isolation and fail-closed aggregation."""

import importlib.util
import json
import re
import subprocess
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


@pytest.mark.parametrize(
    "workflow",
    [
        "arm64-worker-compat",
        "doc-renderer",
        "dependabot-lockfile-regen",
    ],
)
def test_expensive_workflows_cancel_only_superseded_pr_snapshots(workflow):
    config = yaml.safe_load(_repo_file(f".github/workflows/{workflow}.yml").read_text())
    concurrency = config["concurrency"]
    assert (
        concurrency["cancel-in-progress"]
        == "${{ github.event_name == 'pull_request' }}"
    )
    assert "github.event_name" in concurrency["group"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.ref" in concurrency["group"]


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


class _GHObject(dict):
    """Dict with attribute access, mimicking GitHub expression objects."""

    def __getattr__(self, name):
        try:
            return _wrap(self[name])
        except KeyError as error:
            raise AttributeError(name) from error


def _wrap(value):
    if isinstance(value, dict):
        return _GHObject(value)
    if isinstance(value, list):
        return [_wrap(item) for item in value]
    return value


def _from_json(text):
    """GitHub's fromJSON: JSON value with expression-style object access."""
    return _wrap(json.loads(text))


def _admitted(
    expression,
    *,
    lint,
    publisher="success",
    event="pull_request",
    ref="refs/pull/1/merge",
    cancelled=False,
    same_repo=True,
    planner="success",
    shard_total=4,
):
    """Evaluate the small boolean admission language used by these CI jobs."""
    if shard_total:
        matrix = {
            "include": [
                {"shard": number, "total": shard_total}
                for number in range(1, shard_total + 1)
            ]
        }
    else:
        # The plan's zero-shard sentinel: lane is outside the affected closure.
        matrix = {"include": [{"shard": 0, "total": 0}]}
    matrix_json = json.dumps(matrix, separators=(",", ":"))
    values = {
        "needs.lint.result": lint,
        "needs.affected-test-plan.result": planner,
        "needs.publish-ci-test-images.result": publisher,
        "needs.affected-test-plan.outputs.api_e2e_matrix": matrix_json,
        "needs.affected-test-plan.outputs.client_e2e_matrix": matrix_json,
        "github.event_name": event,
        "github.ref": ref,
        "github.repository": "Midtown-Technology-Group/bifrost",
        "github.event.pull_request.head.repo.full_name": (
            "Midtown-Technology-Group/bifrost" if same_repo else "external/bifrost"
        ),
    }
    expression = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    for key, value in sorted(values.items(), key=lambda pair: -len(pair[0])):
        expression = expression.replace(key, repr(value))
    expression = expression.replace("cancelled()", repr(cancelled))
    expression = expression.replace("always()", "True")
    expression = expression.replace("startsWith(", "starts_with(")
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", "not ", expression)
    return eval(
        f"({expression})",
        {
            "__builtins__": {},
            "starts_with": str.startswith,
            "fromJSON": _from_json,
        },
        {},
    )


@pytest.mark.parametrize("planner", ["success", "failure", "cancelled", "skipped"])
def test_required_diagnostics_respect_cancellation_without_hiding_failed_plans(planner):
    jobs = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"]
    for name in ("lint", "test-client-unit"):
        args = {"lint": "failure", "planner": planner}
        assert _admitted(jobs[name]["if"], **args)
        assert not _admitted(jobs[name]["if"], **args, cancelled=True)


def test_required_aggregates_still_reject_incomplete_cancelled_candidates():
    jobs = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"]
    for name in ("candidate-images", "test-e2e-gate"):
        assert _admitted(jobs[name]["if"], lint="cancelled", cancelled=True)


@pytest.mark.parametrize("name", ["lint", "test-client-unit"])
def test_uncancelled_failed_plan_keeps_required_checks_red(name):
    job = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"][name]
    step = next(s for s in job["steps"] if s["name"] == "Require a valid affected test plan")
    assert step["if"] == "needs.affected-test-plan.result != 'success'"
    result = subprocess.run(
        ["bash", "-c", step["run"]], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1


@pytest.mark.parametrize("lint", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize(
    "event,ref",
    [
        ("workflow_dispatch", "refs/heads/codex/test"),
        ("workflow_dispatch", "refs/tags/v1.0.0"),
        ("pull_request", "refs/pull/1/merge"),
        ("merge_group", "refs/heads/gh-readonly-queue/main/pr-1"),
        ("push", "refs/heads/main"),
    ],
)
def test_manual_pre_pr_gate_requires_quality_and_an_uncancelled_branch_run(lint, event, ref):
    job = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"][
        "pre-pr-candidate"
    ]
    assert "lint" in job["needs"]
    args = {"lint": lint, "event": event, "ref": ref}
    expected = lint == "success" and event == "workflow_dispatch" and ref.startswith("refs/heads/")
    assert _admitted(job["if"], **args) == expected
    assert not _admitted(job["if"], **args, cancelled=True)


@pytest.mark.parametrize("lint", ["failure", "cancelled", "skipped"])
def test_failed_quality_never_admits_expensive_work(lint):
    jobs = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"]
    for name in (
        "build-candidate-images",
        "publish-ci-test-images",
        "test-unit",
        "mcp-conformance",
        "test-e2e",
        "test-client-e2e",
    ):
        assert "lint" in jobs[name]["needs"]
        assert not _admitted(jobs[name]["if"], lint=lint)


@pytest.mark.parametrize(
    "event,ref,same_repo,publisher",
    [
        ("pull_request", "refs/pull/1/merge", True, "success"),
        ("pull_request", "refs/pull/1/merge", False, "skipped"),
        ("merge_group", "refs/heads/gh-readonly-queue/main/pr-1", True, "success"),
        ("workflow_dispatch", "refs/heads/codex/ci-fail-fast", True, "success"),
        ("push", "refs/tags/v1.0.0", True, "skipped"),
    ],
)
def test_successful_quality_preserves_supported_test_events(
    event, ref, same_repo, publisher
):
    jobs = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"]
    for name in ("test-unit", "mcp-conformance", "test-e2e", "test-client-e2e"):
        args = {
            "lint": "success",
            "publisher": publisher,
            "event": event,
            "ref": ref,
            "same_repo": same_repo,
        }
        assert _admitted(jobs[name]["if"], **args)
        assert not _admitted(jobs[name]["if"], **args, cancelled=True)
        for failed_publisher in ("failure", "cancelled"):
            assert not _admitted(
                jobs[name]["if"], **{**args, "publisher": failed_publisher}
            )


@pytest.mark.parametrize("name", ["test-e2e", "test-client-e2e"])
def test_zero_shard_sentinel_lane_is_not_admitted(name):
    """A lane outside the affected closure must not allocate a shard runner.

    The plan emits {shard:0,total:0} for such lanes; admission must reject it
    so the job-level condition skips the job entirely instead of running every
    step skipped while still consuming a runner.
    """
    jobs = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"]
    args = {"lint": "success"}

    assert _admitted(jobs[name]["if"], **args)
    assert not _admitted(jobs[name]["if"], **args, shard_total=0)


def test_main_publication_does_not_depend_on_redundant_quality_rerun():
    jobs = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"]
    args = {"lint": "skipped", "event": "push", "ref": "refs/heads/main"}
    assert _admitted(jobs["publish-ci-test-images"]["if"], **args)
    for name in ("test-unit", "mcp-conformance", "test-e2e", "test-client-e2e"):
        assert not _admitted(jobs[name]["if"], **args)


@pytest.mark.parametrize("lint", ["failure", "cancelled", "skipped"])
def test_required_e2e_gate_fails_when_quality_prevents_test_admission(lint):
    gate = yaml.safe_load(_repo_file(".github/workflows/ci.yml").read_text())["jobs"][
        "test-e2e-gate"
    ]
    assert "lint" in gate["needs"]
    script = gate["steps"][0]["run"]
    # Even otherwise-successful lanes cannot disguise failed quality.
    script = re.sub(
        r"\$\{\{ needs\.([\w-]+)\.result \}\}",
        lambda match: lint if match[1] == "lint" else "success",
        script,
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "expensive test lanes were not admitted" in result.stdout
