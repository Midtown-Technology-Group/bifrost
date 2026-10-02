"""Build the two frozen reference recipes through the public static compiler.

No carried module is imported. This checks reviewed source metadata, not live
installation, caller authority, source-commit custody or runtime enforcement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from bifrost.solution_delivery_review import (
    CompiledWorkflowRegistration,
    ReviewedRuntimeBounds,
    ReviewedWorkflowRecipe,
    ReviewedWorkflowRegistration,
    WorkflowRegistrationControls,
    compile_workflow_registrations,
    review_workflow_recipe,
)

_PROVIDER = UUID("00000000-0000-0000-0000-000000000002")
_SOURCE_PINS = (
    (
        "reference/bootstrap.py",
        219,
        "d4e12e1ab615015aa9c3dad267eb7f99096e25a69c96eac9183554d47b2117bc",
    ),
    (
        "features/cove/__init__.py",
        23,
        "0498ef9c35aad1478ab89f334894b7bd3bb418b6901356530ad3e9384fb595a0",
    ),
    (
        "features/cove/workflows/recovery_steward.py",
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
_BOOTSTRAP_PATH = _SOURCE_PINS[0][0]
_TOOL_PATH = _SOURCE_PINS[2][0]
_ENTRIES = (
    (UUID("81c8961e-cedb-4d51-831b-d29a75e99cb9"), "c1_r_reference_bootstrap"),
    (UUID("0760d416-be3a-4a33-b540-5b6b0658075d"), "recovery_steward_inspect_capacity"),
    (UUID("93b7f115-de11-444d-8309-7aee9bac2bec"), "recovery_steward_preview_restore"),
)
_SCHEMA = "bifrost.solution-workflow-delivery/v1"
_FAILURE = "agent_reference_recipe_invalid"


class RecipeError(ValueError):
    """A frozen reference input or public static compiler projection differs."""


@dataclass(frozen=True)
class RecipePair:
    initial: ReviewedWorkflowRecipe
    revision: ReviewedWorkflowRecipe


def _controls(role_id: UUID | None) -> dict[str, Any]:
    return {
        "display_name": None,
        "execution_mode": "async",
        "timeout_seconds": 60,
        "cache_ttl_seconds": 0,
        "time_saved": 0,
        "value": 0.0,
        "endpoint_enabled": False,
        "public_endpoint": False,
        "allowed_methods": ["POST"],
        "disable_global_key": False,
        "access_level": "role_based",
        "role_ids": [] if role_id is None else [str(role_id)],
        "retry_policy": {
            "version": "execution-retry/v1",
            "enabled": False,
            "max_attempts": 2,
            "retry_on": [],
        },
    }


def _bounds(tool: bool) -> dict[str, int]:
    return {
        "max_duration_seconds": 60,
        "max_external_calls": 16 if tool else 1,
        "max_records_read": 200 if tool else 1,
        "max_output_bytes": 4096,
    }


def _recipe(
    solution_id: UUID, role_id: UUID, *, revision: bool
) -> ReviewedWorkflowRecipe:
    workflows = []
    for index, (identity, function) in enumerate(
        _ENTRIES if revision else _ENTRIES[:1]
    ):
        workflows.append(
            ReviewedWorkflowRegistration(
                id=identity,
                path=_TOOL_PATH if index else _BOOTSTRAP_PATH,
                function_name=function,
                organization_id=_PROVIDER,
                runtime_bounds=ReviewedRuntimeBounds(
                    **_bounds(bool(index)),
                    max_records_written=None,
                    max_output_rows=None,
                    max_pages=None,
                ),
                controls=WorkflowRegistrationControls.model_validate(
                    _controls(role_id if index else None)
                ),
            )
        )
    return ReviewedWorkflowRecipe(
        schema_version=_SCHEMA,
        solution_id=solution_id,
        files={
            path: "test-fixtures/agent-reference/" + path
            for path, _size, _digest in (_SOURCE_PINS if revision else _SOURCE_PINS[:2])
        },
        workflows=workflows,
        shared_tables={},
        resources={},
        root_file_bindings={},
    )


def _parameters(preview: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    if preview:
        schema["properties"] = {
            "device_id": {"type": "integer", "title": "Device Id"},
            "recovery_agent_id": {"type": "integer", "title": "Recovery Agent Id"},
            "vm_name": {"type": "string", "title": "Vm Name"},
            "halo_ticket_id": {"type": "integer", "title": "Halo Ticket Id"},
            "cpu_count": {"type": "integer", "title": "Cpu Count", "default": 2},
            "ram_size_mb": {"type": "integer", "title": "Ram Size Mb", "default": 4096},
            "vhd_path": {"type": "string", "title": "Vhd Path", "default": "E:\\"},
        }
        schema["required"] = [
            "device_id",
            "recovery_agent_id",
            "vm_name",
            "halo_ticket_id",
        ]
    return schema


def _definition(index: int, role_id: UUID) -> dict[str, Any]:
    tool = index != 0
    descriptions = (
        "",
        "Read-only inventory of recovery locations and active Cove restores.",
        "Read-only compatibility and restore-point preview with a stable approval fingerprint.",
    )
    tags = ["cove", "recovery", "agent-tool", "read-only"] if tool else []
    if index == 2:
        tags.append("preview")
    return {
        **_controls(role_id if tool else None),
        "path": _TOOL_PATH if tool else _BOOTSTRAP_PATH,
        "function_name": _ENTRIES[index][1],
        "name": _ENTRIES[index][1],
        "description": descriptions[index],
        "category": "Cove Data Protection" if tool else "General",
        "tags": tags,
        "type": "tool" if tool else "workflow",
        "tool_description": descriptions[index] if tool else None,
        "organization_id": str(_PROVIDER),
        "parameters_schema": _parameters(index == 2),
        "runtime_bounds": _bounds(tool),
        "parameters_schema_contract": "bifrost.workflow-parameters-schema/v1",
        "effects": None,
        "source_enforced_bounds": None,
        "source_requested_bounds": None,
    }


def _same(actual: Any, expected: Any) -> bool:
    """Compare the frozen projections without bool/int or int/float coercion."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _check_compiled(
    compiled: dict[str, CompiledWorkflowRegistration], role_id: UUID, *, revision: bool
) -> None:
    count = 3 if revision else 1
    expected_refs = {
        f"{_TOOL_PATH if index else _BOOTSTRAP_PATH}::{function}"
        for index, (_identity, function) in enumerate(_ENTRIES[:count])
    }
    if type(compiled) is not dict or set(compiled) != expected_refs:
        raise RecipeError(_FAILURE)
    for index, (identity, function) in enumerate(_ENTRIES[:count]):
        path, _size, digest = _SOURCE_PINS[2 if index else 0]
        ref = f"{path}::{function}"
        item = compiled[ref]
        if (
            type(item) is not CompiledWorkflowRegistration
            or type(item.resolved_id) is not UUID
            or item.resolved_id != identity
            or item.portable_ref != ref
            or item.source_ref != path
            or item.source_hash != "sha256:" + digest
            or not _same(item.definition, _definition(index, role_id))
        ):
            raise RecipeError(_FAILURE)


def _check_review(review: dict[str, Any], solution_id: UUID, *, revision: bool) -> None:
    expected = {
        "schema_version": "bifrost.solution-delivery-review/v1",
        "solution_id": str(solution_id),
        "source_paths": sorted(
            path for path, _, _ in (_SOURCE_PINS if revision else _SOURCE_PINS[:2])
        ),
        "resource_paths": [],
        "workflow_ids": sorted(
            str(identity) for identity, _ in (_ENTRIES if revision else _ENTRIES[:1])
        ),
        "previous_recipe_checked": revision,
        "live_state_verified": False,
        "runtime_verified": False,
    }
    if not _same(review, expected):
        raise RecipeError(_FAILURE)


def build_reviewed_recipe_pair(
    solution_id: UUID, role_id: UUID, sources: dict[str, bytes]
) -> RecipePair:
    """Require the exact five carried sources and public static R1→R2 review.

    The returned public models are frozen by their existing contracts; their
    nested containers retain normal public-model semantics. No live fact or
    source commit is minted here. Every call constructs fresh recipes.
    """
    if (
        type(solution_id) is not UUID
        or type(role_id) is not UUID
        or type(sources) is not dict
        or len(sources) != len(_SOURCE_PINS)
        or any(type(path) is not str for path in sources)
        or set(sources) != {path for path, _, _ in _SOURCE_PINS}
    ):
        raise RecipeError(_FAILURE)
    files = dict(sources)
    for path, size, digest in _SOURCE_PINS:
        raw = files[path]
        if (
            type(raw) is not bytes
            or len(raw) != size
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise RecipeError(_FAILURE)
    initial_files = {path: files[path] for path, _, _ in _SOURCE_PINS[:2]}
    try:
        initial = _recipe(solution_id, role_id, revision=False)
        revision = _recipe(solution_id, role_id, revision=True)
        _check_compiled(
            compile_workflow_registrations(initial, initial_files),
            role_id,
            revision=False,
        )
        _check_compiled(
            compile_workflow_registrations(revision, files), role_id, revision=True
        )
        _check_review(
            review_workflow_recipe(initial.model_dump(mode="json"), initial_files, {}),
            solution_id,
            revision=False,
        )
        _check_review(
            review_workflow_recipe(
                revision.model_dump(mode="json"),
                files,
                {},
                previous_recipe_value=initial.model_dump(mode="json"),
                previous_files=initial_files,
                previous_resources={},
            ),
            solution_id,
            revision=True,
        )
        return RecipePair(initial=initial, revision=revision)
    except (ValueError, TypeError, KeyError, SyntaxError, RecursionError):
        pass
    # Outside the catch: no compiler/parser exception or carried value retained
    # as __context__/__cause__ in the public static classification.
    raise RecipeError(_FAILURE)
