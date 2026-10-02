"""Static contracts for MTG's serialized delivery lane and nightly coverage."""

from pathlib import Path
from typing import Any

import yaml


def _repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".github" / "workflows" / "ci.yml").is_file():
            return candidate
    raise RuntimeError("could not locate repository workflow sources")


REPO_ROOT = _repository_root()
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
NOOP_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci-noop.yml"
CODEQL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "codeql.yml"
NIGHTLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly.yml"


def _load_workflow(path: Path) -> dict[str, Any]:
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_docs_only_prs_use_required_context_stubs_until_merge_queue() -> None:
    ci = _load_workflow(CI_WORKFLOW)
    noop = _load_workflow(NOOP_WORKFLOW)

    ci_pull_request = ci["on"]["pull_request"]
    noop_pull_request = noop["on"]["pull_request"]
    assert ci_pull_request["paths-ignore"] == noop_pull_request["paths"]
    assert ".github/workflows/**" not in ci_pull_request["paths-ignore"]

    stub_contexts = {job["name"] for job in noop["jobs"].values()}
    assert {
        "Lint & Type Check",
        "Unit Tests",
        "E2E Tests",
        "Candidate Images",
    } <= stub_contexts

    # Docs-only PRs skip ci.yml, but merge_group must stay unconditional so
    # the exact queue candidate still receives real validation before main.
    assert "paths" not in ci["on"]["merge_group"]
    assert "paths-ignore" not in ci["on"]["merge_group"]


def test_ci_supports_native_merge_groups_during_queue_cutover() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert {"merge_group", "pull_request", "push", "workflow_dispatch"} <= triggers.keys()
    assert triggers["merge_group"]["types"] == ["checks_requested"]
    assert "build-dev-candidate" not in jobs
    assert "test-client-smoke" not in jobs
    assert "verify_merge_candidate.py" not in CI_WORKFLOW.read_text()

    codeql_triggers = _load_workflow(CODEQL_WORKFLOW)["on"]
    assert codeql_triggers["merge_group"]["types"] == ["checks_requested"]


def test_mtg_candidate_images_are_promoted_without_rebuild() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    candidates = jobs["build-candidate-images"]
    promotion = jobs["build-dev"]

    assert "github.event_name == 'pull_request'" in candidates["if"]
    assert "github.event_name == 'merge_group'" in candidates["if"]
    assert "MTG-Thomas/bifrost" in candidates["if"]
    assert "Midtown-Technology-Group/bifrost" in candidates["if"]
    assert promotion["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"

    checkout = next(
        step for step in candidates["steps"]
        if step["name"] == "Checkout exact pull-request head"
    )
    identity = next(
        step for step in candidates["steps"]
        if step["name"] == "Compute immutable candidate identity"
    )
    build = next(
        step for step in candidates["steps"]
        if step["name"] == "Build immutable candidate"
    )
    verify = next(
        step for step in candidates["steps"]
        if step["name"] == "Verify candidate identity and signature"
    )
    assert "github.event.merge_group.base_sha" in identity["env"]["BASE_SHA"]
    for value in (
        checkout["with"]["ref"],
        identity["env"]["HEAD_SHA"],
        verify["env"]["HEAD_SHA"],
    ):
        assert "github.sha" in value
    assert "github.sha" in build["with"]["labels"]

    source = "\n".join(step.get("run", "") for step in promotion["steps"])
    assert "candidate_image.py promote" in source
    assert "--tree-sha" in source
    assert "--main-source-sha" in source


def test_release_tags_pass_skipped_candidate_jobs_through_manifest_gate() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    verification = jobs["verify-release-manifest"]
    prerequisites = {"test-unit", "test-e2e-gate", "lint", "mcp-conformance"}

    assert set(verification["needs"]) == prerequisites
    verification_condition = " ".join(verification["if"].split())
    assert "always()" in verification_condition
    assert "startsWith(github.ref, 'refs/tags/v')" in verification_condition
    for prerequisite in prerequisites:
        assert f"needs.{prerequisite}.result == 'success'" in verification_condition

    for job_name in ("build-api", "build-client", "build-worker"):
        job = jobs[job_name]
        assert job["needs"] == ["verify-release-manifest"]
        condition = " ".join(job["if"].split())
        assert "always()" in condition
        assert "needs.verify-release-manifest.result == 'success'" in condition

    release = jobs["create-release"]
    assert set(release["needs"]) == {"build-api", "build-client", "build-worker"}
    release_condition = " ".join(release["if"].split())
    assert "always()" in release_condition
    for prerequisite in release["needs"]:
        assert f"needs.{prerequisite}.result == 'success'" in release_condition


def test_action_pin_versions_are_verified_in_ci() -> None:
    lint = _load_workflow(CI_WORKFLOW)["jobs"]["lint"]
    step = next(
        step for step in lint["steps"] if step.get("name") == "Check GitHub Action pins"
    )
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "--verify-versions" in step["run"]


def test_nightly_owns_full_browser_slow_coverage_and_clean_builds() -> None:
    workflow = _load_workflow(NIGHTLY_WORKFLOW)
    assert {"schedule", "workflow_dispatch"} <= workflow["on"].keys()
    assert set(workflow["jobs"]) == {
        "product-browser",
        "slow-unit-contracts",
        "backend-coverage",
        "client-unit",
        "clean-production-build",
    }

    expected_commands = {
        "product-browser": "./test.sh client nightly",
        "slow-unit-contracts": "-m slow",
        "backend-coverage": "--cov-report=xml:/tmp/bifrost/coverage.xml",
    }
    for job_name, expected in expected_commands.items():
        job = workflow["jobs"][job_name]
        run_step = next(
            step for step in job["steps"] if "./test.sh stack up" in step.get("run", "")
        )
        assert run_step["env"]["BIFROST_TEST_USE_CLEAN_BOOT"] == "1"
        assert expected in run_step["run"]

    assert any(
        step.get("run") == "./test.sh e2e"
        for step in workflow["jobs"]["backend-coverage"]["steps"]
    )
    assert any(
        step.get("run") == "npm test" and step.get("working-directory") == "client"
        for step in workflow["jobs"]["client-unit"]["steps"]
    )

    clean_builds = [
        step
        for step in workflow["jobs"]["clean-production-build"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(clean_builds) == 2
    assert all(step["with"]["no-cache"] == "true" for step in clean_builds)


def test_e2e_failures_export_service_logs_before_runner_cleanup() -> None:
    steps = _load_workflow(CI_WORKFLOW)["jobs"]["test-e2e"]["steps"]
    export = next(step for step in steps if step["name"] == "Export E2E service logs and stop stack")
    artifact = next(step for step in steps if step["name"] == "Preserve E2E failure diagnostics")
    assert export["if"] == "always() && matrix.total != 0"
    assert export["run"] == "./test.sh stack down"
    assert steps.index(export) < steps.index(artifact)
    assert artifact["if"] == "failure() && matrix.total != 0"
    assert "*.log" in artifact["with"]["path"]
    assert "test-results.xml" in artifact["with"]["path"]
