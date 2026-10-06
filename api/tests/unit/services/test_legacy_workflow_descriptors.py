"""Legacy display metadata cannot broaden immutable registration authorization."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_manifest import RuntimeEntityDefinition
from src.services.solutions.source_revision import (
    SolutionSourceRevisionError,
    _entrypoint_signature,
    _require_registration,
    _workflow_snapshot,
    legacy_descriptor_evidence,
)


def test_source_only_signature_includes_resolved_parameter_defaults():
    row, _ = _registration()
    source = b"from bifrost import workflow\nDEFAULT_LIMIT = 10\n@workflow\nasync def run(limit: int = DEFAULT_LIMIT):\n return limit\n"
    before = _entrypoint_signature({row.path: source}, row)
    after = _entrypoint_signature({row.path: source.replace(b"DEFAULT_LIMIT = 10", b"DEFAULT_LIMIT = 20")}, row)
    assert before != after


def _registration():
    row = Workflow(
        id=uuid4(), path="workflows/task.py", function_name="run", name="Reviewed task",
        type="workflow", organization_id=None, is_active=True, roles=[],
        endpoint_enabled=False, public_endpoint=False, api_key_enabled=False,
        access_level="role_based", timeout_seconds=60, execution_mode="sync",
        time_saved=0, value=0, cache_ttl_seconds=0, parameters_schema={},
        display_name=None, description=None, category="General", tags=[],
        allowed_methods=["POST"], disable_global_key=True, retry_policy=None,
        tool_description=None,
    )
    definition = _workflow_snapshot(row)
    definition.update(description="Reviewed description", category="Reviewed category")
    return row, definition


def _entity(row, definition):
    return RuntimeEntityDefinition(portable_ref="task", resolved_id=row.id, definition=definition)


def test_nullable_descriptors_require_separate_exact_immutable_evidence():
    row, definition = _registration()
    with pytest.raises(SolutionSourceRevisionError, match="registration differs"):
        _require_registration(row, _entity(row, definition))
    definition["legacy_descriptor_evidence"] = legacy_descriptor_evidence(_workflow_snapshot(row))
    entity = _entity(row, definition)
    _require_registration(row, entity)
    assert row.description is None and row.category == "General"
    assert entity.definition["description"] == "Reviewed description"
    evidence = entity.definition["legacy_descriptor_evidence"]
    assert isinstance(evidence, dict)
    fields = evidence["fields"]
    assert isinstance(fields, dict)
    with pytest.raises(TypeError, match="immutable"):
        fields["description"] = ""


def test_larger_source_bound_cannot_exceed_retained_registration_timeout():
    from src.services.solutions.workflow_revision import retain_installed_timeouts

    row, definition = _registration()
    definition.update(timeout_seconds=120, runtime_bounds={"max_duration_seconds": 120},
        legacy_descriptor_evidence=legacy_descriptor_evidence(_workflow_snapshot(row)))
    before = {"task": _entity(row, definition)}
    with pytest.raises(SolutionSourceRevisionError, match="registration differs"):
        _require_registration(row, before["task"])
    retained = retain_installed_timeouts(before, [row])
    _require_registration(row, retained["task"])
    assert retained["task"].definition["timeout_seconds"] == row.timeout_seconds == 60


@pytest.mark.parametrize("duration,compiled_timeout", [(60, 30), (120, 30)])
def test_lower_recipe_timeout_cannot_leave_an_unverifiable_retained_registration(duration, compiled_timeout):
    from src.services.solutions.workflow_revision import retain_installed_timeouts

    row, definition = _registration()
    definition.update(timeout_seconds=compiled_timeout, runtime_bounds={"max_duration_seconds": duration},
        legacy_descriptor_evidence=legacy_descriptor_evidence(_workflow_snapshot(row)))
    with pytest.raises(SolutionSourceRevisionError, match="Recipe timeout conflicts"):
        retain_installed_timeouts({"task": _entity(row, definition)}, [row])


def test_reviewed_shorter_source_bound_matches_retained_registration_verification():
    from src.services.solutions.workflow_revision import retain_installed_timeouts

    row, definition = _registration()
    definition.update(timeout_seconds=30, runtime_bounds={"max_duration_seconds": 30},
        legacy_descriptor_evidence=legacy_descriptor_evidence(_workflow_snapshot(row)))
    retained = retain_installed_timeouts({"task": _entity(row, definition)}, [row])
    _require_registration(row, retained["task"])
    assert retained["task"].definition["timeout_seconds"] == 30 and row.timeout_seconds == 60


@pytest.mark.asyncio
async def test_workflow_successor_retains_legacy_metadata_timeout_and_new_effects():
    from src.services.solutions.workflow_revision import project_workflow_registrations, retain_legacy_descriptors

    row, old = _registration()
    old["legacy_descriptor_evidence"] = legacy_descriptor_evidence(_workflow_snapshot(row))
    old["runtime_bounds"] = {"max_duration_seconds": 30}
    old["timeout_seconds"] = 30
    desired = deepcopy(old)
    del desired["legacy_descriptor_evidence"]
    desired.update(effects=[{"kind": "integration.read", "target": "microsoft_csp"}],
        source_enforced_bounds=None, source_requested_bounds=None, parameters_schema_contract="test")
    previous = {"task": _entity(row, old)}
    successor = retain_legacy_descriptors({"task": _entity(row, desired)}, previous)
    entity = successor["task"]
    _require_registration(row, entity)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=1)))
    await project_workflow_registrations(db, uuid4(), successor, {row.id})
    parameters = db.execute.call_args.args[0].compile().params
    assert "timeout_seconds" not in parameters
    assert parameters["description"] is None and parameters["category"] == "General"
    assert "legacy_descriptor_evidence" not in parameters and "effects" not in parameters
    assert parameters["endpoint_enabled"] is False and parameters["public_endpoint"] is False
    effect_list = entity.definition["effects"]
    runtime_bounds = entity.definition["runtime_bounds"]
    assert isinstance(effect_list, list | tuple) and isinstance(effect_list[0], dict)
    assert isinstance(runtime_bounds, dict)
    assert effect_list[0]["target"] == "microsoft_csp"
    assert runtime_bounds["max_duration_seconds"] == 30
    assert row.timeout_seconds == 60
    assert retain_legacy_descriptors({"task": _entity(row, desired)}, successor) == successor


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["hash", "security_field", "missing_field"])
async def test_workflow_projection_rejects_changed_descriptor_evidence_before_write(damage):
    from src.services.solutions.workflow_revision import project_workflow_registrations

    row, definition = _registration()
    evidence = legacy_descriptor_evidence(_workflow_snapshot(row))
    if damage == "hash":
        evidence["content_hash"] = "sha256:" + "0" * 64
    elif damage == "security_field":
        evidence["fields"]["endpoint_enabled"] = True
    else:
        del evidence["fields"]["description"]
    definition.update(legacy_descriptor_evidence=evidence, runtime_bounds={}, effects=[],
        source_enforced_bounds=None, source_requested_bounds=None, parameters_schema_contract="test")
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(SolutionSourceRevisionError, match="descriptor projection evidence"):
        await project_workflow_registrations(db, uuid4(), {"task": _entity(row, definition)}, {row.id})
    db.execute.assert_not_called()


@pytest.mark.parametrize("change", ["hash", "schema", "fields", "security_field", "missing_source", "invalid_source"])
def test_malformed_descriptor_evidence_fails_closed(change):
    row, definition = _registration()
    evidence = legacy_descriptor_evidence(_workflow_snapshot(row))
    if change == "hash":
        evidence["content_hash"] = "sha256:" + "0" * 64
    elif change == "schema":
        evidence["schema_version"] = "other"
    elif change == "fields":
        evidence["fields"]["description"] = ""
    elif change == "security_field":
        evidence["fields"]["endpoint_enabled"] = True
    elif change == "missing_source":
        del definition["category"]
    elif change == "invalid_source":
        definition["description"] = None
    definition["legacy_descriptor_evidence"] = evidence
    with pytest.raises(SolutionSourceRevisionError):
        _require_registration(row, _entity(row, definition))


@pytest.mark.parametrize("field,value", [
    ("id", uuid4()), ("organization_id", uuid4()), ("path", "workflows/other.py"),
    ("function_name", "other"), ("description", ""), ("category", "Changed"),
    ("endpoint_enabled", True), ("public_endpoint", True), ("access_level", "authenticated"),
    ("disable_global_key", False), 
    ("parameters_schema", {"changed": True}),
])
def test_descriptor_evidence_does_not_hide_identity_scope_security_or_signature_drift(field, value):
    row, definition = _registration()
    definition["legacy_descriptor_evidence"] = legacy_descriptor_evidence(_workflow_snapshot(row))
    entity = _entity(row, deepcopy(definition))
    setattr(row, field, value)
    with pytest.raises(SolutionSourceRevisionError):
        _require_registration(row, entity)



def test_adoption_compiler_preserves_json_lists_before_freezing_descriptor_evidence():
    from bifrost.workflow_parameters import WorkflowParameterCompiler

    from src.services.solutions.reviewed_workflow_artifact import compile_reviewed_workflows
    from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe

    row, _ = _registration()
    recipe = ReviewedWorkflowRecipe.model_validate({
        "schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": str(uuid4()),
        "files": {row.path: "solutions/task.py"}, "workflows": [{
            "id": str(row.id), "path": row.path, "function_name": "run", "organization_id": None,
            "controls": {}, "runtime_bounds": {"max_duration_seconds": 60,
                "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096},
        }],
    })
    files = {row.path: b"from bifrost import workflow\n@workflow(name='Reviewed task', effects=[])\nasync def run():\n    return 1\n"}
    entities = compile_reviewed_workflows(recipe, files, {}, WorkflowParameterCompiler(),
        legacy_descriptor_snapshots={row.id: _workflow_snapshot(row)})
    entity = next(iter(entities.values()))
    marker = entity.definition["legacy_descriptor_evidence"]
    assert isinstance(marker, dict)
    assert marker["fields"] == {"description": None, "category": "General"}
    dumped = entity.model_dump(mode="json")
    assert dumped["definition"]["role_ids"] == []
    assert dumped["definition"]["effects"] == []
    assert dumped["definition"]["allowed_methods"] == ["POST"]


def test_adoption_compiler_seals_tags_and_tool_description_without_registry_projection():
    from bifrost.workflow_parameters import WorkflowParameterCompiler
    from bifrost.workspace_release import canonical_digest

    from src.services.solutions.reviewed_workflow_artifact import compile_reviewed_workflows
    from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe

    row, _ = _registration()
    row.type = "tool"
    row.retry_policy = {"version": "execution-retry/v1", "enabled": False, "max_attempts": 2, "retry_on": []}
    snapshot = _workflow_snapshot(row)
    controls = {key: snapshot[key] for key in (
        "display_name", "execution_mode", "timeout_seconds", "cache_ttl_seconds", "time_saved", "value",
        "retry_policy", "access_level", "role_ids", "endpoint_enabled", "public_endpoint", "allowed_methods",
        "disable_global_key",
    )}
    recipe = ReviewedWorkflowRecipe.model_validate({
        "schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": str(uuid4()),
        "files": {row.path: "solutions/task.py"}, "workflows": [{
            "id": str(row.id), "path": row.path, "function_name": "run", "organization_id": None,
            "controls": controls, "runtime_bounds": {"max_duration_seconds": 60,
                "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096},
        }],
    })
    files = {row.path: b"from bifrost import tool\n@tool(name='Reviewed task', description='New source help', tags=['reviewed'], effects=[])\nasync def run():\n    return 1\n"}
    unmarked = next(iter(compile_reviewed_workflows(recipe, files, {}, WorkflowParameterCompiler()).values()))
    row.parameters_schema = unmarked.model_dump(mode="json")["definition"]["parameters_schema"]
    baseline = _workflow_snapshot(row)
    entity = next(iter(compile_reviewed_workflows(recipe, files, {}, WorkflowParameterCompiler(),
        legacy_descriptor_snapshots={row.id: baseline}).values()))
    _require_registration(row, entity)
    evidence = entity.model_dump(mode="json")["definition"]["legacy_descriptor_evidence"]
    assert evidence == {
        "schema_version": "bifrost.solution-legacy-descriptors/v2",
        "fields": {"description": None, "category": "General", "tags": [], "tool_description": None},
        "content_hash": canonical_digest({"description": None, "category": "General", "tags": [], "tool_description": None}),
    }
    assert _workflow_snapshot(row) == baseline
    assert entity.model_dump(mode="json")["definition"]["tags"] == ["reviewed"]
    assert entity.definition["tool_description"] == "New source help"


@pytest.mark.parametrize("field,value", [
    ("tags", ["changed"]), ("tool_description", "changed"),
    ("organization_id", uuid4()), ("endpoint_enabled", True),
    ("access_level", "authenticated"), ("parameters_schema", {"changed": True}),
])
def test_extended_descriptor_evidence_still_rejects_metadata_scope_auth_and_signature_drift(field, value):
    row, definition = _registration()
    definition.update(tags=["source-tag"], tool_description="source help")
    definition["legacy_descriptor_evidence"] = legacy_descriptor_evidence(_workflow_snapshot(row), extended=True)
    entity = _entity(row, definition)
    setattr(row, field, value)
    with pytest.raises(SolutionSourceRevisionError):
        _require_registration(row, entity)


def test_v1_evidence_cannot_hide_new_tag_or_tool_description_drift():
    row, definition = _registration()
    definition.update(tags=["source-tag"], tool_description="source help")
    definition["legacy_descriptor_evidence"] = legacy_descriptor_evidence(_workflow_snapshot(row))
    with pytest.raises(SolutionSourceRevisionError, match="registration differs"):
        _require_registration(row, _entity(row, definition))


@pytest.mark.parametrize("change", ["hash", "schema", "security_field", "missing_field", "tags", "tool_description"])
def test_extended_evidence_shape_and_source_types_fail_closed(change):
    row, definition = _registration()
    definition.update(tags=["source-tag"], tool_description="source help")
    evidence = legacy_descriptor_evidence(_workflow_snapshot(row), extended=True)
    if change == "hash":
        evidence["content_hash"] = "sha256:" + "0" * 64
    elif change == "schema":
        evidence["schema_version"] = "other"
    elif change == "security_field":
        evidence["fields"]["endpoint_enabled"] = False
    elif change == "missing_field":
        del evidence["fields"]["tool_description"]
    elif change == "tags":
        definition["tags"] = [1]
    else:
        definition["tool_description"] = False
    definition["legacy_descriptor_evidence"] = evidence
    with pytest.raises(SolutionSourceRevisionError):
        _require_registration(row, _entity(row, definition))
