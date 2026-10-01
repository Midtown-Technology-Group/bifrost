"""Protect dependency reuse and the PR/queue registry-cache write boundary."""

import subprocess
from pathlib import Path

import pytest
import yaml
from scripts.check_mtg_ci_boundaries import _repo_root


def _candidate_job():
    workflow = _repo_root() / ".github/workflows/ci.yml"
    return yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)["jobs"][
        "build-candidate-images"
    ]


@pytest.mark.parametrize(
    ("event", "number", "expected"),
    [
        ("merge_group", "", "buildcache-queue"),
        ("pull_request", "1005", "buildcache-pr-1005"),
    ],
)
def test_pr_cache_writes_are_isolated_from_queue_cache(
    tmp_path: Path, event: str, number: str, expected: str
):
    identity = next(
        step for step in _candidate_job()["steps"] if step.get("id") == "identity"
    )
    script = identity["run"].split("# PRs can read", 1)[1]
    script = script[script.index("\n") + 1 :]
    output = tmp_path / "output"
    subprocess.run(
        ["bash", "-eu", "-c", script],
        env={"EVENT_NAME": event, "PR_NUMBER": number, "GITHUB_OUTPUT": str(output)},
        check=True,
    )
    assert output.read_text().strip() == f"cache_tag={expected}"


def test_candidates_reuse_registry_dependency_layers_across_queue_refs():
    build = next(
        step for step in _candidate_job()["steps"] if step.get("id") == "build"
    )
    cache = build["with"]
    assert (
        "type=registry,ref=ghcr.io/${{ matrix.image }}:buildcache-queue"
        in cache["cache-from"]
    )
    assert cache["cache-to"] == (
        "type=registry,ref=ghcr.io/${{ matrix.image }}:"
        "${{ steps.identity.outputs.cache_tag }},mode=max"
    )


def test_source_and_version_changes_do_not_invalidate_dependency_installation():
    dockerfile = (_repo_root() / "api/Dockerfile").read_text()
    source = dockerfile.index("COPY api/src/ /app/src/")
    version = dockerfile.index("ARG BIFROST_VERSION")
    for dependency in (
        "RUN apt-get update",
        "RUN pip install",
        "RUN cd /app/src/services/app_compiler",
        "RUN cd /app/src/services/app_bundler",
        "RUN cd /app/src/services/sdk_package",
    ):
        assert dockerfile.rindex(dependency) < source < version
