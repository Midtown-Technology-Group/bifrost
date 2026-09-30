"""A reviewed source revision changes one pointer and preserves queued pins."""

import base64
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

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
from src.models.orm.tables import Table
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
    SharedRootTableBinding,
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
from src.services.solutions.live_handoff_source import source_archive
from src.services.solutions.shared_table_bindings import table_metadata_hash
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict,
    SolutionSourceRevisionError,
    SolutionSourceRevisionService,
)

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        outer = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await outer.rollback()


async def _seed_adopted_revision(db_session, platform_admin, monkeypatch):
    """One synthetic adopted runtime shared by source and Git delivery proofs."""
    from types import SimpleNamespace
    from src.services.solutions import deployment_api, source_revision

    solution_id, base_id, revision_id, workflow_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    path = f"features/revision_{uuid4().hex}.py"
    old_source = b"from bifrost import tables\nasync def run():\n    return 1\n"
    new_source = b"from bifrost import tables\nasync def run():\n    return 2\n"
    table = Table(id=uuid4(), name=f"revision_state_{uuid4().hex}", organization_id=None)
    bindings = {table.name: SharedRootTableBinding(table_id=table.id, metadata_hash=table_metadata_hash(table))}
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
        shared_tables=bindings,
        workflows={entity.portable_ref: entity},
        sources={
            path: RuntimeSourceResolution(
                object_key=f"{base_prefix}{path}", content_hash=source_hash
            )
        },
    )
    manifest = CompiledDeploymentManifest(
        shared_tables=bindings,
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
        state="draft",
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
    objects: dict[tuple[str, str], bytes] = {
        (str(base_id), "source"): source_archive({path: old_source})
    }

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
    db_session.add(solution)
    await db_session.flush()
    db_session.add_all([workflow, base, table])
    await db_session.flush()
    repository = SolutionDeploymentRepository(db_session)
    previous_state = "draft"
    for next_state in ("building", "validated", "ready", "activating", "active"):
        await repository.transition(
            base_id,
            PROVIDER_ORG_ID,
            expected_state=previous_state,
            new_state=next_state,
        )
        previous_state = next_state
    solution.active_deployment_id = base_id
    await db_session.commit()
    await db_session.refresh(base)

    return SimpleNamespace(solution_id=solution_id, base_id=base_id, revision_id=revision_id, workflow_id=workflow_id, path=path, old_source=old_source, new_source=new_source, bindings=bindings, manifest=manifest, objects=objects, solution=solution, base=base, workflow=workflow, table=table)


@pytest.mark.asyncio
async def test_source_revision_keeps_old_queue_pin_and_fences_stale_review(
    db_session, platform_admin, monkeypatch
):
    fixture = await _seed_adopted_revision(db_session, platform_admin, monkeypatch)
    (solution_id, base_id, revision_id, workflow_id, path, new_source, manifest, solution, base) = (
        fixture.solution_id, fixture.base_id, fixture.revision_id, fixture.workflow_id,
        fixture.path, fixture.new_source, fixture.manifest, fixture.solution, fixture.base
    )
    try:
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
        missing_entrypoint = request.model_copy(
            update={
                "files": [
                    SolutionSourceFile(
                        path=path,
                        content_base64=base64.b64encode(
                            new_source.replace(b"run", b"renamed")
                        ).decode("ascii"),
                    )
                ]
            }
        )
        with pytest.raises(SolutionSourceRevisionError, match="function"):
            await SolutionSourceRevisionService(db_session).stage(
                solution_id,
                revision_id,
                platform_admin.user_id,
                missing_entrypoint,
            )
        await db_session.rollback()
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
        successor = await db_session.get(SolutionDeployment, revision_id)
        assert successor is not None
        assert successor.compiled_manifest["shared_tables"] == base.compiled_manifest["shared_tables"]
        assert successor.resolution_map["shared_tables"] == base.resolution_map["shared_tables"]
        new_pin = await pin_workflow_runtime(db_session, workflow_id)
        assert new_pin is not None and new_pin.deployment_id == revision_id
        queued_pin = await resolve_pinned_workflow_runtime(
            db_session, base_id, workflow_id
        )
        assert queued_pin.queue_evidence() == old_pin.queue_evidence()
    finally:
        await db_session.rollback()


@pytest_asyncio.fixture
async def committed_delivery_db(async_session_factory):
    from sqlalchemy import text
    async with async_session_factory() as session:
        assert await session.scalar(text("SELECT current_database()")) == "bifrost_test"
        try:
            yield session
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_verified_git_delivery_recovers_cancelled_stage_and_preserves_queue_pin(
    committed_delivery_db, platform_admin, monkeypatch, async_session_factory
):
    db_session = committed_delivery_db
    from contextlib import asynccontextmanager
    from sqlalchemy import select
    from src.services import operation_receipts
    from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.models.orm.operation_receipts import OperationReceipt
    from src.services.solutions.github_delivery_source import GitDeliveryIdentity, GitDeliverySourceError, VerifiedGitSource
    from src.services.solutions.github_source_delivery import GitSourceDeliveryService
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()

    @asynccontextmanager
    async def receipt_context():
        async with async_session_factory() as session:
            yield session
    monkeypatch.setattr(operation_receipts, "get_db_context", receipt_context)
    fixture = await _seed_adopted_revision(db_session, platform_admin, monkeypatch)
    (solution_id, base_id, workflow_id, path, new_source, objects) = (
        fixture.solution_id, fixture.base_id, fixture.workflow_id,
        fixture.path, fixture.new_source, fixture.objects
    )
    try:
        policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
            repository_id=1197464564, repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
            workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
            ci_workflow_id=257449914, solutions={solution_id: "config/solution-delivery/fixture.json"})
        request = SolutionGitSourceDeliveryRequest(source_commit_sha="a" * 40, ci_run_id=123,
            ci_run_attempt=1, artifact_digest="sha256:" + "b" * 64)
        desired = VerifiedGitSource(solution_id, request.source_commit_sha, "c" * 40,
            policy.solutions[solution_id], {path: sha256_digest(new_source)}, {path: new_source}, request.artifact_digest)

        class Reader:
            def __init__(self):
                self.checks = 0
                self.cancel_after_stage = True
            async def verify_ci(self, *_):
                self.checks += 1
                if self.cancel_after_stage and self.checks == 2:
                    raise GitDeliverySourceError("Source was superseded before activation")
            async def source(self, *_):
                return desired

        reader = Reader()
        old_pin = await pin_workflow_runtime(db_session, workflow_id)
        assert old_pin is not None
        service = GitSourceDeliveryService(db_session, policy, reader)
        with pytest.raises(GitDeliverySourceError, match="superseded"):
            await service.deliver(solution_id, request, GitDeliveryIdentity("456", 1))
        await db_session.rollback()
        unchanged = await db_session.get(Solution, solution_id)
        assert unchanged.active_deployment_id == base_id
        staged = (await db_session.scalars(select(SolutionDeployment).where(
            SolutionDeployment.solution_id == solution_id, SolutionDeployment.state == "ready"))).one()
        staged_id = staged.id
        snapshot_objects = dict(objects)
        reader.cancel_after_stage = False
        result = await service.deliver(solution_id, request, GitDeliveryIdentity("456", 2))
        assert result.state == "active" and result.deployment_id == staged_id
        assert result.model_dump(mode="json")["source_verified"] is True
        assert result.model_dump(mode="json")["runtime_verified"] is False
        assert objects == snapshot_objects  # Same create-only staging bytes.
        active = await db_session.get(SolutionDeployment, result.deployment_id)
        assert active.validation_result["github_delivery"]["ci_run_id"] == 123
        receipt = await db_session.get(OperationReceipt, result.receipt_id)
        assert receipt.status == "succeeded"
        queued = await resolve_pinned_workflow_runtime(db_session, base_id, workflow_id)
        assert queued.queue_evidence() == old_pin.queue_evidence()
        current = await pin_workflow_runtime(db_session, workflow_id)
        assert current.deployment_id == result.deployment_id
        replay = await service.deliver(solution_id, request, GitDeliveryIdentity("456", 2))
        assert replay.state == "already_active" and replay.deployment_id == result.deployment_id
        assert replay.receipt_id == result.receipt_id and objects == snapshot_objects
    finally:
        # Immutable history and permanent receipts are not row-deleted. The
        # canonical test.sh phase reset destroys this disposable test DB.
        await db_session.rollback()
