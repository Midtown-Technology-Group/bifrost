"""A native retirement marker proves the exact terminal registration state."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from bifrost.manifest import ManifestWorkflow
from bifrost.workspace_release import canonical_digest
from src.models.orm.workflows import Workflow
from src.services.workflow_registration_retirement import (
    WorkflowRetirementEvidenceError,
    build_workflow_retirement_evidence,
    validate_workflow_retirement_evidence,
    workflow_retirement_snapshot_hash,
)


def _workflow():
    return Workflow(
        id=uuid4(), path="features\\fixture\\workflow.py", function_name="retire",
        name="Retired fixture", type="workflow", organization_id=None, solution_id=None,
        is_active=True, roles=[], endpoint_enabled=False, public_endpoint=False,
        api_key_enabled=False, access_level="role_based", timeout_seconds=60,
        execution_mode="sync", time_saved=0, value=0, cache_ttl_seconds=0,
        parameters_schema={}, display_name=None, description=None, category="General",
        tags=[], allowed_methods=["POST"], disable_global_key=True, retry_policy=None,
        tool_description=None, retirement_evidence=None,
    )


def _retire(row=None, **changes):
    row = row or _workflow()
    before = workflow_retirement_snapshot_hash(row)
    row.is_active = False
    arguments = {
        "release_id": "sha256:" + "a" * 64, "artifact_id": uuid4(),
        "before_registration_hash": before, "caller_inventory_digest": "sha256:" + "b" * 64,
        "review_digest": "sha256:" + "c" * 64, "reason": "Reviewed obsolete registration",
        "retired_by_user_id": uuid4(), "retired_at": datetime(2026, 10, 3, tzinfo=UTC),
    }
    arguments.update(changes)
    row.retirement_evidence = build_workflow_retirement_evidence(row, **arguments)
    return row


def test_native_marker_retains_exact_identity_and_hashes_without_credentials():
    row = _workflow()
    row.api_key_hash = "d" * 64
    row.api_key_description = "credential description"
    before = workflow_retirement_snapshot_hash(row)
    retired = _retire(row)
    evidence = validate_workflow_retirement_evidence(retired)
    assert evidence["identity"] == {
        "workflow_id": str(row.id), "raw_path": "features\\fixture\\workflow.py",
        "path": "features/fixture/workflow.py", "function_name": "retire",
        "organization_id": None,
    }
    assert evidence["before_registration_hash"] == before
    assert evidence["after_registration_hash"] != before
    assert evidence["after_registration_hash"] == workflow_retirement_snapshot_hash(row)
    assert row.api_key_hash == "d" * 64
    assert "credential description" not in str(evidence) and "d" * 64 not in str(evidence)
    assert "retirement_evidence" not in ManifestWorkflow.model_fields


def test_ordinary_inactive_registration_is_not_retired():
    row = _workflow()
    row.is_active = False
    with pytest.raises(WorkflowRetirementEvidenceError, match="absent or malformed"):
        validate_workflow_retirement_evidence(row)


@pytest.mark.parametrize("field,value", [
    ("is_active", True), ("solution_id", uuid4()), ("endpoint_enabled", True),
    ("public_endpoint", True), ("api_key_enabled", True),
])
def test_marker_requires_loose_inactive_registration_and_closed_admission(field, value):
    row = _retire()
    setattr(row, field, value)
    with pytest.raises(WorkflowRetirementEvidenceError, match="closed admission"):
        validate_workflow_retirement_evidence(row)


@pytest.mark.parametrize("field,value", [
    ("id", uuid4()), ("organization_id", uuid4()),
    ("path", "features/fixture/workflow.py"), ("function_name", "replacement"),
])
def test_marker_cannot_follow_identity_drift_even_when_normalized_path_is_equal(field, value):
    row = _retire()
    setattr(row, field, value)
    with pytest.raises(WorkflowRetirementEvidenceError, match="identity changed"):
        validate_workflow_retirement_evidence(row)


@pytest.mark.parametrize("field,value", [
    ("name", "Changed"), ("access_level", "authenticated"),
    ("parameters_schema", {"changed": True}), ("allowed_methods", ["GET"]),
])
def test_inactive_metadata_edits_preserve_historical_retirement_evidence(field, value):
    row = _retire()
    setattr(row, field, value)
    retained = row.retirement_evidence
    assert validate_workflow_retirement_evidence(row) == retained
    assert workflow_retirement_snapshot_hash(row) != retained["after_registration_hash"]


@pytest.mark.parametrize("field,value", [
    ("schema_version", "unreviewed"), ("release_id", "not-a-release"),
    ("artifact_id", "not-a-uuid"), ("retired_by_user_id", "not-a-uuid"),
    ("retired_at", "2026-10-03T00:00:00"), ("reason", " "),
    ("caller_inventory_digest", None), ("review_digest", "unreviewed"),
    ("before_registration_hash", "sha256:" + "f" * 63),
])
def test_rehashed_malformed_evidence_still_fails_closed(field, value):
    row = _retire()
    evidence = deepcopy(row.retirement_evidence)
    evidence[field] = value
    evidence["evidence_id"] = canonical_digest({key: value for key, value in evidence.items() if key != "evidence_id"})
    row.retirement_evidence = evidence
    with pytest.raises(WorkflowRetirementEvidenceError):
        validate_workflow_retirement_evidence(row)


def test_marker_tampering_or_unknown_fields_cannot_reuse_an_evidence_digest():
    row = _retire()
    assert row.retirement_evidence is not None
    row.retirement_evidence = {**row.retirement_evidence, "reason": "Changed review"}
    with pytest.raises(WorkflowRetirementEvidenceError, match="digest differs"):
        validate_workflow_retirement_evidence(row)
    row = _retire()
    assert row.retirement_evidence is not None
    row.retirement_evidence = {**row.retirement_evidence, "solution_id": str(uuid4())}
    with pytest.raises(WorkflowRetirementEvidenceError, match="malformed"):
        validate_workflow_retirement_evidence(row)


def test_builder_requires_first_disposition_and_valid_review_inputs():
    row = _retire()
    with pytest.raises(WorkflowRetirementEvidenceError, match="write-once"):
        _retire(row)
    with pytest.raises(WorkflowRetirementEvidenceError, match="requires an offset|actor, artifact or time"):
        _retire(retired_at=datetime(2026, 10, 3))
