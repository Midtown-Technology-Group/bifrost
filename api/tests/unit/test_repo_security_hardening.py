"""Static checks for repo and dev-surface hardening invariants."""

from pathlib import Path
import os
import re
import subprocess
from typing import Any

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github").is_dir() and (parent / "debug.sh").exists():
            return parent
    raise RuntimeError("Could not locate repository root")


REPO_ROOT = _repo_root()
REQUIRED_CI_CHECK_NAMES = {"Lint & Type Check", "Unit Tests", "E2E Tests"}


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _load_yaml(relative_path: str) -> dict[str, Any]:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_retired_snyk_gate_is_not_reintroduced_by_workflows() -> None:
    workflow_root = REPO_ROOT / ".github" / "workflows"

    assert not (workflow_root / "snyk.yml").exists()
    workflows = sorted(workflow_root.glob("*.yml")) + sorted(
        workflow_root.glob("*.yaml")
    )
    for workflow in workflows:
        assert "snyk" not in workflow.read_text(encoding="utf-8").lower(), workflow.name


def test_pull_request_ci_does_not_use_noop_path_ignore() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    pull_request = ci[True]["pull_request"]

    assert "paths-ignore" not in pull_request


def test_required_e2e_gate_includes_playwright_and_mcp_conformance() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    jobs = ci["jobs"]

    assert jobs["test-client-e2e"]["name"] == (
        "Client E2E Tests (shard ${{ matrix.shard }}/${{ matrix.total }})"
    )
    assert set(jobs["test-e2e-gate"]["needs"]) == {
        "affected-test-plan",
        "lint",
        "publish-ci-test-images",
        "test-e2e",
        "test-client-e2e",
        "test-client-unit",
        "mcp-conformance",
    }
    gate_script = jobs["test-e2e-gate"]["steps"][0]["run"]
    assert 'needs.test-client-unit.result' in gate_script

    assert (
        jobs["deploy-dry-run"]["if"]
        == "github.repository == 'gobifrost/bifrost' && github.event_name == 'workflow_dispatch'"
    )

    compose = yaml.safe_load(_read("docker-compose.test.yml"))
    assert compose["services"]["playwright-runner"]["tmpfs"] == [
        "/app/e2e/.auth:uid=1000,gid=1000,mode=0700"
    ]


def test_ci_test_image_consumers_use_the_exact_published_tag() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    jobs = ci["jobs"]
    image_jobs = {"test-unit", "mcp-conformance", "test-e2e", "test-client-e2e"}

    assert ci["env"]["CI_TEST_IMAGE_TAG"] == "${{ format('sha-{0}', github.sha) }}"
    assert "github.event_name == 'pull_request'" in jobs["publish-ci-test-images"][
        "if"
    ]
    assert (
        "github.event.pull_request.head.repo.full_name == github.repository"
        in jobs["publish-ci-test-images"]["if"]
    )
    for job_name in image_jobs:
        assert set(jobs[job_name]["needs"]) == {
            "affected-test-plan",
            "lint",
            "publish-ci-test-images",
        }

    expected_commands = {
        "test-unit": "bash api/scripts/ci/prepare-test-images.sh api client",
        "mcp-conformance": "bash api/scripts/ci/prepare-test-images.sh api client",
        "test-e2e": "bash api/scripts/ci/prepare-test-images.sh api client",
        "test-client-e2e": (
            "bash api/scripts/ci/prepare-test-images.sh api client client-e2e playwright"
        ),
    }
    for job_name, expected_command in expected_commands.items():
        prepare = next(
            step
            for step in jobs[job_name]["steps"]
            if step["name"] == "Prepare CI test images"
        )
        assert prepare["run"] == expected_command
        assert prepare["env"]["GHCR_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"

    api_root = Path(__file__).resolve().parents[2]
    image_script = (api_root / "scripts/ci/prepare-test-images.sh").read_text(
        encoding="utf-8"
    )
    assert 'image_tag="${CI_TEST_IMAGE_TAG:?CI_TEST_IMAGE_TAG is required}"' in image_script
    assert 'remote_ref="${registry}/${remote_image}:${image_tag}"' in image_script


def test_feature_branch_dispatch_cannot_overwrite_main_ci_test_images() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    publish_steps = ci["jobs"]["publish-ci-test-images"]["steps"]
    image_steps = [
        step for step in publish_steps if step["name"].startswith("Build and push")
    ]

    assert len(image_steps) == 3
    for step in image_steps:
        tags = step["with"]["tags"]
        assert "github.ref == 'refs/heads/main'" in tags
        assert ":main" in tags
        assert ":sha-${{ github.sha }}" in tags


def test_playwright_suite_has_no_retries_or_skipped_tests() -> None:
    config = _read("client/playwright.config.ts")
    e2e_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "client" / "e2e").rglob("*.ts"))
    )

    retry_values = re.findall(r"\bretries:\s*([^,\n]+)", config)
    assert retry_values
    assert all(value.strip() == "0" for value in retry_values)
    assert 'outputDir: "playwright-results/test-results"' in config
    assert not re.search(r"\b(?:test|describe)\.(?:skip|fixme)\b", e2e_source)


def test_docs_noop_workflow_cannot_spoof_required_ci_check_names() -> None:
    noop = _load_yaml(".github/workflows/ci-noop.yml")
    job_names = {job.get("name", job_id) for job_id, job in noop["jobs"].items()}

    assert noop["name"] != "CI"
    assert job_names.isdisjoint(REQUIRED_CI_CHECK_NAMES)


def test_release_assets_upload_before_immutable_release_publication() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    steps = ci["jobs"]["create-release"]["steps"]
    create_index = next(
        index for index, step in enumerate(steps) if step["name"] == "Create release"
    )
    publish_index = next(
        index for index, step in enumerate(steps) if step["name"] == "Publish release"
    )

    create_with = steps[create_index]["with"]
    publish_run = steps[publish_index]["run"]

    assert create_with["draft"] is True
    assert "${{ steps.source_tarball.outputs.tarball }}" in create_with["files"]
    assert "${{ steps.source_tarball.outputs.tarball }}.sha256" in create_with["files"]
    assert (
        "${{ steps.source_tarball.outputs.tarball }}.sigstore" in create_with["files"]
    )
    assert publish_index > create_index
    assert "args=(-F draft=false)" in publish_run
    assert "args+=(-F prerelease=true)" in publish_run
    assert "args+=(-f make_latest=true)" in publish_run
    assert 'gh api --method PATCH "$endpoint" "${args[@]}"' in publish_run
    assert steps[publish_index]["env"]["RELEASE_ID"] == "${{ steps.release.outputs.id }}"
    assert '.draft == false and .immutable == true' in publish_run


def test_dependabot_lock_validation_does_not_commit_to_pr_branch() -> None:
    workflow = _load_yaml(".github/workflows/dependabot-lockfile-regen.yml")
    text = _read(".github/workflows/dependabot-lockfile-regen.yml")

    assert workflow["permissions"] == {"contents": "read"}
    assert "git push" not in text
    assert "git commit" not in text
    assert "contents: write" not in text


def test_dependabot_auto_merge_requires_python_lockfile_updates() -> None:
    text = _read(".github/workflows/dependabot-auto-merge.yml")

    assert "Require reviewed Python lockfile updates" in text
    assert "steps.metadata.outputs.package-ecosystem == 'pip'" in text
    assert "requirements.lock" in text
    assert "needs-review" in text


def test_debug_port_mode_binds_client_to_loopback_only() -> None:
    port_overlay = _load_yaml("docker-compose.debug.port.yml")
    ports = port_overlay["services"]["client"]["ports"]

    assert ports == ["127.0.0.1:${DEBUG_CLIENT_PORT}:80"]


def test_debug_stack_has_no_checked_in_default_admin_password() -> None:
    compose = _load_yaml("docker-compose.debug.yml")
    debug_env = _read(".env.debug")
    debug_script = _read("debug.sh")
    debug_skill = _read(".claude/skills/bifrost-debug/SKILL.md")

    api_env = compose["services"]["api"]["environment"]
    assert (
        api_env["BIFROST_DEFAULT_USER_PASSWORD"] == "${BIFROST_DEFAULT_USER_PASSWORD:-}"
    )
    assert "BIFROST_DEFAULT_USER_PASSWORD=password" not in debug_env
    assert "password: password" not in debug_script
    assert "dev@gobifrost.com" not in debug_skill
    assert "--password password" not in debug_skill


def test_debug_env_loader_does_not_source_env_files() -> None:
    debug_script = _read("debug.sh")

    assert 'source "$SCRIPT_DIR/scripts/lib/test_helpers.sh"' in debug_script
    assert 'source "$SCRIPT_DIR/.env"' not in debug_script
    assert 'source "$SCRIPT_DIR/.env.debug"' not in debug_script
    assert 'source "$HOME/.config/bifrost/debug.env"' not in debug_script


def test_debug_storage_credential_is_private_stable_and_respects_override(
    tmp_path: Path,
) -> None:
    text = _read("debug.sh")
    function = re.search(r"^configure_debug_storage\(\) \{\n.*?^\}", text, re.M | re.S)
    assert function is not None
    env = {key: value for key, value in os.environ.items() if key != "SEAWEEDFS_SECRET_KEY"}
    env.update(XDG_STATE_HOME=str(tmp_path), COMPOSE_PROJECT_NAME="debug-storage-test")
    subprocess.run(
        [
            "bash",
            "-euc",
            function.group() + "\n" + """
configure_debug_storage
test -n "$SEAWEEDFS_SECRET_KEY"
task_first_key="$SEAWEEDFS_SECRET_KEY"
unset SEAWEEDFS_SECRET_KEY
configure_debug_storage
test "$SEAWEEDFS_SECRET_KEY" = "$task_first_key"
export COMPOSE_PROJECT_NAME=debug-explicit-storage-test
export SEAWEEDFS_SECRET_KEY=explicit-local-test-credential
configure_debug_storage
test "$SEAWEEDFS_SECRET_KEY" = explicit-local-test-credential
""",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    directory = tmp_path / "bifrost/debug/debug-storage-test"
    secret = directory / "storage-secret"
    assert re.fullmatch(r"[0-9a-f]{64}\n", secret.read_text())
    assert directory.stat().st_mode & 0o777 == 0o700
    assert secret.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "bifrost/debug/debug-explicit-storage-test").exists()


def test_debug_signing_key_is_private_stable_and_required_by_compose(
    tmp_path: Path,
) -> None:
    text = _read("debug.sh")
    function = re.search(
        r"^configure_debug_signing_key\(\) \{\n.*?^\}", text, re.M | re.S
    )
    assert function is not None
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "BIFROST_SECRET_KEY"
    }
    env.update(XDG_STATE_HOME=str(tmp_path), COMPOSE_PROJECT_NAME="debug-signing-test")
    subprocess.run(
        [
            "bash",
            "-euc",
            function.group()
            + "\n"
            + """
configure_debug_signing_key
test -n "$BIFROST_SECRET_KEY"
task_first_key="$BIFROST_SECRET_KEY"
unset BIFROST_SECRET_KEY
configure_debug_signing_key
test "$BIFROST_SECRET_KEY" = "$task_first_key"
export COMPOSE_PROJECT_NAME=debug-published-signing-test
export BIFROST_SECRET_KEY=dev-secret-key-change-in-production-must-be-32-chars
configure_debug_signing_key
test "$BIFROST_SECRET_KEY" != dev-secret-key-change-in-production-must-be-32-chars
export COMPOSE_PROJECT_NAME=debug-explicit-signing-test
export BIFROST_SECRET_KEY=explicit-local-signing-credential
configure_debug_signing_key
test "$BIFROST_SECRET_KEY" = explicit-local-signing-credential
""",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    for project in ("debug-signing-test", "debug-published-signing-test"):
        directory = tmp_path / "bifrost/debug" / project
        secret = directory / "signing-secret"
        assert re.fullmatch(r"[0-9a-f]{64}\n", secret.read_text())
        assert directory.stat().st_mode & 0o777 == 0o700
        assert secret.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "bifrost/debug/debug-explicit-signing-test").exists()

    compose = _load_yaml("docker-compose.debug.yml")
    required = "${BIFROST_SECRET_KEY:?Use debug.sh}"
    for service in ("init", "api", "scheduler", "worker"):
        assert (
            compose["services"][service]["environment"]["BIFROST_SECRET_KEY"]
            == required
        )

    cmd_up = re.search(r"^cmd_up\(\) \{\n.*?^\}", text, re.M | re.S)
    assert cmd_up is not None
    assert cmd_up.group().index("configure_debug_signing_key") < cmd_up.group().index(
        "stack_is_running"
    )
    assert "service_signing_key" in text
    assert "Applying the private debug signing key" in text


def test_claude_hook_shell_quotes_exported_env_values() -> None:
    hook = _read(".claude/hooks/bifrost-detect.sh")

    assert "printf 'export %s=%q\\n'" in hook
    assert 'echo "export BIFROST_DEV_URL=\\"$BIFROST_DEV_URL\\""' not in hook


def test_claude_skills_avoid_unquoted_url_and_body_shell_patterns() -> None:
    setup = _read(".claude/skills/bifrost-setup/SKILL.md")
    issues = _read(".claude/skills/bifrost-issues/SKILL.md")

    assert "$BIFROST_PIP_CMD {url}/api/cli/download" not in setup
    assert "bifrost login --url {url}" not in setup
    assert "claude mcp add --transport http bifrost {url}/mcp" not in setup
    assert "curl {url}/api/cli/download" not in setup
    assert '--body "$(cat <<' not in issues
    assert 'gh issue list --search "<2-3 key terms>"' not in issues
