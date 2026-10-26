from __future__ import annotations

import os
from pathlib import Path
import subprocess


API_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = API_ROOT / "scripts/ci/prepare-test-images.sh"


def _run(tmp_path: Path, *, api_changed: str, image_present: bool = True) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [ "${1:-}" = image ] && [ "${IMAGE_PRESENT:-true}" != true ]; then
  exit 1
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    summary = tmp_path / "summary.md"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "IMAGE_PRESENT": str(image_present).lower(),
        "REGISTRY": "ghcr.io",
        "CI_TEST_IMAGE_TAG": "main",
        "CI_API_TEST_IMAGE": "example/api",
        "API_IMAGE_CHANGED": api_changed,
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    subprocess.run(["bash", str(SCRIPT), "api"], cwd=API_ROOT, env=env, check=True)
    return log.read_text(encoding="utf-8").splitlines()


def test_unchanged_cached_image_is_tagged_without_build(tmp_path: Path) -> None:
    calls = _run(tmp_path, api_changed="false")

    assert calls == [
        "image inspect ghcr.io/example/api:main",
        "tag ghcr.io/example/api:main bifrost-test-api-dev:latest",
    ]


def test_changed_image_builds_from_reviewed_source_with_pulled_cache(tmp_path: Path) -> None:
    calls = _run(tmp_path, api_changed="true")

    assert calls == [
        "image inspect ghcr.io/example/api:main",
        (
            "build --file ./api/Dockerfile.dev --tag bifrost-test-api-dev:latest "
            "--cache-from ghcr.io/example/api:main ."
        ),
    ]


def test_missing_cache_falls_back_to_fail_closed_local_build(tmp_path: Path) -> None:
    calls = _run(tmp_path, api_changed="false", image_present=False)

    assert calls == [
        "image inspect ghcr.io/example/api:main",
        "build --file ./api/Dockerfile.dev --tag bifrost-test-api-dev:latest .",
    ]


def test_client_e2e_builds_reviewed_production_image(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "REGISTRY": "ghcr.io",
        "CI_TEST_IMAGE_TAG": "main",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    subprocess.run(
        ["bash", str(SCRIPT), "client-e2e"],
        cwd=API_ROOT,
        env=env,
        check=True,
    )

    assert log.read_text(encoding="utf-8").splitlines() == [
        (
            "build --file ./client/Dockerfile --tag "
            "bifrost-test-client-e2e:latest --target production "
            "--build-arg VITE_BIFROST_VERSION=test ./client"
        ),
    ]


def test_checked_out_helper_has_no_registry_credential_inputs() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "GHCR_TOKEN" not in script
    assert "GHCR_USERNAME" not in script
    assert "docker login" not in script
    assert "docker pull" not in script


def test_playwright_image_uses_the_compose_service_tag() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert '"bifrost-test-playwright-runner:latest"' in script
