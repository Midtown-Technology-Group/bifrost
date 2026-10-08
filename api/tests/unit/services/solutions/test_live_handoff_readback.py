"""Missing loose registrations require a certified immutable replacement."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.root_file_bindings import RootFileBinding
from bifrost.solution_delivery_review import SharedRootTableBinding
from bifrost.workspace_release import canonical_digest
from src.services.solutions import live_handoff_readback as readback
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest, DeploymentGitProvenance, DeploymentResolutionMap,
    DeploymentSource, RuntimeEntityDefinition, RuntimeSourceResolution,
    canonical_json, sha256_digest,
)
from src.services.solutions.deployment_storage import (
    deployment_runtime_prefix, deployment_source_artifact_key,
)
from src.services.solutions.live_handoff_preflight import handoff_preflight_evidence_id
from src.services.solutions.live_handoff_source import source_archive
from src.services.workspace_promotions import WorkspacePromotionInvalid, WorkspacePromotionPreviewService
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow


@pytest.fixture
def handoff(monkeypatch):
    solution_id, deployment_id, workflow_id = uuid4(), uuid4(), uuid4()
    path, content = 'features/cipp.py', b'async def run():\n    return 1\n'
    bounds = {'max_duration_seconds': 30, 'max_external_calls': 10, 'max_records_read': 100, 'max_output_bytes': 4096}
    inherited = {'workflow_id': str(workflow_id), 'path': path, 'function': 'run',
        'name': 'CIPP', 'type': 'workflow', 'organization_id': None, 'is_active': True,
        'source_sha256': sha256_digest(content).removeprefix('sha256:'),
        'runtime_bounds': bounds, 'endpoint_enabled': False, 'public_endpoint': False,
        'api_key_enabled': False, 'access_level': 'role_based', 'role_ids': []}
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id, organization_id=None,
        path=path, function_name='run', name='CIPP', type='workflow', is_active=True,
        timeout_seconds=30, execution_mode='async', time_saved=0, value=0, cache_ttl_seconds=0,
        endpoint_enabled=False, public_endpoint=False, api_key_enabled=False, access_level='role_based',
        roles=[], parameters_schema={}, display_name=None, description=None, category=None, tags=[],
        allowed_methods=['GET', 'POST'], disable_global_key=False, retry_policy=None, tool_description=None)
    solution = SimpleNamespace(id=solution_id, organization_id=None, status='active',
        active_deployment_id=deployment_id, execution_runtime_mode='deployment-v1')
    release = SimpleNamespace(release_row_id=uuid4(), release_id='sha256:'+'a'*64, artifact_id=uuid4(),
        governed_manifest_id='sha256:'+'b'*64, registration_state_fingerprint='sha256:'+'c'*64,
        source_commit_sha='d'*40, source_hashes={path: inherited['source_sha256']},
        governed_paths=(path,), effective_registrations={path+'::run': inherited})
    runtime_prefix = deployment_runtime_prefix(solution_id, deployment_id)
    entity = RuntimeEntityDefinition(portable_ref=path+'::run', resolved_id=workflow_id,
        source_ref=path, source_hash=sha256_digest(content), definition={
            'path': path, 'function_name': 'run', 'name': 'CIPP', 'type': 'workflow',
            'organization_id': None, 'timeout_seconds': 30, 'execution_mode': 'async',
            'time_saved': 0, 'value': 0.0, 'cache_ttl_seconds': 0, 'runtime_bounds': bounds})
    resolution = DeploymentResolutionMap(workflows={entity.portable_ref: entity}, sources={path:
        RuntimeSourceResolution(object_key=runtime_prefix+path, content_hash=sha256_digest(content))})
    bundle_hash = sha256_digest(canonical_json({'release_id': release.release_id,
        'workflow_ids': [str(workflow_id)], 'shared_tables': {}, 'source_hashes': {path: sha256_digest(content)}}))
    manifest = CompiledDeploymentManifest(solution_id=solution_id, deployment_id=deployment_id,
        bundle_hash=bundle_hash, resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key=deployment_source_artifact_key(solution_id, deployment_id),
            runtime_prefix=runtime_prefix), workflows=resolution.workflows,
        git=DeploymentGitProvenance(commit_sha=release.source_commit_sha))
    deployment = SimpleNamespace(id=deployment_id, solution_id=solution_id, organization_id=None,
        state='active', activated_at=datetime.now(UTC), parent_deployment_id=None, base_deployment_id=None,
        compiled_manifest=manifest, compiled_manifest_hash=manifest.content_hash(),
        resolution_map=resolution, resolution_map_hash=manifest.resolution_map_hash,
        bundle_hash=bundle_hash, source_artifact_key=manifest.source.artifact_key,
        runtime_storage_prefix=runtime_prefix, dependencies=[], validation_result={
            'schema_version': readback.HANDOFF_MARKER, 'release_row_id': str(release.release_row_id),
            'release_id': release.release_id, 'workflow_ids': [str(workflow_id)],
            'verified_source_paths': [path], 'preflight_evidence_id': handoff_preflight_evidence_id(
                release, manifest, resolution, workflow_ids=[workflow_id])})
    deployments = {deployment_id: deployment}
    class DB:
        async def scalar(self, statement):
            entity_type = statement.column_descriptions[0]['entity']
            if entity_type is Workflow:
                return workflow
            if entity_type is Solution:
                return solution
            if entity_type is SolutionDeployment:
                # The first predicate is always the exact immutable deployment ID.
                identifier = statement._where_criteria[0].right.value
                return deployments.get(identifier)
            raise AssertionError(str(statement))
        async def scalars(self, statement):
            return SimpleNamespace(all=lambda: [workflow])
    storage_state = {'archive': source_archive({path: content}), 'runtime': content}
    class Storage:
        def __init__(self, sid, did):
            self.deployment_id = did
            self.source_artifact_key = deployment_source_artifact_key(sid, did)
            self.runtime_prefix = deployment_runtime_prefix(sid, did)
        async def read_source_artifact(self):
            return storage_state.get('revisions', {}).get(self.deployment_id, storage_state)['archive']
        async def read_runtime_file(self, requested_path):
            assert requested_path == path
            return storage_state.get('revisions', {}).get(self.deployment_id, storage_state)['runtime']
    monkeypatch.setattr(readback, 'SolutionDeploymentStorage', Storage)
    monkeypatch.setattr(readback, 'require_shared_tables', AsyncMock())
    monkeypatch.setattr(readback, 'require_root_workspace_files', AsyncMock())
    guard = readback.LiveHandoffReadback(DB(), release)
    return SimpleNamespace(guard=guard, workflow=workflow, solution=solution, release=release,
        inherited=inherited, deployment=deployment, deployments=deployments,
        manifest=manifest, resolution=resolution, storage=storage_state, path=path)


@pytest.mark.asyncio
async def test_certified_handoff_leaves_owner_and_immutable_receipt_unchanged(handoff):
    original = dict(handoff.deployment.validation_result)
    await handoff.guard.require(handoff.inherited)
    await handoff.guard.require(handoff.inherited)
    assert handoff.workflow.solution_id == handoff.solution.id
    assert handoff.deployment.validation_result == original
    assert len(handoff.guard.solutions) == 1


def _reviewed_successor(handoff, schema):
    origin = handoff.deployment
    origin.state = 'superseded'
    new_id = uuid4()
    prefix = deployment_runtime_prefix(handoff.solution.id, new_id)
    content = b'async def run():\n    return 2\n'
    entity = next(iter(handoff.resolution.workflows.values()))
    definition = dict(entity.definition)
    if schema == readback.WORKFLOW_REVISION_MARKER:
        handoff.workflow.endpoint_enabled = True
        definition.update(endpoint_enabled=True, public_endpoint=False, api_key_enabled=False,
            access_level='role_based', role_ids=[])
    entity = RuntimeEntityDefinition.model_validate({**entity.model_dump(mode='json'),
        'source_hash': sha256_digest(content), 'definition': definition})
    resolution = handoff.resolution.model_copy(update={'sources': {handoff.path: RuntimeSourceResolution(
        object_key=prefix+handoff.path, content_hash=sha256_digest(content))},
        'workflows': {entity.portable_ref: entity}})
    manifest = handoff.manifest.model_copy(update={'deployment_id': new_id,
        'workflows': resolution.workflows,
        'resolution_map_hash': sha256_digest(canonical_json(resolution)),
        'source': DeploymentSource(artifact_key=deployment_source_artifact_key(handoff.solution.id, new_id), runtime_prefix=prefix)})
    candidate = SimpleNamespace(**{**vars(origin), 'id': new_id, 'state': 'active',
        'parent_deployment_id': origin.id, 'base_deployment_id': origin.id,
        'compiled_manifest': manifest, 'compiled_manifest_hash': manifest.content_hash(),
        'resolution_map': resolution, 'resolution_map_hash': manifest.resolution_map_hash,
        'source_artifact_key': manifest.source.artifact_key, 'runtime_storage_prefix': prefix,
        'validation_result': {'schema_version': schema, 'preflight_evidence_id': canonical_digest({'reviewed': True}),
            'workflow_ids': [str(handoff.workflow.id)],
            'source_hashes': {handoff.path: resolution.sources[handoff.path].content_hash}}})
    handoff.deployments[new_id] = candidate
    handoff.storage['revisions'] = {new_id: {'archive': source_archive({handoff.path: content}), 'runtime': content}}
    handoff.solution.active_deployment_id = new_id


@pytest.mark.asyncio
@pytest.mark.parametrize('schema', [readback.SOURCE_MARKER, readback.WORKFLOW_REVISION_MARKER])
async def test_reviewed_revision_descendant_preserves_the_handoff(handoff, schema):
    _reviewed_successor(handoff, schema)
    await handoff.guard.require(handoff.inherited)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    'missing_owner', 'inactive', 'scope', 'path', 'function', 'name', 'timeout',
    'public_endpoint', 'api_key_enabled', 'roles', 'legacy_runtime', 'missing_pointer',
    'wrong_pointer', 'inactive_solution', 'inactive_deployment', 'unactivated',
    'manifest_hash', 'resolution_hash', 'artifact_key', 'runtime_prefix', 'bundle_hash',
    'receipt_digest', 'receipt_release', 'receipt_row', 'receipt_paths', 'receipt_workflows',
    'unreviewed', 'detached_revision', 'archive_bytes', 'runtime_bytes',
])
async def test_missing_loose_binding_fails_closed(handoff, change):
    workflow, solution, deployment = handoff.workflow, handoff.solution, handoff.deployment
    if change == 'missing_owner':
        workflow.solution_id = None
    elif change == 'inactive':
        workflow.is_active = False
    elif change == 'scope':
        workflow.organization_id = uuid4()
    elif change == 'path':
        workflow.path = 'other.py'
    elif change == 'function':
        workflow.function_name = 'other'
    elif change == 'name':
        workflow.name = 'other'
    elif change == 'timeout':
        workflow.timeout_seconds = 0
    elif change == 'public_endpoint':
        workflow.public_endpoint = True
    elif change == 'api_key_enabled':
        workflow.api_key_enabled = True
    elif change == 'roles':
        workflow.roles = [SimpleNamespace(id=uuid4())]
    elif change == 'legacy_runtime':
        solution.execution_runtime_mode = 'repo-v1'
    elif change == 'missing_pointer':
        solution.active_deployment_id = None
    elif change == 'wrong_pointer':
        solution.active_deployment_id = uuid4()
    elif change == 'inactive_solution':
        solution.status = 'inactive'
    elif change == 'inactive_deployment':
        deployment.state = 'ready'
    elif change == 'unactivated':
        deployment.activated_at = None
    elif change == 'manifest_hash':
        deployment.compiled_manifest_hash = 'sha256:'+'f'*64
    elif change == 'resolution_hash':
        deployment.resolution_map_hash = 'sha256:'+'f'*64
    elif change == 'artifact_key':
        deployment.source_artifact_key = 'mutable/source.zip'
    elif change == 'runtime_prefix':
        deployment.runtime_storage_prefix = 'mutable/'
    elif change == 'bundle_hash':
        deployment.bundle_hash = 'sha256:'+'f'*64
    elif change == 'receipt_digest':
        deployment.validation_result['preflight_evidence_id'] = 'sha256:'+'f'*64
    elif change == 'receipt_release':
        deployment.validation_result['release_id'] = 'sha256:'+'f'*64
    elif change == 'receipt_row':
        deployment.validation_result['release_row_id'] = str(uuid4())
    elif change == 'receipt_paths':
        deployment.validation_result['verified_source_paths'] = []
    elif change == 'receipt_workflows':
        deployment.validation_result['workflow_ids'] = []
    elif change == 'unreviewed':
        deployment.validation_result['schema_version'] = 'bifrost.initial-reviewed-workflow-install/v1'
    elif change == 'detached_revision':
        deployment.validation_result['schema_version'] = readback.SOURCE_MARKER
    elif change == 'archive_bytes':
        handoff.storage['archive'] = source_archive({handoff.path: b'changed'})
    elif change == 'runtime_bytes':
        handoff.storage['runtime'] = b'changed'
    with pytest.raises((ValueError, KeyError)):
        await handoff.guard.require(handoff.inherited)


@pytest.mark.asyncio
async def test_preview_omits_only_a_verified_handoff(handoff, monkeypatch):
    monkeypatch.setattr('src.services.workspace_promotions.find_workspace_workflow', AsyncMock(return_value=None))
    service = WorkspacePromotionPreviewService(handoff.guard.db, uuid4(), repo_storage=SimpleNamespace())
    assert await service._current_registration_snapshot(handoff.release) == {}
    handoff.workflow.public_endpoint = True
    with pytest.raises(WorkspacePromotionInvalid, match='handoff could not be verified'):
        await service._current_registration_snapshot(handoff.release)


@pytest.mark.asyncio
async def test_predecessor_release_receipt_still_proves_the_handoff(handoff):
    """A receipt coherently bound to an earlier Live release remains valid
    while its coverage, verified paths and source hashes match the current
    release's governed bytes (#1122)."""
    handoff.deployment.validation_result['release_row_id'] = str(uuid4())
    handoff.deployment.validation_result['release_id'] = 'sha256:' + 'e' * 64
    await handoff.guard.require(handoff.inherited)


@pytest.mark.asyncio
async def test_active_coverage_proves_a_key_with_no_recorded_lineage(handoff):
    """A solution-managed key whose deployment chain carries no reviewed
    lineage marker is verified against its active covering deployment."""
    handoff.deployment.validation_result = None
    await handoff.guard.require(handoff.inherited)
    assert handoff.workflow.solution_id == handoff.solution.id


@pytest.mark.asyncio
async def test_preview_omits_a_key_proven_by_active_coverage(handoff, monkeypatch):
    handoff.deployment.validation_result = None
    monkeypatch.setattr('src.services.workspace_promotions.find_workspace_workflow', AsyncMock(return_value=None))
    service = WorkspacePromotionPreviewService(handoff.guard.db, uuid4(), repo_storage=SimpleNamespace())
    assert await service._current_registration_snapshot(handoff.release) == {}
    handoff.workflow.public_endpoint = True
    with pytest.raises(WorkspacePromotionInvalid, match='handoff could not be verified'):
        await service._current_registration_snapshot(handoff.release)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing_pointer', 'wrong_pointer', 'legacy_runtime', 'scope', 'exposure'])
async def test_active_coverage_still_fails_closed(handoff, change):
    """Active coverage never substitutes for an active covering deployment or
    an exact owner/exposure match (#1122)."""
    handoff.deployment.validation_result = None
    if change == 'missing_pointer':
        handoff.solution.active_deployment_id = None
    elif change == 'wrong_pointer':
        handoff.solution.active_deployment_id = uuid4()
    elif change == 'legacy_runtime':
        handoff.solution.execution_runtime_mode = 'repo-v1'
    elif change == 'scope':
        handoff.workflow.organization_id = uuid4()
    elif change == 'exposure':
        handoff.workflow.public_endpoint = True
    with pytest.raises((ValueError, KeyError)):
        await handoff.guard.require(handoff.inherited)


@pytest.mark.asyncio
async def test_active_coverage_rejects_inherited_identity_drift(handoff):
    handoff.deployment.validation_result = None
    handoff.inherited['name'] = 'Renamed elsewhere'
    with pytest.raises(ValueError, match='origin differs'):
        await handoff.guard.require(handoff.inherited)


@pytest.mark.asyncio
async def test_shadow_loose_uuid_is_not_hidden_by_a_valid_solution(handoff, monkeypatch):
    shadow = SimpleNamespace(id=uuid4(), name='CIPP', type='workflow', is_active=True)
    monkeypatch.setattr('src.services.workspace_promotions.find_workspace_workflow', AsyncMock(return_value=shadow))
    service = WorkspacePromotionPreviewService(handoff.guard.db, uuid4(), repo_storage=SimpleNamespace())
    with pytest.raises(WorkspacePromotionInvalid, match='changed outside its release'):
        await service._current_registration_snapshot(handoff.release)

@pytest.mark.asyncio
async def test_preview_cannot_recreate_a_handed_off_entry(handoff):
    from src.services.workspace_promotions import _BaseSnapshot
    base = _BaseSnapshot(release_id=handoff.release.release_id, manifest_id='sha256:'+'a'*64,
        files={}, hashes={}, registrations={}, handed_off_registration_keys=(handoff.path+'::run',))
    service = WorkspacePromotionPreviewService(handoff.guard.db, uuid4(), repo_storage=SimpleNamespace())
    service._validate_source_release_cohort = AsyncMock()
    service._read_protected_source = AsyncMock(return_value=(None, {}))
    service._resolve_base = AsyncMock(return_value=base)
    request = SimpleNamespace(entry=SimpleNamespace(path=handoff.path, function='run'))
    from unittest.mock import patch
    with patch('src.services.workspace_promotions._validate_closure_files', return_value={}):
        with pytest.raises(WorkspacePromotionInvalid, match='use reviewed Solution delivery'):
            await service.preview(request, uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["shared_tables", "root_file_bindings"])
async def test_reviewed_replacement_checks_current_contract_not_obsolete_origin(handoff, monkeypatch, kind):
    h1, h2, h3 = ("sha256:" + char * 64 for char in ("1", "2", "3"))
    field = "metadata_hash" if kind == "shared_tables" else "expected_read_sha256"
    binding = (SharedRootTableBinding(table_id=uuid4(), metadata_hash=h1)
        if kind == "shared_tables" else RootFileBinding(location="workspace",
            path="features/assets/tool.ps1", operations=("read",), max_bytes=4096,
            expected_read_sha256=h1))
    original_bindings = {"tool": binding}
    resolution = handoff.resolution.model_copy(update={kind: original_bindings})
    bundle = sha256_digest(canonical_json({"release_id": handoff.release.release_id,
        "workflow_ids": [str(handoff.workflow.id)], "shared_tables": {
            name: value.model_dump(mode="json") for name, value in resolution.shared_tables.items()},
        "source_hashes": {path: source.content_hash for path, source in resolution.sources.items()}}))
    manifest = handoff.manifest.model_copy(update={kind: original_bindings,
        "bundle_hash": bundle, "resolution_map_hash": sha256_digest(canonical_json(resolution))})
    origin = handoff.deployment
    origin.compiled_manifest, origin.resolution_map = manifest, resolution
    origin.bundle_hash, origin.compiled_manifest_hash = bundle, manifest.content_hash()
    origin.resolution_map_hash = manifest.resolution_map_hash
    origin.validation_result["preflight_evidence_id"] = handoff_preflight_evidence_id(
        handoff.release, manifest, resolution, workflow_ids=[handoff.workflow.id])
    handoff.manifest, handoff.resolution = manifest, resolution
    _reviewed_successor(handoff, readback.WORKFLOW_REVISION_MARKER)
    active = handoff.deployments[handoff.solution.active_deployment_id]
    current_bindings = {"tool": binding.model_copy(update={field: h2})}
    active.resolution_map = active.resolution_map.model_copy(update={kind: current_bindings})
    active.resolution_map_hash = sha256_digest(canonical_json(active.resolution_map))
    active.compiled_manifest = active.compiled_manifest.model_copy(update={kind: current_bindings,
        "resolution_map_hash": active.resolution_map_hash})
    active.compiled_manifest_hash = active.compiled_manifest.content_hash()
    observed, current = [], {"hash": h2}
    async def require_current(_db, bindings, *, solution_organization_id=None):
        assert solution_organization_id is None
        observed.append(bindings["tool"])
        if getattr(bindings["tool"], field) != current["hash"]:
            raise ValueError("Current Root contract drifted")
    monkeypatch.setattr(readback, "require_shared_tables" if kind == "shared_tables"
        else "require_root_workspace_files", require_current)
    await readback.LiveHandoffReadback(handoff.guard.db, handoff.release).require(handoff.inherited)
    assert observed == [current_bindings["tool"]]
    current["hash"] = h3
    with pytest.raises(ValueError, match="Current Root contract drifted"):
        await readback.LiveHandoffReadback(handoff.guard.db, handoff.release).require(handoff.inherited)
    current["hash"] = h2
    handoff.storage["runtime"] = b"corrupted historical runtime"
    with pytest.raises(ValueError, match="runtime source bytes differ"):
        await readback.LiveHandoffReadback(handoff.guard.db, handoff.release).require(handoff.inherited)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,grant,allowed", [
    ("provider", "provider", True), ("provider", "foreign", False),
    ("provider", "global", True), ("global", "foreign", True),
])
async def test_current_table_contract_uses_its_deployment_owner_scope(handoff, monkeypatch, owner, grant, allowed):
    from src.services.solutions import shared_table_bindings
    provider, foreign = uuid4(), uuid4()
    scope = provider if owner == "provider" else None
    grant_scope = {"provider": provider, "foreign": foreign, "global": None}[grant]
    binding = SharedRootTableBinding(table_id=uuid4(), metadata_hash="sha256:" + "a" * 64,
        organization_id=grant_scope)
    manifest = handoff.manifest.model_copy(update={"shared_tables": {"tool": binding}})
    metadata = AsyncMock()
    monkeypatch.setattr(shared_table_bindings, "require_shared_table", metadata)
    monkeypatch.setattr(readback, "require_shared_tables", shared_table_bindings.require_shared_tables)
    if allowed:
        await handoff.guard._current_contracts(manifest, scope)
        metadata.assert_awaited_once_with(handoff.guard.db, "tool", binding)
    else:
        with pytest.raises(shared_table_bindings.SharedTableBindingError, match="organization differs"):
            await handoff.guard._current_contracts(manifest, scope)
        metadata.assert_not_awaited()
