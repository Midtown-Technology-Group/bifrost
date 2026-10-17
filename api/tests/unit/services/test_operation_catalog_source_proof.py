"""The metadata-only source comparison must reject authorization/body changes."""

import importlib.util
from pathlib import Path
import subprocess
import sys

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


@pytest.fixture
def tree_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    subprocess.check_call(["git", "init", "--bare", "--quiet", str(tmp_path)])
    tree_oid = subprocess.check_output(["git", "mktree"], cwd=tmp_path, input=b"").decode().strip()
    subprocess.check_call(["git", "update-ref", "refs/tags/base-\u0394", tree_oid], cwd=tmp_path)
    monkeypatch.setattr(proof, "REPO_ROOT", tmp_path)
    return tree_oid


def test_base_ref_preserves_object_ids_unicode_refs_and_treeish_syntax(tree_repo: str) -> None:
    for base_ref in (tree_repo, "refs/tags/base-\u0394", "refs/tags/base-\u0394^{tree}"):
        assert proof.resolve_base_ref(base_ref) == tree_repo


@pytest.mark.parametrize("base_ref", ["--show-toplevel", "--local-env-vars", "--git-dir"])
def test_base_ref_rejects_git_options(tree_repo: str, base_ref: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        proof.resolve_base_ref(base_ref)


@pytest.mark.parametrize("oid", ["a" * 40, "b" * 64])
def test_base_ref_accepts_git_object_id_formats(monkeypatch: pytest.MonkeyPatch, oid: str) -> None:
    monkeypatch.setattr(proof, "git", lambda *args: oid + "\n")
    assert proof.resolve_base_ref("HEAD") == oid


@pytest.mark.parametrize("resolved", ["", "--all", "a" * 39, "a" * 41, "g" * 40, "a" * 40 + "\n" + "b" * 40])
def test_invalid_resolved_oid_never_reaches_tree_or_source_reads(
    monkeypatch: pytest.MonkeyPatch, resolved: str,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        calls.append(args)
        return resolved

    monkeypatch.setattr(proof, "git", fake_git)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--base-ref", "HEAD"])
    with pytest.raises(ValueError, match="one object ID"):
        proof.main()
    assert calls == [("rev-parse", "--verify", "--end-of-options", "HEAD")]
