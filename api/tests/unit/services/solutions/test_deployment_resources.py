"""Pinned source resources never resolve through current pointers or Root files."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.auth import ExecutionContext
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.orm.executions import Execution
from src.models.orm.solutions import Solution
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest, DeploymentResolutionMap, DeploymentSource,
    RuntimeEntityDefinition, RuntimeResourceResolution, RuntimeSourceResolution,
    canonical_json, sha256_digest, validate_runtime_closure,
)
from src.services.solutions.deployment_resources import DeploymentResourceDenied, read_execution_resource
from src.services.solutions.deployment_runtime import _pin_from_deployment
from src.services.solutions.deployment_storage import SolutionDeploymentStorage


def _fixture(monkeypatch):
    from src.core import auth
    from src.repositories.solution_deployments import SolutionDeploymentRepository

    sid, did, wid, eid = uuid4(), uuid4(), uuid4(), uuid4()
    prefix = f"_solutions/{sid}/{did}/"
    path = "data/reviewed_rates.json"
    content = b'{"reviewed_rate": 42}'
    entry = RuntimeEntityDefinition(portable_ref="functions/run.py::run", resolved_id=wid,
        source_ref="functions/run.py", source_hash="sha256:source", definition={
            "name": "Run", "path": "functions/run.py", "function_name": "run", "timeout_seconds": 30,
        })
    resource = RuntimeResourceResolution(object_key=prefix + "_resources/" + path,
        content_hash=sha256_digest(content), size_bytes=len(content))
    resolution = DeploymentResolutionMap(workflows={entry.portable_ref: entry},
        sources={entry.source_ref: RuntimeSourceResolution(object_key=prefix + "functions/run.py", content_hash="sha256:source")},
        resources={path: resource})
    manifest = CompiledDeploymentManifest(solution_id=sid, deployment_id=did, bundle_hash="sha256:bundle",
        resolution_map_hash=sha256_digest(canonical_json(resolution)), workflows=resolution.workflows,
        resources=resolution.resources, source=DeploymentSource(artifact_key="source.zip", runtime_prefix=prefix))
    deployment = SimpleNamespace(id=did, solution_id=sid, organization_id=None, state="superseded",
        compiled_manifest=manifest.model_dump(mode="json"), compiled_manifest_hash=manifest.content_hash(),
        resolution_map=resolution.model_dump(mode="json"), resolution_map_hash=manifest.resolution_map_hash,
        dependencies=[], bundle_hash=manifest.bundle_hash, runtime_storage_prefix=prefix, git_commit_sha=None)
    solution = SimpleNamespace(id=sid, status="active", organization_id=None,
        allow_outbound_access=False, active_deployment_id=uuid4())
    evidence = _pin_from_deployment(wid, solution, deployment, allow_superseded=True).queue_evidence()
    execution = SimpleNamespace(id=eid, workflow_id=wid, organization_id=None, runtime_mode="deployment-v1",
        solution_deployment_id=did, runtime_evidence=evidence,
        runtime_evidence_hash=sha256_digest(canonical_json(evidence)))

    async def get(model, identity, **_kwargs):
        if model is Execution:
            assert identity == eid
            return execution
        assert model is Solution and identity == sid
        return solution

    db = SimpleNamespace(get=AsyncMock(side_effect=get))
    user = UserPrincipal(user_id=SYSTEM_USER_UUID, email="engine@bifrost.internal",
        organization_id=None, is_engine_token=True, engine_execution_id=eid,
        engine_attempt_token=uuid4(), engine_solution_id=str(sid))
    ctx = ExecutionContext(user=user, org_id=None, db=db)
    lease = AsyncMock(return_value=True)
    loader = AsyncMock(return_value=deployment)
    storage = AsyncMock(return_value=content)
    monkeypatch.setattr(auth, "_active_engine_attempt", lease)
    monkeypatch.setattr(SolutionDeploymentRepository, "get_by_id_for_runtime", loader)
    monkeypatch.setattr(SolutionDeploymentStorage, "read_resource", storage)
    return SimpleNamespace(ctx=ctx, deployment=deployment, solution=solution, execution=execution,
        path=path, content=content, lease=lease, loader=loader, storage=storage,
        manifest=manifest, resolution=resolution)


@pytest.mark.asyncio
async def test_superseded_execution_reads_its_pinned_resource_after_pointer_changes(monkeypatch):
    f = _fixture(monkeypatch)
    assert f.solution.active_deployment_id != f.deployment.id
    assert await read_execution_resource(f.ctx, f.path) == f.content
    f.loader.assert_awaited_once_with(f.execution.solution_deployment_id)
    f.storage.assert_awaited_once_with(f.path, len(f.content))
    assert "deployment_resource_hashes" not in f.execution.runtime_evidence
    # The existing manifest hash anchors resources without changing the durable
    # queue-evidence shape or adding source entries to the Python import loader.
    assert set(f.execution.runtime_evidence["deployment_source_hashes"]) == {"functions/run.py"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["human", "revoked", "cross_target", "cross_owner", "cross_org",
    "inactive", "changed_evidence", "changed_manifest", "ready_deployment", "loose_runtime"])
async def test_unauthorized_or_changed_pin_never_reaches_storage(monkeypatch, change):
    f = _fixture(monkeypatch)
    if change == "human":
        f.ctx.user.is_engine_token = False
        f.ctx.user.is_superuser = True
    elif change == "revoked":
        f.lease.return_value = False
    elif change == "cross_target":
        f.ctx.solution_id = str(uuid4())
    elif change == "cross_owner":
        f.deployment.solution_id = uuid4()
    elif change == "cross_org":
        f.execution.organization_id = uuid4()
    elif change == "inactive":
        f.solution.status = "uninstalled"
    elif change == "changed_evidence":
        f.execution.runtime_evidence_hash = "sha256:" + "0" * 64
    elif change == "changed_manifest":
        f.deployment.compiled_manifest_hash = "sha256:" + "0" * 64
    elif change == "ready_deployment":
        f.deployment.state = "ready"
    else:
        f.execution.runtime_mode = "repo-v1"
    with pytest.raises(DeploymentResourceDenied):
        await read_execution_resource(f.ctx, f.path)
    f.storage.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_resource_and_corrupt_bytes_never_fall_back(monkeypatch):
    f = _fixture(monkeypatch)
    with pytest.raises(FileNotFoundError):
        await read_execution_resource(f.ctx, "data/current_business_state.json")
    f.storage.assert_not_awaited()
    f.storage.return_value = b"changed"
    with pytest.raises(DeploymentResourceDenied, match="pinned hash"):
        await read_execution_resource(f.ctx, f.path)


def test_empty_resource_extension_preserves_existing_immutable_hashes():
    resolution = DeploymentResolutionMap()
    assert "resources" not in resolution.model_dump(mode="json")
    manifest = CompiledDeploymentManifest(solution_id=uuid4(), deployment_id=uuid4(),
        bundle_hash="sha256:bundle", resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key="archive", runtime_prefix="runtime/"))
    legacy = manifest.model_dump(mode="json", exclude_none=True)
    assert "resources" not in legacy
    assert canonical_json(legacy) == manifest.canonical_bytes()
    validate_runtime_closure(legacy, resolution.model_dump(mode="json"), [],
        expected_manifest_hash=sha256_digest(canonical_json(legacy)),
        expected_resolution_hash=sha256_digest(canonical_json(resolution)))


@pytest.mark.parametrize("size", [True, -1, 0, 2 * 1024 * 1024 + 1])
def test_resource_contract_rejects_invalid_size(size):
    with pytest.raises(ValidationError):
        RuntimeResourceResolution(object_key="resource", content_hash="sha256:" + "a" * 64, size_bytes=size)


@pytest.mark.parametrize("change", ["cross_deployment", "traversal", "python", "conflicting_source", "map_mismatch"])
def test_even_self_consistent_hashes_cannot_authorize_invalid_resource_resolution(monkeypatch, change):
    f = _fixture(monkeypatch)
    resource = next(iter(f.resolution.resources.values()))
    path = f.path
    if change == "cross_deployment":
        resource = resource.model_copy(update={"object_key": resource.object_key.replace(str(f.deployment.id), str(uuid4()))})
    elif change == "traversal":
        path = "../rates.json"
    elif change == "python":
        path = "data/rates.py"
    elif change == "conflicting_source":
        path = "functions/run.py"
    resolution = f.resolution.model_copy(update={"resources": {path: resource}})
    manifest = f.manifest.model_copy(update={"resources": {} if change == "map_mismatch" else resolution.resources,
        "resolution_map_hash": sha256_digest(canonical_json(resolution))})
    with pytest.raises(ValueError):
        validate_runtime_closure(manifest, resolution, [], expected_manifest_hash=manifest.content_hash(),
            expected_resolution_hash=manifest.resolution_map_hash)
