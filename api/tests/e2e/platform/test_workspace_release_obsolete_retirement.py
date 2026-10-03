"""One retirement transaction seals obsolete rows without losing historical pins."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from src.models.contracts.workspace_promotions import WorkspaceLiveRetireRequest
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from src.models.orm.workspace_promotions import WorkspaceSourceRelease
from src.services.audit_context import ActorContext, clear_actor, set_actor
from src.services.workflow_registration_retirement import (
    validate_workflow_retirement_evidence,
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
            organization_id=release.organization_id, is_active=False))
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
