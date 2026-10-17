"""Bounded native-caller and accepted-work inventory contract."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.models.orm.agent_action_approvals import AgentActionApproval
from src.models.orm.agents import Agent, AgentTool
from src.models.orm.applications import Application
from src.models.orm.events import EventDelivery, EventSubscription
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.forms import Form, FormField
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.workflows import Workflow
from src.services.workflow_retirement_consumers import (
    MAX_INVENTORY_ROWS,
    WorkflowRetirementInventoryError,
    inspect_workflow_retirement_consumers,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows):
        self.rows_by_model = rows

    async def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        rows = self.rows_by_model.get(model, [])
        if model is Execution:
            unfinished_workflow = {
                row.execution_id for row in self.rows_by_model.get(WorkflowExecutionAttempt, [])
                if row.completed_at is None
            }
            unfinished_generic = {
                row.logical_job_id for row in self.rows_by_model.get(ExecutionAttempt, [])
                if row.logical_job_type == "workflow" and row.completed_at is None
            }
            accepted = {status.value for status in (
                ExecutionStatus.SCHEDULED, ExecutionStatus.PENDING, ExecutionStatus.RUNNING,
                ExecutionStatus.CANCELLING, ExecutionStatus.STUCK,
            )}
            rows = [row for row in rows if (
                row.status in accepted or row.completed_at is None
                or row.id in unfinished_workflow or row.id in unfinished_generic
            )]
        return _Result(rows)


def _workflow():
    return Workflow(
        id=uuid4(), name="legacy_entry", path="automation\\legacy.py",
        function_name="legacy_entry", organization_id=None, solution_id=None,
    )


@pytest.mark.asyncio
async def test_inventory_includes_inactive_scoped_references_and_only_accepted_work():
    workflow = _workflow()
    organization_id, solution_id = uuid4(), uuid4()
    form = SimpleNamespace(
        id=uuid4(), workflow_id=str(workflow.id), launch_workflow_id="legacy_entry",
        workflow_path="/automation\\legacy.py", workflow_function_name="legacy_entry",
        is_active=False, organization_id=organization_id, solution_id=solution_id,
    )
    function_ref_form = SimpleNamespace(
        id=uuid4(), workflow_id=workflow.function_name,
        launch_workflow_id="\\automation\\legacy.py::legacy_entry",
        workflow_path=None, workflow_function_name=None, is_active=False,
        organization_id=organization_id, solution_id=solution_id,
    )
    form_field = SimpleNamespace(id=uuid4(), form_id=form.id, data_provider_id=workflow.id)
    agent = SimpleNamespace(id=uuid4(), organization_id=organization_id, solution_id=solution_id)
    agent_tool = SimpleNamespace(agent_id=agent.id, workflow_id=workflow.id)
    subscription = SimpleNamespace(
        id=uuid4(), workflow_id=workflow.id, organization_id=organization_id,
        solution_id=solution_id, is_active=False,
    )
    service = SimpleNamespace(
        id=uuid4(), workflow_id=workflow.id, organization_id=organization_id,
        solution_id=solution_id,
    )
    service_attempt = SimpleNamespace(id=uuid4(), service_id=service.id, state="running")
    delivery = SimpleNamespace(
        id=uuid4(), event_subscription_id=subscription.id, status="queued",
    )
    execution = SimpleNamespace(
        id=uuid4(), workflow_id=workflow.id, status=ExecutionStatus.PENDING,
        completed_at=None, organization_id=organization_id, solution_id=solution_id,
    )
    workflow_attempt = SimpleNamespace(
        id=uuid4(), execution_id=execution.id, completed_at=None,
    )
    generic_attempt = SimpleNamespace(
        id=uuid4(), logical_job_type="workflow", logical_job_id=execution.id,
        completed_at=None,
    )
    approval = SimpleNamespace(
        id=uuid4(), workflow_id=workflow.id, status="pending",
        organization_id=organization_id,
    )
    app = Application(id=uuid4(), name="app", slug="app", active_deployment_id=uuid4())
    db = _DB({
        Application: [app],
        # Form query is split for the path and field-parent scope queries.
        Form: [form, function_ref_form], FormField: [form_field], Agent: [agent], AgentTool: [agent_tool],
        EventSubscription: [subscription], EventDelivery: [delivery],
        ServiceDefinition: [service], ServiceAttempt: [service_attempt],
        Execution: [execution], WorkflowExecutionAttempt: [workflow_attempt],
        ExecutionAttempt: [generic_attempt], AgentActionApproval: [approval],
    })

    inventory = await inspect_workflow_retirement_consumers(db, workflow)

    callers = {(row["entity_type"], row["reference_type"]) for row in inventory["native_callers"]}
    assert callers == {
        ("Form", "workflow_id"), ("Form", "launch_workflow_id"),
        ("Form", "workflow_path_function"), ("FormField", "data_provider_id"),
        ("AgentTool", "workflow_id"), ("EventSubscription", "workflow_id"),
        ("ServiceDefinition", "workflow_id"),
    }
    assert all(row["organization_id"] == str(organization_id) for row in inventory["native_callers"])
    work = {(row["entity_type"], row["id"]) for row in inventory["accepted_work"]}
    assert work == {
        ("EventDelivery", str(delivery.id)), ("ServiceAttempt", str(service_attempt.id)),
        ("Execution", str(execution.id)), ("WorkflowExecutionAttempt", str(workflow_attempt.id)),
        ("ExecutionAttempt", str(generic_attempt.id)), ("AgentActionApproval", str(approval.id)),
    }
    assert inventory["application_source_dist_review_required"] is True
    assert inventory["application_inventory_digest"].startswith("sha256:")
    assert inventory["inventory_digest"].startswith("sha256:")
    assert "secret" not in str(inventory).lower()


@pytest.mark.asyncio
async def test_app_inventory_digest_tracks_activation_pointer_without_returning_app_values():
    workflow = _workflow()
    app = Application(id=uuid4(), name="sensitive app name", slug="sensitive-app")
    first = await inspect_workflow_retirement_consumers(_DB({Application: [app]}), workflow)
    app.active_deployment_id = uuid4()
    second = await inspect_workflow_retirement_consumers(_DB({Application: [app]}), workflow)
    assert first["application_inventory_digest"] != second["application_inventory_digest"]
    assert "sensitive app name" not in str(first)


@pytest.mark.asyncio
async def test_inventory_fails_closed_when_bounded_app_inventory_overflows():
    workflow = _workflow()
    apps = [Application(id=uuid4(), name=f"app-{i}", slug=f"app-{i}")
            for i in range(MAX_INVENTORY_ROWS + 1)]
    with pytest.raises(WorkflowRetirementInventoryError, match="Application inventory exceeds"):
        await inspect_workflow_retirement_consumers(_DB({Application: apps}), workflow)


@pytest.mark.asyncio
async def test_completed_execution_with_unfinished_attempt_remains_accepted_work():
    workflow = _workflow()
    execution = SimpleNamespace(
        id=uuid4(), workflow_id=workflow.id, status=ExecutionStatus.SUCCESS,
        completed_at=object(), organization_id=None, solution_id=None,
    )
    unfinished_attempt = SimpleNamespace(
        id=uuid4(), execution_id=execution.id, completed_at=None,
    )
    inventory = await inspect_workflow_retirement_consumers(
        _DB({Execution: [execution], WorkflowExecutionAttempt: [unfinished_attempt]}),
        workflow,
    )
    assert {(row["entity_type"], row["id"]) for row in inventory["accepted_work"]} == {
        ("Execution", str(execution.id)),
        ("WorkflowExecutionAttempt", str(unfinished_attempt.id)),
    }


@pytest.mark.asyncio
async def test_pending_legacy_execution_without_workflow_id_remains_accepted_work():
    workflow = _workflow()
    execution = SimpleNamespace(
        id=uuid4(), workflow_id=None, workflow_name=workflow.function_name,
        status=ExecutionStatus.PENDING, completed_at=None,
        organization_id=None, solution_id=None,
    )
    inventory = await inspect_workflow_retirement_consumers(
        _DB({Execution: [execution]}), workflow,
    )
    assert ("Execution", str(execution.id)) in {
        (row["entity_type"], row["id"]) for row in inventory["accepted_work"]
    }


@pytest.mark.asyncio
async def test_completed_legacy_execution_with_unfinished_attempt_is_retained():
    workflow = _workflow()
    execution = SimpleNamespace(
        id=uuid4(), workflow_id=None, workflow_name=f"/automation/legacy.py::{workflow.function_name}",
        status=ExecutionStatus.SUCCESS, completed_at=object(),
        organization_id=None, solution_id=None,
    )
    unfinished_attempt = SimpleNamespace(
        id=uuid4(), execution_id=execution.id, completed_at=None,
    )
    inventory = await inspect_workflow_retirement_consumers(
        _DB({Execution: [execution], WorkflowExecutionAttempt: [unfinished_attempt]}),
        workflow,
    )
    assert {(row["entity_type"], row["id"]) for row in inventory["accepted_work"]} == {
        ("Execution", str(execution.id)),
        ("WorkflowExecutionAttempt", str(unfinished_attempt.id)),
    }


@pytest.mark.asyncio
async def test_terminal_execution_history_does_not_consume_active_inventory_bound():
    workflow = _workflow()
    history = [SimpleNamespace(
        id=uuid4(), workflow_id=workflow.id, status=ExecutionStatus.SUCCESS,
        completed_at=object(), organization_id=None, solution_id=None,
    ) for _ in range(MAX_INVENTORY_ROWS + 1)]
    inventory = await inspect_workflow_retirement_consumers(
        _DB({Execution: history}), workflow,
    )
    assert inventory["accepted_work"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("uuid_format", [
    "canonical", "uppercase", "mixedcase", "hyphenless", "braced", "urn", "uuid_prefix", "urn_prefix",
])
async def test_form_workflow_string_accepts_uuid_parser_formats(uuid_format):
    workflow = _workflow()
    target = str(workflow.id)
    if uuid_format == "canonical":
        reference = target
    elif uuid_format == "uppercase":
        reference = target.upper()
    elif uuid_format == "mixedcase":
        reference = "".join(char.upper() if index % 2 else char
                            for index, char in enumerate(target))
    elif uuid_format == "hyphenless":
        reference = target.replace("-", "")
    elif uuid_format == "braced":
        reference = f"{{{target}}}"
    elif uuid_format == "uuid_prefix":
        reference = f"uuid:{target}"
    elif uuid_format == "urn_prefix":
        reference = f"urn:{target}"
    else:
        reference = f"urn:uuid:{target}"
    # Confirm the test input really is accepted by the resolver's UUID parser.
    assert UUID(reference) == workflow.id
    form = SimpleNamespace(
        id=uuid4(), workflow_id=reference, launch_workflow_id=None,
        workflow_path=None, workflow_function_name=None,
        organization_id=None, solution_id=None,
    )
    inventory = await inspect_workflow_retirement_consumers(
        _DB({Form: [form]}), workflow,
    )
    assert any(
        caller["entity_type"] == "Form" and caller["reference_type"] == "workflow_id"
        and caller["id"] == str(form.id)
        for caller in inventory["native_callers"]
    )
