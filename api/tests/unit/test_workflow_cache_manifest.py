"""Explicit cache controls round-trip; omission preserves the database value."""

from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml
from pydantic import ValidationError

from bifrost.manifest import ManifestWorkflow, parse_manifest, serialize_manifest
from bifrost.manifest_codec import Destination

WORKFLOW_ID = "11111111-1111-4111-8111-111111111111"


def workflow_entry(**extra):
    return {
        "id": WORKFLOW_ID,
        "name": "Cache contract",
        "path": "workflows/cache_contract.py",
        "function_name": "cache_contract",
        **extra,
    }


@pytest.mark.parametrize("ttl", [0, 600, 86400])
@pytest.mark.parametrize("destination", [Destination.GIT_SYNC, Destination.INSTALL])
def test_cache_survives_yaml_portable_view_and_upsert_values(ttl, destination):
    manifest = parse_manifest(
        yaml.safe_dump(
            {
                "workflows": {WORKFLOW_ID: workflow_entry(cache_ttl_seconds=ttl)},
            }
        )
    )
    serialized = serialize_manifest(manifest)
    assert (
        yaml.safe_load(serialized)["workflows"][WORKFLOW_ID]["cache_ttl_seconds"] == ttl
    )
    model = parse_manifest(serialized).workflows[WORKFLOW_ID]
    portable = model.view(Destination.INSTALL)
    assert portable["cache_ttl_seconds"] == ttl
    assert (
        ManifestWorkflow.model_validate(portable)
        .to_orm_values(destination)
        .direct["cache_ttl_seconds"]
        == ttl
    )


@pytest.mark.parametrize("destination", [Destination.GIT_SYNC, Destination.INSTALL])
@pytest.mark.parametrize("extra", [{}, {"cache_ttl_seconds": None}])
def test_omitted_or_null_cache_leaves_no_upsert_intent(destination, extra):
    model = ManifestWorkflow.model_validate(workflow_entry(**extra))
    assert model.cache_ttl_seconds is None
    assert "cache_ttl_seconds" not in model.to_orm_values(destination).direct
    assert "cache_ttl_seconds" not in model.view(Destination.INSTALL)


@pytest.mark.parametrize("ttl", [0, 600])
def test_from_row_preserves_cache_including_zero(ttl):
    row = SimpleNamespace(
        id=UUID(WORKFLOW_ID),
        name="Cache contract",
        path="workflows/cache_contract.py",
        function_name="cache_contract",
        type="workflow",
        description=None,
        tool_description=None,
        organization_id=None,
        access_level="role_based",
        endpoint_enabled=False,
        allowed_methods=["POST"],
        timeout_seconds=20,
        public_endpoint=False,
        category="General",
        tags=[],
        cache_ttl_seconds=ttl,
    )
    model = ManifestWorkflow.from_row(row)
    for destination in (Destination.GIT_SYNC, Destination.INSTALL):
        assert model.view(destination)["cache_ttl_seconds"] == ttl
        assert model.to_orm_values(destination).direct["cache_ttl_seconds"] == ttl


@pytest.mark.parametrize("invalid", [-1, 86401, True, False, "0", "600", 0.5])
def test_cache_requires_a_bounded_integer(invalid):
    with pytest.raises(ValidationError):
        ManifestWorkflow.model_validate(workflow_entry(cache_ttl_seconds=invalid))
