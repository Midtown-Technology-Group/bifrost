"""One retirement transaction seals obsolete rows without losing historical pins."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from src.core.constants import PROVIDER_ORG_ID
from src.core.security import mint_engine_token
from src.models.contracts.workspace_promotions import WorkspaceLiveRetireRequest
from src.models.enums import ExecutionStatus
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from src.models.orm.workspace_promotions import WorkspaceSourceRelease
from src.services.audit_context import ActorContext, clear_actor, set_actor
from src.services.workflow_registration_retirement import (
    validate_workflow_retirement_evidence,
)
from src.services.workflow_retirement_consumers import (
    MAX_INVENTORY_ROWS,
    inspect_workflow_retirement_consumers,
)
from src.services.workspace_release_retirement import (
    WorkspaceReleaseRetirementError,
    WorkspaceReleaseRetirementService,
)
from src.services.workspace_release_runtime import resolve_pinned_workspace_runtime

from tests.e2e.platform.test_solution_source_revision import db_session as db_session
from tests.e2e.platform.test_workspace_release_retirement import (
    _pinned_evidence,
    _seed_live_release,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_public_retirement_rejects_engine_superuser_transport(e2e_client, platform_admin, async_engine):
    """A signed execution token cannot supply its own external cutover review."""
    execution_id, claim_token = uuid4(), uuid4()
    async with AsyncSession(async_engine) as db:
        db.add(Execution(id=execution_id, workflow_name="Retirement auth fixture",
            executed_by_name="Synthetic", organization_id=PROVIDER_ORG_ID,
            status=ExecutionStatus.RUNNING))
        await db.flush()
        db.add(WorkflowExecutionAttempt(execution_id=execution_id, attempt_number=1,
            claim_token=claim_token, status="running", phase="execution",
            published_at=datetime.now(UTC), claimed_at=datetime.now(UTC), started_at=datetime.now(UTC)))
        await db.commit()
    token, _ = mint_engine_token(execution_id=str(execution_id), attempt_token=str(claim_token),
        solution_id=str(uuid4()), organization_id=str(PROVIDER_ORG_ID),
        delegated_user_id=str(platform_admin.user_id), delegated_is_superuser=True)
    request = WorkspaceLiveRetireRequest(expected_release_id="sha256:" + "a" * 64,
        expected_artifact_id=uuid4(), governed_manifest_id="sha256:" + "b" * 64,
        reason="Execution transport is not cutover authority",
        acknowledgement="retire-live-workspace-release")
    try:
        response = e2e_client.post("/api/workspace-promotions/live/retire",
            headers={"Authorization": "Bearer " + token}, json=request.model_dump(mode="json"))
        assert response.status_code == 403, response.text
        assert "execution token" in response.json()["detail"]
    finally:
        async with AsyncSession(async_engine) as db:
            await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.commit()


async def _fixture(db_session, platform_admin):
    path = f"features/obsolete_retirement/{uuid4().hex}.py"
    artifact, release, _job = await _seed_live_release(db_session, source_path=path,
        function_name="obsolete", user_id=platform_admin.user_id)
    pin, workflow_id = _pinned_evidence(artifact, release, path, "obsolete")
    row = Workflow(id=workflow_id, name="Obsolete fixture", function_name="obsolete",
        path=path, organization_id=release.organization_id, roles=[],
        endpoint_enabled=True, public_endpoint=False, api_key_enabled=True,
        api_key_hash="retained-credential-material")
    db_session.add(row)
    await db_session.commit()
    service = WorkspaceReleaseRetirementService(db_session, release.organization_id)
    inventory = await service.inspect()
    observed = next(item for item in inventory.loose_registrations if item.workflow_id == row.id)
    request = WorkspaceLiveRetireRequest(expected_release_id=artifact.release_id,
        expected_artifact_id=artifact.id, governed_manifest_id=inventory.governed_manifest_id,
        reason="Reviewed Live cutover; obsolete target has no callers",
        acknowledgement="retire-live-workspace-release", obsolete_registrations=[{
            "workflow_id": row.id, "expected_registration_hash": observed.registration_hash,
            "expected_consumer_inventory_digest": observed.consumer_inventory.inventory_digest,
            "reviewed_external_callers_digest": "sha256:" + "8" * 64,
            "review_reference": "Synthetic reviewed Source, App dist and external caller census",
            "reason": "Obsolete registration; retain original history and immutable pins",
        }])
    return artifact, release, row, service, request, pin


async def _retire(service, request, platform_admin):
    actor = set_actor(ActorContext(user_id=platform_admin.user_id,
        organization_id=service.organization_id, source="http"))
    try:
        return await service.retire(request, user_id=platform_admin.user_id)
    finally:
        clear_actor(actor)


@pytest.mark.asyncio
async def test_one_cas_retires_live_and_obsolete_uuid_preserving_original_pin(db_session, platform_admin):
    _artifact, release, row, service, request, pin = await _fixture(db_session, platform_admin)
    original_id = row.id
    result = await _retire(service, request, platform_admin)
    retained = await db_session.scalar(select(Workflow).where(Workflow.id == original_id)
        .options(selectinload(Workflow.roles)).execution_options(populate_existing=True))
    assert retained is not None and retained.is_active is False
    assert retained.endpoint_enabled is False and retained.api_key_enabled is False
    assert retained.api_key_hash == "retained-credential-material"
    marker = validate_workflow_retirement_evidence(retained)
    assert marker["identity"]["workflow_id"] == str(original_id)
    assert result.release_id == pin["workspace_release_id"]
    resolved = await resolve_pinned_workspace_runtime(db_session, pin, original_id)
    assert resolved.queue_evidence() == pin
    readback = await service.inspect()
    assert readback.state == "retired" and readback.release_row_id == release.id
    observed = next(item for item in readback.loose_registrations if item.workflow_id == original_id)
    assert observed.retirement_evidence_id == marker["evidence_id"]
    assert not observed.is_active
    # Role assignments can be removed independently after cutover. Their
    # historical hash stays sealed without making the retired row executable.
    from src.models.orm.users import Role
    from src.models.orm.workflow_roles import WorkflowRole
    role = Role(name=f"Retirement historical role {uuid4().hex}", created_by=str(platform_admin.user_id))
    db_session.add(role)
    await db_session.flush()
    db_session.add(WorkflowRole(workflow_id=original_id, role_id=role.id))
    await db_session.commit()
    assert (await service.inspect()).loose_registrations[0].retirement_evidence_id == marker["evidence_id"]
    await db_session.execute(delete(WorkflowRole).where(WorkflowRole.workflow_id == original_id))
    await db_session.commit()
    assert (await _retire(service, request, platform_admin)).evidence_id == result.evidence_id
    with pytest.raises(IntegrityError, match="retired workflow"):
        async with db_session.begin_nested():
            await db_session.execute(update(Workflow).where(Workflow.id == original_id).values(is_active=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["stale_row", "new_caller", "source_debt", "unlisted_row"])
async def test_failed_retirement_keeps_live_and_registration_unmodified(db_session, platform_admin, blocker):
    _artifact, release, row, service, request, _pin = await _fixture(db_session, platform_admin)
    row_id, release_id = row.id, release.id
    if blocker == "stale_row":
        row.name = "Changed after observed inventory"
    elif blocker == "new_caller":
        db_session.add(Form(name="Stored inactive caller", workflow_id=str(row.id),
            organization_id=release.organization_id, is_active=False, created_by=str(platform_admin.user_id)))
    elif blocker == "unlisted_row":
        db_session.add(Workflow(name="Unreviewed duplicate", function_name="obsolete",
            path=row.path.replace("/", "\\"), organization_id=release.organization_id, is_active=False))
    else:
        db_session.add(WorkspaceSourceRelease(organization_id=release.organization_id,
            source_commit_sha=uuid4().hex + "1" * 8, source_tree_sha="2" * 40,
            paths={row.path: "a" * 64}, declaration_actor="platform_admin",
            declared_disposition="pending", disposition="attention_required",
            created_by=platform_admin.user_id, reason="Unresolved production Source"))
    await db_session.commit()
    with pytest.raises(WorkspaceReleaseRetirementError):
        await _retire(service, request, platform_admin)
    # Independent persisted readback proves transaction rollback, even when a
    # blocker is found after the obsolete row's marker/audit have been flushed.
    current = await db_session.scalar(select(Workflow).where(Workflow.id == row_id)
        .execution_options(populate_existing=True))
    assert current is not None and current.is_active is True
    assert current.retirement_evidence is None and current.api_key_enabled is True
    from src.models.orm.workspace_promotions import WorkspacePromotionRelease
    live = await db_session.get(WorkspacePromotionRelease, release_id, populate_existing=True)
    assert live is not None and live.activation_state == "live" and live.retirement_evidence is None


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["hex", "urn", "braced", "mixed_case"])
async def test_native_inventory_finds_inactive_form_uuid_spellings_in_postgresql(
    db_session, platform_admin, spelling,
):
    _artifact, release, row, service, request, _pin = await _fixture(db_session, platform_admin)
    reference = {
        "hex": row.id.hex,
        "urn": row.id.urn,
        "braced": "{" + str(row.id).upper() + "}",
        "mixed_case": str(row.id)[:18].upper() + str(row.id)[18:],
    }[spelling]
    form = Form(name="Retained inactive caller", workflow_id=reference,
        organization_id=release.organization_id, is_active=False, created_by=str(platform_admin.user_id))
    db_session.add(form)
    await db_session.commit()
    inventory = await inspect_workflow_retirement_consumers(db_session, row)
    assert any(item["id"] == str(form.id) and item["reference_type"] == "workflow_id"
        for item in inventory["native_callers"])
    with pytest.raises(WorkspaceReleaseRetirementError):
        await _retire(service, request, platform_admin)


@pytest.mark.asyncio
async def test_unrelated_forms_do_not_exhaust_native_caller_inventory(db_session, platform_admin):
    _artifact, release, row, _service, _request, _pin = await _fixture(db_session, platform_admin)
    unrelated = [Form(name=f"Unrelated caller {i}", workflow_id=str(uuid4()),
        organization_id=release.organization_id, is_active=False, created_by=str(platform_admin.user_id))
        for i in range(MAX_INVENTORY_ROWS + 1)]
    caller = Form(name="Actual hex caller", launch_workflow_id=row.id.hex,
        organization_id=release.organization_id, is_active=False, created_by=str(platform_admin.user_id))
    db_session.add_all([*unrelated, caller])
    await db_session.commit()
    try:
        inventory = await inspect_workflow_retirement_consumers(db_session, row)
        assert [item["id"] for item in inventory["native_callers"] if item["entity_type"] == "Form"] == [str(caller.id)]
    finally:
        await db_session.execute(delete(Form).where(Form.id.in_([form.id for form in unrelated] + [caller.id])))
        await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["legacy_urn", "legacy_hex", "workflow_attempt", "generic_attempt"])
async def test_native_inventory_finds_accepted_work_even_with_terminal_logical_state(
    db_session, platform_admin, kind,
):
    _artifact, _release, row, service, request, _pin = await _fixture(db_session, platform_admin)
    legacy = kind.startswith("legacy_")
    execution = Execution(workflow_id=None if legacy else row.id,
        workflow_name=row.id.urn if kind == "legacy_urn" else row.id.hex if legacy else row.name,
        executed_by_name="Retirement query fixture",
        status=ExecutionStatus.PENDING if legacy else ExecutionStatus.SUCCESS,
        completed_at=None if legacy else datetime.now(UTC))
    # Historical terminal executions and another UUID must be excluded by SQL,
    # rather than consuming the bounded accepted-work census.
    history = Execution(workflow_id=row.id, workflow_name=row.name,
        executed_by_name="Retained history", status=ExecutionStatus.SUCCESS,
        completed_at=datetime.now(UTC))
    unrelated = Execution(workflow_id=None, workflow_name=uuid4().urn,
        executed_by_name="Unrelated accepted work", status=ExecutionStatus.PENDING)
    db_session.add_all([execution, history, unrelated])
    await db_session.flush()
    if kind == "workflow_attempt":
        db_session.add(WorkflowExecutionAttempt(execution_id=execution.id,
            attempt_number=1, claim_token=uuid4(), status="running", phase="execution",
            published_at=datetime.now(UTC), claimed_at=datetime.now(UTC), started_at=datetime.now(UTC)))
    elif kind == "generic_attempt":
        db_session.add(ExecutionAttempt(logical_job_type="workflow", logical_job_id=execution.id,
            attempt_number=1, policy_identifier="retirement-test", workload_class="workflow",
            admission_policy="accepted", mechanism="queue"))
    await db_session.commit()
    inventory = await inspect_workflow_retirement_consumers(db_session, row)
    observed = {item["id"] for item in inventory["accepted_work"] if item["entity_type"] == "Execution"}
    assert str(execution.id) in observed
    assert str(history.id) not in observed and str(unrelated.id) not in observed
    with pytest.raises(WorkspaceReleaseRetirementError):
        await _retire(service, request, platform_admin)
