"""The guarded handoff moves owners and pointers together and can roll back."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.solution_deployments import (
    WorkspaceLiveHandoffCommitRequest,
    WorkspaceLiveHandoffPreflightRequest,
)
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_runtime import (
    pin_workflow_runtime,
    resolve_pinned_workflow_runtime,
)
from src.services.solutions.deployment_storage import (
    deployment_runtime_prefix,
    deployment_source_artifact_key,
)
from src.services.solutions.live_handoff_commit import (
    WorkspaceLiveHandoffCommitService,
)
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightConflict,
    WorkspaceLiveHandoffPreflightError,
    WorkspaceLiveHandoffPreflightService,
)
from src.services.solutions.live_handoff_source import source_archive
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    pin_workspace_runtime,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_handoff_activation_and_rollback_keep_both_execution_pins(
    db_session, platform_admin, monkeypatch
):
    from src.services import workspace_release_runtime
    from src.services.solutions import (
        live_handoff_commit,
        live_handoff_preflight,
    )
    from src.services.workspace_release_files import WorkspaceReleaseFileView

    solution_id, deployment_id, workflow_id = uuid4(), uuid4(), uuid4()
    path = f"features/handoff_{uuid4().hex}.py"
    source = b"async def run():\n    return 1\n"
    source_hash = sha256_digest(source)
    bounds = {
        "max_duration_seconds": 20,
        "max_external_calls": 10,
        "max_records_read": 100,
        "max_output_bytes": 4096,
    }
    release = WorkspaceReleaseDescriptor(
        release_row_id=uuid4(),
        artifact_id=uuid4(),
        organization_id=PROVIDER_ORG_ID,
        release_id="sha256:" + "a" * 64,
        effective_manifest_id="sha256:" + "b" * 64,
        runtime_storage_prefix="_workspace_releases/test/files/",
        source_hashes={path: source_hash.removeprefix("sha256:")},
        governed_paths=(path,),
        governed_manifest_id="sha256:" + "c" * 64,
        effective_registrations={
            f"{path}::run": {
                "workflow_id": str(workflow_id),
                "path": path,
                "function": "run",
                "name": "Handoff test",
                "type": "workflow",
                "source_sha256": source_hash.removeprefix("sha256:"),
                "organization_id": str(PROVIDER_ORG_ID),
                "is_active": True,
                "endpoint_enabled": False,
                "public_endpoint": False,
                "api_key_enabled": False,
                "access_level": "role_based",
                "role_ids": [],
                "runtime_bounds": bounds,
            }
        },
        effective_registration_manifest_id="sha256:" + "d" * 64,
        source_commit_sha="1" * 40,
        source_tree_sha="2" * 40,
        registration_state_fingerprint="sha256:" + "e" * 64,
    )
    source_key = deployment_source_artifact_key(solution_id, deployment_id)
    runtime_prefix = deployment_runtime_prefix(solution_id, deployment_id)
    entity = RuntimeEntityDefinition(
        portable_ref=f"{path}::run",
        resolved_id=workflow_id,
        definition={
            "path": path,
            "function_name": "run",
            "name": "Handoff test",
            "type": "workflow",
            "organization_id": str(PROVIDER_ORG_ID),
            "timeout_seconds": 20,
            "execution_mode": "async",
            "time_saved": 0,
            "value": 0.0,
            "cache_ttl_seconds": 0,
            "runtime_bounds": bounds,
        },
        source_ref=path,
        source_hash=source_hash,
    )
    resolution = DeploymentResolutionMap(
        workflows={entity.portable_ref: entity},
        sources={
            path: RuntimeSourceResolution(
                object_key=f"{runtime_prefix}{path}", content_hash=source_hash
            )
        },
    )
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash=sha256_digest(source),
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key=source_key, runtime_prefix=runtime_prefix),
        workflows={entity.portable_ref: entity},
    )
    solution = Solution(
        id=solution_id,
        slug=f"live-handoff-{uuid4().hex[:12]}",
        name="Live handoff test",
        organization_id=PROVIDER_ORG_ID,
        execution_runtime_mode="repo-v1",
        setup_complete=True,
        allow_outbound_access=False,
        git_connected=False,
    )
    workflow = Workflow(
        id=workflow_id,
        name="Handoff test",
        function_name="run",
        path=path,
        type="workflow",
        organization_id=PROVIDER_ORG_ID,
        is_active=True,
        endpoint_enabled=False,
        public_endpoint=False,
        api_key_enabled=False,
        access_level="role_based",
        timeout_seconds=30,
        execution_mode="async",
        time_saved=0,
        value=0,
        cache_ttl_seconds=0,
    )
    deployment = SolutionDeployment(
        id=deployment_id,
        organization_id=PROVIDER_ORG_ID,
        solution_id=solution_id,
        state="draft",
        bundle_hash=manifest.bundle_hash,
        compiled_manifest=manifest.model_dump(mode="json", exclude_none=True),
        compiled_manifest_hash=manifest.content_hash(),
        resolution_map=resolution.model_dump(mode="json", exclude_none=True),
        resolution_map_hash=manifest.resolution_map_hash,
        source_artifact_key=source_key,
        runtime_storage_prefix=runtime_prefix,
        created_by=platform_admin.user_id,
    )

    class Storage:
        def __init__(self, *_args):
            self.source_artifact_key = source_key
            self.runtime_prefix = runtime_prefix

        async def read_compiled_manifest(self):
            return manifest.canonical_bytes()

        async def read_runtime_file(self, requested_path):
            assert requested_path == path
            return source

        async def read_source_artifact(self):
            return source_archive({path: source})

    monkeypatch.setattr(
        live_handoff_preflight,
        "active_workspace_release",
        AsyncMock(return_value=release),
    )
    monkeypatch.setattr(
        live_handoff_commit,
        "active_workspace_release",
        AsyncMock(return_value=release),
    )
    monkeypatch.setattr(
        workspace_release_runtime,
        "active_workspace_release",
        AsyncMock(return_value=release),
    )
    monkeypatch.setattr(
        WorkspaceReleaseFileView,
        "from_release",
        lambda *_args: SimpleNamespace(
            read_many=AsyncMock(return_value={path: source})
        ),
    )
    monkeypatch.setattr(live_handoff_preflight, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(live_handoff_commit, "SolutionDeploymentStorage", Storage)

    try:
        db_session.add_all([solution, workflow])
        await db_session.flush()
        db_session.add(deployment)
        await db_session.flush()
        for next_state in ("building", "validated", "ready"):
            deployment.state = next_state
            await db_session.flush()
        await db_session.commit()

        base = WorkspaceLiveHandoffPreflightRequest(
            expected_release_row_id=release.release_row_id,
            expected_release_id=release.release_id,
            expected_artifact_id=release.artifact_id,
            expected_governed_manifest_id=release.governed_manifest_id,
            expected_registration_state_fingerprint=release.registration_state_fingerprint,
            expected_active_deployment_id=None,
            workflow_ids=[workflow_id],
        )
        inspection = await WorkspaceLiveHandoffPreflightService(db_session).inspect(
            solution_id, deployment_id, base
        )
        commit = WorkspaceLiveHandoffCommitRequest(
            **base.model_dump(), expected_evidence_id=inspection.evidence_id
        )
        stale_commit = commit.model_copy(
            update={"expected_evidence_id": "sha256:" + "f" * 64}
        )
        with pytest.raises(WorkspaceLiveHandoffPreflightConflict, match="evidence"):
            await WorkspaceLiveHandoffCommitService(db_session).activate(
                solution_id, deployment_id, stale_commit
            )
        await db_session.rollback()
        await db_session.refresh(solution)
        await db_session.refresh(workflow)
        await db_session.refresh(deployment)
        assert solution.active_deployment_id is None
        assert workflow.solution_id is None
        assert deployment.state == "ready"

        activated = await WorkspaceLiveHandoffCommitService(db_session).activate(
            solution_id, deployment_id, commit
        )
        await db_session.commit()
        await db_session.refresh(solution)
        await db_session.refresh(workflow)
        await db_session.refresh(deployment)
        assert activated.state == "active"
        assert solution.active_deployment_id == deployment_id
        assert solution.execution_runtime_mode == "deployment-v1"
        assert workflow.solution_id == solution_id
        assert deployment.state == "active"
        solution_pin = await pin_workflow_runtime(db_session, workflow_id)
        assert solution_pin is not None
        original_evidence = solution_pin.queue_evidence()

        rollback = WorkspaceLiveHandoffCommitRequest(
            **{
                **base.model_dump(),
                "expected_active_deployment_id": deployment_id,
            },
            expected_evidence_id=inspection.evidence_id,
        )
        monkeypatch.setattr(
            live_handoff_commit,
            "active_workspace_release",
            AsyncMock(return_value=replace(release, release_id="sha256:" + "f" * 64)),
        )
        with pytest.raises(WorkspaceLiveHandoffPreflightConflict, match="Live release"):
            await WorkspaceLiveHandoffCommitService(db_session).rollback(
                solution_id, deployment_id, rollback
            )
        await db_session.rollback()
        await db_session.refresh(solution)
        await db_session.refresh(workflow)
        assert solution.active_deployment_id == deployment_id
        assert workflow.solution_id == solution_id
        monkeypatch.setattr(
            live_handoff_commit,
            "active_workspace_release",
            AsyncMock(return_value=release),
        )

        restored = await WorkspaceLiveHandoffCommitService(db_session).rollback(
            solution_id, deployment_id, rollback
        )
        await db_session.commit()
        await db_session.refresh(solution)
        await db_session.refresh(workflow)
        await db_session.refresh(deployment)
        assert restored.state == "rolled_back_to_live"
        assert solution.active_deployment_id is None
        assert solution.execution_runtime_mode == "repo-v1"
        assert workflow.solution_id is None
        assert deployment.state == "superseded"
        old_pin = await resolve_pinned_workflow_runtime(
            db_session, deployment_id, workflow_id
        )
        assert old_pin.queue_evidence() == original_evidence
        live_pin = await pin_workspace_runtime(db_session, workflow_id)
        assert live_pin is not None
        assert live_pin.queue_evidence()["workspace_release_id"] == release.release_id

        with pytest.raises(WorkspaceLiveHandoffPreflightError):
            await WorkspaceLiveHandoffCommitService(db_session).activate(
                solution_id, deployment_id, commit
            )
    finally:
        await db_session.rollback()
        await db_session.execute(
            update(Solution)
            .where(Solution.id == solution_id)
            .values(active_deployment_id=None)
        )
        await db_session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        await db_session.execute(
            delete(SolutionDeployment).where(SolutionDeployment.id == deployment_id)
        )
        await db_session.execute(delete(Solution).where(Solution.id == solution_id))
        await db_session.commit()
