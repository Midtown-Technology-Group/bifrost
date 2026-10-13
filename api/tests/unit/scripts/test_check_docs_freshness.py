"""Tests for the upstream-only release documentation gate."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


_CONTAINER_REPO_ROOT = Path("/repo")
REPO_ROOT = (
    _CONTAINER_REPO_ROOT
    if (_CONTAINER_REPO_ROOT / "scripts/release/check-docs-freshness.sh").exists()
    else Path(__file__).resolve().parents[4]
)
SCRIPT = REPO_ROOT / "scripts/release/check-docs-freshness.sh"
RELEASE_SKILLS = (
    REPO_ROOT / ".agents/skills/bifrost-release/SKILL.md",
    REPO_ROOT / ".claude/skills/bifrost-release/SKILL.md",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(tmp_path: Path, origin: str) -> Path:
    repo = tmp_path / "bifrost"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release-test@example.com")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", origin)
    return repo


def _run(repo: Path, docs_repo: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("GITHUB_REPOSITORY", None)
    env.pop("BIFROST_GITHUB_REPOSITORY", None)
    env.update(
        {
            "BIFROST_REPO": str(repo),
            "DOCS_REPO": str(docs_repo),
            **overrides,
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_fork_origin_waives_missing_upstream_docs_checkout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "https://github.com/MTG-Thomas/bifrost.git")

    result = _run(repo, tmp_path / "missing-docs")

    assert result.returncode == 0
    assert "upstream documentation check waived for fork MTG-Thomas/bifrost" in result.stdout
    assert "docs repo not found" not in result.stderr


def test_github_repository_waives_fork_even_with_upstream_origin(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "git@github.com:gobifrost/bifrost.git")

    result = _run(
        repo,
        tmp_path / "missing-docs",
        GITHUB_REPOSITORY="Midtown-Technology-Group/bifrost",
    )

    assert result.returncode == 0
    assert (
        "upstream documentation check waived for fork "
        "Midtown-Technology-Group/bifrost"
    ) in result.stdout
    assert "docs repo not found" not in result.stderr


def test_upstream_still_requires_docs_checkout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "ssh://git@github.com/gobifrost/bifrost.git")

    result = _run(repo, tmp_path / "missing-docs")

    assert result.returncode == 2
    assert "docs repo not found" in result.stderr
    assert "upstream documentation check waived" not in result.stdout


def test_freshness_queries_the_release_ref_instead_of_origin_main() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'BIFROST_REF="${BIFROST_REF:-HEAD}"' in source
    assert 'log -1 --format=%cI "$BIFROST_REF"' in source
    assert '--name-only --pretty=format: "$BIFROST_REF"' in source
    assert '--oneline "$BIFROST_REF"' in source


def test_release_skill_uses_nonempty_ranges_without_prior_tag() -> None:
    for skill in RELEASE_SKILLS:
        source = skill.read_text(encoding="utf-8")
        assert 'git log ${LAST_TAG}..HEAD' not in source
        assert '"${RELEASE_RANGE:-HEAD}"' in source
