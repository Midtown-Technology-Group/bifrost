"""Missing loose registrations require a certified immutable replacement."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

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
    bounds = {'max_duration_seconds': 30}
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
            self.source_artifact_key = deployment_source_artifact_key(sid, did)
            self.runtime_prefix = deployment_runtime_prefix(sid, did)
        async def read_source_artifact(self):
            return storage_state['archive']
        async def read_runtime_file(self, requested_path):
            assert requested_path == path
            return storage_state['runtime']
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


@pytest.mark.asyncio
@pytest.mark.parametrize('schema', [readback.SOURCE_MARKER, readback.WORKFLOW_REVISION_MARKER])
async def test_reviewed_revision_descendant_preserves_the_handoff(handoff, schema):
    origin = handoff.deployment
    origin.state = 'superseded'
    new_id = uuid4()
    prefix = deployment_runtime_prefix(handoff.solution.id, new_id)
    resolution = handoff.resolution.model_copy(update={'sources': {handoff.path: RuntimeSourceResolution(
        object_key=prefix+handoff.path, content_hash=handoff.resolution.sources[handoff.path].content_hash)}})
    manifest = handoff.manifest.model_copy(update={'deployment_id': new_id,
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
    handoff.solution.active_deployment_id = new_id
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
    if change == 'missing_owner': workflow.solution_id = None
    elif change == 'inactive': workflow.is_active = False
    elif change == 'scope': workflow.organization_id = uuid4()
    elif change == 'path': workflow.path = 'other.py'
    elif change == 'function': workflow.function_name = 'other'
    elif change == 'name': workflow.name = 'other'
    elif change == 'timeout': workflow.timeout_seconds = 0
    elif change == 'public_endpoint': workflow.public_endpoint = True
    elif change == 'api_key_enabled': workflow.api_key_enabled = True
    elif change == 'roles': workflow.roles = [SimpleNamespace(id=uuid4())]
    elif change == 'legacy_runtime': solution.execution_runtime_mode = 'repo-v1'
    elif change == 'missing_pointer': solution.active_deployment_id = None
    elif change == 'wrong_pointer': solution.active_deployment_id = uuid4()
    elif change == 'inactive_solution': solution.status = 'inactive'
    elif change == 'inactive_deployment': deployment.state = 'ready'
    elif change == 'unactivated': deployment.activated_at = None
    elif change == 'manifest_hash': deployment.compiled_manifest_hash = 'sha256:'+'f'*64
    elif change == 'resolution_hash': deployment.resolution_map_hash = 'sha256:'+'f'*64
    elif change == 'artifact_key': deployment.source_artifact_key = 'mutable/source.zip'
    elif change == 'runtime_prefix': deployment.runtime_storage_prefix = 'mutable/'
    elif change == 'bundle_hash': deployment.bundle_hash = 'sha256:'+'f'*64
    elif change == 'receipt_digest': deployment.validation_result['preflight_evidence_id'] = 'sha256:'+'f'*64
    elif change == 'receipt_release': deployment.validation_result['release_id'] = 'sha256:'+'f'*64
    elif change == 'receipt_row': deployment.validation_result['release_row_id'] = str(uuid4())
    elif change == 'receipt_paths': deployment.validation_result['verified_source_paths'] = []
    elif change == 'receipt_workflows': deployment.validation_result['workflow_ids'] = []
    elif change == 'unreviewed': deployment.validation_result['schema_version'] = 'bifrost.initial-reviewed-workflow-install/v1'
    elif change == 'detached_revision': deployment.validation_result['schema_version'] = readback.SOURCE_MARKER
    elif change == 'archive_bytes': handoff.storage['archive'] = source_archive({handoff.path: b'changed'})
    elif change == 'runtime_bytes': handoff.storage['runtime'] = b'changed'
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
async def test_shadow_loose_uuid_is_not_hidden_by_a_valid_solution(handoff, monkeypatch):
    shadow = SimpleNamespace(id=uuid4(), name='CIPP', type='workflow', is_active=True)
    monkeypatch.setattr('src.services.workspace_promotions.find_workspace_workflow', AsyncMock(return_value=shadow))
    service = WorkspacePromotionPreviewService(handoff.guard.db, uuid4(), repo_storage=SimpleNamespace())
    with pytest.raises(WorkspacePromotionInvalid, match='changed outside its release'):
        await service._current_registration_snapshot(handoff.release)
