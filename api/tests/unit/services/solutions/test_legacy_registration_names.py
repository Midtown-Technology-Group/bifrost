"""Sealed caller names survive compatible delivery without permitting renames."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.reviewed_workflow_artifact import compile_reviewed_workflows
from src.services.solutions.source_revision import (
    SolutionSourceRevisionError,
    require_legacy_registration_name,
    retain_legacy_registration_names,
)
from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe, compile_workflow_registrations


def compile_pair():
    wid = uuid4()
    recipe = ReviewedWorkflowRecipe.model_validate({
        "schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": str(uuid4()),
        "files": {"task.py": "solutions/task.py"}, "workflows": [{
            "id": str(wid), "path": "task.py", "function_name": "run", "organization_id": None,
            "controls": {}, "runtime_bounds": {"max_duration_seconds": 30, "max_external_calls": 10,
                "max_records_read": 100, "max_output_bytes": 4096},
        }],
    })
    files = {"task.py": b"from bifrost import workflow\n@workflow(name='Friendly task', effects=[])\nasync def run():\n    return 1\n"}
    indexer = WorkflowIndexer(AsyncMock())
    original = compile_workflow_registrations(recipe, files, indexer)
    adopted = compile_reviewed_workflows(recipe, files, {}, indexer, legacy_registration_names={wid: "run"})
    return recipe, files, indexer, original, adopted


def test_native_adoption_keeps_caller_identity_and_authored_declaration_separate():
    _, _, _, original, adopted = compile_pair()
    before = next(iter(original.values())).model_dump(mode="json")["definition"]
    after = next(iter(adopted.values())).model_dump(mode="json")["definition"]
    assert before["name"] == "Friendly task" and after["name"] == "run"
    proof = require_legacy_registration_name(after)
    assert proof is not None
    assert proof["fields"] == {"installed_name": "run", "source_name": "Friendly task"}
    assert {k: v for k, v in after.items() if k not in {"name", "legacy_registration_name_evidence"}} == {
        k: v for k, v in before.items() if k != "name"
    }
    assert retain_legacy_registration_names(original, adopted) == adopted


def test_reviewed_successor_rejects_changed_source_name_even_with_same_uuid_and_controls():
    recipe, files, indexer, _, adopted = compile_pair()
    changed = {path: raw.replace(b"Friendly task", b"Renamed task") for path, raw in files.items()}
    desired = compile_workflow_registrations(recipe, changed, indexer)
    with pytest.raises(SolutionSourceRevisionError, match="source registration name changed"):
        retain_legacy_registration_names(desired, adopted)


@pytest.mark.parametrize("change", ["hash", "extra", "runtime_name", "empty", "schema", "null"])
def test_sealed_name_shape_rejects_tampering(change):
    _, _, _, _, adopted = compile_pair()
    definition = next(iter(adopted.values())).model_dump(mode="json")["definition"]
    proof = definition["legacy_registration_name_evidence"]
    if change == "hash":
        proof["content_hash"] = "sha256:" + "0" * 64
    elif change == "extra":
        proof["fields"]["access_level"] = "authenticated"
    elif change == "runtime_name":
        definition["name"] = "other"
    elif change == "empty":
        proof["fields"]["source_name"] = ""
    elif change == "null":
        definition["legacy_registration_name_evidence"] = None
    else:
        proof["schema_version"] = "unreviewed"
    with pytest.raises(SolutionSourceRevisionError):
        require_legacy_registration_name(definition)
