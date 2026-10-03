"""Adding terminal metadata preserves ordinary prepared adoption controls."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from bifrost.workspace_release import canonical_digest
from sqlalchemy import inspect

from src.models.orm.workflows import Workflow
from src.services.solutions.repo_workflow_adoption import _digest_row


def _ordinary_workflow():
    return Workflow(id=uuid4(), name="Prepared legacy workflow", function_name="run",
        path="features/legacy.py", organization_id=uuid4(), solution_id=uuid4(),
        is_active=True, value=Decimal("12.50"), parameters_schema={"type": "object"},
        created_at=datetime(2026, 10, 2, tzinfo=UTC), retirement_evidence=None)


def _legacy_column_snapshot(workflow):
    """The pre-migration canonical persisted-column contract."""
    result = {}
    for attr in inspect(Workflow).column_attrs:
        if attr.key == "retirement_evidence":
            continue
        value = getattr(workflow, attr.key)
        if isinstance(value, (UUID, Decimal)):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        result[attr.key] = value
    return result


def test_null_retirement_column_preserves_pre_migration_prepared_workflow_digest():
    workflow = _ordinary_workflow()
    old_snapshot = _legacy_column_snapshot(workflow)
    assert "retirement_evidence" not in old_snapshot
    assert old_snapshot["value"] == "12.50"
    assert _digest_row(workflow) == canonical_digest(old_snapshot)


@pytest.mark.parametrize("marker", [{}, {"evidence_id": "sha256:" + "a" * 64}])
def test_nonnull_retirement_marker_remains_part_of_installed_control_digest(marker):
    workflow = _ordinary_workflow()
    old_snapshot = _legacy_column_snapshot(workflow)
    workflow.retirement_evidence = marker
    assert _digest_row(workflow) == canonical_digest({**old_snapshot, "retirement_evidence": marker})
    assert _digest_row(workflow) != canonical_digest(old_snapshot)
