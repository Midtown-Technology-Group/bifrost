"""The metadata-only source comparison must reject authorization/body changes."""

import importlib.util
from pathlib import Path
import subprocess

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
    def unexpected_command(*args, **kwargs):
        raise AssertionError("Unsafe reference reached Git")

    monkeypatch.setattr(proof.subprocess, "check_output", unexpected_command)
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


@pytest.mark.parametrize("result", [b"--output=unreviewed\n", b"a" * 39, b"a" * 40 + b"\n" + b"b" * 40])
def test_base_reference_requires_one_full_resolved_sha(monkeypatch, result: bytes) -> None:
    monkeypatch.setattr(proof.subprocess, "check_output", lambda *args, **kwargs: result)
    with pytest.raises(ValueError, match="complete commit SHA"):
        proof.resolve_base_ref("HEAD")
