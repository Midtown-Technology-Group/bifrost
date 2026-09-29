"""A reviewed source revision changes one pointer and preserves queued pins."""

import base64
from uuid import uuid4

import pytest
from sqlalchemy import delete, update

from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.solution_deployments import (
    SolutionSourceFile,
    SolutionSourceRevisionCommitRequest,
    SolutionSourceRevisionInspectRequest,
    SolutionSourceRevisionRequest,
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
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict,
    SolutionSourceRevisionService,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_source_revision_keeps_old_queue_pin_and_fences_stale_review(
    db_session, platform_admin, monkeypatch
):
    from src.services.solutions import deployment_api, source_revision

    solution_id, base_id, revision_id, workflow_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    path = f"features/revision_{uuid4().hex}.py"
    old_source = b"async def run():\n    return 1\n"
    new_source = b"async def run():\n    return 2\n"
    base_prefix = deployment_runtime_prefix(solution_id, base_id)
    source_hash = sha256_digest(old_source)
    bounds = {
        "max_duration_seconds": 20,
        "max_external_calls": 10,
        "max_records_read": 100,
        "max_output_bytes": 4096,
    }
    entity = RuntimeEntityDefinition(
        portable_ref=f"{path}::run",
        resolved_id=workflow_id,
        definition={
            "path": path,
            "function_name": "run",
            "name": "Revision test",
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
                object_key=f"{base_prefix}{path}", content_hash=source_hash
            )
        },
    )
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=base_id,
        bundle_hash=sha256_digest(old_source),
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(
            artifact_key=deployment_source_artifact_key(solution_id, base_id),
            runtime_prefix=base_prefix,
        ),
        workflows={entity.portable_ref: entity},
    )
    solution = Solution(
        id=solution_id,
        slug=f"source-revision-{uuid4().hex[:12]}",
        name="Source revision test",
        organization_id=PROVIDER_ORG_ID,
        execution_runtime_mode="deployment-v1",
        setup_complete=True,
        allow_outbound_access=False,
        git_connected=False,
    )
    workflow = Workflow(
        id=workflow_id,
        name="Revision test",
        function_name="run",
        path=path,
        type="workflow",
        organization_id=PROVIDER_ORG_ID,
        solution_id=solution_id,
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
    base = SolutionDeployment(
        id=base_id,
        organization_id=PROVIDER_ORG_ID,
        solution_id=solution_id,
        state="active",
        bundle_hash=manifest.bundle_hash,
        compiled_manifest=manifest.model_dump(mode="json", exclude_none=True),
        compiled_manifest_hash=manifest.content_hash(),
        resolution_map=resolution.model_dump(mode="json", exclude_none=True),
        resolution_map_hash=manifest.resolution_map_hash,
        source_artifact_key=manifest.source.artifact_key,
        runtime_storage_prefix=base_prefix,
        created_by=platform_admin.user_id,
        validation_result={"schema_version": "bifrost.workspace-live-handoff/v1"},
    )
    objects: dict[tuple[str, str], bytes] = {}

    class Storage:
        def __init__(self, _solution_id, deployment_id):
            self.deployment_id = str(deployment_id)
            self.source_artifact_key = deployment_source_artifact_key(
                solution_id, deployment_id
            )
            self.runtime_prefix = deployment_runtime_prefix(solution_id, deployment_id)

        async def write_source_artifact(self, content, *, idempotent=False):
            key = (self.deployment_id, "source")
            assert key not in objects or (idempotent and objects[key] == content)
            objects[key] = content

        async def read_source_artifact(self):
            return objects[(self.deployment_id, "source")]

        async def write_runtime_file(
            self, requested_path, content, *, idempotent=False
        ):
            key = (self.deployment_id, requested_path)
            assert key not in objects or (idempotent and objects[key] == content)
            objects[key] = content

        async def read_runtime_file(self, requested_path):
            return objects[(self.deployment_id, requested_path)]

        async def write_compiled_manifest(self, content, *, idempotent=False):
            key = (self.deployment_id, "manifest")
            assert key not in objects or (idempotent and objects[key] == content)
            objects[key] = content

        async def read_compiled_manifest(self):
            return objects[(self.deployment_id, "manifest")]

    monkeypatch.setattr(source_revision, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(deployment_api, "SolutionDeploymentStorage", Storage)
    try:
        db_session.add(solution)
        await db_session.flush()
        db_session.add_all([workflow, base])
        await db_session.flush()
        solution.active_deployment_id = base_id
        await db_session.commit()

        old_pin = await pin_workflow_runtime(db_session, workflow_id)
        assert old_pin is not None and old_pin.deployment_id == base_id
        request = SolutionSourceRevisionRequest(
            expected_active_deployment_id=base_id,
            expected_active_manifest_hash=manifest.content_hash(),
            source_commit_sha="f" * 40,
            files=[
                SolutionSourceFile(
                    path=path,
                    content_base64=base64.b64encode(new_source).decode("ascii"),
                )
            ],
        )
        inspection = await SolutionSourceRevisionService(db_session).stage(
            solution_id, revision_id, platform_admin.user_id, request
        )
        await db_session.commit()
        assert inspection.state == "ready"
        assert inspection.workflow_ids == [workflow_id]
        assert inspection.source_hashes[path] == sha256_digest(new_source)
        expected = SolutionSourceRevisionInspectRequest(
            expected_active_deployment_id=base_id,
            expected_active_manifest_hash=manifest.content_hash(),
        )
        stale = SolutionSourceRevisionCommitRequest(
            **expected.model_dump(), expected_evidence_id="sha256:" + "0" * 64
        )
        with pytest.raises(SolutionSourceRevisionConflict, match="evidence"):
            await SolutionSourceRevisionService(db_session).activate(
                solution_id, revision_id, stale
            )
        await db_session.rollback()
        await db_session.refresh(solution)
        assert solution.active_deployment_id == base_id

        reviewed = SolutionSourceRevisionCommitRequest(
            **expected.model_dump(), expected_evidence_id=inspection.evidence_id
        )
        activated = await SolutionSourceRevisionService(db_session).activate(
            solution_id, revision_id, reviewed
        )
        await db_session.commit()
        await db_session.refresh(solution)
        await db_session.refresh(base)
        assert activated.state == "active"
        assert solution.active_deployment_id == revision_id
        assert base.state == "superseded"
        new_pin = await pin_workflow_runtime(db_session, workflow_id)
        assert new_pin is not None and new_pin.deployment_id == revision_id
        queued_pin = await resolve_pinned_workflow_runtime(
            db_session, base_id, workflow_id
        )
        assert queued_pin.queue_evidence() == old_pin.queue_evidence()
    finally:
        await db_session.rollback()
        await db_session.execute(
            update(Solution)
            .where(Solution.id == solution_id)
            .values(active_deployment_id=None)
        )
        await db_session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        await db_session.execute(
            delete(SolutionDeployment).where(SolutionDeployment.id == revision_id)
        )
        await db_session.execute(
            delete(SolutionDeployment).where(SolutionDeployment.id == base_id)
        )
        await db_session.execute(delete(Solution).where(Solution.id == solution_id))
        await db_session.commit()
