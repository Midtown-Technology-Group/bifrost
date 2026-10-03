"""A green native runtime cannot credit omitted or stale authored components."""

import hashlib
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import yaml
from src.services.solution_deploy_obligations import solution_source_content_id
from src.services.solutions.github_delivery_source import (
    VerifiedAuthoredSolution,
    VerifiedAuthoredSolutionFile,
)
from src.services.solutions.native_authored_source import (
    NativeAuthoredSourceMismatch,
    native_authored_install_readback,
    native_authored_metadata,
    require_native_python_closure,
)

SID = UUID("00000000-0000-0000-0000-000000000010")
WID = "00000000-0000-0000-0000-000000000011"
TID = "00000000-0000-0000-0000-000000000012"
PREFIX = "solutions/fixture"


def authored(**changes):
    files = {
        "bifrost.solution.yaml": yaml.safe_dump({"slug": "fixture", "name": "Fixture", "version": "0.1.0",
            "repo_subpath": PREFIX, "global_repo_access": False}).encode(),
        "README.md": b"Reviewed source instructions\n",
        ".bifrost/workflows.yaml": yaml.safe_dump({"workflows": {WID: {"id": WID, "name": "probe",
            "path": "functions/probe.py", "function_name": "probe"}}}).encode(),
        ".bifrost/tables.yaml": yaml.safe_dump({"tables": {TID: {"id": TID, "name": "evidence",
            "policies": [{"$ref": "admin_bypass"}]}}}).encode(),
        "functions/probe.py": b"from modules.runtime import helper\ndef probe():\n    return helper()\n",
        "modules/runtime.py": b"def helper():\n    return 1\n",
        "shared/__init__.py": b"",
        "shared/microsoft/__init__.py": b"",
    }
    for path, value in changes.items():
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    manifest = tuple(VerifiedAuthoredSolutionFile(path=PREFIX + "/" + path, mode="100644",
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)) for path, raw in sorted(files.items()))
    entries = [{"path": item.path, "mode": item.mode, "sha256": item.sha256, "size": item.size} for item in manifest]
    return VerifiedAuthoredSolution(commit_sha="a" * 40, tree_sha="b" * 40, subtree_sha="c" * 40,
        solution_slug="fixture", repo_subpath=PREFIX,
        source_content_id=solution_source_content_id(solution_slug="fixture", repo_subpath=PREFIX, source_files=entries),
        source_files=manifest, files=MappingProxyType(files))


def test_authored_inventory_is_not_the_native_runtime_archive():
    source = authored()
    metadata = native_authored_metadata(source)
    runtime = {path: source.files[path] for path in ("functions/probe.py", "modules/runtime.py")}
    assert require_native_python_closure(source, runtime, {"functions/probe.py"}, has_table_bindings=True) == [
        "shared/__init__.py", "shared/microsoft/__init__.py"]
    assert len(source.file_manifest()) == 8
    assert metadata.readme == "Reviewed source instructions\n"
    assert metadata.descriptor.allow_outbound_access is False
    assert metadata.workflows[0]["id"] == WID
    assert metadata.tables[0]["policies"] == [{"$ref": "admin_bypass"}]


@pytest.mark.parametrize("change", [
    {"shared/__init__.py": b"print('new behavior')\n"},
    {"unused.py": b"NEW_BEHAVIOR = True\n"},
    {"modules/runtime.py": b"def helper():\n    return 2\n"},
])
def test_runtime_success_cannot_credit_unmapped_or_different_python(change):
    baseline = authored()
    runtime = {path: baseline.files[path] for path in ("functions/probe.py", "modules/runtime.py")}
    with pytest.raises(NativeAuthoredSourceMismatch):
        require_native_python_closure(authored(**change), runtime, {"functions/probe.py"}, has_table_bindings=True)


def test_empty_initializer_is_not_ignored_when_it_is_a_dependency():
    source = authored(**{"functions/probe.py": b"import shared\ndef probe():\n    return shared\n"})
    with pytest.raises(NativeAuthoredSourceMismatch, match="dependency closure"):
        require_native_python_closure(source, {"functions/probe.py": source.files["functions/probe.py"]},
            {"functions/probe.py"}, has_table_bindings=True)


@pytest.mark.parametrize("path", ["src/index.tsx", "dist/index.html", ".bifrost/apps.yaml", "settings.json"])
def test_apps_and_other_components_do_not_become_workflow_source(path):
    with pytest.raises(NativeAuthoredSourceMismatch, match="different delivery component"):
        native_authored_metadata(authored(**{path: b"not runtime Python"}))


@pytest.mark.parametrize("content", [
    b"slug: fixture\nslug: other\nname: Fixture\nrepo_subpath: solutions/fixture\n",
    b"slug: fixture\nname: Fixture\nrepo_subpath: solutions/fixture\nnew_grant: true\n",
    b"slug: fixture\nname: Fixture\nrepo_subpath: solutions/fixture\nglobal_repo_access: false\nallow_outbound_access: true\n",
    b"slug: &slug fixture\nname: *slug\nrepo_subpath: solutions/fixture\n",
])
def test_unknown_or_ambiguous_metadata_remains_unproven(content):
    with pytest.raises(NativeAuthoredSourceMismatch):
        native_authored_metadata(authored(**{"bifrost.solution.yaml": content}))


def test_missing_and_empty_readme_are_distinct():
    assert native_authored_metadata(authored(**{"README.md": None})).readme is None
    assert native_authored_metadata(authored(**{"README.md": b""})).readme == ""


def test_readme_uses_existing_text_import_semantics_without_changing_byte_evidence():
    source = authored(**{"README.md": b"one\r\ntwo\rthree\n"})
    assert native_authored_metadata(source).readme == "one\ntwo\nthree\n"
    assert next(item.sha256 for item in source.source_files if item.path.endswith("/README.md")) == hashlib.sha256(
        b"one\r\ntwo\rthree\n").hexdigest()


@pytest.mark.asyncio
async def test_stale_readme_cannot_be_credited_by_a_green_runtime():
    source = authored()
    metadata = native_authored_metadata(source)
    solution = SimpleNamespace(**metadata.descriptor.model_dump(exclude={"logo"}), readme="Earlier instructions\n", id=SID)
    db = AsyncMock()
    with pytest.raises(NativeAuthoredSourceMismatch, match="README"):
        await native_authored_install_readback(db, solution, source,
            expected_active_deployment_id=SID, expected_active_manifest_hash="sha256:" + "1" * 64)
    db.scalars.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("version", "99.0.0"), ("allow_outbound_access", True), ("allow_inbound_access", False),
    ("repo_subpath", "solutions/other"), ("git_connected", True), ("name", "Other name"),
])
async def test_descriptor_drift_does_not_allow_accounting(field, value):
    source = authored()
    metadata = native_authored_metadata(source)
    values = {**metadata.descriptor.model_dump(exclude={"logo"}), "readme": metadata.readme, "id": SID}
    values[field] = value
    db = AsyncMock()
    with pytest.raises(NativeAuthoredSourceMismatch, match="descriptor"):
        await native_authored_install_readback(db, SimpleNamespace(**values), source,
            expected_active_deployment_id=SID, expected_active_manifest_hash="sha256:" + "1" * 64)
    db.commit.assert_not_awaited()
