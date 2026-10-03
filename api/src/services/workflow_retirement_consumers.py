"""Bounded native inventory of callers and accepted work for a workflow row.

This deliberately does not read App Source or deployment artifacts. The App
digest seals the bounded metadata and active pointers seen during review; App
code/dist caller review remains an external prerequisite to retirement.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect as orm_inspect

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

WORKFLOW_RETIREMENT_CONSUMER_SCHEMA = "bifrost.workflow-retirement-consumers/v1"
MAX_INVENTORY_ROWS = 1024
_ACCEPTED_EXECUTION_STATUSES = (
    ExecutionStatus.SCHEDULED,
    ExecutionStatus.PENDING,
    ExecutionStatus.RUNNING,
    ExecutionStatus.CANCELLING,
    ExecutionStatus.STUCK,
)
_ACTIVE_SERVICE_ATTEMPT_STATES = ("starting", "running", "stopping")
_OPEN_APPROVAL_STATUSES = ("pending", "approved", "dispatched")


class WorkflowRetirementInventoryError(ValueError):
    """The native inventory cannot be completed within its fail-closed bound."""


def _digest_row(row: Any) -> str:
    """Hash persisted columns without returning values or retaining secrets."""
    def value(item: Any) -> Any:
        if isinstance(item, bytes):
            return {"sha256": hashlib.sha256(item).hexdigest()}
        if isinstance(item, (UUID, Decimal)):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        return item

    return canonical_digest({
        attr.key: value(getattr(row, attr.key))
        for attr in orm_inspect(type(row)).column_attrs
    })


class _Inventory:
    def __init__(self, db: AsyncSession, lock: bool):
        self.db = db
        self.lock = lock
        self.callers: list[dict[str, Any]] = []
        self.accepted: list[dict[str, Any]] = []

    async def rows(self, model: type, predicate: Any):
        query = select(model).where(predicate).order_by(*orm_inspect(model).primary_key).limit(
            MAX_INVENTORY_ROWS + 1
        )
        if self.lock:
            query = query.with_for_update(of=model)
        rows = list((await self.db.scalars(
            query.execution_options(populate_existing=True)
        )).all())
        if len(rows) > MAX_INVENTORY_ROWS:
            raise WorkflowRetirementInventoryError(
                f"{model.__name__} inventory exceeds {MAX_INVENTORY_ROWS} rows"
            )
        return rows

    @staticmethod
    def scope(row: Any) -> dict[str, str | None]:
        organization_id = getattr(row, "organization_id", getattr(row, "org_id", None))
        solution_id = getattr(row, "solution_id", None)
        return {
            "organization_id": str(organization_id) if organization_id else None,
            "solution_id": str(solution_id) if solution_id else None,
        }

    def caller(self, entity_type: str, row: Any, reference_type: str, *,
               scope_row: Any | None = None, row_id: Any | None = None) -> None:
        self.callers.append({
            "entity_type": entity_type,
            "id": str(row.id if row_id is None else row_id),
            "reference_type": reference_type,
            **self.scope(row if scope_row is None else scope_row),
        })

    def work(self, entity_type: str, row: Any, *, scope_row: Any | None = None) -> None:
        self.accepted.append({
            "entity_type": entity_type,
            "id": str(row.id),
            **self.scope(row if scope_row is None else scope_row),
        })


async def inspect_workflow_retirement_consumers(
    db: AsyncSession, workflow: Workflow, *, lock: bool = False,
    application_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return bounded native caller/work evidence for this exact registration.

    No organization/solution or active/enabled filters are applied: inactive
    controls and every stored scope remain visible to the reviewer. With
    ``lock=True``, every selected row and the bounded App metadata set is locked
    for the caller's surrounding transaction; this function never commits.
    """
    scan = _Inventory(db, lock)
    workflow_id = str(workflow.id)
    normalized_path = workflow.path.replace("\\", "/").strip("/")
    path_variants = {workflow.path}
    for separator in ("/", "\\"):
        path = normalized_path.replace("/", separator)
        path_variants.update({path, f"/{path}", f"\\{path}"})
    names = {
        workflow_id,
        workflow_id.upper(),
        workflow.name,
        workflow.function_name,
        *(f"{path}::{workflow.function_name}" for path in path_variants),
    }

    def matches_reference(value: Any) -> bool:
        if value is None:
            return False
        if value in names:
            return True
        try:
            return UUID(str(value)) == workflow.id
        except (AttributeError, TypeError, ValueError):
            return False

    # SQL selects a superset of every legacy UUID spelling accepted below,
    # including URN/braces/hex/mixed case. The bound is on potential callers,
    # not on unrelated Forms across the platform.
    def uuid_text(column):
        return func.replace(func.replace(func.replace(func.replace(func.lower(column),
            "urn:uuid:", ""), "{", ""), "}", ""), "-", "")

    form_path = func.ltrim(func.replace(Form.workflow_path, "\\", "/"), "/")
    forms = await scan.rows(Form, or_(Form.workflow_id.in_(names), Form.launch_workflow_id.in_(names),
        uuid_text(Form.workflow_id) == workflow.id.hex, uuid_text(Form.launch_workflow_id) == workflow.id.hex,
        (form_path == normalized_path) & (Form.workflow_function_name == workflow.function_name)))
    for form in forms:
        if matches_reference(form.workflow_id):
            scan.caller("Form", form, "workflow_id")
        if matches_reference(form.launch_workflow_id):
            scan.caller("Form", form, "launch_workflow_id")
        form_path = (form.workflow_path or "").replace("\\", "/").lstrip("/")
        if form_path == normalized_path and form.workflow_function_name == workflow.function_name:
            scan.caller("Form", form, "workflow_path_function")

    fields = await scan.rows(FormField, FormField.data_provider_id == workflow.id)
    field_form_ids = {row.form_id for row in fields}
    field_forms = await scan.rows(Form, Form.id.in_(field_form_ids)) if field_form_ids else []
    field_form_by_id = {row.id: row for row in field_forms}
    for field in fields:
        scan.caller("FormField", field, "data_provider_id",
                    scope_row=field_form_by_id.get(field.form_id))

    agent_tools = await scan.rows(AgentTool, AgentTool.workflow_id == workflow.id)
    tool_agent_ids = {row.agent_id for row in agent_tools}
    tool_agents = await scan.rows(Agent, Agent.id.in_(tool_agent_ids)) if tool_agent_ids else []
    tool_agent_by_id = {row.id: row for row in tool_agents}
    for row in agent_tools:
        scan.caller("AgentTool", row, "workflow_id",
                    scope_row=tool_agent_by_id.get(row.agent_id),
                    row_id=f"{row.agent_id}:{row.workflow_id}")

    subscriptions = await scan.rows(
        EventSubscription, EventSubscription.workflow_id == workflow.id
    )
    for row in subscriptions:
        scan.caller("EventSubscription", row, "workflow_id")

    services = await scan.rows(ServiceDefinition, ServiceDefinition.workflow_id == workflow.id)
    for row in services:
        scan.caller("ServiceDefinition", row, "workflow_id")
    service_ids = [row.id for row in services]
    if service_ids:
        attempts = await scan.rows(
            ServiceAttempt,
            (ServiceAttempt.service_id.in_(service_ids))
            & ServiceAttempt.state.in_(_ACTIVE_SERVICE_ATTEMPT_STATES),
        )
        service_by_id = {row.id: row for row in services}
        for row in attempts:
            scan.work("ServiceAttempt", row, scope_row=service_by_id.get(row.service_id))

    subscription_ids = [row.id for row in subscriptions]
    delivery_predicates = [EventDelivery.workflow_id == workflow.id]
    if subscription_ids:
        delivery_predicates.append(EventDelivery.event_subscription_id.in_(subscription_ids))
    deliveries = await scan.rows(
        EventDelivery,
        or_(*delivery_predicates) & EventDelivery.status.in_(("pending", "queued")),
    )
    delivery_subscription_ids = {row.event_subscription_id for row in deliveries}
    delivery_subscriptions = (
        await scan.rows(EventSubscription, EventSubscription.id.in_(delivery_subscription_ids))
        if delivery_subscription_ids else []
    )
    delivery_subscription_by_id = {row.id: row for row in delivery_subscriptions}
    for row in deliveries:
        scan.work("EventDelivery", row,
                  scope_row=delivery_subscription_by_id.get(row.event_subscription_id))

    active_workflow_attempt = exists(select(WorkflowExecutionAttempt.id).where(
        WorkflowExecutionAttempt.execution_id == Execution.id,
        WorkflowExecutionAttempt.completed_at.is_(None),
    ))
    active_generic_attempt = exists(select(ExecutionAttempt.id).where(
        ExecutionAttempt.logical_job_type == "workflow",
        ExecutionAttempt.logical_job_id == Execution.id,
        ExecutionAttempt.completed_at.is_(None),
    ))
    legacy_uuid = func.replace(func.replace(func.replace(func.replace(
        func.lower(Execution.workflow_name), "urn:uuid:", ""), "{", ""), "}", ""), "-", "")
    execution_identity = or_(
        Execution.workflow_id == workflow.id,
        # Legacy/inline rows may lack the FK. Retain any exact stored-name or
        # portable-reference match across every organization; absent stronger
        # provenance, ambiguity is a retirement blocker rather than a scope
        # guess that could discard accepted work.
        (Execution.workflow_id.is_(None)
         & or_(Execution.workflow_name.in_(names), legacy_uuid == workflow.id.hex)),
    )
    executions = await scan.rows(Execution, execution_identity & or_(
        Execution.status.in_(_ACCEPTED_EXECUTION_STATUSES),
        Execution.completed_at.is_(None),
        active_workflow_attempt,
        active_generic_attempt,
    ))
    # Selected by SQL from accepted status, incomplete row state, or an
    # unfinished attempt. Terminal execution history is intentionally excluded.
    candidate_executions = executions
    for row in candidate_executions:
        scan.work("Execution", row)
    execution_ids = [row.id for row in candidate_executions]
    execution_by_id = {row.id: row for row in candidate_executions}
    if execution_ids:
        workflow_attempts = await scan.rows(
            WorkflowExecutionAttempt,
            WorkflowExecutionAttempt.execution_id.in_(execution_ids)
            & WorkflowExecutionAttempt.completed_at.is_(None),
        )
        for row in workflow_attempts:
            scan.work("WorkflowExecutionAttempt", row,
                      scope_row=execution_by_id.get(row.execution_id))
        generic_attempts = await scan.rows(
            ExecutionAttempt,
            (ExecutionAttempt.logical_job_type == "workflow")
            & ExecutionAttempt.logical_job_id.in_(execution_ids)
            & ExecutionAttempt.completed_at.is_(None),
        )
        for row in generic_attempts:
            scan.work("ExecutionAttempt", row,
                      scope_row=execution_by_id.get(row.logical_job_id))

    approvals = await scan.rows(
        AgentActionApproval,
        (AgentActionApproval.workflow_id == workflow.id)
        & AgentActionApproval.status.in_(_OPEN_APPROVAL_STATUSES),
    )
    for row in approvals:
        scan.work("AgentActionApproval", row)

    # Apps have no relational workflow FK, and this inventory intentionally
    # avoids reading authored Source, bundles, or compiled dist. Hash all
    # bounded app metadata, dependencies, and activation pointers instead.
    # Reuse only within one census or one already-fenced apply transaction.
    # A new invocation must collect a new complete bounded App snapshot.
    if application_cache is not None and "digest" in application_cache:
        application_inventory_digest = application_cache["digest"]
    else:
        applications = await scan.rows(Application, Application.id.is_not(None))
        application_inventory_digest = canonical_digest({
            "scope": "all-applications",
            "source_review_required": True,
            "rows": [_digest_row(row) for row in applications],
        })
        if application_cache is not None:
            application_cache["digest"] = application_inventory_digest

    scan.callers.sort(key=lambda row: (
        row["entity_type"], row["id"], row["reference_type"],
        row["organization_id"] or "", row["solution_id"] or "",
    ))
    scan.accepted.sort(key=lambda row: (
        row["entity_type"], row["id"], row["organization_id"] or "",
        row["solution_id"] or "",
    ))
    inventory_digest = canonical_digest({
        "schema_version": WORKFLOW_RETIREMENT_CONSUMER_SCHEMA,
        "workflow_id": workflow_id,
        "native_callers": scan.callers,
        "accepted_work": scan.accepted,
        "application_inventory_digest": application_inventory_digest,
        "application_source_dist_review_required": True,
    })
    return {
        "schema_version": WORKFLOW_RETIREMENT_CONSUMER_SCHEMA,
        "native_callers": scan.callers,
        "accepted_work": scan.accepted,
        "application_inventory_digest": application_inventory_digest,
        "application_source_dist_review_required": True,
        "inventory_digest": inventory_digest,
    }
