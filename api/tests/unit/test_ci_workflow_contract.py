"""Static contracts for MTG's serialized delivery lane and nightly coverage."""

import subprocess
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
CODEQL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "codeql.yml"
NIGHTLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly.yml"


def _load_workflow(path: Path) -> dict[str, Any]:
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_ci_supports_native_merge_groups_during_queue_cutover() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert {"merge_group", "pull_request", "push", "workflow_dispatch"} <= triggers.keys()
    assert triggers["merge_group"]["types"] == ["checks_requested"]
    assert triggers["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]
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
    assert any(
        "e2e/support/acceptance-report.test.mjs" in step.get("run", "")
        and "e2e/support/playwright-projects.test.mjs" in step.get("run", "")
        for step in workflow["jobs"]["client-unit"]["steps"]
    )

    clean_builds = [
        step
        for step in workflow["jobs"]["clean-production-build"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(clean_builds) == 2
    assert all(step["with"]["no-cache"] == "true" for step in clean_builds)


# Caller jobs that `uses:` a reusable workflow cannot set timeout-minutes.
# The bound lives on the called jobs instead.
REUSABLE_WORKFLOW_JOBS = ("sonar-readonly-diagnostic", "sonar-coverage")
CI_JOB_TIMEOUTS = {
    "pre-pr-candidate": "90",
    "affected-test-plan": "10",
    "build-candidate-images": "30",
    "verify-dependabot-candidates": "70",
    "candidate-images": "10",
    "publish-ci-test-images": "15",
    "lint": "15",
    "test-client-unit": "15",
    "test-unit": "25",
    "mcp-conformance": "25",
    "test-e2e": "30",
    "test-client-e2e": "30",
    "test-e2e-gate": "10",
    "build-dev": "30",
    "deploy-dry-run": "15",
    "deploy-dev": "20",
    "verify-release-manifest": "15",
    "build-api": "20",
    "build-client": "20",
    "build-worker": "30",
    "create-release": "20",
}


def test_dependabot_requires_trusted_publication_and_exact_signatures() -> None:
    """The local test-image fallback must never waive signed candidates."""
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    for publisher in ("build-candidate-images", "publish-ci-test-images"):
        assert "github.event.pull_request.user.login != 'dependabot[bot]'" in jobs[publisher]["if"]
        assert "github.event_name == 'merge_group'" in jobs[publisher]["if"]
    verifier = jobs["verify-dependabot-candidates"]
    assert verifier["permissions"] == {"contents": "read", "actions": "read", "packages": "read"}
    assert "candidate_image.py verify" in verifier["steps"][-1]["run"]
    assert "--source-sha" in verifier["steps"][-1]["run"]
    assert '--expected-digest "$digest"' in verifier["steps"][-1]["run"]
    assert verifier["steps"][-1]["env"]["API_DIGEST"] == "${{ steps.digests.outputs.api_digest }}"
    gate = jobs["candidate-images"]
    assert set(gate["needs"]) == {"build-candidate-images", "verify-dependabot-candidates"}
    step = gate["steps"][0]
    for is_bot in ("true", "false"):
        for build in ("success", "failure", "skipped", "cancelled"):
            for published in ("success", "failure", "skipped", "cancelled"):
                result = subprocess.run(
                    ["bash", "-c", step["run"]],
                    env={"IS_DEPENDABOT": is_bot, "BUILD_RESULT": build, "DEPENDABOT_RESULT": published},
                    capture_output=True,
                    check=False,
                )
                expected = published if is_bot == "true" else build
                assert (result.returncode == 0) == (expected == "success")


def test_dispatch_cannot_cancel_a_pull_request_producer() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    concurrency = workflow["concurrency"]
    group = concurrency["group"]
    assert "ci-branch-" not in group
    assert "github.event_name == 'pull_request' && format('ci-pull_request-{0}-{1}'" in group
    assert "github.event.pull_request.head.repo.full_name" in group
    assert "github.head_ref" in group
    assert (
        "github.event_name == 'workflow_dispatch' && "
        "format('ci-dispatch-{0}-{1}-{2}-{3}-{4}'"
    ) in group
    assert "inputs.inspect_sonar" in group
    assert "inputs.reviewed_workflow_sha" in group
    assert "github.sha" in group
    assert "format('ci-{0}-{1}', github.event_name, github.ref)" in group
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' || github.event_name == 'workflow_dispatch' }}"
    )
    deploy = workflow["jobs"]["deploy-dev"]["concurrency"]
    assert deploy["group"] == "deploy-dev"
    assert deploy["cancel-in-progress"] == "true"


def test_sonar_is_outside_the_merge_queue_and_draft_prs() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    for name, job in jobs.items():
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        assert "sonar-coverage" not in needs, name
    sonar_if = " ".join(jobs["sonar-coverage"]["if"].split())
    assert "github.event_name != 'merge_group'" in sonar_if
    assert "github.event.pull_request.draft == false" in sonar_if
    assert "refs/heads/main" in sonar_if
    assert "sonar-coverage" not in jobs["test-e2e-gate"]["needs"]


def test_every_ci_job_has_a_bounded_timeout() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    assert set(jobs) == set(CI_JOB_TIMEOUTS) | set(REUSABLE_WORKFLOW_JOBS)
    for name, minutes in CI_JOB_TIMEOUTS.items():
        assert jobs[name]["timeout-minutes"] == minutes
        assert "uses" not in jobs[name]
    for name in REUSABLE_WORKFLOW_JOBS:
        assert "uses" in jobs[name]
        assert "timeout-minutes" not in jobs[name]
    sonar_jobs = _load_workflow(REPO_ROOT / ".github/workflows/sonar-coverage.yml")["jobs"]
    assert sonar_jobs["evidence"]["timeout-minutes"] == "75"
    assert sonar_jobs["scan"]["timeout-minutes"] == "20"
    assert sonar_jobs["free-plan-disposition"]["timeout-minutes"] == "10"
    diagnostic = _load_workflow(REPO_ROOT / ".github/workflows/sonar-readonly-diagnostic.yml")
    assert diagnostic["jobs"]["inspect"]["timeout-minutes"] == "5"


def _e2e_gate_exit(**results: str) -> int:
    """Run the required E2E gate script with concrete job results.

    GitHub expressions are substituted in full. A leftover ``${{`` means the
    gate gained a condition this regression does not cover.
    """
    script = _load_workflow(CI_WORKFLOW)["jobs"]["test-e2e-gate"]["steps"][0]["run"]
    replacements = {
        "${{ needs.affected-test-plan.outputs.client_e2e_mode }}": results.get(
            "client_e2e_mode", "comprehensive"
        ),
        "${{ needs.affected-test-plan.outputs.api_e2e_mode }}": results.get(
            "api_e2e_mode", "comprehensive"
        ),
        "${{ needs.publish-ci-test-images.result }}": results.get(
            "publish-ci-test-images", "success"
        ),
        "${{ needs.affected-test-plan.result }}": results.get(
            "affected-test-plan", "success"
        ),
        "${{ needs.test-client-unit.result }}": results.get("test-client-unit", "success"),
        "${{ needs.test-client-e2e.result }}": results.get("test-client-e2e", "success"),
        "${{ needs.mcp-conformance.result }}": results.get("mcp-conformance", "success"),
        "${{ needs.test-e2e.result }}": results.get("test-e2e", "success"),
        "${{ needs.lint.result }}": results.get("lint", "success"),
    }
    rendered = script
    for expression, value in replacements.items():
        assert value.replace("_", "").isalnum()
        rendered = rendered.replace(expression, value)
    assert "${{" not in rendered
    completed = subprocess.run(
        ["bash", "-c", rendered],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode


def test_fail_once_then_pass_still_fails_the_required_e2e_gate() -> None:
    """A browser job that is red keeps the required E2E Tests check red.

    The wrapper turns a fail-once/pass-on-retry into a nonzero process exit,
    including tests that are not in the acceptance ledger. This gate fails
    when that job result is anything other than success, except an explicit
    affected-plan skip.
    """
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    vitest = next(step for step in jobs["test-client-unit"]["steps"] if step["name"] == "Vitest")
    assert "e2e/support/acceptance-report.test.mjs" in vitest["run"]
    assert "e2e/support/playwright-projects.test.mjs" in vitest["run"]
    gate_script = jobs["test-e2e-gate"]["steps"][0]["run"]
    assert 'needs.test-client-e2e.result' in gate_script
    assert '!= "success"' in gate_script

    assert _e2e_gate_exit() == 0
    assert _e2e_gate_exit(**{"test-client-e2e": "failure"}) == 1
    assert _e2e_gate_exit(**{"test-client-e2e": "cancelled"}) == 1
    assert (
        _e2e_gate_exit(**{"test-client-e2e": "skipped", "client_e2e_mode": "skip"})
        == 0
    )
    assert (
        _e2e_gate_exit(
            **{"test-client-e2e": "skipped", "client_e2e_mode": "comprehensive"}
        )
        == 1
    )


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
