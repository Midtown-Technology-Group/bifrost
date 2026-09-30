"""A reviewed workflow recipe can create the first immutable Solution runtime."""

import base64
from uuid import uuid4

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

