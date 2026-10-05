"""Pure validation of an internal, terminal workflow registration disposition.

The reviewed retirement service owns caller/drain checks and strict audit. This
module only seals and verifies its retained evidence; an inactive row alone is
never retirement proof. Snapshot hashes omit credentials and mutable timestamps.
"""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest

from src.models.orm.workflows import Workflow
from src.services.solutions.source_revision import _workflow_snapshot

WORKFLOW_RETIREMENT_SCHEMA = "bifrost.workflow-registration-retirement/v1"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FIELDS = frozenset({
    "schema_version", "identity", "release_id", "artifact_id",
    "before_registration_hash", "after_registration_hash", "caller_inventory_digest",
    "review_digest", "reason", "retired_by_user_id", "retired_at", "evidence_id",
})


class WorkflowRetirementEvidenceError(ValueError):
    """An internal marker does not prove this registration's retired state."""


def workflow_retirement_snapshot_hash(workflow: Workflow) -> str:
    """Reuse the immutable registration snapshot without retaining its values."""
    return canonical_digest(_workflow_snapshot(workflow))


def _identity(workflow: Workflow) -> dict[str, Any]:
    return {
        "workflow_id": str(workflow.id), "raw_path": workflow.path,
        "path": workflow.path.replace("\\", "/").lstrip("/"),
        "function_name": workflow.function_name,
        "organization_id": str(workflow.organization_id) if workflow.organization_id else None,
    }


def _require_retired_state(workflow: Workflow) -> None:
    if (workflow.is_active is not False or workflow.solution_id is not None
            or workflow.endpoint_enabled is not False or workflow.public_endpoint is not False
            or workflow.api_key_enabled is not False):
        raise WorkflowRetirementEvidenceError("registration is not loose and retired with closed admission")


def _validate(workflow: Workflow, evidence: Any) -> dict[str, Any]:
    _require_retired_state(workflow)
    if (not isinstance(evidence, dict) or set(evidence) != _FIELDS
            or evidence.get("schema_version") != WORKFLOW_RETIREMENT_SCHEMA):
        raise WorkflowRetirementEvidenceError("retirement evidence is absent or malformed")
    if evidence["identity"] != _identity(workflow):
        raise WorkflowRetirementEvidenceError("retired registration identity changed")
    for field in ("release_id", "before_registration_hash", "after_registration_hash",
                  "caller_inventory_digest", "review_digest", "evidence_id"):
        value = evidence[field]
        if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
            raise WorkflowRetirementEvidenceError(f"retirement {field} is invalid")
    try:
        for field in ("artifact_id", "retired_by_user_id"):
            value = evidence[field]
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError("noncanonical UUID")
        timestamp = evidence["retired_at"]
        if not isinstance(timestamp, str) or datetime.fromisoformat(timestamp).utcoffset() is None:
            raise ValueError("retirement time requires an offset")
    except (ValueError, TypeError) as exc:
        raise WorkflowRetirementEvidenceError("retirement actor, artifact or time is invalid") from exc
    reason = evidence["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise WorkflowRetirementEvidenceError("retirement requires a bounded review reason")
    # The full before/after hashes retain the cutover's control evidence. Later
    # metadata edits or role deletion cannot reopen this terminal registration,
    # and must not invalidate its immutable historical disposition. Identity
    # and closed admission above are the current-state invariants.
    if canonical_digest({key: value for key, value in evidence.items() if key != "evidence_id"}) != evidence["evidence_id"]:
        raise WorkflowRetirementEvidenceError("retirement evidence digest differs")
    return evidence


def build_workflow_retirement_evidence(
    workflow: Workflow, *, release_id: str, artifact_id: UUID,
    before_registration_hash: str, caller_inventory_digest: str, review_digest: str,
    reason: str, retired_by_user_id: UUID, retired_at: datetime,
) -> dict[str, Any]:
    """Seal the already-retired row after its service has verified the cutover."""
    if workflow.retirement_evidence is not None:
        raise WorkflowRetirementEvidenceError("retirement evidence is write-once")
    evidence = {
        "schema_version": WORKFLOW_RETIREMENT_SCHEMA, "identity": _identity(workflow),
        "release_id": release_id, "artifact_id": str(artifact_id),
        "before_registration_hash": before_registration_hash,
        "after_registration_hash": workflow_retirement_snapshot_hash(workflow),
        "caller_inventory_digest": caller_inventory_digest, "review_digest": review_digest,
        "reason": reason, "retired_by_user_id": str(retired_by_user_id),
        "retired_at": retired_at.isoformat(),
    }
    evidence["evidence_id"] = canonical_digest(evidence)
    return _validate(workflow, evidence)


def validate_workflow_retirement_evidence(workflow: Workflow) -> dict[str, Any]:
    """Verify native evidence and the exact current row; never accept ordinary inactivity."""
    return _validate(workflow, workflow.retirement_evidence)
