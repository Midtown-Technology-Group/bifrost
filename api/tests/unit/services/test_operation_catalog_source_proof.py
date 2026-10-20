"""The metadata-only source comparison must reject authorization/body changes."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, call

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/operation_catalog/verify_metadata_only.py"
spec = importlib.util.spec_from_file_location("metadata_only_proof", SCRIPT)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

BEFORE = '''from src.core.auth import CurrentSuperuser
@router.get("/items", dependencies=[Depends(require_admin)])
async def items(user: CurrentSuperuser):
    return "items"
'''
AFTER = '''from src.core.auth import CurrentSuperuser
from src.services.operation_catalog import operation_route
@router.get("/items", dependencies=[Depends(require_admin)], **operation_route("items.list"))
async def items(user: CurrentSuperuser):
    return "items"
'''


def test_only_catalog_import_and_route_metadata_are_removed() -> None:
    assert proof.normalized(BEFORE) == proof.normalized(AFTER)
    assert proof.route_dependencies(BEFORE) == proof.route_dependencies(AFTER)


@pytest.mark.parametrize("before,after", [
    ("CurrentSuperuser", "CurrentActiveUser"),
    ("Depends(require_admin)", "Depends(require_user)"),
    ('"/items"', '"/other"'),
    ('return "items"', 'return "changed"'),
])
def test_dependency_signature_path_and_handler_changes_fail(before: str, after: str) -> None:
    assert proof.normalized(BEFORE) != proof.normalized(AFTER.replace(before, after))


@pytest.mark.parametrize("reference", [
    "", "--output=/tmp/unreviewed", "-c", "HEAD\n--help", "HEAD other",
    "HEAD^{tree}", "HEAD~1", "HEAD;touch", "refs/heads/a:b",
])
def test_base_reference_rejects_options_before_starting_git(monkeypatch, reference: str) -> None:
    def unexpected_repository(*args, **kwargs):
        raise AssertionError("Unsafe reference reached Git")

    monkeypatch.setattr(proof, "GitRepo", unexpected_repository)
    with pytest.raises(ValueError, match="named Git reference"):
        proof.resolve_base_ref(reference)


def test_base_reference_resolves_named_ref_to_immutable_commit(tmp_path, monkeypatch) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-c", "user.name=Source Proof", "-c", "user.email=proof@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "fixture"],
                   cwd=tmp_path, check=True)
    monkeypatch.setattr(proof, "REPO_ROOT", tmp_path)
    expected = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path).decode().strip()
    assert proof.resolve_base_ref("HEAD") == expected
    assert proof.resolve_base_ref(expected) == expected


@pytest.mark.parametrize("result", ["--output=unreviewed", "a" * 39, "a" * 40 + "\n" + "b" * 40])
def test_base_reference_requires_one_full_resolved_sha(monkeypatch, result: str) -> None:
    repo = MagicMock()
    repo.commit.return_value.hexsha = result
    monkeypatch.setattr(proof, "GitRepo", MagicMock(return_value=repo))
    with pytest.raises(ValueError, match="complete commit SHA"):
        proof.resolve_base_ref("HEAD")


def test_source_proof_reads_git_objects_by_exact_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_sha = "a" * 40
    routers_blob = MagicMock(type="blob", path="api/src/routers/example.py")
    routers_directory = MagicMock(type="tree")
    routers_directory.traverse.return_value = [routers_blob]
    shared_blob = MagicMock(type="blob", path="api/shared/example.py")
    shared_directory = MagicMock(type="tree")
    shared_directory.traverse.return_value = [shared_blob]
    direct_blob = MagicMock(type="blob", path="api/src/main.py")
    source_blob = MagicMock()
    source_blob.data_stream.read.return_value = b"print('base')\n"
    tree = MagicMock()
    tree.__truediv__.side_effect = [
        routers_directory,
        shared_directory,
        direct_blob,
        source_blob,
    ]
    repo = MagicMock()
    repo.commit.return_value.tree = tree
    monkeypatch.setattr(proof, "GitRepo", MagicMock(return_value=repo))

    assert proof.base_source_files(base_sha) == [
        "api/src/routers/example.py",
        "api/shared/example.py",
        "api/src/main.py",
    ]
    assert proof.base_source(base_sha, "api/src/main.py") == "print('base')\n"
    assert repo.commit.call_args_list == [call(base_sha)] * 2


def test_source_proof_skips_source_paths_missing_from_base_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_sha = "a" * 40
    shared_blob = MagicMock(type="blob", path="api/shared/example.py")
    shared_directory = MagicMock(type="tree")
    shared_directory.traverse.return_value = [shared_blob]
    direct_blob = MagicMock(type="blob", path="api/src/main.py")
    tree = MagicMock()
    tree.__truediv__.side_effect = [
        KeyError("api/src/routers"),
        shared_directory,
        direct_blob,
    ]
    repo = MagicMock()
    repo.commit.return_value.tree = tree
    monkeypatch.setattr(proof, "GitRepo", MagicMock(return_value=repo))

    assert proof.base_source_files(base_sha) == [
        "api/shared/example.py",
        "api/src/main.py",
    ]


def test_main_writes_a_passing_resolved_commit_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base_sha = "a" * 40
    output = tmp_path / "proof.json"
    monkeypatch.setattr(proof, "resolve_base_ref", lambda value: base_sha)
    monkeypatch.setattr(proof, "base_source_files", lambda value: [])
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_metadata_only.py", "--base-ref", "HEAD", "--output", str(output)],
    )

    assert proof.main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report == {"base_sha": base_sha, "files": [], "result": "PASS"}
