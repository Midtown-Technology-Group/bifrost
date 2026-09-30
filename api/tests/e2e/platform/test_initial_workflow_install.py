"""A reviewed workflow recipe can create the first immutable Solution runtime."""

import base64
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.solution_deployments import (
    InitialWorkflowInstallInspectRequest,
    InitialWorkflowInstallRequest,
    SolutionSourceFile,
)
from src.models.orm.solutions import Solution
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_storage import (
    deployment_runtime_prefix,
    deployment_source_artifact_key,
)
from src.services.solutions.workflow_revision_recipe import (
    WORKFLOW_RECIPE_SCHEMA,
    ReviewedWorkflowRecipe,
)

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        outer = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await outer.rollback()


@pytest.fixture
def artifact_store(monkeypatch):
    from src.services.solutions import (
        deployment_api,
        initial_workflow_install,
        resource_delivery,
    )

    objects: dict[tuple[str, str], bytes] = {}

    class Storage:
        def __init__(self, solution_id, candidate_id):
            self.deployment_id = str(candidate_id)
            self.source_artifact_key = deployment_source_artifact_key(solution_id, candidate_id)
            self.runtime_prefix = deployment_runtime_prefix(solution_id, candidate_id)

        async def _write(self, name, content, idempotent=False):
            key = (self.deployment_id, name)
            assert key not in objects or (idempotent and objects[key] == content)
            objects[key] = content

        async def _read(self, name):
            return objects[(self.deployment_id, name)]

        async def write_source_artifact(self, content, *, idempotent=False):
            await self._write("source", content, idempotent)

        async def read_source_artifact(self):
            return await self._read("source")

        async def write_resources_artifact(self, content, *, idempotent=False):
            await self._write("resources", content, idempotent)

        async def read_resources_artifact(self):
            return await self._read("resources")

        async def read_resource(self, path, _size_bytes):
            return await self._read("_resources/" + path)

        async def write_runtime_file(self, path, content, *, idempotent=False):
            await self._write(path, content, idempotent)

        async def read_runtime_file(self, path):
            return await self._read(path)

        async def write_compiled_manifest(self, content, *, idempotent=False):
            await self._write("manifest", content, idempotent)

        async def read_compiled_manifest(self):
            return await self._read("manifest")

    for module in (deployment_api, initial_workflow_install, resource_delivery):
        monkeypatch.setattr(module, "SolutionDeploymentStorage", Storage)
    return objects


def _recipe(solution_id, workflow_id, path, organization_id, *, controls=None, resource_path=None):
    source_files = {path: "solutions/initial.py"}
    resources = {resource_path: "fixtures/rates.json"} if resource_path else {}
    return ReviewedWorkflowRecipe.model_validate({
        "schema_version": WORKFLOW_RECIPE_SCHEMA,
        "solution_id": str(solution_id), "files": source_files, "resources": resources,
        "workflows": [{
            "id": str(workflow_id), "path": path, "function_name": "run",
            "organization_id": str(organization_id) if organization_id else None,
            "runtime_bounds": {
                "max_duration_seconds": 60, "max_external_calls": 10,
                "max_records_read": 100, "max_output_bytes": 4096,
            },
            "controls": controls or {},
        }],
    })


async def _stage_initial(
    db_session, platform_admin, organization_id, artifact_store, *, controls=None,
    resources=None, workflow_organization_id=None, workflow_id=None, occupy_uuid=False,
):
    from src.services.solutions.initial_workflow_install import (
        InitialWorkflowInstallService,
    )

    solution_id, deployment_id = uuid4(), uuid4()
    workflow_id = workflow_id or uuid4()
    path = f"solutions/initial_{uuid4().hex}.py"
    source = (
        b"from bifrost import workflow\n"
        b"@workflow(name='Initial reviewed task', effects=[])\n"
        b"async def run(user: str = 'system'):\n    return user\n"
    )
    if resources:
        source = source.replace(b"from bifrost import workflow", b"from bifrost import workflow, resources")
        source = source.replace(b"return user", b"return resources.read('data/rates.json')")
    solution = Solution(
        id=solution_id, slug=f"initial-{uuid4().hex[:12]}", name="Initial reviewed install",
        organization_id=organization_id, execution_runtime_mode="repo-v1", setup_complete=True,
        allow_outbound_access=False, git_connected=False,
    )
    db_session.add(solution)
    if occupy_uuid:
        db_session.add(Workflow(
            id=workflow_id, name="Inactive UUID owner", function_name="run",
            path="features/inactive_uuid_owner.py", organization_id=None,
            solution_id=None, is_active=False,
        ))
    await db_session.flush()
    recipe = _recipe(
        solution_id, workflow_id, path,
        organization_id if workflow_organization_id is None else workflow_organization_id,
        controls=controls,
        resource_path="data/rates.json" if resources else None,
    )
    body = InitialWorkflowInstallRequest(
        source_commit_sha="a" * 40, reviewed_recipe=recipe,
        files=[SolutionSourceFile(path=path, content_base64=base64.b64encode(source).decode())],
        resources=[SolutionSourceFile(
            path=resource_path, content_base64=base64.b64encode(content).decode(),
        ) for resource_path, content in (resources or {}).items()],
    )
    request = InitialWorkflowInstallInspectRequest(reviewed_recipe=recipe)
    service = InitialWorkflowInstallService(db_session)
    staged = await service.stage(
        solution_id, deployment_id, platform_admin.user_id, body, {path: source}, resources or {},
    )
    return solution, service, request, staged, deployment_id, workflow_id, path


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_id", [None, PROVIDER_ORG_ID])
async def test_initial_reviewed_recipe_stages_and_activates_empty_solution(
    db_session, platform_admin, organization_id, artifact_store,
):
    solution, service, request, staged, deployment_id, workflow_id, _path = await _stage_initial(
        db_session, platform_admin, organization_id, artifact_store,
    )
    solution_id = solution.id
    inspected = await service.inspect(solution_id, deployment_id, request)
    assert staged.evidence_id == inspected.evidence_id
    assert inspected.state == "ready"
    assert await db_session.scalar(select(Workflow.id).where(Workflow.id == workflow_id)) is None

    active = await service.activate(solution_id, deployment_id, request, inspected.evidence_id)
    await db_session.refresh(solution)
    workflow = await db_session.get(Workflow, workflow_id)
    assert active.state == "active"
    assert solution.active_deployment_id == deployment_id
    assert solution.execution_runtime_mode == "deployment-v1"
    assert workflow is not None
    assert workflow.solution_id == solution_id
    assert workflow.endpoint_enabled is False
    assert workflow.public_endpoint is False
    assert workflow.access_level == "role_based"


@pytest.mark.asyncio
@pytest.mark.parametrize("controls,workflow_scope,error", [
    ({"endpoint_enabled": True}, None, "disabled endpoints"),
    ({"role_ids": [str(uuid4())]}, None, "role is missing"),
    ({}, PROVIDER_ORG_ID, "install scope"),
])
async def test_initial_recipe_rejects_unsafe_registration_controls(
    db_session, platform_admin, artifact_store, controls, workflow_scope, error,
):
    from src.services.solutions.source_revision import SolutionSourceRevisionError

    with pytest.raises(SolutionSourceRevisionError, match=error):
        await _stage_initial(
            db_session, platform_admin, None, artifact_store,
            controls=controls, workflow_organization_id=workflow_scope,
        )


@pytest.mark.asyncio
async def test_initial_recipe_rejects_inactive_global_uuid_collision(db_session, platform_admin, artifact_store):
    from src.services.solutions.source_revision import SolutionSourceRevisionConflict

    with pytest.raises(SolutionSourceRevisionConflict, match="UUID is already registered"):
        await _stage_initial(
            db_session, platform_admin, None, artifact_store, occupy_uuid=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact", ["source", "runtime", "resources"])
async def test_initial_preflight_rejects_changed_immutable_bytes(
    db_session, platform_admin, artifact_store, artifact,
):
    resources = {"data/rates.json": b'{"records":[]}'} if artifact == "resources" else None
    solution, service, request, _staged, deployment_id, _workflow_id, path = await _stage_initial(
        db_session, platform_admin, None, artifact_store, resources=resources,
    )
    key = (str(deployment_id), "source" if artifact == "source" else
           "_resources/data/rates.json" if artifact == "resources" else path)
    artifact_store[key] = b"tampered"
    from src.services.solutions.source_revision import SolutionSourceRevisionError

    with pytest.raises(SolutionSourceRevisionError):
        await service.inspect(solution.id, deployment_id, request)
    assert solution.active_deployment_id is None
    assert await db_session.scalar(select(Workflow.id).where(Workflow.solution_id == solution.id)) is None


@pytest.mark.asyncio
async def test_initial_activation_requires_exact_preflight_evidence(db_session, platform_admin, artifact_store):
    solution, service, request, _staged, deployment_id, _workflow_id, _path = await _stage_initial(
        db_session, platform_admin, None, artifact_store,
    )
    from src.services.solutions.source_revision import SolutionSourceRevisionConflict

    with pytest.raises(SolutionSourceRevisionConflict, match="evidence changed"):
        await service.activate(solution.id, deployment_id, request, "sha256:" + "0" * 64)
    assert solution.active_deployment_id is None
    assert await db_session.scalar(select(Workflow.id).where(Workflow.solution_id == solution.id)) is None


@pytest.mark.asyncio
async def test_initial_activation_cas_loss_rolls_back_registrations(db_session, platform_admin, artifact_store, monkeypatch):
    from src.repositories.solution_deployments import SolutionDeploymentRepository
    from src.services.solutions.source_revision import SolutionSourceRevisionConflict

    solution, service, request, staged, deployment_id, _workflow_id, _path = await _stage_initial(
        db_session, platform_admin, None, artifact_store,
    )

    async def lose_pointer_race(*_args, **_kwargs):
        return False

    monkeypatch.setattr(SolutionDeploymentRepository, "compare_and_set_active_deployment", lose_pointer_race)
    with pytest.raises(SolutionSourceRevisionConflict, match="pointer changed"):
        await service.activate(solution.id, deployment_id, request, staged.evidence_id)
    await db_session.rollback()
    assert await db_session.scalar(
        select(Solution.active_deployment_id).where(Solution.id == solution.id)
    ) is None
    assert await db_session.scalar(select(Workflow.id).where(Workflow.solution_id == solution.id)) is None


@pytest.mark.asyncio
async def test_initial_workflow_install_over_http_uses_real_postgres_and_object_store(
    e2e_client, platform_admin, db_session,
):
    """Exercise candidate, preflight, and activation through the running Docker stack."""
    from src.models.orm.executions import Execution
    from src.models.orm.executions import WorkflowExecutionAttempt
    from src.services.solutions.deployment_storage import (
        SolutionDeploymentStorage,
        deployment_manifest_key,
    )

    headers = platform_admin.headers
    token = uuid4().hex[:12]
    slug = f"initial-http-{token}"
    path = f"solutions/initial_http_{token}.py"
    workflow_id, deployment_id = uuid4(), uuid4()
    resource_path = "data/rates.json"
    resource_bytes = b'{"records":[{"rate":4}]}'
    source = (
        "from bifrost import workflow, resources\n"
        "@workflow(name='Initial HTTP reviewed task', effects=[])\n"
        "async def run(user: str = 'system'):\n"
        "    return await resources.read('data/rates.json')\n"
    )
    solution_id = None
    created_solution = False
    try:
        created = e2e_client.post("/api/solutions", headers=headers, json={
            "slug": slug, "name": "Initial HTTP reviewed install", "organization_id": None,
        })
        assert created.status_code == 201, created.text
        solution_id = UUID(created.json()["id"])
        created_solution = True

        recipe = _recipe(solution_id, workflow_id, path, None, resource_path=resource_path)
        candidate_body = {
            "source_commit_sha": "b" * 40,
            "reviewed_recipe": recipe.model_dump(mode="json"),
            "files": [{
                "path": path,
                "content_base64": base64.b64encode(source.encode()).decode(),
            }],
            "resources": [{
                "path": resource_path,
                "content_base64": base64.b64encode(resource_bytes).decode(),
            }],
        }
        base = f"/api/solutions/{solution_id}/deployments/{deployment_id}/initial-workflow"
        staged = e2e_client.post(f"{base}/candidate", headers=headers, json=candidate_body)
        assert staged.status_code == 200, staged.text
        staged_body = staged.json()
        assert staged_body["state"] == "ready"
        assert staged_body["workflow_ids"] == [str(workflow_id)]

        inspect_body = {"reviewed_recipe": recipe.model_dump(mode="json")}
        inspected = e2e_client.post(f"{base}/preflight", headers=headers, json=inspect_body)
        assert inspected.status_code == 200, inspected.text
        evidence_id = inspected.json()["evidence_id"]
        assert evidence_id == staged_body["evidence_id"]

        # Prove the staged closure crossed the HTTP handler into real object storage.
        storage = SolutionDeploymentStorage(solution_id, deployment_id)
        source_archive_bytes = await storage.read_source_artifact()
        resources_archive_bytes = await storage.read_resources_artifact()
        runtime_bytes = await storage.read_runtime_file(path)
        resource_runtime_bytes = await storage.read_resource(resource_path, len(resource_bytes))
        manifest_bytes = await storage.read_compiled_manifest()
        assert source.encode() in source_archive_bytes
        assert resource_bytes in resources_archive_bytes
        assert runtime_bytes == source.encode()
        assert resource_runtime_bytes == resource_bytes
        assert str(deployment_id).encode() in manifest_bytes

        activated = e2e_client.post(
            f"{base}/activate", headers=headers,
            json={**inspect_body, "expected_evidence_id": evidence_id},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["state"] == "active"

        solution = await db_session.get(Solution, solution_id)
        workflow = await db_session.get(Workflow, workflow_id)
        deployment = await db_session.get(SolutionDeployment, deployment_id)
        assert solution is not None
        assert solution.execution_runtime_mode == "deployment-v1"
        assert solution.active_deployment_id == deployment_id
        assert workflow is not None
        assert workflow.solution_id == solution_id
        assert workflow.endpoint_enabled is False
        assert workflow.public_endpoint is False
        assert workflow.access_level == "role_based"
        assert deployment is not None and deployment.state == "active"
        # The live worker stack is running, but reviewed initial activation has
        # no execution/trigger side effect and must not enqueue a run.
        assert await db_session.scalar(
            select(Execution.id).where(Execution.solution_deployment_id == deployment_id)
        ) is None

        # Dispatch explicitly only after activation. The worker must use the
        # immutable pin and read the resource through the signed attempt context.
        from tests.e2e.conftest import execute_workflow_sync

        execution_result = execute_workflow_sync(
            e2e_client, headers, str(workflow_id), request_sync=True, max_wait=60,
        )
        assert execution_result["status"] == "Success", execution_result
        assert execution_result["result"] == resource_bytes.decode("utf-8")
        execution = await db_session.get(Execution, UUID(execution_result["execution_id"]))
        assert execution is not None
        assert execution.solution_deployment_id == deployment_id
        assert execution.runtime_mode == "deployment-v1"
        assert execution.runtime_evidence is not None
        assert execution.runtime_evidence["solution_deployment_id"] == str(deployment_id)
        attempt = await db_session.scalar(
            select(WorkflowExecutionAttempt)
            .where(WorkflowExecutionAttempt.execution_id == execution.id)
            .order_by(WorkflowExecutionAttempt.attempt_number.desc())
        )
        assert attempt is not None
        assert attempt.status == "succeeded"
        assert attempt.worker_id
        assert attempt.runtime_evidence_hash == execution.runtime_evidence_hash
    finally:
        if created_solution and solution_id is not None:
            # Immutable deployment history intentionally prevents public
            # hard-delete. Confirm that guard, then remove only this test's local
            # object bytes; the isolated test stack resets its database after run.
            deletion = e2e_client.delete(
                f"/api/solutions/{solution_id}", headers=headers, params={"confirm": slug},
            )
            assert deletion.status_code == 409, deletion.text
            assert "Referenced resource" in deletion.text
            storage = SolutionDeploymentStorage(solution_id, deployment_id)
            async with storage._client_factory() as client:
                for key in (
                    storage.source_artifact_key,
                    storage.resources_artifact_key,
                    deployment_manifest_key(solution_id, deployment_id),
                    f"{storage.runtime_prefix}{path}",
                    f"{storage.runtime_prefix}_resources/{resource_path}",
                ):
                    await client.delete_object(Bucket=storage._bucket, Key=key)

