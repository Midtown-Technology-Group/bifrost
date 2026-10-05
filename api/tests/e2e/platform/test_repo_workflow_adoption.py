"""A populated install changes runtime only with current source/control proof."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from src.core.constants import PROVIDER_ORG_ID
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
from src.models.orm.workspace_promotions import WorkspaceSourceRelease
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.repo_workflow_adoption import RepoWorkflowAdoptionService, _digest_row
from src.services.solutions.source_revision import SolutionSourceRevisionConflict, SolutionSourceRevisionError
from src.services.solutions.workflow_revision import project_workflow_registrations
from src.services.solutions.workflow_revision_recipe import ReviewedWorkflowRecipe, compile_workflow_registrations

from tests.e2e.platform.test_initial_workflow_install import artifact_store as artifact_store
from tests.e2e.platform.test_initial_workflow_install import db_session as db_session

pytestmark = pytest.mark.e2e


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
                with_owned_query=False, legacy_descriptors=False, constant_default=False, legacy_name=False, stage=True):
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
    if constant_default:
        source = source.replace(b"@workflow", b"DEFAULT_USER = 'root'\n@workflow").replace(
            b"user: str = 'root'", b"user: str = DEFAULT_USER")
    if legacy_descriptors:
        source = source.replace(b"effects=[]", b"category='Reviewed category', description='Reviewed description', effects=[]")
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
    if legacy_descriptors:
        await db_session.execute(update(Workflow).where(Workflow.id == wid).values(
            description=None, category="General"))
        await db_session.refresh(row)
    if legacy_name:
        await db_session.execute(update(Workflow).where(Workflow.id == wid).values(name="run"))
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
    staged = await service.stage(sid, did, platform_admin.user_id, body, {path: source}, {}) if stage else None
    return solution, did, row, inactive, table, config, service, request, staged, before


@pytest.mark.asyncio
async def test_adoption_resolves_unchanged_constant_defaults_without_rewriting_legacy_rows(
    db_session, platform_admin, artifact_store, legacy_store,
):
    solution, did, row, inactive, table, config, service, request, staged, before = await _seed(
        db_session, platform_admin, artifact_store, legacy_store,
        legacy_schema=True, with_owned_query=True, constant_default=True)
    result = await service.activate(solution.id, did, request, staged.evidence_id)
    assert result.state == "active"
    assert row.parameters_schema[0]["default_value"] == "root"
    assert before == {str(item.id): _digest_row(item) for item in (row, inactive, table, config)}


@pytest.mark.asyncio
async def test_adoption_rejects_changed_constant_default_before_storing_a_candidate(
    db_session, platform_admin, artifact_store, legacy_store,
):
    solution, did, row, _, _, _, service, request, _, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store,
        legacy_schema=True, with_owned_query=True, constant_default=True, stage=False)
    changed = legacy_store[solution.id][row.path].replace(b"DEFAULT_USER = 'root'", b"DEFAULT_USER = 'other'")
    body = InitialWorkflowInstallRequest(source_commit_sha="c" * 40, reviewed_recipe=request.reviewed_recipe,
        files=[{"path": row.path, "content_base64": "AA=="}])
    with pytest.raises(SolutionSourceRevisionConflict, match="source parameter contract"):
        await service.stage(solution.id, did, platform_admin.user_id, body, {row.path: changed}, {})
    assert solution.active_deployment_id is None


@pytest.mark.asyncio
async def test_adoption_adds_optional_parameter_with_original_rows_and_complete_runtime_pin(
    db_session, platform_admin, artifact_store, legacy_store,
):
    from src.services.solutions.deployment_runtime import pin_workflow_runtime
    solution, did, row, inactive, table, config, service, request, _, before = await _seed(
        db_session, platform_admin, artifact_store, legacy_store,
        legacy_schema=True, with_owned_query=True, stage=False)
    source = legacy_store[solution.id][row.path].replace(
        b"user: str = 'root'", b"user: str = 'root', *, approved_id: int | None = None")
    body = InitialWorkflowInstallRequest(source_commit_sha="c" * 40, reviewed_recipe=request.reviewed_recipe,
        files=[{"path": row.path, "content_base64": "AA=="}])
    staged = await service.stage(solution.id, did, platform_admin.user_id, body, {row.path: source}, {})
    await service.activate(solution.id, did, request, staged.evidence_id)
    assert before == {str(item.id): _digest_row(item) for item in (row, inactive, table, config)}
    pin = await pin_workflow_runtime(db_session, row.id)
    assert pin is not None and pin.parameters_schema is not None
    assert pin.parameters_schema["properties"]["approved_id"]["default"] is None
    assert pin.parameters_schema["properties"]["user"]["default"] == "root"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["old_default", "required", "modern_registry"])
async def test_adoption_rejects_optional_addition_that_changes_existing_admission(
    db_session, platform_admin, artifact_store, legacy_store, change,
):
    from src.models.orm.solution_deployments import SolutionDeployment
    solution, did, row, _, _, _, service, request, _, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store,
        legacy_schema=change != "modern_registry", with_owned_query=True, stage=False)
    argument = b"approved_id: int" if change == "required" else b"approved_id: int | None = None"
    source = legacy_store[solution.id][row.path].replace(
        b"user: str = 'root'", b"user: str = 'root', *, " + argument)
    if change == "old_default":
        source = source.replace(b"user: str = 'root'", b"user: str = 'other'")
    body = InitialWorkflowInstallRequest(source_commit_sha="c" * 40, reviewed_recipe=request.reviewed_recipe,
        files=[{"path": row.path, "content_base64": "AA=="}])
    with pytest.raises(SolutionSourceRevisionConflict, match="source parameter contract"):
        await service.stage(solution.id, did, platform_admin.user_id, body, {row.path: source}, {})
    assert await db_session.get(SolutionDeployment, did) is None
    assert solution.active_deployment_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("successor", [False, True])
async def test_native_runtime_anchor_supersedes_old_source_only_with_matching_closure(
    db_session, platform_admin, artifact_store, legacy_store, successor,
):
    from src.models.contracts.solution_deployments import (
        SolutionSourceRevisionCommitRequest, SolutionSourceRevisionInspectRequest,
    )
    from src.models.contracts.workspace_promotions import (
        WorkspaceSourceSupersessionEvidence, WorkspaceSourceSupersessionPath,
    )
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.deployment_manifest import sha256_digest
    from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService
    from src.services.workspace_source_releases import WorkspaceSourceReleaseConflict, WorkspaceSourceReleaseService

    solution, did, row, _, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store)
    await service.activate(solution.id, did, request, staged.evidence_id)
    source = legacy_store[solution.id][row.path]
    if successor:
        base = await db_session.get(SolutionDeployment, did)
        expected = SolutionSourceRevisionInspectRequest(
            expected_active_deployment_id=did, expected_active_manifest_hash=base.compiled_manifest_hash)
        did = uuid4()
        source = source.replace(b"'old'", b"'new'")
        revision = SolutionWorkflowRevisionService(db_session)
        inspected = await revision.stage_workflows(solution.id, did, platform_admin.user_id,
            expected, request.reviewed_recipe, {row.path: source}, "d" * 40)
        await revision.activate_workflows(solution.id, did, SolutionSourceRevisionCommitRequest(
            **expected.model_dump(), expected_evidence_id=inspected.evidence_id), request.reviewed_recipe)
    digest = sha256_digest(source).removeprefix("sha256:")
    old_path = "features/adoption/workflows/adopt.py"
    record = WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
        source_commit_sha=uuid4().hex + "a" * 8, source_tree_sha="b" * 40,
        paths={old_path: "c" * 64}, declaration_actor="platform_admin",
        disposition="attention_required", declared_disposition="pending", reason="Historical loose source",
        created_by=platform_admin.user_id, created_at=datetime.now(UTC) - timedelta(days=1))
    db_session.add(record)
    await db_session.flush()
    evidence = WorkspaceSourceSupersessionEvidence(superseding_solution_deployment_ids=[did],
        reviewed_global_solution_ids=[solution.id], production_readback_id="sha256:" + "d" * 64,
        verified_at=datetime.now(UTC), paths={old_path: WorkspaceSourceSupersessionPath(
            current_git_sha256=digest, runtime_owner="solution", runtime_ref=str(did),
            runtime_path=row.path, runtime_source_sha256="0" * 64)})
    accounting = WorkspaceSourceReleaseService(db_session, PROVIDER_ORG_ID)
    with pytest.raises(WorkspaceSourceReleaseConflict, match="runtime hash is unverified"):
        await accounting.set_manual_disposition(record.id, disposition="superseded",
            reason="Reviewed native runtime replaces historical source", supersession_evidence=evidence)
    assert record.disposition == "attention_required" and record.completion_evidence is None
    evidence.paths[old_path].runtime_source_sha256 = digest
    result = await accounting.set_manual_disposition(record.id, disposition="superseded",
        reason="Reviewed native runtime replaces historical source", supersession_evidence=evidence)
    assert result.disposition == "superseded"
    assert result.completion_evidence["review"]["paths"][old_path]["runtime_ref"] == str(did)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_schema", [False, True])
@pytest.mark.parametrize("legacy_descriptors", [False, True])
async def test_adoption_preserves_every_existing_row_and_credentials(
    db_session, platform_admin, artifact_store, legacy_store, legacy_schema, legacy_descriptors,
):
    solution, did, row, inactive, table, config, service, request, staged, before = await _seed(
        db_session, platform_admin, artifact_store, legacy_store, legacy_schema=legacy_schema, legacy_descriptors=legacy_descriptors)
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
@pytest.mark.parametrize("change", ["source", "inactive_source", "credential", "inactive", "table", "config", "description", "category", "name"])
async def test_adoption_rejects_stale_mutable_source_or_any_retained_control(
    db_session, platform_admin, artifact_store, legacy_store, change,
):
    solution, did, row, inactive, table, config, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store, legacy_descriptors=True)
    if change == "name":
        await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(name="Changed caller"))
    elif change == "description":
        await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(description=""))
    elif change == "category":
        await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(category="Changed category"))
    elif change == "source":
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
@pytest.mark.parametrize("legacy_name", [False, True])
async def test_adoption_then_source_successor_preserves_owned_controls_and_old_pin(
    db_session, platform_admin, artifact_store, legacy_store, legacy_name,
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
        legacy_schema=True, with_owned_query=True, legacy_descriptors=True, legacy_name=legacy_name)
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
    definition = next(iter(base.resolution_map["workflows"].values()))["definition"]
    assert definition["description"] == "Reviewed description"
    assert definition["category"] == "Reviewed category"
    assert definition["legacy_descriptor_evidence"]["fields"] == {"description": None, "category": "General"}
    assert row.description is None and row.category == "General"
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
    assert new_pin.name == old_pin.name == ("run" if legacy_name else "Adopted task")
    assert row.name == new_pin.name
    assert new_pin.parameters_schema == old_pin.parameters_schema
    accepted = await resolve_pinned_workflow_runtime(db_session, did, row.id)
    assert accepted.queue_evidence() == old_pin.queue_evidence()
    assert isinstance(row.parameters_schema, dict)
    assert row.description == "Reviewed description" and row.category == "Reviewed category"
    assert row.api_key_hash == "a" * 64 and row.api_key_enabled
    for item in (inactive, table, config):
        await db_session.refresh(item)
        assert _digest_row(item) == before[str(item.id)]
    with pytest.raises(DeploymentRuntimeError, match="not executable"):
        await pin_workflow_runtime(db_session, inactive.id)
    successor = await db_session.get(SolutionDeployment, next_id)
    assert successor is not None
    successor_definition = next(iter(successor.resolution_map["workflows"].values()))["definition"]
    assert "legacy_descriptor_evidence" not in successor_definition
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
@pytest.mark.parametrize("change", ["schema", "name"])
async def test_adopted_registration_drift_blocks_source_successor(
    db_session, platform_admin, artifact_store, legacy_store, change,
):
    from src.models.contracts.solution_deployments import SolutionSourceRevisionInspectRequest
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService
    solution, did, row, _, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store,
        legacy_schema=True, with_owned_query=True, legacy_name=True)
    await service.activate(solution.id, did, request, staged.evidence_id)
    base = await db_session.get(SolutionDeployment, did)
    assert base is not None
    drift = {"name": "Renamed caller"} if change == "name" else {
        "parameters_schema": [{"name": "user", "type": "string", "required": True}]}
    await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(**drift))
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


@pytest_asyncio.fixture
async def committed_adoption_db(async_session_factory):
    # Real HTTP and workers use independent sessions; a savepoint fixture would
    # hide this synthetic populated install even after session.commit().
    async with async_session_factory() as session:
        yield session
        await session.rollback()


@pytest.mark.asyncio
async def test_nullable_legacy_adoption_over_http_preserves_rows_and_executes_sealed_bytes(
    committed_adoption_db, platform_admin, e2e_client,
):
    """The populated NULL descriptor failure crosses real HTTP, SQL, S3 and worker boundaries."""
    import base64
    from uuid import UUID

    from src.models.orm.executions import WorkflowExecutionAttempt
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.deployment_storage import SolutionDeploymentStorage, deployment_manifest_key
    from src.services.solutions.storage import SolutionStorage
    from tests.e2e.conftest import execute_workflow_sync

    db_session = committed_adoption_db
    legacy_files = {}
    solution, did, row, inactive, table, config, _, request, _, before = await _seed(
        db_session, platform_admin, None, legacy_files, legacy_descriptors=True, stage=False)
    storage = SolutionStorage(solution.id)
    try:
        for path, content in legacy_files[solution.id].items():
            await storage.write(path, content)
        await db_session.commit()
        source = legacy_files[solution.id][row.path]
        base = f"/api/solutions/{solution.id}/deployments/{did}/repo-workflow-adoption"
        inspect_body = {"reviewed_recipe": request.reviewed_recipe.model_dump(mode="json")}
        staged = e2e_client.post(f"{base}/candidate", headers=platform_admin.headers, json={
            **inspect_body, "source_commit_sha": "c" * 40,
            "files": [{"path": row.path, "content_base64": base64.b64encode(source).decode()}],
        })
        assert staged.status_code == 200, staged.text
        inspected = e2e_client.post(f"{base}/preflight", headers=platform_admin.headers, json=inspect_body)
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()["evidence_id"] == staged.json()["evidence_id"]
        activated = e2e_client.post(f"{base}/activate", headers=platform_admin.headers, json={
            **inspect_body, "expected_evidence_id": inspected.json()["evidence_id"],
        })
        assert activated.status_code == 200, activated.text
        for item in (solution, row, inactive, table, config):
            await db_session.refresh(item)
        assert solution.active_deployment_id == did and solution.execution_runtime_mode == "deployment-v1"
        assert before == {str(item.id): _digest_row(item) for item in (row, inactive, table, config)}
        deployment = await db_session.get(SolutionDeployment, did)
        assert deployment is not None
        definition = next(iter(deployment.resolution_map["workflows"].values()))["definition"]
        assert definition["legacy_descriptor_evidence"]["fields"] == {"description": None, "category": "General"}
        assert await SolutionDeploymentStorage(solution.id, did).read_runtime_file(row.path) == source
        assert await storage.read(row.path) == source
        result = execute_workflow_sync(e2e_client, platform_admin.headers, str(row.id), request_sync=True, max_wait=60)
        assert result["status"] == "Success" and result["result"] == "old", result
        execution = await db_session.get(Execution, UUID(result["execution_id"]))
        assert execution is not None and execution.solution_deployment_id == did
        assert execution.runtime_mode == "deployment-v1"
        attempt = await db_session.scalar(select(WorkflowExecutionAttempt).where(
            WorkflowExecutionAttempt.execution_id == execution.id))
        assert attempt is not None and attempt.status == "succeeded" and attempt.worker_id
        assert attempt.runtime_evidence_hash == execution.runtime_evidence_hash
    finally:
        for path in legacy_files[solution.id]:
            await storage.delete(path)
        immutable = SolutionDeploymentStorage(solution.id, did)
        async with immutable._client_factory() as client:
            for key in (immutable.source_artifact_key, deployment_manifest_key(solution.id, did),
                        f"{immutable.runtime_prefix}{row.path}"):
                await client.delete_object(Bucket=immutable._bucket, Key=key)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "fields", "source_descriptors", "name_missing", "name_source", "name_installed", "name_extra"])
async def test_preflight_recompiles_legacy_evidence_even_if_artifact_hashes_are_resealed(
    db_session, platform_admin, artifact_store, legacy_store, monkeypatch, change,
):
    from copy import deepcopy
    from types import SimpleNamespace
    from typing import cast

    from src.services.solutions import repo_workflow_adoption

    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.deployment_manifest import CompiledDeploymentManifest, canonical_json, sha256_digest

    solution, did, _, _, _, _, service, request, _, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store, legacy_descriptors=True, legacy_name=True)
    deployment = await db_session.get(SolutionDeployment, did)
    resolution = deepcopy(deployment.resolution_map)
    definition = next(iter(resolution["workflows"].values()))["definition"]
    if change == "missing":
        del definition["legacy_descriptor_evidence"]
    elif change == "fields":
        from src.services.solutions.source_revision import legacy_descriptor_evidence
        definition["legacy_descriptor_evidence"] = legacy_descriptor_evidence({"description": "", "category": "General"})
    elif change == "source_descriptors":
        definition["description"] = "Other source description"
    elif change == "name_missing":
        del definition["legacy_registration_name_evidence"]
    else:
        from src.services.solutions.source_revision import legacy_registration_name_evidence
        installed = "other" if change == "name_installed" else "run"
        declared = "Other declaration" if change == "name_source" else "Adopted task"
        definition["legacy_registration_name_evidence"] = legacy_registration_name_evidence(installed, declared)
        definition["name"] = installed
        if change == "name_extra":
            definition["legacy_registration_name_evidence"]["fields"]["access_level"] = "authenticated"

    manifest_data = deepcopy(deployment.compiled_manifest)
    manifest_data["workflows"] = resolution["workflows"]
    manifest_data["resolution_map_hash"] = sha256_digest(canonical_json(resolution))
    manifest = CompiledDeploymentManifest.model_validate(manifest_data)
    # SQL correctly forbids rewriting a ready closure. Inject a forged read at
    # the artifact-inspection boundary to exercise its independent recompilation
    # defense; leave the real immutable database row and its trigger intact.
    forged = SimpleNamespace(
        resolution_map=resolution, resolution_map_hash=manifest.resolution_map_hash,
        compiled_manifest=manifest.model_dump(mode="json", exclude_none=True),
        compiled_manifest_hash=sha256_digest(manifest.canonical_bytes()),
        dependencies=deployment.dependencies, git_commit_sha=deployment.git_commit_sha,
        source_artifact_key=deployment.source_artifact_key,
        runtime_storage_prefix=deployment.runtime_storage_prefix,
    )
    inspect_artifact = repo_workflow_adoption.inspect_reviewed_artifact

    async def inspect_forged(sid, candidate_id, observed, *args, **kwargs):
        assert observed.id == did
        return await inspect_artifact(sid, candidate_id, cast(SolutionDeployment, forged), *args, **kwargs)

    monkeypatch.setattr(repo_workflow_adoption, "inspect_reviewed_artifact", inspect_forged)
    artifact_store[(str(did), "manifest")] = manifest.canonical_bytes()
    with pytest.raises(SolutionSourceRevisionConflict, match="differs from reviewed recipe"):
        await service.inspect(solution.id, did, request)
    assert solution.active_deployment_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_name", [False, True])
async def test_source_only_successor_retains_exact_legacy_descriptor_evidence_and_old_pin(
    db_session, platform_admin, artifact_store, legacy_store, legacy_name,
):
    import base64

    from src.models.contracts.solution_deployments import SolutionSourceRevisionCommitRequest, SolutionSourceRevisionRequest
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.deployment_runtime import pin_workflow_runtime, resolve_pinned_workflow_runtime
    from src.services.solutions.source_revision import SolutionSourceRevisionService

    solution, did, row, _, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store, legacy_descriptors=True, legacy_name=legacy_name)
    await service.activate(solution.id, did, request, staged.evidence_id)
    base = await db_session.get(SolutionDeployment, did)
    assert base is not None
    old_pin = await pin_workflow_runtime(db_session, row.id)
    assert old_pin is not None
    source = legacy_store[solution.id][row.path].replace(b"'old'", b"'new'")
    next_id = uuid4()
    revision = SolutionSourceRevisionService(db_session)
    body = SolutionSourceRevisionRequest(expected_active_deployment_id=did,
        expected_active_manifest_hash=base.compiled_manifest_hash, source_commit_sha="d" * 40,
        files=[{"path": row.path, "content_base64": base64.b64encode(source).decode()}])
    staged_revision = await revision.stage(solution.id, next_id, platform_admin.user_id, body)
    await revision.activate(solution.id, next_id, SolutionSourceRevisionCommitRequest(
        expected_active_deployment_id=did, expected_active_manifest_hash=base.compiled_manifest_hash,
        expected_evidence_id=staged_revision.evidence_id))
    await db_session.refresh(row)
    assert row.description is None and row.category == "General"
    assert row.name == ("run" if legacy_name else "Adopted task")
    successor = await db_session.get(SolutionDeployment, next_id)
    assert next(iter(successor.resolution_map["workflows"].values()))["definition"] == next(iter(base.resolution_map["workflows"].values()))["definition"]
    new_pin = await pin_workflow_runtime(db_session, row.id)
    assert new_pin is not None
    assert new_pin.deployment_id == next_id and new_pin.source_hash != old_pin.source_hash
    assert (await resolve_pinned_workflow_runtime(db_session, did, row.id)).queue_evidence() == old_pin.queue_evidence()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_name", [False, True])
async def test_reviewed_workflow_successor_rejects_source_rename_for_native_adoption(
    db_session, platform_admin, artifact_store, legacy_store, legacy_name,
):
    from src.models.contracts.solution_deployments import SolutionSourceRevisionInspectRequest
    from src.models.orm.solution_deployments import SolutionDeployment
    from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService

    solution, did, row, _, _, _, service, request, staged, _ = await _seed(
        db_session, platform_admin, artifact_store, legacy_store, legacy_name=legacy_name)
    await service.activate(solution.id, did, request, staged.evidence_id)
    base = await db_session.get(SolutionDeployment, did)
    expected = SolutionSourceRevisionInspectRequest(expected_active_deployment_id=did,
        expected_active_manifest_hash=base.compiled_manifest_hash)
    renamed = legacy_store[solution.id][row.path].replace(b"Adopted task", b"Renamed task")
    with pytest.raises(SolutionSourceRevisionError, match="source registration name changed|Rename, scope"):
        await SolutionWorkflowRevisionService(db_session).stage_workflows(
            solution.id, uuid4(), platform_admin.user_id, expected, request.reviewed_recipe,
            {row.path: renamed}, "d" * 40)
    assert solution.active_deployment_id == did
