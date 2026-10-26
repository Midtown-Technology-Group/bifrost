"""Security tests for workspace Git path validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from shared.workspace_paths import (
    resolve_workspace_path,
    validate_workspace_relative_path,
)
from src.models.contracts.github import DiffRequest, DiscardRequest, ResolveRequest


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../secret",
        "workflows/../../secret",
        ".git/config",
        "nested/.GIT/config",
        r"C:\Windows\win.ini",
        r"..\secret",
        "./workflow.py",
        "workflow.py/",
        ".",
        "bad\x00path",
        "bad\npath",
    ],
)
def test_workspace_git_requests_reject_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        validate_workspace_relative_path(path)
    with pytest.raises(ValidationError):
        DiffRequest(path=path)
    with pytest.raises(ValidationError):
        DiscardRequest(paths=[path])
    with pytest.raises(ValidationError):
        ResolveRequest(resolutions={path: "ours"})


def test_workspace_git_requests_accept_canonical_relative_paths() -> None:
    path = "workflows/ticket_tools.py"

    assert DiffRequest(path=path).path == path
    assert DiscardRequest(paths=[path]).paths == [path]
    assert ResolveRequest(resolutions={path: "theirs"}).resolutions == {
        path: "theirs"
    }


def test_resolve_workspace_path_rejects_symlink_escape(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (repository / "linked.txt").symlink_to(secret)

    with pytest.raises(ValueError, match="remain within"):
        resolve_workspace_path(repository, "linked.txt")


def test_resolve_workspace_path_rejects_symlink_to_git_metadata(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    git_metadata = repository / ".git"
    git_metadata.mkdir(parents=True)
    (git_metadata / "fixture").write_text("harmless metadata fixture")
    (repository / "linked.txt").symlink_to(".git/fixture")

    with pytest.raises(ValueError, match="Git internals"):
        resolve_workspace_path(repository, "linked.txt")
