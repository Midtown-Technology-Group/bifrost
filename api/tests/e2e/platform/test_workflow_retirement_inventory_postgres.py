"""PostgreSQL coverage for retained read-only caller and accepted-work inventory."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.models.enums import ExecutionStatus
from src.models.orm.agent_action_approvals import AgentActionApproval
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from src.services.workflow_retirement_consumers import (
    MAX_INVENTORY_ROWS,
    inspect_workflow_retirement_consumers,
)
from tests.e2e.platform.test_solution_source_revision import (
    db_session as db_session,  # noqa: PLC0414
)
from tests.e2e.platform.test_workspace_release_retirement import (
    _pinned_evidence,
    _seed_live_release,
)

pytestmark = pytest.mark.e2e


async def _fixture(db_session, platform_admin):
    path = f"features/retirement_inventory/{uuid4().hex}.py"
    artifact, release, _job = await _seed_live_release(
        db_session, source_path=path, function_name="obsolete",
        user_id=platform_admin.user_id,
    )
    _pin, workflow_id = _pinned_evidence(artifact, release, path, "obsolete")
    row = Workflow(
        id=workflow_id, name="Inventory fixture", function_name="obsolete",
        path=path, organization_id=release.organization_id, roles=[],
        endpoint_enabled=True, public_endpoint=False, api_key_enabled=True,
        api_key_hash="retained-credential-material",
    )
    db_session.add(row)
    await db_session.commit()
    return release, row


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["hex", "urn", "uuid_prefix", "urn_prefix", "braced", "mixed_case"])
@pytest.mark.parametrize("reference_type", ["workflow_id", "launch_workflow_id"])
async def test_native_inventory_finds_inactive_form_uuid_spellings_in_postgresql(
    db_session, platform_admin, spelling, reference_type,
):
    release, row = await _fixture(db_session, platform_admin)
    reference = {
        "hex": row.id.hex,
        "urn": row.id.urn,
        "uuid_prefix": "uuid:" + str(row.id),
        "urn_prefix": "urn:" + str(row.id),
        "braced": "{" + str(row.id).upper() + "}",
        "mixed_case": str(row.id)[:18].upper() + str(row.id)[18:],
    }[spelling]
    form = Form(name="Retained inactive caller", **{reference_type: reference},
        organization_id=release.organization_id, is_active=False, created_by=str(platform_admin.user_id))
    db_session.add(form)
    await db_session.commit()
    inventory = await inspect_workflow_retirement_consumers(db_session, row)
    assert any(item["id"] == str(form.id) and item["reference_type"] == reference_type
        for item in inventory["native_callers"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    ExecutionStatus.SUCCESS, ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT,
    ExecutionStatus.CANCELLED, ExecutionStatus.COMPLETED_WITH_ERRORS,
])
async def test_dispatched_approval_with_completed_execution_remains_history(
    db_session, platform_admin, status,
):
    _release, row = await _fixture(db_session, platform_admin)
    now = datetime.now(UTC)
    execution = Execution(workflow_id=row.id, workflow_name=row.name,
        executed_by_name="Retained approval execution", status=status, completed_at=now)
    db_session.add(execution)
    await db_session.flush()
    db_session.add_all([
        WorkflowExecutionAttempt(execution_id=execution.id, attempt_number=1,
            status="succeeded", phase="terminal", completed_at=now),
        ExecutionAttempt(logical_job_type="workflow", logical_job_id=execution.id,
            attempt_number=1, status="succeeded", completed_at=now,
            policy_identifier="retirement-test", workload_class="workflow",
            admission_policy="accepted", mechanism="queue"),
    ])
    approval = AgentActionApproval(agent_id=uuid4(), workflow_id=row.id,
        execution_id=execution.id, status="dispatched", parameters={}, caller={})
    db_session.add(approval)
    await db_session.commit()
    inventory = await inspect_workflow_retirement_consumers(db_session, row)
    assert inventory["accepted_work"] == []
    retained = await db_session.get(AgentActionApproval, approval.id, populate_existing=True)
    assert retained is not None and retained.status == "dispatched"
    assert retained.execution_id == execution.id
    retained_execution = await db_session.get(Execution, execution.id, populate_existing=True)
    assert retained_execution is not None and retained_execution.status == status
    assert retained_execution.completed_at == now


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [
    "pending_approval", "approved_approval", "unfinished_execution", "nonterminal_execution",
    "workflow_attempt", "generic_attempt", "unknown_attempt", "missing_execution", "missing_execution_id",
    "wrong_workflow", "unknown_legacy_name",
])
async def test_approval_without_proven_finished_dispatch_is_accepted_work(
    db_session, platform_admin, kind,
):
    _release, row = await _fixture(db_session, platform_admin)
    now = datetime.now(UTC)
    execution_id = uuid4() if kind == "missing_execution" else None
    if kind not in {"missing_execution", "missing_execution_id"}:
        workflow_id = row.id
        if kind == "wrong_workflow":
            unrelated = Workflow(name="Unrelated approval execution", function_name="unrelated",
                path=f"features/unrelated_approval/{uuid4().hex}.py")
            db_session.add(unrelated)
            await db_session.flush()
            workflow_id = unrelated.id
        elif kind == "unknown_legacy_name":
            workflow_id = None
        execution = Execution(workflow_id=workflow_id,
            workflow_name="Unknown legacy workflow" if kind == "unknown_legacy_name" else row.name,
            executed_by_name="Unresolved approval execution",
            status=ExecutionStatus.STUCK if kind == "nonterminal_execution" else ExecutionStatus.SUCCESS,
            completed_at=None if kind == "unfinished_execution" else now)
        db_session.add(execution)
        await db_session.flush()
        execution_id = execution.id
        if kind == "workflow_attempt":
            db_session.add(WorkflowExecutionAttempt(execution_id=execution.id,
                attempt_number=1, status="dispatching", phase="dispatch"))
        elif kind in {"generic_attempt", "unknown_attempt"}:
            db_session.add(ExecutionAttempt(logical_job_type="workflow", logical_job_id=execution.id,
                attempt_number=1, status="unknown" if kind == "unknown_attempt" else "running",
                completed_at=now if kind == "unknown_attempt" else None,
                policy_identifier="retirement-test", workload_class="workflow",
                admission_policy="accepted", mechanism="queue"))
    approval = AgentActionApproval(agent_id=uuid4(), workflow_id=row.id,
        execution_id=execution_id, parameters={}, caller={},
        status=kind.removesuffix("_approval") if kind.endswith("_approval") else "dispatched")
    db_session.add(approval)
    await db_session.commit()
    inventory = await inspect_workflow_retirement_consumers(db_session, row)
    assert any(item["entity_type"] == "AgentActionApproval" and item["id"] == str(approval.id)
        for item in inventory["accepted_work"])


@pytest.mark.asyncio
async def test_dispatched_approval_history_does_not_exhaust_inventory_bound(db_session, platform_admin):
    _release, row = await _fixture(db_session, platform_admin)
    execution = Execution(workflow_id=row.id, workflow_name=row.name,
        executed_by_name="Retained approval history", status=ExecutionStatus.SUCCESS,
        completed_at=datetime.now(UTC))
    db_session.add(execution)
    await db_session.flush()
    history = [AgentActionApproval(agent_id=uuid4(), workflow_id=row.id,
        execution_id=execution.id, status="dispatched", parameters={}, caller={})
        for _ in range(MAX_INVENTORY_ROWS + 1)]
    db_session.add_all(history)
    await db_session.commit()
    inventory = await inspect_workflow_retirement_consumers(db_session, row)
    assert inventory["accepted_work"] == []


@pytest.mark.asyncio
async def test_unrelated_forms_do_not_exhaust_native_caller_inventory(db_session, platform_admin):
    release, row = await _fixture(db_session, platform_admin)
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
    _release, row = await _fixture(db_session, platform_admin)
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

