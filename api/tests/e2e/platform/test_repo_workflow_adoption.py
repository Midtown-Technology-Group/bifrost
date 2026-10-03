"""A populated install changes runtime only with current source/control proof."""

from uuid import uuid4

import pytest
from sqlalchemy import select, update

from src.models.contracts.solution_deployments import (
    InitialWorkflowInstallInspectRequest, InitialWorkflowInstallRequest,
)
from src.models.enums import ExecutionStatus
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.executions import Execution
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.repo_workflow_adoption import RepoWorkflowAdoptionService, _digest_row
from src.services.solutions.source_revision import SolutionSourceRevisionConflict, SolutionSourceRevisionError
from src.services.solutions.workflow_revision import project_workflow_registrations
from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe, compile_workflow_registrations

pytestmark = pytest.mark.e2e
pytest_plugins = ["tests.e2e.platform.test_initial_workflow_install"]


@pytest.fixture
def legacy_store(monkeypatch):
    from src.services.solutions import repo_workflow_adoption
    files = {}

    class Storage:
        def __init__(self, solution_id):
            self.sid = solution_id

        async def list(self):
            return list(files.get(self.sid, {}))

        async def read(self, path):
            return files[self.sid][path]

    monkeypatch.setattr(repo_workflow_adoption, "SolutionStorage", Storage)
    return files


async def _seed(db_session, platform_admin, artifact_store, legacy_store, *, legacy_schema=False,
                with_owned_query=False):
    sid, did, wid = uuid4(), uuid4(), uuid4()
    path = "workflows/adopt.py"
    source = (b"from bifrost import workflow\n"
              b"@workflow(name='Adopted task', effects=[])\n"
              b"async def run():\n    return 'old'\n")
    if with_owned_query:
        source = ("from bifrost import workflow, tables\n"
                  "@workflow(name='Adopted task', effects=[])\n"
                  "async def run(user: str = 'root'):\n"
                  f"    rows = await tables.query('adoption-{sid.hex}', limit=1)\n"
                  "    return {'user': user, 'version': 'old', 'count': rows.total}\n").encode()
    solution = Solution(id=sid, slug=f"adopt-{sid.hex[:12]}", name="Adopted install",
        organization_id=None, execution_runtime_mode="repo-v1", setup_complete=True,
        allow_outbound_access=False, git_connected=False)
    db_session.add(solution)
    await db_session.flush()
    recipe = ReviewedWorkflowRecipe.model_validate({
        "schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": str(sid),
        "files": {path: "solutions/adoption/workflows/adopt.py"}, "workflows": [{
            "id": str(wid), "path": path, "function_name": "run", "organization_id": None,
            "controls": {}, "runtime_bounds": {"max_duration_seconds": 60,
                "max_external_calls": 10, "max_records_read": 100, "max_output_bytes": 4096},
        }],
    })
    entities = compile_workflow_registrations(recipe, {path: source}, WorkflowIndexer(db_session))
    await project_workflow_registrations(db_session, sid, entities, set())
    row = await db_session.get(Workflow, wid)
    row.api_key_hash, row.api_key_enabled, row.api_key_description = "a" * 64, True, "Preserve installed key"
    if legacy_schema:
        schema = [{"name": "user", "type": "string", "required": False,
                   "default_value": "root"}] if with_owned_query else []
        await db_session.execute(update(Workflow).where(Workflow.id == wid).values(parameters_schema=schema))
        await db_session.refresh(row)
    inactive = Workflow(id=uuid4(), solution_id=sid, organization_id=None, name="Inactive retained",
        function_name="dormant", path="workflows/dormant.py", is_active=False, is_orphaned=True)
    table = Table(id=uuid4(), name=f"adoption-{sid.hex}", solution_id=sid, organization_id=None)
    config = SolutionConfigSchema(solution_id=sid, key="preserved_option", type="string", required=False)
    db_session.add_all([inactive, table, config])
    await db_session.flush()
    legacy_store[sid] = {path: source, "workflows/dormant.py": b"def dormant():\n    return 0\n"}
    body = InitialWorkflowInstallRequest(source_commit_sha="c" * 40, reviewed_recipe=recipe,
        files=[{"path": path, "content_base64": "AA=="}])
    request = InitialWorkflowInstallInspectRequest(reviewed_recipe=recipe)
    service = RepoWorkflowAdoptionService(db_session)
    # Compare persisted values: Numeric defaults round-trip as Decimal, not
    # the constructor's integer zero. Adoption independently reads DB rows.
    for item in (row, inactive, table, config):
        await db_session.refresh(item)
    before = {str(item.id): _digest_row(item) for item in (row, inactive, table, config)}
    staged = await service.stage(sid, did, platform_admin.user_id, body, {path: source}, {})
    return solution, did, row, inactive, table, config, service, request, staged, before


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_schema", [False, True])
async def test_adoption_preserves_every_existing_row_and_credentials(
    db_session, platform_admin, artifact_store, legacy_store, legacy_schema,
):
    solution, did, row, inactive, table, config, service, request, staged, before = await _seed(
        db_session, platform_admin, artifact_store, legacy_store, legacy_schema=legacy_schema)
    assert solution.active_deployment_id is None
    assert staged.retained_inactive_workflow_ids == [inactive.id]
    result = await service.activate(solution.id, did, request, staged.evidence_id)
    await db_session.refresh(solution)
    assert result.state == "active"
    assert solution.active_deployment_id == did and solution.execution_runtime_mode == "deployment-v1"
    assert before == {str(item.id): _digest_row(item) for item in (row, inactive, table, config)}
    assert row.api_key_hash == "a" * 64 and row.api_key_enabled
    assert inactive.is_active is False and inactive.is_orphaned
    assert "a" * 64 not in staged.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ExecutionStatus.SCHEDULED, ExecutionStatus.PENDING,
    ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING, ExecutionStatus.STUCK])
async def test_accepted_work_on_inactive_uuid_blocks_without_cancellation(
    db_session, platform_admin, artifact_store, legacy_store, status,
):
    solution, did, _, inactive, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    execution = Execution(workflow_id=inactive.id, workflow_name=inactive.name,
        status=status, executed_by_name="existing caller", runtime_mode="repo-v1")
    db_session.add(execution)
    await db_session.flush()
    with pytest.raises(SolutionSourceRevisionConflict, match="accepted legacy work"):
        await service.activate(solution.id, did, request, staged.evidence_id)
    await db_session.refresh(solution)
    await db_session.refresh(execution)
    assert solution.active_deployment_id is None and execution.status == status
    assert execution.solution_deployment_id is None


@pytest.mark.asyncio
async def test_unfinished_attempt_blocks_even_when_execution_is_terminal(
    db_session, platform_admin, artifact_store, legacy_store,
):
    solution, did, row, _, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    execution = Execution(id=uuid4(), workflow_id=row.id, workflow_name=row.name,
        status=ExecutionStatus.SUCCESS, executed_by_name="existing caller", runtime_mode="repo-v1")
    db_session.add(execution)
    await db_session.flush()
    db_session.add(ExecutionAttempt(logical_job_type="workflow", logical_job_id=execution.id,
        attempt_number=1, policy_identifier="test", workload_class="standard", admission_policy="test",
        mechanism="queue", completed_at=None))
    await db_session.flush()
    with pytest.raises(SolutionSourceRevisionConflict, match="accepted legacy work"):
        await service.activate(solution.id, did, request, staged.evidence_id)
    assert await db_session.scalar(select(Solution.active_deployment_id).where(Solution.id == solution.id)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "inactive_source", "credential", "inactive", "table", "config"])
async def test_adoption_rejects_stale_mutable_source_or_any_retained_control(
    db_session, platform_admin, artifact_store, legacy_store, change,
):
    solution, did, row, inactive, table, config, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    if change == "source":
        legacy_store[solution.id][row.path] += b"\n# concurrent edit\n"
    elif change == "inactive_source":
        legacy_store[solution.id][inactive.path] += b"\n# concurrent edit\n"
    elif change == "credential":
        row.api_key_hash = "b" * 64
    elif change == "inactive":
        await db_session.execute(update(Workflow).where(Workflow.id == inactive.id).values(name="Changed inactive registration"))
    elif change == "table":
        await db_session.execute(update(Table).where(Table.id == table.id).values(description="Changed table contract"))
    elif change == "config":
        await db_session.execute(update(SolutionConfigSchema).where(SolutionConfigSchema.id == config.id).values(required=True))
    await db_session.flush()
    with pytest.raises(SolutionSourceRevisionConflict, match="source or controls changed"):
        await service.activate(solution.id, did, request, staged.evidence_id)
    assert solution.active_deployment_id is None


@pytest.mark.asyncio
async def test_adoption_rejects_stale_review_digest(
    db_session, platform_admin, artifact_store, legacy_store,
):
    solution, did, _, _, _, _, service, request, _, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    with pytest.raises(SolutionSourceRevisionConflict, match="evidence changed"):
        await service.activate(solution.id, did, request, "sha256:" + "0" * 64)
    assert solution.active_deployment_id is None


@pytest.mark.asyncio
async def test_adoption_then_source_successor_preserves_owned_controls_and_old_pin(
    db_session, platform_admin, artifact_store, legacy_store,
):
    from src.models.contracts.solution_deployments import (
        SolutionSourceRevisionCommitRequest, SolutionSourceRevisionInspectRequest,
    )
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.deployment_runtime import (
        DeploymentRuntimeError, pin_workflow_runtime, resolve_pinned_workflow_runtime,
    )
    from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService

    solution, did, row, inactive, table, config, service, request, staged, before = await _seed(
        db_session, platform_admin, artifact_store, legacy_store,
        legacy_schema=True, with_owned_query=True)
    await service.activate(solution.id, did, request, staged.evidence_id)
    old_pin = await pin_workflow_runtime(db_session, row.id)
    assert old_pin is not None and old_pin.deployment_id == did
    assert old_pin.parameters_schema is not None
    assert old_pin.parameters_schema["properties"]["user"]["default"] == "root"
    assert isinstance(row.parameters_schema, list) and row.parameters_schema
    with pytest.raises(DeploymentRuntimeError, match="not executable"):
        await pin_workflow_runtime(db_session, inactive.id)
    base = await db_session.get(SolutionDeployment, did)
    assert base is not None
    expected = SolutionSourceRevisionInspectRequest(
        expected_active_deployment_id=did, expected_active_manifest_hash=base.compiled_manifest_hash)
    new_source = legacy_store[solution.id][row.path].replace(b"'old'", b"'new'")
    next_id = uuid4()
    revision = SolutionWorkflowRevisionService(db_session)
    inspected = await revision.stage_workflows(solution.id, next_id, platform_admin.user_id,
        expected, request.reviewed_recipe, {row.path: new_source}, "d" * 40)
    result = await revision.activate_workflows(solution.id, next_id,
        SolutionSourceRevisionCommitRequest(**expected.model_dump(),
            expected_evidence_id=inspected.evidence_id), request.reviewed_recipe)
    assert result.state == "active"
    await db_session.refresh(row)
    await db_session.refresh(solution)
    new_pin = await pin_workflow_runtime(db_session, row.id)
    assert new_pin is not None
    assert solution.active_deployment_id == next_id and new_pin.deployment_id == next_id
    assert new_pin.parameters_schema == old_pin.parameters_schema
    accepted = await resolve_pinned_workflow_runtime(db_session, did, row.id)
    assert accepted.queue_evidence() == old_pin.queue_evidence()
    assert isinstance(row.parameters_schema, dict)
    assert row.api_key_hash == "a" * 64 and row.api_key_enabled
    for item in (inactive, table, config):
        await db_session.refresh(item)
        assert _digest_row(item) == before[str(item.id)]
    with pytest.raises(DeploymentRuntimeError, match="not executable"):
        await pin_workflow_runtime(db_session, inactive.id)
    successor = await db_session.get(SolutionDeployment, next_id)
    assert successor is not None
    assert successor.compiled_manifest["tables"] == {} and table.solution_id == solution.id
    assert successor.resolution_map["sources"][row.path]["content_hash"] != base.resolution_map["sources"][row.path]["content_hash"]


@pytest.mark.asyncio
async def test_adoption_candidate_cannot_replace_its_staged_legacy_baseline(
    db_session, platform_admin, artifact_store, legacy_store,
):
    solution, did, row, _, _, _, service, request, _, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    from src.models.orm.solution_deployments import SolutionDeployment
    deployment = await db_session.get(SolutionDeployment, did)
    original = dict(deployment.validation_result)
    source = legacy_store[solution.id][row.path]
    legacy_store[solution.id][row.path] += b"\n# concurrent source edit\n"
    body = InitialWorkflowInstallRequest(source_commit_sha="c" * 40,
        reviewed_recipe=request.reviewed_recipe, files=[{"path": row.path, "content_base64": "AA=="}])
    with pytest.raises(SolutionSourceRevisionConflict, match="another legacy baseline"):
        await service.stage(solution.id, did, platform_admin.user_id, body, {row.path: source}, {})
    await db_session.refresh(deployment)
    assert deployment.validation_result == original and solution.active_deployment_id is None


@pytest.mark.asyncio
async def test_adopted_legacy_schema_drift_blocks_source_successor(
    db_session, platform_admin, artifact_store, legacy_store,
):
    from src.models.contracts.solution_deployments import SolutionSourceRevisionInspectRequest
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService
    solution, did, row, _, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store,
        legacy_schema=True, with_owned_query=True)
    await service.activate(solution.id, did, request, staged.evidence_id)
    base = await db_session.get(SolutionDeployment, did)
    assert base is not None
    await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(
        parameters_schema=[{"name": "user", "type": "string", "required": True}]))
    expected = SolutionSourceRevisionInspectRequest(expected_active_deployment_id=did,
        expected_active_manifest_hash=base.compiled_manifest_hash)
    with pytest.raises(SolutionSourceRevisionError, match="registration differs"):
        await SolutionWorkflowRevisionService(db_session).stage_workflows(
            solution.id, uuid4(), platform_admin.user_id, expected, request.reviewed_recipe,
            {row.path: legacy_store[solution.id][row.path]}, "d" * 40)
    assert solution.active_deployment_id == did


@pytest.mark.asyncio
async def test_populated_app_requires_application_runtime_adapter(
    db_session, platform_admin, artifact_store, legacy_store,
):
    from src.models.orm.applications import Application
    solution, did, _, _, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    app = Application(id=uuid4(), name="Retained app", slug=f"adoption-{uuid4().hex}",
        solution_id=solution.id, organization_id=None, app_model="standalone_v2")
    db_session.add(app)
    await db_session.flush()
    with pytest.raises(SolutionSourceRevisionConflict, match="Application requires its reviewed runtime adapter"):
        await service.activate(solution.id, did, request, staged.evidence_id)
    assert solution.active_deployment_id is None and app.active_deployment_id is None


@pytest.mark.asyncio
async def test_adoption_refuses_outbound_root_fallback(
    db_session, platform_admin, artifact_store, legacy_store,
):
    solution, did, _, _, _, _, service, request, _, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    solution.allow_outbound_access = True
    await db_session.flush()
    with pytest.raises(SolutionSourceRevisionError, match="sealed disconnected"):
        await service.inspect(solution.id, did, request)
