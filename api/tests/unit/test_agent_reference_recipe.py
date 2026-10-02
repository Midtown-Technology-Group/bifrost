"""Real public static compilation of carried assets; no nominal execution proof."""

from __future__ import annotations

import builtins
import hashlib
import importlib.abc
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from bifrost.solution_delivery_review import (
    ReviewedWorkflowRecipe,
    WorkflowRecipeError,
    compile_workflow_registrations,
    review_workflow_recipe,
)
from bifrost.solution_source_closure import source_closure
from tests.e2e.platform import agent_reference_recipe as recipe

SOLUTION = UUID("11111111-2222-4333-8444-555555555555")
ROLE = UUID("22222222-3333-4444-8555-666666666666")
ORG = UUID("00000000-0000-0000-0000-000000000002")
BOOTSTRAP = UUID("81c8961e-cedb-4d51-831b-d29a75e99cb9")
CAPACITY = UUID("0760d416-be3a-4a33-b540-5b6b0658075d")
PREVIEW = UUID("93b7f115-de11-444d-8309-7aee9bac2bec")
BOOTSTRAP_PATH = "reference/bootstrap.py"
TOOL_PATH = "features/cove/workflows/recovery_steward.py"
PINS = (
    (
        BOOTSTRAP_PATH,
        219,
        "d4e12e1ab615015aa9c3dad267eb7f99096e25a69c96eac9183554d47b2117bc",
    ),
    (
        "features/cove/__init__.py",
        23,
        "0498ef9c35aad1478ab89f334894b7bd3bb418b6901356530ad3e9384fb595a0",
    ),
    (
        TOOL_PATH,
        17642,
        "71bc320261ab6f6da2abb3b4f1bc3335fc9925ae28ac9e922a1f260e378529eb",
    ),
    (
        "features/cove/workflows/recovery_testing.py",
        32957,
        "cf0876ecf4fd793f709e6070771e5c0f84dce34128581cf68049a1cce844879a",
    ),
    (
        "modules/cove.py",
        60566,
        "778ec86607025eabf60efb768cf7f05b641a13532ea1686c19709211f5f52133",
    ),
)
MARKER = "synthetic-source-must-not-be-disclosed"


@pytest.fixture
def sources() -> dict[str, bytes]:
    # Supported /app units require the canonical RO mount, with no skip or
    # embedded source. Outside containers this is the identical full checkout.
    unit_path = Path(__file__).resolve()
    assets = (
        Path("/app/reference-assets")
        if unit_path.is_relative_to("/app")
        else unit_path.parents[3] / "test-fixtures/agent-reference"
    )
    carried = {path: (assets / path).read_bytes() for path, _, _ in PINS}
    for path, size, digest in PINS:
        assert len(carried[path]) == size
        assert hashlib.sha256(carried[path]).hexdigest() == digest
    return carried


def _denied(sources: Any, solution: Any = SOLUTION, role: Any = ROLE) -> None:
    with pytest.raises(recipe.RecipeError) as caught:
        recipe.build_reviewed_recipe_pair(solution, role, sources)
    assert caught.value.args == ("agent_reference_recipe_invalid",)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert MARKER not in str(caught.value)


def _controls(tool: bool) -> dict[str, Any]:
    return {
        "display_name": None,
        "execution_mode": "async",
        "timeout_seconds": 60,
        "cache_ttl_seconds": 0,
        "time_saved": 0,
        "value": 0.0,
        "retry_policy": {
            "version": "execution-retry/v1",
            "enabled": False,
            "max_attempts": 2,
            "retry_on": [],
        },
        "endpoint_enabled": False,
        "public_endpoint": False,
        "allowed_methods": ["POST"],
        "disable_global_key": False,
        "access_level": "role_based",
        "role_ids": [str(ROLE)] if tool else [],
    }


def test_actual_public_recipes_have_exact_selected_metadata_and_retained_bootstrap(
    sources,
):
    before = dict(sources)
    pair = recipe.build_reviewed_recipe_pair(SOLUTION, ROLE, sources)
    assert isinstance(pair.initial, ReviewedWorkflowRecipe)
    assert isinstance(pair.revision, ReviewedWorkflowRecipe)
    assert pair.initial.workflows == pair.revision.workflows[:1]
    for stage, count in ((pair.initial, 2), (pair.revision, 5)):
        assert stage.schema_version == "bifrost.solution-workflow-delivery/v1"
        assert stage.solution_id == SOLUTION
        assert stage.files == {
            path: "test-fixtures/agent-reference/" + path for path, _, _ in PINS[:count]
        }
        assert stage.shared_tables == stage.resources == stage.root_file_bindings == {}
        assert "source_commit_sha" not in stage.model_dump(mode="json")
    assert [entry.id for entry in pair.initial.workflows] == [BOOTSTRAP]
    assert [entry.id for entry in pair.revision.workflows] == [
        BOOTSTRAP,
        CAPACITY,
        PREVIEW,
    ]
    assert [entry.function_name for entry in pair.revision.workflows] == [
        "c1_r_reference_bootstrap",
        "recovery_steward_inspect_capacity",
        "recovery_steward_preview_restore",
    ]
    for index, entry in enumerate(pair.revision.workflows):
        assert entry.path == (TOOL_PATH if index else BOOTSTRAP_PATH)
        assert entry.organization_id == ORG
        assert entry.controls.model_dump(mode="json") == _controls(bool(index))
        assert entry.runtime_bounds.model_dump() == {
            "max_duration_seconds": 60,
            "max_external_calls": 16 if index else 1,
            "max_records_read": 200 if index else 1,
            "max_output_bytes": 4096,
            "max_records_written": None,
            "max_output_rows": None,
            "max_pages": None,
        }
    assert sources == before
    with pytest.raises(FrozenInstanceError):
        setattr(pair, "initial", pair.revision)
    with pytest.raises(ValueError):
        setattr(pair.initial, "solution_id", ROLE)


def test_actual_closures_and_public_revision_review_are_exact_and_static(sources):
    pair = recipe.build_reviewed_recipe_pair(SOLUTION, ROLE, sources)
    initial_files = {path: sources[path] for path, _, _ in PINS[:2]}
    assert sum(map(len, initial_files.values())) == 242
    assert sum(map(len, sources.values())) == 111407
    assert source_closure(initial_files, {BOOTSTRAP_PATH}) == initial_files
    assert source_closure(sources, {BOOTSTRAP_PATH, TOOL_PATH}) == sources
    for stage, files, previous in (
        (pair.initial, initial_files, {}),
        (
            pair.revision,
            sources,
            {
                "previous_recipe_value": pair.initial.model_dump(mode="json"),
                "previous_files": initial_files,
                "previous_resources": {},
            },
        ),
    ):
        observed = review_workflow_recipe(
            stage.model_dump(mode="json"), files, {}, **previous
        )
        assert observed == {
            "schema_version": "bifrost.solution-delivery-review/v1",
            "solution_id": str(SOLUTION),
            "source_paths": sorted(files),
            "resource_paths": [],
            "workflow_ids": sorted(str(entry.id) for entry in stage.workflows),
            "previous_recipe_checked": stage is pair.revision,
            "live_state_verified": False,
            "runtime_verified": False,
        }


def test_actual_compiler_preserves_root_schema_titles_required_defaults_and_source(
    sources,
):
    pair = recipe.build_reviewed_recipe_pair(SOLUTION, ROLE, sources)
    compiled = compile_workflow_registrations(pair.revision, sources)
    assert set(compiled) == {
        BOOTSTRAP_PATH + "::c1_r_reference_bootstrap",
        TOOL_PATH + "::recovery_steward_inspect_capacity",
        TOOL_PATH + "::recovery_steward_preview_restore",
    }
    no_args = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    for item in compiled.values():
        assert item.source_ref == item.definition["path"]
        assert (
            item.portable_ref
            == item.source_ref + "::" + item.definition["function_name"]
        )
        assert (
            item.source_hash
            == "sha256:" + hashlib.sha256(sources[item.source_ref]).hexdigest()
        )
        assert item.definition["organization_id"] == str(ORG)
        assert item.definition["effects"] is None
        assert item.definition["source_enforced_bounds"] is None
        assert item.definition["source_requested_bounds"] is None
        assert (
            item.definition["parameters_schema_contract"]
            == "bifrost.workflow-parameters-schema/v1"
        )
        for key, value in _controls(item.resolved_id != BOOTSTRAP).items():
            assert item.definition[key] == value
    bootstrap = compiled[BOOTSTRAP_PATH + "::c1_r_reference_bootstrap"]
    assert bootstrap.resolved_id == BOOTSTRAP
    assert bootstrap.definition["parameters_schema"] == no_args
    assert bootstrap.definition["category"] == "General"
    assert bootstrap.definition["description"] == ""
    assert (
        bootstrap.definition["tags"] == []
        and bootstrap.definition["type"] == "workflow"
    )
    assert bootstrap.definition["tool_description"] is None
    capacity = compiled[TOOL_PATH + "::recovery_steward_inspect_capacity"]
    assert capacity.resolved_id == CAPACITY
    assert capacity.definition["parameters_schema"] == no_args
    assert (
        capacity.definition["description"]
        == "Read-only inventory of recovery locations and active Cove restores."
    )
    assert capacity.definition["tags"] == [
        "cove",
        "recovery",
        "agent-tool",
        "read-only",
    ]
    preview = compiled[TOOL_PATH + "::recovery_steward_preview_restore"]
    assert preview.resolved_id == PREVIEW
    assert preview.definition["parameters_schema"] == {
        **no_args,
        "properties": {
            "device_id": {"type": "integer", "title": "Device Id"},
            "recovery_agent_id": {"type": "integer", "title": "Recovery Agent Id"},
            "vm_name": {"type": "string", "title": "Vm Name"},
            "halo_ticket_id": {"type": "integer", "title": "Halo Ticket Id"},
            "cpu_count": {"type": "integer", "title": "Cpu Count", "default": 2},
            "ram_size_mb": {"type": "integer", "title": "Ram Size Mb", "default": 4096},
            "vhd_path": {"type": "string", "title": "Vhd Path", "default": "E:\\"},
        },
        "required": ["device_id", "recovery_agent_id", "vm_name", "halo_ticket_id"],
    }
    assert (
        preview.definition["description"]
        == "Read-only compatibility and restore-point preview with a stable approval fingerprint."
    )
    assert preview.definition["tags"] == [
        "cove",
        "recovery",
        "agent-tool",
        "read-only",
        "preview",
    ]
    for item in (capacity, preview):
        assert (
            item.definition["type"] == "tool"
            and item.definition["category"] == "Cove Data Protection"
        )
        assert item.definition["tool_description"] == item.definition["description"]


def test_authored_imports_are_blocked_without_changing_carried_files(
    sources, monkeypatch, tmp_path
):
    original_import = builtins.__import__
    attempts = []

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"features", "modules", "reference"}:
            attempts.append(name)
            raise AssertionError("authored import attempted")
        return original_import(name, *args, **kwargs)

    class BlockAuthored(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"features", "modules", "reference"}:
                attempts.append(fullname)
                raise AssertionError("authored import attempted")
            return None

    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.setattr(sys, "meta_path", [BlockAuthored(), *sys.meta_path])
    monkeypatch.chdir(tmp_path)
    before = dict(sources)
    pair = recipe.build_reviewed_recipe_pair(SOLUTION, ROLE, sources)
    assert len(pair.revision.workflows) == 3
    assert sources == before and attempts == []


@pytest.mark.parametrize("argument", ["solution", "role"])
@pytest.mark.parametrize("value", [None, True, 1, str(SOLUTION), SOLUTION.bytes])
def test_ids_require_actual_uuid_instances_before_compiler(
    sources, monkeypatch, argument, value
):
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid identity reached public compilation")

    monkeypatch.setattr(recipe, "compile_workflow_registrations", unexpected)
    _denied(
        sources,
        value if argument == "solution" else SOLUTION,
        value if argument == "role" else ROLE,
    )


@pytest.mark.parametrize("path", [path for path, _, _ in PINS])
@pytest.mark.parametrize(
    "damage", ["missing", "one_byte", "length", "text", "bytearray"]
)
def test_each_source_key_byte_digest_and_exact_type_is_required(
    sources, monkeypatch, path, damage
):
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid carried source reached public compilation")

    monkeypatch.setattr(recipe, "compile_workflow_registrations", unexpected)
    changed: dict[str, Any] = dict(sources)
    raw = changed[path]
    if damage == "missing":
        del changed[path]
    elif damage == "one_byte":
        changed[path] = bytes([raw[0] ^ 1]) + raw[1:]
    elif damage == "length":
        changed[path] = raw + MARKER.encode()
    elif damage == "text":
        changed[path] = raw.decode()
    else:
        changed[path] = bytearray(raw)
    _denied(changed)


@pytest.mark.parametrize("damage", ["extra", "renamed", "nonstring_key", "not_dict"])
def test_source_map_is_closed(sources, damage):
    changed: Any = dict(sources)
    if damage == "extra":
        changed["unused.py"] = MARKER.encode()
    elif damage == "renamed":
        changed["wrong.py"] = changed.pop(BOOTSTRAP_PATH)
    elif damage == "nonstring_key":
        changed[1] = changed.pop(BOOTSTRAP_PATH)
    else:
        changed = list(changed.items())
    _denied(changed)


@pytest.mark.parametrize("revision", [False, True])
@pytest.mark.parametrize(
    "damage",
    [
        "extra",
        "missing",
        "id",
        "portable_ref",
        "source_ref",
        "source_hash",
        "name",
        "schema",
        "default",
        "effects",
        "bound",
        "source_bound",
        "role",
        "retry",
        "coercion",
    ],
)
def test_builder_rejects_actual_compiler_projection_drift(
    sources, monkeypatch, revision, damage
):
    def drift(stage, files):
        actual = compile_workflow_registrations(stage, files)
        if (len(stage.workflows) == 3) != revision:
            return actual
        ref = next(reversed(actual))
        item = actual[ref]
        if damage == "extra":
            actual["extra.py::extra"] = item
        elif damage == "missing":
            del actual[ref]
        elif damage in {"id", "portable_ref", "source_ref", "source_hash"}:
            field = "resolved_id" if damage == "id" else damage
            actual[ref] = replace(item, **{field: ROLE if damage == "id" else MARKER})
        else:
            definition = deepcopy(item.definition)
            if damage == "name":
                definition["name"] = "unselected"
            elif damage == "schema":
                del definition["parameters_schema"]["$schema"]
            elif damage == "default":
                if revision:
                    definition["parameters_schema"]["properties"]["vhd_path"][
                        "default"
                    ] = "wrong"
                else:
                    definition["parameters_schema"]["required"] = []
            elif damage == "effects":
                definition["effects"] = []
            elif damage == "bound":
                definition["runtime_bounds"]["max_external_calls"] += 1
            elif damage == "source_bound":
                definition["source_enforced_bounds"] = {"max_duration_seconds": 60}
            elif damage == "role":
                definition["role_ids"] = [] if revision else [str(ROLE)]
            elif damage == "retry":
                definition["retry_policy"]["enabled"] = True
            else:
                definition["endpoint_enabled"] = 0
            actual[ref] = replace(item, definition=definition)
        return actual

    monkeypatch.setattr(recipe, "compile_workflow_registrations", drift)
    _denied(sources)


@pytest.mark.parametrize("revision", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_ids", []),
        ("source_paths", []),
        ("resource_paths", ["extra.json"]),
        ("solution_id", str(ROLE)),
        ("previous_recipe_checked", None),
        ("live_state_verified", True),
        ("runtime_verified", 0),
        ("extra", MARKER),
    ],
)
def test_builder_rejects_review_drift_and_false_runtime_claims(
    sources, monkeypatch, revision, field, value
):
    def drift(stage, files, resources, **previous):
        actual = review_workflow_recipe(stage, files, resources, **previous)
        if bool(previous) == revision:
            actual[field] = value
        return actual

    monkeypatch.setattr(recipe, "review_workflow_recipe", drift)
    _denied(sources)


@pytest.mark.parametrize("compiler", [True, False])
def test_compiler_reviewer_failures_have_no_chained_source_values(
    sources, monkeypatch, compiler
):
    def failed(*_args, **_kwargs):
        raise WorkflowRecipeError(MARKER)

    monkeypatch.setattr(
        recipe,
        "compile_workflow_registrations" if compiler else "review_workflow_recipe",
        failed,
    )
    _denied(sources)


def test_public_review_rejects_extra_closure_and_bootstrap_removal(sources):
    pair = recipe.build_reviewed_recipe_pair(SOLUTION, ROLE, sources)
    initial_files = {path: sources[path] for path, _, _ in PINS[:2]}
    extra = pair.revision.model_dump(mode="json")
    extra["files"]["unused.py"] = "test-fixtures/agent-reference/unused.py"
    with pytest.raises(WorkflowRecipeError, match="complete dependency closure"):
        review_workflow_recipe(extra, {**sources, "unused.py": b"pass\n"}, {})
    removed = pair.revision.model_dump(mode="json")
    removed["workflows"] = removed["workflows"][1:]
    removed["files"].pop(BOOTSTRAP_PATH)
    removed["files"].pop("features/cove/__init__.py")
    without_bootstrap = {
        path: raw for path, raw in sources.items() if path in removed["files"]
    }
    with pytest.raises(WorkflowRecipeError, match="Workflow removal"):
        review_workflow_recipe(
            removed,
            without_bootstrap,
            {},
            previous_recipe_value=pair.initial.model_dump(mode="json"),
            previous_files=initial_files,
            previous_resources={},
        )


def test_each_call_is_fresh_even_after_public_nested_containers_are_mutated(sources):
    first = recipe.build_reviewed_recipe_pair(SOLUTION, ROLE, sources)
    first.initial.files["unselected.py"] = "unselected.py"
    first.initial.workflows[0].controls.role_ids.append(ROLE)
    second = recipe.build_reviewed_recipe_pair(SOLUTION, ROLE, sources)
    assert set(second.initial.files) == {path for path, _, _ in PINS[:2]}
    assert second.initial.workflows[0].controls.role_ids == []
    assert second.initial.workflows[0] == second.revision.workflows[0]
