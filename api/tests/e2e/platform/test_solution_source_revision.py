"""A reviewed source revision changes one pointer and preserves queued pins."""

import base64
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

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
    DeploymentRuntimeError,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["source", "workflow"])
async def test_revision_cannot_activate_resources_without_reviewed_delivery_adapter(
    db_session, platform_admin, monkeypatch, adapter
):
    from src.models.contracts.solution_deployments import SolutionDeploymentCreate
    from src.services.solutions.deployment_api import SolutionDeploymentAPIService
    from src.services.solutions.deployment_manifest import DeploymentGitProvenance, RuntimeResourceResolution
    from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService
    from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe

    fixture = await _seed_adopted_revision(db_session, platform_admin, monkeypatch)
    service = SolutionSourceRevisionService(db_session)
    prefix = deployment_runtime_prefix(fixture.solution_id, fixture.revision_id)
    resolution = DeploymentResolutionMap.model_validate(fixture.base.resolution_map)
    resource = RuntimeResourceResolution(object_key=f"{prefix}_resources/rates.json",
        content_hash=sha256_digest(b"{}"), size_bytes=2)
    resolution = resolution.model_copy(update={"resources": {"rates.json": resource}, "sources": {
        path: item.model_copy(update={"object_key": prefix + path}) for path, item in resolution.sources.items()}})
    manifest = fixture.manifest.model_copy(update={"deployment_id": fixture.revision_id,
        "source": DeploymentSource(artifact_key=deployment_source_artifact_key(fixture.solution_id, fixture.revision_id),
            runtime_prefix=prefix), "git": DeploymentGitProvenance(commit_sha="f" * 40),
        "resources": resolution.resources, "resolution_map_hash": sha256_digest(canonical_json(resolution))})
    # The generic ready-draft API can store a self-consistent resource contract.
    # Neither reviewed revision adapter may activate it without resource proof.
    candidate = await SolutionDeploymentAPIService(db_session).create_ready_draft(fixture.solution_id,
        platform_admin.user_id, SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution,
            base_deployment_id=fixture.base_id, parent_deployment_id=fixture.base_id, git_commit_sha="f" * 40))
    expected = SolutionSourceRevisionInspectRequest(expected_active_deployment_id=fixture.base_id,
        expected_active_manifest_hash=fixture.manifest.content_hash())
    if adapter == "source":
        with pytest.raises(SolutionSourceRevisionError, match="identities"):
            await service.inspect(fixture.solution_id, candidate.id, expected)
    else:
        recipe = ReviewedWorkflowRecipe.model_validate({"schema_version": "bifrost.solution-workflow-delivery/v1",
            "solution_id": str(fixture.solution_id), "files": {fixture.path: fixture.path},
            "workflows": [{"id": str(fixture.workflow_id), "path": fixture.path, "function_name": "run",
                "organization_id": str(PROVIDER_ORG_ID), "runtime_bounds": {
                    "max_duration_seconds": 20, "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096},
                "controls": {}}], "shared_tables": {name: item.model_dump(mode="json") for name, item in fixture.bindings.items()}})
        with pytest.raises(SolutionSourceRevisionError, match="resources differ"):
            await SolutionWorkflowRevisionService(db_session).inspect_workflows(fixture.solution_id, candidate.id, expected, recipe)
    assert fixture.solution.active_deployment_id == fixture.base_id
    assert fixture.workflow.solution_id == fixture.solution_id


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


async def _seed_adopted_revision(db_session, platform_admin, monkeypatch, *, source_pair=None, organization_id=PROVIDER_ORG_ID, root_file_bindings=None):
    """One synthetic adopted runtime shared by source and Git delivery proofs."""
    from types import SimpleNamespace
    from src.services.solutions import deployment_api, deployment_resources, resource_delivery, source_revision, workflow_revision

    solution_id, base_id, revision_id, workflow_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    path = f"features/revision_{uuid4().hex}.py"
    old_source = b"from bifrost import tables\nasync def run():\n    return 1\n"
    new_source = b"from bifrost import tables\nasync def run():\n    return 2\n"
    if source_pair is not None:
        old_source, new_source = source_pair
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
            "organization_id": str(organization_id) if organization_id else None,
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
        root_file_bindings=root_file_bindings or {},
        workflows={entity.portable_ref: entity},
        sources={
            path: RuntimeSourceResolution(
                object_key=f"{base_prefix}{path}", content_hash=source_hash
            )
        },
    )
    manifest = CompiledDeploymentManifest(
        shared_tables=bindings,
        root_file_bindings=root_file_bindings or {},
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
        organization_id=organization_id,
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
        organization_id=organization_id,
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
        organization_id=organization_id,
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

        async def write_resources_artifact(self, content, *, idempotent=False):
            key = (self.deployment_id, "resources")
            assert key not in objects or (idempotent and objects[key] == content)
            objects[key] = content

        async def read_resources_artifact(self):
            return objects[(self.deployment_id, "resources")]

        async def read_resource(self, requested_path, size_bytes):
            content = objects[(self.deployment_id, "_resources/" + requested_path)]
            assert len(content) == size_bytes
            return content

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
    monkeypatch.setattr(workflow_revision, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(deployment_api, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(resource_delivery, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(deployment_resources, "SolutionDeploymentStorage", Storage)
    db_session.add(solution)
    await db_session.flush()
    db_session.add_all([workflow, base, table])
    await db_session.flush()
    repository = SolutionDeploymentRepository(db_session)
    previous_state = "draft"
    for next_state in ("building", "validated", "ready", "activating", "active"):
        await repository.transition(
            base_id,
            organization_id,
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
        # Registration retirement must stop new admissions without invalidating
        # executions already accepted against a source successor or its base.
        await db_session.execute(update(Workflow).where(
            Workflow.id == workflow_id, Workflow.solution_id == solution_id
        ).values(is_active=False, is_orphaned=True))
        await db_session.commit()
        with pytest.raises(DeploymentRuntimeError, match="not executable"):
            await pin_workflow_runtime(db_session, workflow_id)
        for accepted in (old_pin, new_pin):
            retired_pin = await resolve_pinned_workflow_runtime(
                db_session, accepted.deployment_id, workflow_id
            )
            assert retired_pin.queue_evidence() == accepted.queue_evidence()
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
@pytest.mark.parametrize("with_resources", [False, True])
async def test_git_workflow_revision_adds_registration_updates_defaults_and_repairs_table_drift(
    committed_delivery_db, platform_admin, monkeypatch, async_session_factory, with_resources
):
    from contextlib import asynccontextmanager
    from sqlalchemy import select
    from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.services import operation_receipts
    from src.services.solutions.github_delivery_source import GitDeliveryIdentity, GitDeliverySourceError, VerifiedGitSource
    from src.services.solutions.github_source_delivery import GitSourceDeliveryService
    from src.services.solutions.guard import install_solution_write_guard
    from src.services.solutions.workflow_revision_recipe import WORKFLOW_RECIPE_SCHEMA, ReviewedWorkflowRecipe

    install_solution_write_guard()
    @asynccontextmanager
    async def receipt_context():
        async with async_session_factory() as session:
            yield session
    monkeypatch.setattr(operation_receipts, "get_db_context", receipt_context)
    db = committed_delivery_db
    old = b'from bifrost import workflow, tables\n@workflow(name="Revision test")\nasync def run(user: str = "root"):\n    return user\n'
    new = old.replace(b'"root"', b'"system"') + b'\n@workflow(name="Added")\nasync def added(count: int = 3):\n    return count\n'
    fixture = await _seed_adopted_revision(db, platform_admin, monkeypatch, source_pair=(old, new))
    policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
        repository_id=1197464564, repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
        workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
        ci_workflow_id=257449914, solutions={fixture.solution_id: "config/solution-delivery/fixture.json"})
    added_id = uuid4()
    spec = {"schema_version": WORKFLOW_RECIPE_SCHEMA, "solution_id": str(fixture.solution_id),
        "files": {fixture.path: "solutions/fixture.py"},
        "shared_tables": {name: item.model_dump(mode="json") for name, item in fixture.bindings.items()},
        "workflows": [{"id": str(identity), "path": fixture.path, "function_name": function,
            "organization_id": str(PROVIDER_ORG_ID), "runtime_bounds": {
                "max_duration_seconds": 20, "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096},
            "controls": {}} for identity, function in [(fixture.workflow_id, "run"), (added_id, "added")]]}
    resources = {"data/rates.json": b'{"rate":1}', "scripts/audit.ps1": b'Write-Output "reviewed"'} if with_resources else {}
    if resources:
        spec["resources"] = {path: "fixtures/" + path for path in resources}
    hashes = {fixture.path: sha256_digest(new), **{path: sha256_digest(content) for path, content in resources.items()}}
    recipe = ReviewedWorkflowRecipe.model_validate(spec)
    request = SolutionGitSourceDeliveryRequest(source_commit_sha="a" * 40, ci_run_id=123,
        ci_run_attempt=1, artifact_digest="sha256:" + "b" * 64)
    desired = VerifiedGitSource(fixture.solution_id, request.source_commit_sha, "c" * 40,
        policy.solutions[fixture.solution_id], hashes, {fixture.path: new}, request.artifact_digest, recipe, resources)
    class Reader:
        checks = 0
        cancel_after_stage = True
        async def verify_ci(self, *_):
            self.checks += 1
            if self.cancel_after_stage and self.checks == 2:
                raise GitDeliverySourceError("Source superseded after complete staging")
        async def source(self, *_):
            return desired
    reader = Reader()
    before = await pin_workflow_runtime(db, fixture.workflow_id)
    assert before is not None
    service = GitSourceDeliveryService(db, policy, reader)
    with pytest.raises(GitDeliverySourceError, match="superseded"):
        await service.deliver(fixture.solution_id, request, GitDeliveryIdentity("901", 1))
    await db.rollback()
    assert await db.get(Workflow, added_id) is None
    current = await db.get(Solution, fixture.solution_id)
    assert current.active_deployment_id == fixture.base_id
    staged_objects = dict(fixture.objects)
    reader.cancel_after_stage = False
    result = await service.deliver(fixture.solution_id, request, GitDeliveryIdentity("901", 2))
    assert fixture.objects == staged_objects
    await db.refresh(fixture.workflow)
    assert fixture.workflow.id == fixture.workflow_id
    assert fixture.workflow.parameters_schema["properties"]["user"]["default"] == "system"
    added = await db.get(Workflow, added_id)
    assert added is not None and added.solution_id == fixture.solution_id
    assert added.endpoint_enabled is False and added.public_endpoint is False
    after = await pin_workflow_runtime(db, fixture.workflow_id)
    assert after is not None and after.deployment_id == result.deployment_id
    assert after.parameters_schema["properties"]["user"]["default"] == "system"
    accepted = await resolve_pinned_workflow_runtime(db, before.deployment_id, fixture.workflow_id)
    assert accepted.queue_evidence() == before.queue_evidence()

    # Drifted Root metadata is repaired by reviewing its new exact binding.
    # Neither the table owner nor its document data is adopted by deployment.
    await db.execute(update(Table).where(Table.id == fixture.table.id).values(schema={"type": "object"}))
    await db.commit()
    await db.refresh(fixture.table)
    spec["shared_tables"][fixture.table.name]["metadata_hash"] = table_metadata_hash(fixture.table)
    next_recipe = ReviewedWorkflowRecipe.model_validate(spec)
    desired = VerifiedGitSource(fixture.solution_id, "d" * 40, "e" * 40,
        policy.solutions[fixture.solution_id], hashes, {fixture.path: new},
        "sha256:" + "f" * 64, next_recipe, resources)
    next_request = request.model_copy(update={"source_commit_sha": desired.commit_sha, "artifact_digest": desired.artifact_digest})
    repaired = await service.deliver(fixture.solution_id, next_request, GitDeliveryIdentity("902", 1))
    assert repaired.deployment_id != result.deployment_id
    await db.refresh(fixture.table)
    assert fixture.table.solution_id is None and fixture.table.organization_id is None
    retained = await db.get(SolutionDeployment, result.deployment_id)
    assert retained.compiled_manifest["shared_tables"][fixture.table.name]["metadata_hash"] != spec["shared_tables"][fixture.table.name]["metadata_hash"]

    snapshot_objects = dict(fixture.objects)
    spec["workflows"] = spec["workflows"][:1]
    desired = VerifiedGitSource(fixture.solution_id, "1" * 40, "2" * 40,
        policy.solutions[fixture.solution_id], hashes, {fixture.path: new},
        "sha256:" + "3" * 64, ReviewedWorkflowRecipe.model_validate(spec), resources)
    blocked = request.model_copy(update={"source_commit_sha": desired.commit_sha, "artifact_digest": desired.artifact_digest})
    with pytest.raises(SolutionSourceRevisionError, match="removal requires"):
        await service.deliver(fixture.solution_id, blocked, GitDeliveryIdentity("903", 1))
    await db.rollback()
    assert fixture.objects == snapshot_objects
    current = await db.get(Solution, fixture.solution_id)
    assert current.active_deployment_id == repaired.deployment_id
    assert await db.scalar(select(Workflow.is_active).where(Workflow.id == added_id)) is True


@pytest.mark.asyncio
async def test_git_resource_updates_and_revert_preserve_durable_attempt_reads(
    committed_delivery_db, platform_admin, monkeypatch, async_session_factory
):
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from src.core.auth import ExecutionContext
    from src.core.constants import SYSTEM_USER_UUID
    from src.core.principal import UserPrincipal
    from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution, WorkflowExecutionAttempt
    from src.services import operation_receipts
    from src.services.solutions.deployment_resources import DeploymentResourceDenied, read_execution_resource
    from src.services.solutions.github_delivery_source import GitDeliveryIdentity, VerifiedGitSource
    from src.services.solutions.github_source_delivery import GitSourceDeliveryService
    from src.services.solutions.guard import install_solution_write_guard
    from src.services.solutions.workflow_revision_recipe import WORKFLOW_RECIPE_SCHEMA, ReviewedWorkflowRecipe

    install_solution_write_guard()
    @asynccontextmanager
    async def receipt_context():
        async with async_session_factory() as session:
            yield session
    monkeypatch.setattr(operation_receipts, "get_db_context", receipt_context)
    db = committed_delivery_db
    old = b'from bifrost import workflow, tables\n@workflow(name="Revision test")\nasync def run():\n return 1\n'
    new = b'from bifrost import workflow, resources\nRATE_PATH="data/rates.json"\n@workflow(name="Revision test")\nasync def run():\n return await resources.read(RATE_PATH)\n'
    f = await _seed_adopted_revision(db, platform_admin, monkeypatch, source_pair=(old, new))
    recipe = ReviewedWorkflowRecipe.model_validate({"schema_version": WORKFLOW_RECIPE_SCHEMA,
        "solution_id": str(f.solution_id), "files": {f.path: "solutions/fixture.py"},
        "resources": {"data/rates.json": "data/reviewed.json"},
        "shared_tables": {name: item.model_dump(mode="json") for name, item in f.bindings.items()},
        "workflows": [{"id": str(f.workflow_id), "path": f.path, "function_name": "run",
            "organization_id": str(PROVIDER_ORG_ID), "runtime_bounds": {
                "max_duration_seconds": 20, "max_external_calls": 10, "max_records_read": 100,
                "max_output_bytes": 4096}, "controls": {}}]})
    policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace", repository_id=1197464564,
        repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
        workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
        ci_workflow_id=257449914, solutions={f.solution_id: "config/solution-delivery/fixture.json"})
    desired = None
    class Reader:
        async def verify_ci(self, *_):
            pass
        async def source(self, *_):
            return desired
    service = GitSourceDeliveryService(db, policy, Reader())
    async def deliver(version, content):
        nonlocal desired
        resources = {"data/rates.json": content}
        hashes = {f.path: sha256_digest(new), "data/rates.json": sha256_digest(content)}
        desired = VerifiedGitSource(f.solution_id, str(version) * 40, "a" * 40,
            policy.solutions[f.solution_id], hashes, {f.path: new}, "sha256:" + str(version) * 64,
            recipe, resources)
        request = SolutionGitSourceDeliveryRequest(source_commit_sha=desired.commit_sha, ci_run_id=version,
            ci_run_attempt=1, artifact_digest=desired.artifact_digest)
        producer = GitDeliveryIdentity(str(100 + version), 1)
        result = await service.deliver(f.solution_id, request, producer)
        assert result.source_hashes == hashes and result.runtime_verified is False
        return result, request, producer
    async def accepted_context():
        pin = await pin_workflow_runtime(db, f.workflow_id)
        assert pin is not None
        evidence = pin.queue_evidence()
        execution_id, attempt_id, token = uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)
        db.add(Execution(id=execution_id, workflow_id=f.workflow_id, workflow_name="Revision test",
            executed_by_name="Engine", organization_id=PROVIDER_ORG_ID, status=ExecutionStatus.RUNNING,
            solution_deployment_id=pin.deployment_id, runtime_mode="deployment-v1", runtime_evidence=evidence,
            runtime_evidence_hash=sha256_digest(canonical_json(evidence))))
        await db.flush()
        db.add(WorkflowExecutionAttempt(id=attempt_id, execution_id=execution_id, attempt_number=1,
            claim_token=token, status="claimed", phase="claim", published_at=now, claimed_at=now))
        await db.commit()
        user = UserPrincipal(user_id=SYSTEM_USER_UUID, email="engine@bifrost.internal",
            organization_id=PROVIDER_ORG_ID, is_engine_token=True, engine_execution_id=execution_id,
            engine_attempt_token=token, engine_solution_id=str(f.solution_id))
        return ExecutionContext(user=user, org_id=PROVIDER_ORG_ID, db=db), attempt_id
    v1 = b'{"rate":1}'
    first, _, _ = await deliver(1, v1)
    first_ctx, first_attempt = await accepted_context()
    second, _, _ = await deliver(2, b'{"rate":2}')
    second_ctx, _ = await accepted_context()
    assert first.deployment_id != second.deployment_id
    assert await read_execution_resource(first_ctx, "data/rates.json") == v1
    assert await read_execution_resource(second_ctx, "data/rates.json") == b'{"rate":2}'
    reverted, request, producer = await deliver(3, v1)
    assert reverted.deployment_id not in {first.deployment_id, second.deployment_id}
    assert await read_execution_resource(first_ctx, "data/rates.json") == v1
    assert await read_execution_resource(second_ctx, "data/rates.json") == b'{"rate":2}'
    replay = await service.deliver(f.solution_id, request, producer)
    assert replay.state == "already_active" and replay.deployment_id == reverted.deployment_id

    # Fault injection into synthetic storage proves replay rechecks actual bytes.
    for path, original in [("_resources/data/rates.json", v1), (f.path, new)]:
        key = (str(reverted.deployment_id), path)
        f.objects[key] = original.replace(b"1", b"9") if path.startswith("_resources/") else new + b"# changed\n"
        with pytest.raises(SolutionSourceRevisionError, match="runtime bytes"):
            await service.deliver(f.solution_id, request, producer)
        await db.rollback()
        current = await db.get(Solution, f.solution_id)
        assert current.active_deployment_id == reverted.deployment_id
        f.objects[key] = original
    await db.execute(update(WorkflowExecutionAttempt).where(WorkflowExecutionAttempt.id == first_attempt)
        .values(status="cancelled", phase="terminal", completed_at=datetime.now(UTC)))
    await db.commit()
    with pytest.raises(DeploymentResourceDenied, match="attempt"):
        await read_execution_resource(first_ctx, "data/rates.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_id,explicit_scope", [
    (PROVIDER_ORG_ID, False), (PROVIDER_ORG_ID, True), (None, True),
])
async def test_verified_git_delivery_recovers_cancelled_stage_and_preserves_queue_pin(
    committed_delivery_db, platform_admin, monkeypatch, async_session_factory, organization_id, explicit_scope
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
    fixture = await _seed_adopted_revision(db_session, platform_admin, monkeypatch, organization_id=organization_id)
    (solution_id, base_id, workflow_id, path, new_source, objects) = (
        fixture.solution_id, fixture.base_id, fixture.workflow_id,
        fixture.path, fixture.new_source, fixture.objects
    )
    try:
        policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
            repository_id=1197464564, repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
            workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
            ci_workflow_id=257449914, solutions={solution_id: "config/solution-delivery/fixture.json"},
            **({"solution_organization_ids": {solution_id: organization_id}} if explicit_scope else {}))
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
        assert active.organization_id == organization_id
        assert active.validation_result["github_delivery"]["ci_run_id"] == 123
        assert active.validation_result["github_delivery"]["organization_id"] == (
            str(organization_id) if organization_id is not None else None
        )
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


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_id,explicit_global_scope", [
    (None, False), (PROVIDER_ORG_ID, True),
])
async def test_git_delivery_rejects_install_scope_mismatch_before_any_candidate(
    committed_delivery_db, platform_admin, monkeypatch, organization_id, explicit_global_scope
):
    from sqlalchemy import select
    from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
    from src.models.contracts.solution_deployments import SolutionGitSourceDeliveryRequest
    from src.services.solutions.github_delivery_source import GitDeliveryIdentity, GitDeliverySourceError, VerifiedGitSource
    from src.services.solutions.github_source_delivery import GitSourceDeliveryService

    db = committed_delivery_db
    f = await _seed_adopted_revision(db, platform_admin, monkeypatch, organization_id=organization_id)
    policy = SolutionGitDeliveryPolicy(repository="MTG-Thomas/bifrost-workspace",
        repository_id=1197464564, repository_owner_id=87775189, organization_id=PROVIDER_ORG_ID,
        workflow_path=".github/workflows/deliver-solutions.yml", ci_workflow_path=".github/workflows/ci.yml",
        ci_workflow_id=257449914, solutions={f.solution_id: "config/solution-delivery/fixture.json"},
        **({"solution_organization_ids": {f.solution_id: None}} if explicit_global_scope else {}))
    request = SolutionGitSourceDeliveryRequest(source_commit_sha="a" * 40, ci_run_id=123,
        ci_run_attempt=1, artifact_digest="sha256:" + "b" * 64)
    desired = VerifiedGitSource(f.solution_id, request.source_commit_sha, "c" * 40,
        policy.solutions[f.solution_id], {f.path: sha256_digest(f.new_source)},
        {f.path: f.new_source}, request.artifact_digest)

    class Reader:
        async def verify_ci(self, *_):
            pass
        async def source(self, *_):
            return desired

    objects = dict(f.objects)
    with pytest.raises(GitDeliverySourceError, match="organization/runtime scope"):
        await GitSourceDeliveryService(db, policy, Reader()).deliver(
            f.solution_id, request, GitDeliveryIdentity("789", 1))
    assert f.objects == objects
    assert (await db.get(Solution, f.solution_id)).active_deployment_id == f.base_id
    assert list(await db.scalars(select(SolutionDeployment.id).where(
        SolutionDeployment.solution_id == f.solution_id))) == [f.base_id]
