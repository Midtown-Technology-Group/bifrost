"""A package cannot change installed grants or resource bindings by rebuilding."""

import copy

import pytest

from src.models.orm.base import Base
from src.services.solutions.package_controls import (
    CHILD_FIELDS,
    CONTROL_FIELDS,
    DECLARATION_FIELDS,
    require_preserved_package_controls,
)


def test_snapshot_fields_exist_and_exclude_credentials_and_operational_data():
    for name, fields in CONTROL_FIELDS.items():
        assert set(fields) <= set(Base.metadata.tables[name].c.keys())
    for name, (_, parent, fields) in CHILD_FIELDS.items():
        assert {parent, *fields} <= set(Base.metadata.tables[name].c.keys())
    for name, (_, fields) in DECLARATION_FIELDS.items():
        assert set(fields) <= set(Base.metadata.tables[name].c.keys())
    assert "api_key_enabled" in CONTROL_FIELDS["workflows"]
    assert "api_key_hash" not in CONTROL_FIELDS["workflows"]
    assert "connection_id" in CHILD_FIELDS["agent_mcp_connections"][2]
    assert "state" not in CHILD_FIELDS["webhook_sources"][2]
    assert "documents" not in CONTROL_FIELDS


@pytest.mark.parametrize("kind", [
    "workflow_roles", "agent_mcp_connections", "tables", "solution_file_locations",
    "solution_connection_schema", "workflows",
])
@pytest.mark.parametrize("operation", ["remove", "replace", "add_grant"])
def test_retained_controls_reject_loss_replacement_or_added_grants(kind, operation):
    before = {kind: {"original": [{"grant": "reviewed"}]}}
    after = copy.deepcopy(before)
    if operation == "remove":
        del after[kind]["original"]
    elif operation == "replace":
        after[kind]["original"] = [{"grant": "other"}]
    else:
        after[kind]["original"].append({"grant": "additional"})
    with pytest.raises(ValueError, match="installed controls"):
        require_preserved_package_controls(before, after)


def test_reviewed_new_entities_and_declarations_do_not_replace_existing_controls():
    before = {"tables": {"retained": {"policy": "admin-only"}}}
    after = copy.deepcopy(before)
    after["tables"]["new"] = {"policy": "admin-only"}
    after["solution_file_locations"] = {"audit-evidence": {"location": "audit-evidence"}}
    require_preserved_package_controls(before, after)
