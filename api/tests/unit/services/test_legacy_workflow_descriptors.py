"""Legacy display metadata cannot broaden immutable registration authorization."""

from copy import deepcopy
from uuid import uuid4

import pytest

from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_manifest import RuntimeEntityDefinition
from src.services.solutions.source_revision import (
    SolutionSourceRevisionError,
    _require_registration,
    _entrypoint_signature,
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
