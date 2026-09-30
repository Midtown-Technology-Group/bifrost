"""Shared bindings preserve Root identity and reject changed contracts or forged callers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.auth import ExecutionContext
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.orm.executions import Execution
from src.models.orm.solutions import Solution
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest, DeploymentResolutionMap, DeploymentSource,
    RuntimeEntityDefinition, RuntimeSourceResolution, SharedRootTableBinding,
    canonical_json, sha256_digest, validate_runtime_closure,
)
from src.services.solutions.deployment_runtime import _pin_from_deployment
from src.services.solutions.live_handoff_source import LiveHandoffSourceError, source_closure
from src.services.solutions.shared_table_bindings import (
    SharedTableBindingError, require_shared_table, resolve_execution_shared_table,
    table_metadata_hash,
)


def _table():
    return SimpleNamespace(
        id=uuid4(), name="incident_state", solution_id=None, organization_id=None,
        schema=None, access={"policies": []},
    )


def test_binding_hash_rejects_owner_scope_and_named_policy_changes():
    table = _table()
    original = table_metadata_hash(table)
    table.schema = {"version": 2}
    assert table_metadata_hash(table) != original
    table.solution_id = uuid4()
    with pytest.raises(SharedTableBindingError, match="global Root"):
        table_metadata_hash(table)
    table.solution_id = None
    table.organization_id = uuid4()
    with pytest.raises(SharedTableBindingError, match="global Root"):
        table_metadata_hash(table)
    table.organization_id = None
    table.access = {"policies": [{"$ref": "mutable_rule"}]}
    with pytest.raises(SharedTableBindingError, match="separately pinned"):
        table_metadata_hash(table)


def test_empty_binding_extension_preserves_existing_canonical_documents():
    resolution = DeploymentResolutionMap()
    assert "shared_tables" not in resolution.model_dump(mode="json")
    manifest = CompiledDeploymentManifest(
        solution_id=uuid4(), deployment_id=uuid4(), bundle_hash="sha256:bundle",
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key="archive", runtime_prefix="runtime/"),
    )
    original = manifest.model_dump(mode="json", exclude_none=True)
    assert "shared_tables" not in original
    assert manifest.canonical_bytes() == canonical_json(original)
    validate_runtime_closure(
        original, resolution.model_dump(mode="json"), [],
        expected_manifest_hash=manifest.content_hash(),
        expected_resolution_hash=manifest.resolution_map_hash,
    )


def test_bound_table_imports_still_reject_file_and_hidden_namespace_dependencies():
    path = "features/demo.py"
    assert source_closure({path: b"from bifrost import tables\n"}, {path}, has_table_bindings=True)
    for raw in (b"from bifrost import files\n", b"import bifrost as sdk\n"):
        with pytest.raises(LiveHandoffSourceError, match="resource bindings"):
            source_closure({path: raw}, {path}, has_table_bindings=True)


@pytest.mark.asyncio
async def test_changed_metadata_is_rejected_under_a_shared_row_lock():
    table = _table()
    binding = SharedRootTableBinding(table_id=table.id, metadata_hash=table_metadata_hash(table))
    db = SimpleNamespace(scalar=AsyncMock(return_value=table))
    assert await require_shared_table(db, table.name, binding) is table
    statement = db.scalar.call_args.args[0]
    assert statement._for_update_arg.read is True
    assert statement.get_execution_options()["populate_existing"] is True
    table.schema = {"changed": True}
    with pytest.raises(SharedTableBindingError, match="metadata changed"):
        await require_shared_table(db, table.name, binding)


@pytest.mark.asyncio
async def test_signed_attempt_uses_its_superseded_pin_and_preserves_root_table(monkeypatch):
    from src.core import auth
    from src.repositories.solution_deployments import SolutionDeploymentRepository

    table = _table()
    sid, did, wid, eid = uuid4(), uuid4(), uuid4(), uuid4()
    binding = SharedRootTableBinding(table_id=table.id, metadata_hash=table_metadata_hash(table))
    path = "features/demo.py"
    entity = RuntimeEntityDefinition(
        portable_ref=path + "::run", resolved_id=wid, source_ref=path,
        source_hash="sha256:source", definition={
            "name": "Demo", "path": path, "function_name": "run", "timeout_seconds": 30,
        },
    )
    resolution = DeploymentResolutionMap(
        workflows={entity.portable_ref: entity},
        sources={path: RuntimeSourceResolution(object_key="runtime/" + path, content_hash="sha256:source")},
        shared_tables={table.name: binding},
    )
    manifest = CompiledDeploymentManifest(
        solution_id=sid, deployment_id=did, bundle_hash="sha256:bundle",
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key="archive", runtime_prefix="runtime/"),
        workflows=resolution.workflows, shared_tables=resolution.shared_tables,
    )
    deployment = SimpleNamespace(
        id=did, solution_id=sid, organization_id=None, state="superseded",
        compiled_manifest=manifest.model_dump(mode="json"), compiled_manifest_hash=manifest.content_hash(),
        resolution_map=resolution.model_dump(mode="json"), resolution_map_hash=manifest.resolution_map_hash,
        dependencies=[], bundle_hash=manifest.bundle_hash, runtime_storage_prefix="runtime/", git_commit_sha=None,
    )
    solution = SimpleNamespace(
        id=sid, status="active", organization_id=None, allow_outbound_access=False,
        active_deployment_id=uuid4(),
    )
    evidence = _pin_from_deployment(wid, solution, deployment, allow_superseded=True).queue_evidence()
    execution = SimpleNamespace(
        id=eid, workflow_id=wid, organization_id=None, runtime_mode="deployment-v1",
        solution_deployment_id=did, runtime_evidence=evidence,
        runtime_evidence_hash=sha256_digest(canonical_json(evidence)),
    )

    async def get(model, identity, **_kwargs):
        if model is Execution:
            assert identity == eid
            return execution
        assert model is Solution and identity == sid
        return solution

    db = SimpleNamespace(get=AsyncMock(side_effect=get), scalar=AsyncMock(return_value=table))
    user = UserPrincipal(
        user_id=SYSTEM_USER_UUID, email="engine@bifrost.internal", name="Engine",
        organization_id=None,
        is_engine_token=True, engine_execution_id=eid, engine_attempt_token=uuid4(),
        engine_solution_id=str(sid),
    )
    ctx = ExecutionContext(user=user, org_id=None, db=db, solution_id=str(sid))
    monkeypatch.setattr(auth, "_active_engine_attempt", AsyncMock(return_value=True))
    loader = AsyncMock(return_value=deployment)
    monkeypatch.setattr(SolutionDeploymentRepository, "get_by_id_for_runtime", loader)
    assert await resolve_execution_shared_table(ctx, table.name) is table
    assert loader.call_args.args[0] == did
    assert table.solution_id is None and table.organization_id is None
    with pytest.raises(SharedTableBindingError, match="document writes"):
        await resolve_execution_shared_table(ctx, str(table.id), write=True)
    assert await resolve_execution_shared_table(ctx, "unbound_name") is None
    ctx.solution_id = str(uuid4())
    assert await resolve_execution_shared_table(ctx, table.name) is None
    ctx.solution_id = str(sid)
    user.is_engine_token = False
    assert await resolve_execution_shared_table(ctx, table.name) is None
    user.is_engine_token = True
    auth._active_engine_attempt.return_value = False
    with pytest.raises(SharedTableBindingError, match="no longer active"):
        await resolve_execution_shared_table(ctx, table.name)
