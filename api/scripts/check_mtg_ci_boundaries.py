#!/usr/bin/env python3
"""Guard MTG fork CI ownership boundaries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


EXPECTED_DEPLOY_DEV_IF = (
    "github.repository == 'gobifrost/bifrost' "
    "&& github.event_name == 'push' "
    "&& github.ref == 'refs/heads/main'"
)
EXPECTED_DEPLOY_DRY_RUN_IF = (
    "github.repository == 'gobifrost/bifrost' "
    "&& github.event_name == 'workflow_dispatch'"
)
REQUIRED_CI_JOB_NAMES = {
    "candidate-images": "Candidate Images",
    "lint": "Lint & Type Check",
    "test-client-unit": "Client Unit Tests",
    "test-unit": "Unit Tests",
    "mcp-conformance": "MCP Conformance",
    "test-e2e-gate": "E2E Tests",
}
QUEUE_SKIPPED_ON_MAIN = (
    "lint",
    "test-client-unit",
    "test-unit",
    "mcp-conformance",
    "test-e2e",
    "test-client-e2e",
    "test-e2e-gate",
)
ACTION_FREE_REQUIRED_TEST_JOBS = (
    "test-unit",
    "mcp-conformance",
    "test-e2e",
    "test-client-e2e",
)
ALLOWED_REQUIRED_TEST_ACTIONS = (
    "actions/checkout@",
    "actions/upload-artifact@",
    "./",
)
EXPENSIVE_TEST_IF = (
    "${{ !cancelled() && needs.lint.result == 'success' "
    "&& (needs.publish-ci-test-images.result == 'success' "
    "|| needs.publish-ci-test-images.result == 'skipped') "
    "&& (github.event_name != 'push' || github.ref != 'refs/heads/main') }}"
)

# Lanes whose runner count comes from a plan-produced shard matrix. They carry
# the same admission guard as EXPENSIVE_TEST_IF plus the zero-shard sentinel
# guard: when the plan marks the lane out of scope it emits
# {"shard":0,"total":0}, and the shard job must skip the whole job rather than
# allocate a runner to a run where every step is skipped (the org caps
# concurrent runners at 20). The push-guard suffix stays last, unchanged.
MATRIX_LANE_TEST_IF_OUTPUT = {
    "test-e2e": "api_e2e_matrix",
    "test-client-e2e": "client_e2e_matrix",
}
ZERO_SHARD_SENTINEL = "'{\"include\":[{\"shard\":0,\"total\":0}]}'"


def matrix_lane_test_if(matrix_output: str) -> str:
    """Admission guard for a sharded expensive lane (sentinel-aware)."""
    return (
        "${{ !cancelled() && needs.lint.result == 'success' "
        "&& (needs.publish-ci-test-images.result == 'success' "
        "|| needs.publish-ci-test-images.result == 'skipped') "
        f"&& fromJSON(needs.affected-test-plan.outputs.{matrix_output} || "
        f"{ZERO_SHARD_SENTINEL}).include[0].total != 0 "
        "&& (github.event_name != 'push' || github.ref != 'refs/heads/main') }}"
    )
EXPECTED_PR_CANCELLATION = (
    "cancel-in-progress: ${{ github.event_name == 'pull_request' "
    "|| github.event_name == 'workflow_dispatch' }}"
)

ALLOWED_CODEOWNERS = (
    '@MTG-Thomas',
    '@Midtown-Technology-Group/',
)
CI_IMAGE_CONSUMER_JOBS = (
    "test-unit",
    "mcp-conformance",
    "test-e2e",
    "test-client-e2e",
)
CACHE_PULL_STEP = "Pull CI image caches before checkout"
CI_IMAGE_PUBLISH_JOB = "publish-ci-test-images"
CI_IMAGE_BUILD_STEPS = (
    "Build API CI test image without registry credentials",
    "Build client CI test image without registry credentials",
    "Build Playwright CI test image without registry credentials",
)
CI_IMAGE_PUBLISH_STEP = "Publish fixed CI test image references"
DOCKER_LOGIN_COMMAND = "docker login"
DOCKER_LOGOUT_COMMAND = "docker logout"


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    root = path.parents[2]
    if root == root.parent:
        # The API test container mounts api/ at /app, so api/scripts becomes
        # /app/scripts and parents[2] is the filesystem root.
        return path.parents[1]
    return root


def _resolve_workflow_path(path: Path) -> Path:
    root = _repo_root()
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{path}: workflow path must stay within {root}")
    return resolved


def _job_block(lines: list[str], job_name: str) -> tuple[int, list[str]] | None:
    marker = f"  {job_name}:"
    for index, line in enumerate(lines):
        if line == marker:
            block: list[str] = []
            for block_line in lines[index + 1 :]:
                if block_line.startswith("  ") and not block_line.startswith("    "):
                    break
                block.append(block_line)
            return index + 1, block
    return None


def _parse_if_line(block: list[str]) -> str | None:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("if:"):
            return stripped.removeprefix("if:").strip()
    return None


def _parse_name_line(block: list[str]) -> str | None:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.removeprefix("name:").strip().strip("\"'")
    return None


def _named_step(block: list[str], name: str) -> tuple[int, list[str]] | None:
    marker = f"- name: {name}"
    for index, line in enumerate(block):
        if line.strip() != marker:
            continue
        step = [line]
        for step_line in block[index + 1 :]:
            if step_line.strip().startswith("- name:"):
                break
            step.append(step_line)
        return index, step
    return None


def _check_image_consumer_job(
    *,
    workflow_path: Path,
    job_name: str,
    block: list[str],
    expected_token: str,
    checkout_name: str,
) -> list[str]:
    location = f"{workflow_path}:{job_name}"
    pull = _named_step(block, CACHE_PULL_STEP)
    checkout = _named_step(block, checkout_name)
    prepare_name = (
        "Prepare exact-source test images"
        if job_name == "evidence"
        else "Prepare CI test images"
    )
    prepare = _named_step(block, prepare_name)
    failures: list[str] = []
    if pull is None or checkout is None or prepare is None:
        return [
            f"{location}: image consumers require a trusted cache pull, checkout, "
            "and credential-free image preparation step."
        ]

    pull_index, pull_step = pull
    checkout_index, checkout_step = checkout
    prepare_index, prepare_step = prepare
    if not pull_index < checkout_index < prepare_index:
        failures.append(
            f"{location}: registry authentication must finish before checkout, and "
            "image preparation must run after checkout."
        )

    if not any(line.strip() == "persist-credentials: false" for line in checkout_step):
        failures.append(f"{location}: checkout must set persist-credentials: false.")

    if not any(
        line.strip() == f"GHCR_TOKEN: {expected_token}" for line in pull_step
    ):
        failures.append(
            f"{location}: the pre-checkout cache pull must receive the read-only GitHub token."
        )
    required_commands = (DOCKER_LOGIN_COMMAND, "docker pull", DOCKER_LOGOUT_COMMAND)
    pull_text = "\n".join(pull_step)
    if any(command not in pull_text for command in required_commands):
        failures.append(
            f"{location}: the pre-checkout cache pull must login, pull, and logout inline."
        )
    elif not (
        pull_text.index(DOCKER_LOGOUT_COMMAND)
        < pull_text.index(DOCKER_LOGIN_COMMAND)
        < pull_text.index("docker pull")
    ):
        failures.append(
            f"{location}: logout cleanup must be armed before registry login and pulls."
        )
    if "bash api/scripts/ci/prepare-test-images.sh" not in "\n".join(prepare_step):
        failures.append(
            f"{location}: image preparation must use the credential-free repository helper."
        )

    for offset, line in enumerate(block):
        if (
            "GHCR_TOKEN" in line
            or "GITHUB_TOKEN" in line
            or "github.token" in line
        ) and not (pull_index <= offset < checkout_index):
            failures.append(
                f"{location}: line {offset + 1} exposes the GitHub token outside "
                "the pre-checkout cache pull."
            )
    return failures


def _check_untrusted_image_builds(
    *, location: str, builds: list[tuple[int, list[str]]]
) -> list[str]:
    """Ensure PR-controlled image builds cannot publish or receive credentials."""
    failures: list[str] = []
    for name, (_, build_step) in zip(CI_IMAGE_BUILD_STEPS, builds, strict=True):
        build_text = "\n".join(build_step)
        if "load: true" not in build_text or "push: false" not in build_text:
            failures.append(
                f"{location}:{name}: untrusted image builds must load locally and must not push."
            )
        if "GHCR_TOKEN" in build_text or "GITHUB_TOKEN" in build_text:
            failures.append(
                f"{location}:{name}: package credentials must not reach untrusted image builds."
            )
    return failures


def _check_fixed_reference_publish_step(
    *, location: str, publish_step: list[str]
) -> list[str]:
    """Validate the sole package-authenticated fixed-reference publish step."""
    failures: list[str] = []
    publish_text = "\n".join(publish_step)
    if not any(
        line.strip()
        == "GHCR_TOKEN: ${{ secrets.GHCR_TOKEN || secrets.GITHUB_TOKEN }}"
        for line in publish_step
    ):
        failures.append(
            f"{location}: only the final fixed-reference publish step may receive the package token."
        )

    required_commands = (DOCKER_LOGOUT_COMMAND, DOCKER_LOGIN_COMMAND, "docker push")
    if any(command not in publish_text for command in required_commands):
        failures.append(
            f"{location}: the fixed-reference publish step must login, push, and logout inline."
        )
    elif not (
        publish_text.index(DOCKER_LOGOUT_COMMAND)
        < publish_text.index(DOCKER_LOGIN_COMMAND)
        < publish_text.index("docker push")
    ):
        failures.append(
            f"{location}: logout cleanup must be armed before registry login and pushes."
        )

    required_fixed_refs = (
        '[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]',
        'for image in "$CI_API_TEST_IMAGE" "$CI_CLIENT_TEST_IMAGE" "$CI_PLAYWRIGHT_TEST_IMAGE"',
        'remote_ref="$REGISTRY/$image:sha-$GITHUB_SHA"',
    )
    if any(required not in publish_text for required in required_fixed_refs):
        failures.append(
            f"{location}: the publish step must validate and use fixed exact-SHA image refs."
        )
    return failures


def _check_image_publisher_job(
    *, workflow_path: Path, block: list[str]
) -> list[str]:
    """Keep package-write credentials out of PR-controlled image builds."""
    location = f"{workflow_path}:{CI_IMAGE_PUBLISH_JOB}"
    checkout = _named_step(block, "Checkout repository")
    builds = [_named_step(block, name) for name in CI_IMAGE_BUILD_STEPS]
    publish = _named_step(block, CI_IMAGE_PUBLISH_STEP)
    if checkout is None or any(build is None for build in builds) or publish is None:
        return [
            f"{location}: image publication requires credential-free checkout and "
            "build steps followed by one fixed-reference publish step."
        ]

    checkout_index, checkout_step = checkout
    typed_builds = [build for build in builds if build is not None]
    publish_index, publish_step = publish
    failures: list[str] = []
    if not (
        checkout_index
        < typed_builds[0][0]
        < typed_builds[1][0]
        < typed_builds[2][0]
        < publish_index
    ):
        failures.append(
            f"{location}: checkout and every untrusted image build must finish before "
            "registry authentication and publication."
        )

    if not any(line.strip() == "persist-credentials: false" for line in checkout_step):
        failures.append(f"{location}: checkout must set persist-credentials: false.")

    failures.extend(
        _check_untrusted_image_builds(location=location, builds=typed_builds)
    )
    failures.extend(
        _check_fixed_reference_publish_step(
            location=location,
            publish_step=publish_step,
        )
    )

    if any(line.startswith("      - ") for line in block[publish_index + 1 :]):
        failures.append(
            f"{location}: no step may run after the package-authenticated publish step."
        )
    for offset, line in enumerate(block):
        if (
            "GHCR_TOKEN" in line
            or "secrets.GITHUB_TOKEN" in line
            or "github.token" in line
        ) and offset < publish_index:
            failures.append(
                f"{location}: line {offset + 1} exposes a package credential before "
                "the fixed-reference publish step."
            )
    return failures


def check_ci_image_token_boundaries(ci_path: Path, sonar_path: Path) -> list[str]:
    """Keep package credentials out of checked-out pull-request code."""
    failures: list[str] = []
    for workflow_path, job_names, expected_token, checkout_name in (
        (
            ci_path,
            CI_IMAGE_CONSUMER_JOBS,
            "${{ secrets.GITHUB_TOKEN }}",
            "Checkout repository",
        ),
        (
            sonar_path,
            ("evidence",),
            "${{ github.token }}",
            "Checkout exact analysis source",
        ),
    ):
        if not workflow_path.exists():
            failures.append(f"{workflow_path}: workflow file does not exist")
            continue
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        for job_name in job_names:
            parsed = _job_block(lines, job_name)
            if parsed is None:
                failures.append(
                    f"{workflow_path}:{job_name}: required image consumer job is missing"
                )
                continue
            failures.extend(
                _check_image_consumer_job(
                    workflow_path=workflow_path,
                    job_name=job_name,
                    block=parsed[1],
                    expected_token=expected_token,
                    checkout_name=checkout_name,
                )
            )
    if ci_path.exists():
        publisher = _job_block(
            ci_path.read_text(encoding="utf-8").splitlines(),
            CI_IMAGE_PUBLISH_JOB,
        )
        if publisher is None:
            failures.append(
                f"{ci_path}:{CI_IMAGE_PUBLISH_JOB}: required image publisher job is missing"
            )
        else:
            failures.extend(
                _check_image_publisher_job(
                    workflow_path=ci_path,
                    block=publisher[1],
                )
            )
    return failures


def check_ci_workflow(path: Path) -> list[str]:
    if not path.exists():
        return [f"{path}: workflow file does not exist"]

    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        merge_group_index = lines.index("  merge_group:")
    except ValueError:
        return [f"{path}: native merge queue checks require the merge_group trigger."]
    if lines[merge_group_index + 1].strip() != "types: [checks_requested]":
        return [f"{path}: merge_group must handle only checks_requested."]
    if not any(line.strip() == EXPECTED_PR_CANCELLATION for line in lines):
        return [
            f"{path}: a pull_request run may cancel only an older pull_request "
            "run of the same branch. A workflow_dispatch run may cancel only an "
            "older dispatch with the same inputs and commit. Push and merge_group "
            "runs must not be cancelled."
        ]
    for job, expected_name in REQUIRED_CI_JOB_NAMES.items():
        parsed = _job_block(lines, job)
        if parsed is None:
            return [f"{path}: required CI job {job!r} is missing"]
        start_line, block = parsed
        actual_name = _parse_name_line(block)
        if actual_name != expected_name:
            return [
                f"{path}:{start_line}: required CI check identity changed for {job!r}.",
                f"expected: name: {expected_name}",
                f"actual:   name: {actual_name or '<missing>'}",
                "Update repository rules first; never silently orphan a required check.",
            ]

    for job in ACTION_FREE_REQUIRED_TEST_JOBS:
        parsed = _job_block(lines, job)
        if parsed is None:
            return [f"{path}: required CI job {job!r} is missing"]
        start_line, block = parsed
        for offset, line in enumerate(block, start=1):
            stripped = line.strip()
            if not stripped.startswith("uses:"):
                continue
            action = stripped.removeprefix("uses:").strip()
            if not action.startswith(ALLOWED_REQUIRED_TEST_ACTIONS):
                return [
                    f"{path}:{start_line + offset}: required test job {job!r} "
                    f"must not preload third-party action {action}; use a repo-owned "
                    "script or an explicitly allowed GitHub-owned action.",
                ]

    expected_skip = (
        "${{ !cancelled() && (github.event_name != 'push' || github.ref != 'refs/heads/main') }}"
    )
    for job in QUEUE_SKIPPED_ON_MAIN:
        parsed = _job_block(lines, job)
        if job in MATRIX_LANE_TEST_IF_OUTPUT:
            expected_if = matrix_lane_test_if(MATRIX_LANE_TEST_IF_OUTPUT[job])
        elif job in ACTION_FREE_REQUIRED_TEST_JOBS:
            expected_if = EXPENSIVE_TEST_IF
        else:
            expected_if = expected_skip
        if job == "test-e2e-gate":
            expected_if = "always() && (github.event_name != 'push' || github.ref != 'refs/heads/main')"
        if parsed is None or _parse_if_line(parsed[1]) != expected_if:
            return [
                f"{path}: {job!r} admission guard changed; it must skip the redundant "
                "main-push rerun after queue validation and must not allocate a runner "
                "for the zero-shard sentinel."
            ]

    deploy_dry_run = _job_block(lines, "deploy-dry-run")
    if deploy_dry_run is None:
        return [
            f"{path}: deploy-dry-run job is missing; remove the MTG boundary guard "
            "only if the upstream DigitalOcean dry-run lane has been deleted intentionally."
        ]

    dry_run_start_line, dry_run_block = deploy_dry_run
    dry_run_if = _parse_if_line(dry_run_block)
    if dry_run_if != EXPECTED_DEPLOY_DRY_RUN_IF:
        return [
            f"{path}:{dry_run_start_line}: deploy-dry-run must stay upstream-only for MTG-Thomas/bifrost.",
            f"expected: if: {EXPECTED_DEPLOY_DRY_RUN_IF}",
            f"actual:   if: {dry_run_if or '<missing>'}",
            "MTG has no DigitalOcean deploy credentials; validate Azure deployments from bifrost-infra.",
        ]

    deploy_dev = _job_block(lines, "deploy-dev")
    if deploy_dev is None:
        return [
            f"{path}: deploy-dev job is missing; remove the MTG boundary guard "
            "only if the upstream DigitalOcean deploy lane has been deleted intentionally."
        ]

    start_line, block = deploy_dev
    if_condition = _parse_if_line(block)
    if if_condition != EXPECTED_DEPLOY_DEV_IF:
        return [
            f"{path}:{start_line}: deploy-dev must stay upstream-only for MTG-Thomas/bifrost.",
            f"expected: if: {EXPECTED_DEPLOY_DEV_IF}",
            f"actual:   if: {if_condition or '<missing>'}",
            "MTG deploys from bifrost-infra to Azure; do not re-enable DigitalOcean CI/CD in this fork.",
        ]

    return []


def check_codeowners(path: Path) -> list[str]:
    if not path.exists():
        return []

    violations: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        for token in parts[1:]:
            if token.startswith("#"):
                break
            if not token.startswith("@"):
                if "@" in token:
                    violations.append(
                        f"{path}:{lineno}: CODEOWNERS names email owner {token}; "
                        "email owners cannot be constrained to the MTG allowlist. "
                        "Use an explicit MTG username or team."
                    )
                continue
            if token in ALLOWED_CODEOWNERS or token.startswith("@Midtown-Technology-Group/"):
                continue
            violations.append(
                f"{path}:{lineno}: CODEOWNERS names {token}; only MTG owners survive upstream merges.",
            )
    return violations

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check MTG fork CI boundaries that must survive upstream merges."
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/ci.yml"),
        help="CI workflow to inspect.",
    )
    args = parser.parse_args(argv)

    try:
        workflow = _resolve_workflow_path(args.workflow)
    except ValueError as exc:
        print(f"MTG CI boundary check failed: {exc}", file=sys.stderr)
        return 2

    violations = check_ci_workflow(workflow)
    violations += check_ci_image_token_boundaries(
        workflow,
        _repo_root() / ".github/workflows/sonar-coverage.yml",
    )
    violations += check_codeowners(_repo_root() / ".github/CODEOWNERS")
    if not violations:
        print("MTG CI boundary checks passed.")
        return 0

    print("MTG CI boundary check failed:", file=sys.stderr)
    for violation in violations:
        print(f"- {violation}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
