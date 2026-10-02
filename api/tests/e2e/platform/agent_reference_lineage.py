"""Bounded committed material observations, never a lineage acceptance oracle.

C1-R-MATERIAL-S, frozen interface9eb08293. No connection acquisition, settings,
product imports, decryption, storage, semantic acceptance or side-effect driver.
Only an exclusively supplied observer connection may be terminated on uncertainty.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from time import monotonic_ns
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias, Union, cast
from uuid import UUID

if TYPE_CHECKING:
    from asyncpg import Connection

JsonValue: TypeAlias = Union[
    None, bool, int, float, str, tuple["JsonValue", ...], Mapping[str, "JsonValue"]
]
MAX_SNAPSHOT_SECONDS = 3
MAX_CELL_BYTES = 65536
MAX_ENVELOPE_BYTES = 131072
MAX_SNAPSHOT_BYTES = 1048576
MAX_JSON_DEPTH = 64
NATIVE_CELL_CHARGE = 128
ROW_CHARGE = 256
ACQUISITION_RESERVE_BYTES = 4096
CAPACITY_ID = UUID("0760d416-be3a-4a33-b540-5b6b0658075d")
PREVIEW_ID = UUID("93b7f115-de11-444d-8309-7aee9bac2bec")
BOOTSTRAP_ID = UUID("81c8961e-cedb-4d51-831b-d29a75e99cb9")
WORKFLOW_IDS = (CAPACITY_ID, PREVIEW_ID, BOOTSTRAP_ID)


class RunReadStage(StrEnum):
    POLL = "poll"
    FINAL = "final"


class ReadKind(StrEnum):
    OBSERVED = "observed"
    CHANGED = "changed"
    FAILED = "failed"


class ReaderCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    ACTIVE_TRANSACTION = "active_transaction"
    ISOLATION_MISMATCH = "isolation_mismatch"
    DEADLINE_EXPIRED = "deadline_expired"
    SNAPSHOT_TIMEOUT = "snapshot_timeout"
    SNAPSHOT_CHANGED = "snapshot_changed"
    CONNECTION_FAILURE = "connection_failure"
    CONNECTION_TAINTED = "connection_tainted"
    SCHEMA_MISMATCH = "schema_mismatch"
    CARDINALITY_EXCESS = "cardinality_excess"
    MATERIAL_OVERSIZE = "material_oversize"
    MATERIAL_INVALID = "material_invalid"
    DISCOVERY_INVALID = "discovery_invalid"


class DeferredChildGroup(StrEnum):
    EXECUTION = "execution"
    WORKFLOW_ATTEMPTS = "workflow_attempts"
    WORKFLOW_DELIVERY = "workflow_delivery"
    KNOWN_ID_GENERIC_EXCLUSION = "known_id_generic_exclusion"
    CHILD_USAGE = "child_usage"


@dataclass(frozen=True, slots=True, repr=False)
class SetupIds:
    user_id: UUID
    organization_id: UUID
    agent_id: UUID
    solution_id: UUID
    initial_deployment_id: UUID
    final_deployment_id: UUID
    role_id: UUID
    profile_id: UUID
    connection_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class RunIds:
    setup: SetupIds
    run_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class TransactionIdentity:
    postmaster_started_at: datetime
    backend_pid: int
    virtual_xid: str


@dataclass(frozen=True, slots=True, repr=False)
class TransactionFacts:
    isolation: str
    read_only: bool
    identity: TransactionIdentity


@dataclass(frozen=True, slots=True, repr=False)
class ReadAcquisition:
    started_ns: int
    completed_ns: int
    transaction: TransactionIdentity


@dataclass(frozen=True, slots=True, repr=False)
class UserRow:
    id: UUID
    email: str
    name: str | None
    organization_id: UUID | None
    is_active: bool
    is_verified: bool
    is_registered: bool
    is_system: bool
    is_superuser: bool
    is_external: bool


@dataclass(frozen=True, slots=True, repr=False)
class OrganizationRow:
    id: UUID
    is_active: bool
    is_provider: bool


@dataclass(frozen=True, slots=True, repr=False)
class UserRoleRow:
    user_id: UUID
    role_id: UUID
    name: str


@dataclass(frozen=True, slots=True, repr=False)
class AgentRow:
    id: UUID
    name: str
    system_prompt: str
    channels: tuple[str, ...]
    access_level: str
    organization_id: UUID | None
    solution_id: UUID | None
    owner_user_id: UUID | None
    is_active: bool
    knowledge_sources: tuple[str, ...]
    system_tools: tuple[str, ...]
    llm_profile_id: UUID | None
    llm_max_tokens: int | None
    max_iterations: int | None
    max_token_budget: int | None
    max_run_timeout: int | None


@dataclass(frozen=True, slots=True, repr=False)
class AgentToolRow:
    agent_id: UUID
    workflow_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class AgentRoleRow:
    agent_id: UUID
    role_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class AgentDelegationRow:
    parent_agent_id: UUID
    child_agent_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class AgentMCPRow:
    agent_id: UUID
    connection_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowRoleRow:
    workflow_id: UUID
    role_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionMetadata:
    id: UUID
    workflow_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class ChildRunMetadata:
    id: UUID
    parent_run_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class DeploymentEdgeRow:
    deployment_id: UUID
    dependency_solution_id: UUID
    dependency_deployment_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class RunRow:
    id: UUID
    agent_id: UUID | None
    trigger_type: str
    trigger_source: str | None
    conversation_id: UUID | None
    event_delivery_id: UUID | None
    input: Mapping[str, JsonValue] | None
    output: Mapping[str, JsonValue] | None
    output_schema: Mapping[str, JsonValue] | None
    status: str
    org_id: UUID | None
    caller_user_id: str | None
    caller_email: str | None
    caller_name: str | None
    iterations_used: int
    tokens_used: int
    budget_max_iterations: int | None
    budget_max_tokens: int | None
    duration_ms: int | None
    llm_model: str | None
    asked: str | None
    did: str | None
    answered: str | None
    metadata: Mapping[str, JsonValue]
    confidence: float | None
    confidence_reason: str | None
    summary_generated_at: datetime | None
    summary_status: str
    summary_delivery_id: UUID | None
    summary_prompt_version: str | None
    started_at: datetime | None
    completed_at: datetime | None
    parent_run_id: UUID | None
    error_absent: bool
    summary_error_absent: bool


@dataclass(frozen=True, slots=True, repr=False)
class StepRow:
    id: UUID
    run_id: UUID
    step_number: int
    type: str
    content: Mapping[str, JsonValue] | None
    tokens_used: int | None
    duration_ms: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionRow:
    id: UUID
    workflow_id: UUID | None
    workflow_name: str
    organization_id: UUID | None
    executed_by: UUID | None
    executed_by_name: str
    parameters: Mapping[str, JsonValue]
    result: Mapping[str, JsonValue] | None
    result_type: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    execution_model: str | None
    form_id: UUID | None
    api_key_id: UUID | None
    session_id: UUID | None
    solution_deployment_id: UUID | None
    runtime_mode: str
    runtime_evidence: Mapping[str, JsonValue] | None
    runtime_evidence_hash: str | None
    dispatch_evidence: Mapping[str, JsonValue] | None
    dispatch_evidence_hash: str | None
    retry_policy: Mapping[str, JsonValue]
    attempt_tracking_version: str | None
    execution_context: Mapping[str, JsonValue] | None
    status: str
    error_absent: bool


@dataclass(frozen=True, slots=True, repr=False)
class ParentAttemptRow:
    id: UUID
    logical_job_type: str
    logical_job_id: UUID
    organization_id: UUID | None
    attempt_number: int
    status: str
    policy_identifier: str
    workload_class: str
    admission_policy: str
    mechanism: str
    queue_name: str | None
    message_id: str | None
    retry_count: int
    replay_count: int
    worker_id: str | None
    process_id: int | None
    started_at: datetime
    completed_at: datetime | None
    lease_absent: bool
    failure_code_absent: bool
    failure_message_absent: bool


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowAttemptRow:
    id: UUID
    execution_id: UUID
    attempt_number: int
    status: str
    phase: str
    worker_id: str | None
    worker_incarnation_id: UUID | None
    process_id: str | None
    runtime_mode: str | None
    runtime_evidence_hash: str | None
    dispatch_evidence_hash: str | None
    policy_digest: str | None
    policy_version: str
    published_at: datetime | None
    claimed_at: datetime | None
    started_at: datetime | None
    heartbeat_at: datetime | None
    completed_at: datetime | None
    claim_present: bool
    failure_phase_absent: bool
    failure_code_absent: bool


@dataclass(frozen=True, slots=True, repr=False)
class DeliveryRow:
    id: UUID
    queue_name: str
    message_id: str
    encrypted_envelope: str
    status: str
    available_at: datetime
    created_at: datetime
    started_at: datetime | None
    settled_at: datetime | None
    claim_count: int
    lease_owner_absent: bool
    lease_token_absent: bool
    lease_expiry_absent: bool


@dataclass(frozen=True, slots=True, repr=False)
class UsageRow:
    id: int
    execution_id: UUID | None
    conversation_id: UUID | None
    agent_run_id: UUID | None
    message_id: UUID | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    provider_cost: Decimal | None
    cost: Decimal | None
    duration_ms: int | None
    timestamp: datetime
    sequence: int
    organization_id: UUID | None
    user_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class WorkflowRow:
    id: UUID
    solution_id: UUID | None
    organization_id: UUID | None
    path: str
    function_name: str
    name: str
    description: str | None
    category: str
    tags: tuple[JsonValue, ...]
    type: str
    tool_description: str | None
    display_name: str | None
    parameters_schema: Mapping[str, JsonValue] | tuple[JsonValue, ...]
    execution_mode: str
    timeout_seconds: int
    cache_ttl_seconds: int
    time_saved: int
    value: Decimal
    retry_policy: Mapping[str, JsonValue]
    access_level: str
    endpoint_enabled: bool
    public_endpoint: bool
    allowed_methods: tuple[str, ...]
    disable_global_key: bool
    is_active: bool
    is_orphaned: bool


@dataclass(frozen=True, slots=True, repr=False)
class SolutionRow:
    id: UUID
    organization_id: UUID | None
    status: str
    active_deployment_id: UUID | None
    execution_runtime_mode: str
    allow_outbound_access: bool


@dataclass(frozen=True, slots=True, repr=False)
class DeploymentRow:
    id: UUID
    organization_id: UUID | None
    solution_id: UUID
    parent_deployment_id: UUID | None
    base_deployment_id: UUID | None
    state: str
    bundle_hash: str
    compiled_manifest: Mapping[str, JsonValue]
    compiled_manifest_hash: str
    resolution_map: Mapping[str, JsonValue]
    resolution_map_hash: str
    source_artifact_key: str
    runtime_storage_prefix: str
    git_repository: str | None
    git_ref: str | None
    git_commit_sha: str | None
    activated_at: datetime | None
    superseded_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class ProfileRow:
    id: UUID
    name: str
    connection_id: UUID
    model: str
    openai_transport: str | None
    default_max_tokens: int | None
    capabilities: Mapping[str, JsonValue] | None
    enabled_for_chat: bool
    failover_profile_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class ConnectionRow:
    id: UUID
    name: str
    provider: str
    endpoint: str | None


@dataclass(frozen=True, slots=True, repr=False)
class AssignmentRow:
    assignment_key: str
    profile_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class PrincipalRows:
    user: UserRow | None
    organization: OrganizationRow | None
    roles: tuple[UserRoleRow, ...]


@dataclass(frozen=True, slots=True, repr=False)
class ModelSetupRows:
    profile: ProfileRow | None
    connection: ConnectionRow | None
    assignments: tuple[AssignmentRow, ...]


@dataclass(frozen=True, slots=True, repr=False)
class SetupSnapshot:
    ids: SetupIds
    transaction_facts: TransactionFacts
    principal_rows: PrincipalRows
    agent_row: AgentRow | None
    agent_tools: tuple[AgentToolRow, ...]
    agent_roles: tuple[AgentRoleRow, ...]
    agent_delegations: tuple[AgentDelegationRow, ...]
    agent_mcp_connections: tuple[AgentMCPRow, ...]
    workflow_rows: tuple[WorkflowRow, ...]
    # Root-selected minimal interface addition: independent by-Solution membership.
    solution_workflow_rows: tuple[WorkflowRow, ...]
    workflow_roles: tuple[WorkflowRoleRow, ...]
    solution_row: SolutionRow | None
    deployment_rows: tuple[DeploymentRow, ...]
    deployment_edges: tuple[DeploymentEdgeRow, ...]
    model_setup_rows: ModelSetupRows
    solution_execution_metadata: tuple[ExecutionMetadata, ...]
    selected_workflow_execution_metadata: tuple[ExecutionMetadata, ...]


@dataclass(frozen=True, slots=True, repr=False)
class RunSnapshot:
    ids: RunIds
    stage: RunReadStage
    transaction_facts: TransactionFacts
    setup_material: SetupSnapshot
    run_row: RunRow | None
    child_run_metadata: tuple[ChildRunMetadata, ...]
    step_rows: tuple[StepRow, ...]
    parent_attempt_rows: tuple[ParentAttemptRow, ...]
    known_generic_rows: tuple[ParentAttemptRow, ...]
    agent_delivery_rows: tuple[DeliveryRow, ...]
    summary_delivery_rows: tuple[DeliveryRow, ...]
    usage_rows: tuple[UsageRow, ...]
    discovered_execution_id: UUID | None
    execution_rows: tuple[ExecutionRow, ...]
    workflow_attempt_rows: tuple[WorkflowAttemptRow, ...]
    workflow_delivery_rows: tuple[DeliveryRow, ...]
    deferred_child_groups: tuple[DeferredChildGroup, ...]


@dataclass(frozen=True, slots=True)
class ReadResult:
    kind: ReadKind
    code: ReaderCode | None
    snapshot: SetupSnapshot | RunSnapshot | None = field(default=None, repr=False)
    acquisition: ReadAcquisition | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _Cell:
    name: str
    kind: str
    nullable: bool = False
    cap: int = MAX_CELL_BYTES

    @property
    def variable(self) -> bool:
        return self.kind == "text" or self.kind.startswith("json_")


@dataclass(frozen=True, slots=True)
class _Group:
    name: str
    metadata_sql: str
    material_sql: str
    keys: tuple[str, ...]
    selectors: tuple[_Cell, ...]
    cells: tuple[_Cell, ...]
    row_type: type
    maximum: int

    @property
    def fixed_charge(self) -> int:
        # Native domain cells + one native length alias per variable cell +
        # native row_charge/oversize aliases. Nullable native cells also count.
        return ROW_CHARGE + NATIVE_CELL_CHARGE * (len(self.cells) + 2)


_COLUMNS = MappingProxyType(
    {
        UserRow: (
            _Cell("id", "uuid", False),
            _Cell("email", "text", False),
            _Cell("name", "text", True),
            _Cell("organization_id", "uuid", True),
            _Cell("is_active", "bool", False),
            _Cell("is_verified", "bool", False),
            _Cell("is_registered", "bool", False),
            _Cell("is_system", "bool", False),
            _Cell("is_superuser", "bool", False),
            _Cell("is_external", "bool", False),
        ),
        OrganizationRow: (
            _Cell("id", "uuid", False),
            _Cell("is_active", "bool", False),
            _Cell("is_provider", "bool", False),
        ),
        UserRoleRow: (
            _Cell("user_id", "uuid", False),
            _Cell("role_id", "uuid", False),
            _Cell("name", "text", False),
        ),
        AgentRow: (
            _Cell("id", "uuid", False),
            _Cell("name", "text", False),
            _Cell("system_prompt", "text", False),
            _Cell("channels", "json_strings", False),
            _Cell("access_level", "text", False),
            _Cell("organization_id", "uuid", True),
            _Cell("solution_id", "uuid", True),
            _Cell("owner_user_id", "uuid", True),
            _Cell("is_active", "bool", False),
            _Cell("knowledge_sources", "json_strings", False),
            _Cell("system_tools", "json_strings", False),
            _Cell("llm_profile_id", "uuid", True),
            _Cell("llm_max_tokens", "int", True),
            _Cell("max_iterations", "int", True),
            _Cell("max_token_budget", "int", True),
            _Cell("max_run_timeout", "int", True),
        ),
        AgentToolRow: (
            _Cell("agent_id", "uuid", False),
            _Cell("workflow_id", "uuid", False),
        ),
        AgentRoleRow: (
            _Cell("agent_id", "uuid", False),
            _Cell("role_id", "uuid", False),
        ),
        AgentDelegationRow: (
            _Cell("parent_agent_id", "uuid", False),
            _Cell("child_agent_id", "uuid", False),
        ),
        AgentMCPRow: (
            _Cell("agent_id", "uuid", False),
            _Cell("connection_id", "uuid", False),
        ),
        WorkflowRoleRow: (
            _Cell("workflow_id", "uuid", False),
            _Cell("role_id", "uuid", False),
        ),
        ExecutionMetadata: (
            _Cell("id", "uuid", False),
            _Cell("workflow_id", "uuid", True),
        ),
        ChildRunMetadata: (
            _Cell("id", "uuid", False),
            _Cell("parent_run_id", "uuid", True),
        ),
        DeploymentEdgeRow: (
            _Cell("deployment_id", "uuid", False),
            _Cell("dependency_solution_id", "uuid", False),
            _Cell("dependency_deployment_id", "uuid", False),
        ),
        RunRow: (
            _Cell("id", "uuid", False),
            _Cell("agent_id", "uuid", True),
            _Cell("trigger_type", "text", False),
            _Cell("trigger_source", "text", True),
            _Cell("conversation_id", "uuid", True),
            _Cell("event_delivery_id", "uuid", True),
            _Cell("input", "json_object", True),
            _Cell("output", "json_object", True),
            _Cell("output_schema", "json_object", True),
            _Cell("status", "text", False),
            _Cell("org_id", "uuid", True),
            _Cell("caller_user_id", "text", True),
            _Cell("caller_email", "text", True),
            _Cell("caller_name", "text", True),
            _Cell("iterations_used", "int", False),
            _Cell("tokens_used", "int", False),
            _Cell("budget_max_iterations", "int", True),
            _Cell("budget_max_tokens", "int", True),
            _Cell("duration_ms", "int", True),
            _Cell("llm_model", "text", True),
            _Cell("asked", "text", True),
            _Cell("did", "text", True),
            _Cell("answered", "text", True),
            _Cell("metadata", "json_object", False),
            _Cell("confidence", "float", True),
            _Cell("confidence_reason", "text", True),
            _Cell("summary_generated_at", "datetime", True),
            _Cell("summary_status", "text", False),
            _Cell("summary_delivery_id", "uuid", True),
            _Cell("summary_prompt_version", "text", True),
            _Cell("started_at", "datetime", True),
            _Cell("completed_at", "datetime", True),
            _Cell("parent_run_id", "uuid", True),
            _Cell("error_absent", "bool", False),
            _Cell("summary_error_absent", "bool", False),
        ),
        StepRow: (
            _Cell("id", "uuid", False),
            _Cell("run_id", "uuid", False),
            _Cell("step_number", "int", False),
            _Cell("type", "text", False),
            _Cell("content", "json_object", True),
            _Cell("tokens_used", "int", True),
            _Cell("duration_ms", "int", True),
            _Cell("created_at", "datetime", False),
        ),
        ExecutionRow: (
            _Cell("id", "uuid", False),
            _Cell("workflow_id", "uuid", True),
            _Cell("workflow_name", "text", False),
            _Cell("organization_id", "uuid", True),
            _Cell("executed_by", "uuid", True),
            _Cell("executed_by_name", "text", False),
            _Cell("parameters", "json_object", False),
            _Cell("result", "json_object", True),
            _Cell("result_type", "text", True),
            _Cell("started_at", "datetime", True),
            _Cell("completed_at", "datetime", True),
            _Cell("duration_ms", "int", True),
            _Cell("execution_model", "text", True),
            _Cell("form_id", "uuid", True),
            _Cell("api_key_id", "uuid", True),
            _Cell("session_id", "uuid", True),
            _Cell("solution_deployment_id", "uuid", True),
            _Cell("runtime_mode", "text", False),
            _Cell("runtime_evidence", "json_object", True),
            _Cell("runtime_evidence_hash", "text", True),
            _Cell("dispatch_evidence", "json_object", True),
            _Cell("dispatch_evidence_hash", "text", True),
            _Cell("retry_policy", "json_object", False),
            _Cell("attempt_tracking_version", "text", True),
            _Cell("execution_context", "json_object", True),
            _Cell("status", "text", False),
            _Cell("error_absent", "bool", False),
        ),
        ParentAttemptRow: (
            _Cell("id", "uuid", False),
            _Cell("logical_job_type", "text", False),
            _Cell("logical_job_id", "uuid", False),
            _Cell("organization_id", "uuid", True),
            _Cell("attempt_number", "int", False),
            _Cell("status", "text", False),
            _Cell("policy_identifier", "text", False),
            _Cell("workload_class", "text", False),
            _Cell("admission_policy", "text", False),
            _Cell("mechanism", "text", False),
            _Cell("queue_name", "text", True),
            _Cell("message_id", "text", True),
            _Cell("retry_count", "int", False),
            _Cell("replay_count", "int", False),
            _Cell("worker_id", "text", True),
            _Cell("process_id", "int", True),
            _Cell("started_at", "datetime", False),
            _Cell("completed_at", "datetime", True),
            _Cell("lease_absent", "bool", False),
            _Cell("failure_code_absent", "bool", False),
            _Cell("failure_message_absent", "bool", False),
        ),
        WorkflowAttemptRow: (
            _Cell("id", "uuid", False),
            _Cell("execution_id", "uuid", False),
            _Cell("attempt_number", "int", False),
            _Cell("status", "text", False),
            _Cell("phase", "text", False),
            _Cell("worker_id", "text", True),
            _Cell("worker_incarnation_id", "uuid", True),
            _Cell("process_id", "text", True),
            _Cell("runtime_mode", "text", True),
            _Cell("runtime_evidence_hash", "text", True),
            _Cell("dispatch_evidence_hash", "text", True),
            _Cell("policy_digest", "text", True),
            _Cell("policy_version", "text", False),
            _Cell("published_at", "datetime", True),
            _Cell("claimed_at", "datetime", True),
            _Cell("started_at", "datetime", True),
            _Cell("heartbeat_at", "datetime", True),
            _Cell("completed_at", "datetime", True),
            _Cell("claim_present", "bool", False),
            _Cell("failure_phase_absent", "bool", False),
            _Cell("failure_code_absent", "bool", False),
        ),
        DeliveryRow: (
            _Cell("id", "uuid", False),
            _Cell("queue_name", "text", False),
            _Cell("message_id", "text", False),
            _Cell("encrypted_envelope", "text", False, 131072),
            _Cell("status", "text", False),
            _Cell("available_at", "datetime", False),
            _Cell("created_at", "datetime", False),
            _Cell("started_at", "datetime", True),
            _Cell("settled_at", "datetime", True),
            _Cell("claim_count", "int", False),
            _Cell("lease_owner_absent", "bool", False),
            _Cell("lease_token_absent", "bool", False),
            _Cell("lease_expiry_absent", "bool", False),
        ),
        UsageRow: (
            _Cell("id", "int", False),
            _Cell("execution_id", "uuid", True),
            _Cell("conversation_id", "uuid", True),
            _Cell("agent_run_id", "uuid", True),
            _Cell("message_id", "uuid", True),
            _Cell("provider", "text", False),
            _Cell("model", "text", False),
            _Cell("input_tokens", "int", False),
            _Cell("output_tokens", "int", False),
            _Cell("cache_read_tokens", "int", False),
            _Cell("cache_write_tokens", "int", False),
            _Cell("provider_cost", "decimal", True),
            _Cell("cost", "decimal", True),
            _Cell("duration_ms", "int", True),
            _Cell("timestamp", "datetime", False),
            _Cell("sequence", "int", False),
            _Cell("organization_id", "uuid", True),
            _Cell("user_id", "uuid", True),
        ),
        WorkflowRow: (
            _Cell("id", "uuid", False),
            _Cell("solution_id", "uuid", True),
            _Cell("organization_id", "uuid", True),
            _Cell("path", "text", False),
            _Cell("function_name", "text", False),
            _Cell("name", "text", False),
            _Cell("description", "text", True),
            _Cell("category", "text", False),
            _Cell("tags", "json_array", False),
            _Cell("type", "text", False),
            _Cell("tool_description", "text", True),
            _Cell("display_name", "text", True),
            _Cell("parameters_schema", "json_container", False),
            _Cell("execution_mode", "text", False),
            _Cell("timeout_seconds", "int", False),
            _Cell("cache_ttl_seconds", "int", False),
            _Cell("time_saved", "int", False),
            _Cell("value", "decimal", False),
            _Cell("retry_policy", "json_object", False),
            _Cell("access_level", "text", False),
            _Cell("endpoint_enabled", "bool", False),
            _Cell("public_endpoint", "bool", False),
            _Cell("allowed_methods", "json_strings", False),
            _Cell("disable_global_key", "bool", False),
            _Cell("is_active", "bool", False),
            _Cell("is_orphaned", "bool", False),
        ),
        SolutionRow: (
            _Cell("id", "uuid", False),
            _Cell("organization_id", "uuid", True),
            _Cell("status", "text", False),
            _Cell("active_deployment_id", "uuid", True),
            _Cell("execution_runtime_mode", "text", False),
            _Cell("allow_outbound_access", "bool", False),
        ),
        DeploymentRow: (
            _Cell("id", "uuid", False),
            _Cell("organization_id", "uuid", True),
            _Cell("solution_id", "uuid", False),
            _Cell("parent_deployment_id", "uuid", True),
            _Cell("base_deployment_id", "uuid", True),
            _Cell("state", "text", False),
            _Cell("bundle_hash", "text", False),
            _Cell("compiled_manifest", "json_object", False),
            _Cell("compiled_manifest_hash", "text", False),
            _Cell("resolution_map", "json_object", False),
            _Cell("resolution_map_hash", "text", False),
            _Cell("source_artifact_key", "text", False),
            _Cell("runtime_storage_prefix", "text", False),
            _Cell("git_repository", "text", True),
            _Cell("git_ref", "text", True),
            _Cell("git_commit_sha", "text", True),
            _Cell("activated_at", "datetime", True),
            _Cell("superseded_at", "datetime", True),
        ),
        ProfileRow: (
            _Cell("id", "uuid", False),
            _Cell("name", "text", False),
            _Cell("connection_id", "uuid", False),
            _Cell("model", "text", False),
            _Cell("openai_transport", "text", True),
            _Cell("default_max_tokens", "int", True),
            _Cell("capabilities", "json_object", True),
            _Cell("enabled_for_chat", "bool", False),
            _Cell("failover_profile_id", "uuid", True),
        ),
        ConnectionRow: (
            _Cell("id", "uuid", False),
            _Cell("name", "text", False),
            _Cell("provider", "text", False),
            _Cell("endpoint", "text", True),
        ),
        AssignmentRow: (
            _Cell("assignment_key", "text", False),
            _Cell("profile_id", "uuid", False),
        ),
    }
)


_GROUPS = MappingProxyType(
    {
        "user": _Group(
            "user",
            "/* agent-reference:user:metadata */ SELECT id AS id,octet_length((email)::text) AS email_bytes,octet_length((name)::text) AS name_bytes FROM users WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:user:material */ WITH material AS (SELECT id AS id,email AS email,name AS name,organization_id AS organization_id,is_active AS is_active,is_verified AS is_verified,is_registered AS is_registered,is_system AS is_system,is_superuser AS is_superuser,is_external AS is_external FROM users WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,email,name,organization_id,is_active,is_verified,is_registered,is_system,is_superuser,is_external,octet_length(email) AS email_bytes,octet_length(name) AS name_bytes,(1792+coalesce(octet_length(email),0)+coalesce(octet_length(name),0)) AS row_charge FROM material), admitted AS (SELECT id,email,name,organization_id,is_active,is_verified,is_registered,is_system,is_superuser,is_external,email_bytes,name_bytes,row_charge,((coalesce(email_bytes,0)<=65536 AND coalesce(name_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN email ELSE NULL END AS email,email_bytes,CASE WHEN admitted THEN name ELSE NULL END AS name,name_bytes,organization_id,is_active,is_verified,is_registered,is_system,is_superuser,is_external,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[UserRow],
            UserRow,
            1,
        ),
        "organization": _Group(
            "organization",
            "/* agent-reference:organization:metadata */ SELECT id AS id FROM organizations WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:organization:material */ WITH material AS (SELECT id AS id,is_active AS is_active,is_provider AS is_provider FROM organizations WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,is_active,is_provider,(896) AS row_charge FROM material), admitted AS (SELECT id,is_active,is_provider,row_charge,(row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,is_active,is_provider,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[OrganizationRow],
            OrganizationRow,
            1,
        ),
        "user_roles": _Group(
            "user_roles",
            "/* agent-reference:user_roles:metadata */ SELECT ur.user_id AS user_id,ur.role_id AS role_id,octet_length((r.name)::text) AS name_bytes FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=$1::uuid ORDER BY r.id LIMIT 2",
            "/* agent-reference:user_roles:material */ WITH material AS (SELECT ur.user_id AS user_id,ur.role_id AS role_id,r.name AS name FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=$1::uuid AND ur.role_id=$2::uuid LIMIT 2), sized AS (SELECT user_id,role_id,name,octet_length(name) AS name_bytes,(896+coalesce(octet_length(name),0)) AS row_charge FROM material), admitted AS (SELECT user_id,role_id,name,name_bytes,row_charge,((coalesce(name_bytes,0)<=65536) AND row_charge<=$3::bigint) AS admitted FROM sized) SELECT user_id,role_id,CASE WHEN admitted THEN name ELSE NULL END AS name,name_bytes,row_charge,NOT admitted AS oversize FROM admitted",
            ("user_id", "role_id"),
            (),
            _COLUMNS[UserRoleRow],
            UserRoleRow,
            1,
        ),
        "agent": _Group(
            "agent",
            "/* agent-reference:agent:metadata */ SELECT id AS id,octet_length((name)::text) AS name_bytes,octet_length((system_prompt)::text) AS system_prompt_bytes,octet_length((channels::text)::text) AS channels_bytes,octet_length((access_level::text)::text) AS access_level_bytes,octet_length((array_to_json(knowledge_sources)::text)::text) AS knowledge_sources_bytes,octet_length((array_to_json(system_tools)::text)::text) AS system_tools_bytes FROM agents WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:agent:material */ WITH material AS (SELECT id AS id,name AS name,system_prompt AS system_prompt,channels::text AS channels,access_level::text AS access_level,organization_id AS organization_id,solution_id AS solution_id,owner_user_id AS owner_user_id,is_active AS is_active,array_to_json(knowledge_sources)::text AS knowledge_sources,array_to_json(system_tools)::text AS system_tools,llm_profile_id AS llm_profile_id,llm_max_tokens AS llm_max_tokens,max_iterations AS max_iterations,max_token_budget AS max_token_budget,max_run_timeout AS max_run_timeout FROM agents WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,name,system_prompt,channels,access_level,organization_id,solution_id,owner_user_id,is_active,knowledge_sources,system_tools,llm_profile_id,llm_max_tokens,max_iterations,max_token_budget,max_run_timeout,octet_length(name) AS name_bytes,octet_length(system_prompt) AS system_prompt_bytes,octet_length(channels) AS channels_bytes,octet_length(access_level) AS access_level_bytes,octet_length(knowledge_sources) AS knowledge_sources_bytes,octet_length(system_tools) AS system_tools_bytes,(2560+coalesce(octet_length(name),0)+coalesce(octet_length(system_prompt),0)+coalesce(octet_length(channels),0)+coalesce(octet_length(access_level),0)+coalesce(octet_length(knowledge_sources),0)+coalesce(octet_length(system_tools),0)) AS row_charge FROM material), admitted AS (SELECT id,name,system_prompt,channels,access_level,organization_id,solution_id,owner_user_id,is_active,knowledge_sources,system_tools,llm_profile_id,llm_max_tokens,max_iterations,max_token_budget,max_run_timeout,name_bytes,system_prompt_bytes,channels_bytes,access_level_bytes,knowledge_sources_bytes,system_tools_bytes,row_charge,((coalesce(name_bytes,0)<=65536 AND coalesce(system_prompt_bytes,0)<=65536 AND coalesce(channels_bytes,0)<=65536 AND coalesce(access_level_bytes,0)<=65536 AND coalesce(knowledge_sources_bytes,0)<=65536 AND coalesce(system_tools_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN name ELSE NULL END AS name,name_bytes,CASE WHEN admitted THEN system_prompt ELSE NULL END AS system_prompt,system_prompt_bytes,CASE WHEN admitted THEN channels ELSE NULL END AS channels,channels_bytes,CASE WHEN admitted THEN access_level ELSE NULL END AS access_level,access_level_bytes,organization_id,solution_id,owner_user_id,is_active,CASE WHEN admitted THEN knowledge_sources ELSE NULL END AS knowledge_sources,knowledge_sources_bytes,CASE WHEN admitted THEN system_tools ELSE NULL END AS system_tools,system_tools_bytes,llm_profile_id,llm_max_tokens,max_iterations,max_token_budget,max_run_timeout,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[AgentRow],
            AgentRow,
            1,
        ),
        "agent_tools": _Group(
            "agent_tools",
            "/* agent-reference:agent_tools:metadata */ SELECT agent_id AS agent_id,workflow_id AS workflow_id FROM agent_tools WHERE agent_id=$1::uuid ORDER BY agent_id,workflow_id LIMIT 3",
            "/* agent-reference:agent_tools:material */ WITH material AS (SELECT agent_id AS agent_id,workflow_id AS workflow_id FROM agent_tools WHERE agent_id=$1::uuid AND workflow_id=$2::uuid LIMIT 2), sized AS (SELECT agent_id,workflow_id,(768) AS row_charge FROM material), admitted AS (SELECT agent_id,workflow_id,row_charge,(row_charge<=$3::bigint) AS admitted FROM sized) SELECT agent_id,workflow_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("agent_id", "workflow_id"),
            (),
            _COLUMNS[AgentToolRow],
            AgentToolRow,
            2,
        ),
        "agent_roles": _Group(
            "agent_roles",
            "/* agent-reference:agent_roles:metadata */ SELECT agent_id AS agent_id,role_id AS role_id FROM agent_roles WHERE agent_id=$1::uuid ORDER BY agent_id,role_id LIMIT 1",
            "/* agent-reference:agent_roles:material */ WITH material AS (SELECT agent_id AS agent_id,role_id AS role_id FROM agent_roles WHERE agent_id=$1::uuid AND role_id=$2::uuid LIMIT 2), sized AS (SELECT agent_id,role_id,(768) AS row_charge FROM material), admitted AS (SELECT agent_id,role_id,row_charge,(row_charge<=$3::bigint) AS admitted FROM sized) SELECT agent_id,role_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("agent_id", "role_id"),
            (),
            _COLUMNS[AgentRoleRow],
            AgentRoleRow,
            0,
        ),
        "agent_delegations": _Group(
            "agent_delegations",
            "/* agent-reference:agent_delegations:metadata */ SELECT parent_agent_id AS parent_agent_id,child_agent_id AS child_agent_id FROM agent_delegations WHERE parent_agent_id=$1::uuid ORDER BY parent_agent_id,child_agent_id LIMIT 1",
            "/* agent-reference:agent_delegations:material */ WITH material AS (SELECT parent_agent_id AS parent_agent_id,child_agent_id AS child_agent_id FROM agent_delegations WHERE parent_agent_id=$1::uuid AND child_agent_id=$2::uuid LIMIT 2), sized AS (SELECT parent_agent_id,child_agent_id,(768) AS row_charge FROM material), admitted AS (SELECT parent_agent_id,child_agent_id,row_charge,(row_charge<=$3::bigint) AS admitted FROM sized) SELECT parent_agent_id,child_agent_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("parent_agent_id", "child_agent_id"),
            (),
            _COLUMNS[AgentDelegationRow],
            AgentDelegationRow,
            0,
        ),
        "agent_mcp": _Group(
            "agent_mcp",
            "/* agent-reference:agent_mcp:metadata */ SELECT agent_id AS agent_id,connection_id AS connection_id FROM agent_mcp_connections WHERE agent_id=$1::uuid ORDER BY agent_id,connection_id LIMIT 1",
            "/* agent-reference:agent_mcp:material */ WITH material AS (SELECT agent_id AS agent_id,connection_id AS connection_id FROM agent_mcp_connections WHERE agent_id=$1::uuid AND connection_id=$2::uuid LIMIT 2), sized AS (SELECT agent_id,connection_id,(768) AS row_charge FROM material), admitted AS (SELECT agent_id,connection_id,row_charge,(row_charge<=$3::bigint) AS admitted FROM sized) SELECT agent_id,connection_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("agent_id", "connection_id"),
            (),
            _COLUMNS[AgentMCPRow],
            AgentMCPRow,
            0,
        ),
        "workflow_roles": _Group(
            "workflow_roles",
            "/* agent-reference:workflow_roles:metadata */ SELECT workflow_id AS workflow_id,role_id AS role_id FROM workflow_roles WHERE workflow_id=ANY($1::uuid[]) ORDER BY workflow_id,role_id LIMIT 3",
            "/* agent-reference:workflow_roles:material */ WITH material AS (SELECT workflow_id AS workflow_id,role_id AS role_id FROM workflow_roles WHERE workflow_id=$1::uuid AND role_id=$2::uuid LIMIT 2), sized AS (SELECT workflow_id,role_id,(768) AS row_charge FROM material), admitted AS (SELECT workflow_id,role_id,row_charge,(row_charge<=$3::bigint) AS admitted FROM sized) SELECT workflow_id,role_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("workflow_id", "role_id"),
            (),
            _COLUMNS[WorkflowRoleRow],
            WorkflowRoleRow,
            2,
        ),
        "run": _Group(
            "run",
            "/* agent-reference:run:metadata */ SELECT id AS id,octet_length((trigger_type)::text) AS trigger_type_bytes,octet_length((trigger_source)::text) AS trigger_source_bytes,octet_length((input::text)::text) AS input_bytes,octet_length((output::text)::text) AS output_bytes,octet_length((output_schema::text)::text) AS output_schema_bytes,octet_length((status)::text) AS status_bytes,octet_length((caller_user_id)::text) AS caller_user_id_bytes,octet_length((caller_email)::text) AS caller_email_bytes,octet_length((caller_name)::text) AS caller_name_bytes,octet_length((llm_model)::text) AS llm_model_bytes,octet_length((asked)::text) AS asked_bytes,octet_length((did)::text) AS did_bytes,octet_length((answered)::text) AS answered_bytes,octet_length((metadata::text)::text) AS metadata_bytes,octet_length((confidence_reason)::text) AS confidence_reason_bytes,octet_length((summary_status)::text) AS summary_status_bytes,octet_length((summary_prompt_version)::text) AS summary_prompt_version_bytes FROM agent_runs WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:run:material */ WITH material AS (SELECT id AS id,agent_id AS agent_id,trigger_type AS trigger_type,trigger_source AS trigger_source,conversation_id AS conversation_id,event_delivery_id AS event_delivery_id,input::text AS input,output::text AS output,output_schema::text AS output_schema,status AS status,org_id AS org_id,caller_user_id AS caller_user_id,caller_email AS caller_email,caller_name AS caller_name,iterations_used AS iterations_used,tokens_used AS tokens_used,budget_max_iterations AS budget_max_iterations,budget_max_tokens AS budget_max_tokens,duration_ms AS duration_ms,llm_model AS llm_model,asked AS asked,did AS did,answered AS answered,metadata::text AS metadata,confidence AS confidence,confidence_reason AS confidence_reason,summary_generated_at AS summary_generated_at,summary_status AS summary_status,summary_delivery_id AS summary_delivery_id,summary_prompt_version AS summary_prompt_version,started_at AS started_at,completed_at AS completed_at,parent_run_id AS parent_run_id,error IS NULL AS error_absent,summary_error IS NULL AS summary_error_absent FROM agent_runs WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,agent_id,trigger_type,trigger_source,conversation_id,event_delivery_id,input,output,output_schema,status,org_id,caller_user_id,caller_email,caller_name,iterations_used,tokens_used,budget_max_iterations,budget_max_tokens,duration_ms,llm_model,asked,did,answered,metadata,confidence,confidence_reason,summary_generated_at,summary_status,summary_delivery_id,summary_prompt_version,started_at,completed_at,parent_run_id,error_absent,summary_error_absent,octet_length(trigger_type) AS trigger_type_bytes,octet_length(trigger_source) AS trigger_source_bytes,octet_length(input) AS input_bytes,octet_length(output) AS output_bytes,octet_length(output_schema) AS output_schema_bytes,octet_length(status) AS status_bytes,octet_length(caller_user_id) AS caller_user_id_bytes,octet_length(caller_email) AS caller_email_bytes,octet_length(caller_name) AS caller_name_bytes,octet_length(llm_model) AS llm_model_bytes,octet_length(asked) AS asked_bytes,octet_length(did) AS did_bytes,octet_length(answered) AS answered_bytes,octet_length(metadata) AS metadata_bytes,octet_length(confidence_reason) AS confidence_reason_bytes,octet_length(summary_status) AS summary_status_bytes,octet_length(summary_prompt_version) AS summary_prompt_version_bytes,(4992+coalesce(octet_length(trigger_type),0)+coalesce(octet_length(trigger_source),0)+coalesce(octet_length(input),0)+coalesce(octet_length(output),0)+coalesce(octet_length(output_schema),0)+coalesce(octet_length(status),0)+coalesce(octet_length(caller_user_id),0)+coalesce(octet_length(caller_email),0)+coalesce(octet_length(caller_name),0)+coalesce(octet_length(llm_model),0)+coalesce(octet_length(asked),0)+coalesce(octet_length(did),0)+coalesce(octet_length(answered),0)+coalesce(octet_length(metadata),0)+coalesce(octet_length(confidence_reason),0)+coalesce(octet_length(summary_status),0)+coalesce(octet_length(summary_prompt_version),0)) AS row_charge FROM material), admitted AS (SELECT id,agent_id,trigger_type,trigger_source,conversation_id,event_delivery_id,input,output,output_schema,status,org_id,caller_user_id,caller_email,caller_name,iterations_used,tokens_used,budget_max_iterations,budget_max_tokens,duration_ms,llm_model,asked,did,answered,metadata,confidence,confidence_reason,summary_generated_at,summary_status,summary_delivery_id,summary_prompt_version,started_at,completed_at,parent_run_id,error_absent,summary_error_absent,trigger_type_bytes,trigger_source_bytes,input_bytes,output_bytes,output_schema_bytes,status_bytes,caller_user_id_bytes,caller_email_bytes,caller_name_bytes,llm_model_bytes,asked_bytes,did_bytes,answered_bytes,metadata_bytes,confidence_reason_bytes,summary_status_bytes,summary_prompt_version_bytes,row_charge,((coalesce(trigger_type_bytes,0)<=65536 AND coalesce(trigger_source_bytes,0)<=65536 AND coalesce(input_bytes,0)<=65536 AND coalesce(output_bytes,0)<=65536 AND coalesce(output_schema_bytes,0)<=65536 AND coalesce(status_bytes,0)<=65536 AND coalesce(caller_user_id_bytes,0)<=65536 AND coalesce(caller_email_bytes,0)<=65536 AND coalesce(caller_name_bytes,0)<=65536 AND coalesce(llm_model_bytes,0)<=65536 AND coalesce(asked_bytes,0)<=65536 AND coalesce(did_bytes,0)<=65536 AND coalesce(answered_bytes,0)<=65536 AND coalesce(metadata_bytes,0)<=65536 AND coalesce(confidence_reason_bytes,0)<=65536 AND coalesce(summary_status_bytes,0)<=65536 AND coalesce(summary_prompt_version_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,agent_id,CASE WHEN admitted THEN trigger_type ELSE NULL END AS trigger_type,trigger_type_bytes,CASE WHEN admitted THEN trigger_source ELSE NULL END AS trigger_source,trigger_source_bytes,conversation_id,event_delivery_id,CASE WHEN admitted THEN input ELSE NULL END AS input,input_bytes,CASE WHEN admitted THEN output ELSE NULL END AS output,output_bytes,CASE WHEN admitted THEN output_schema ELSE NULL END AS output_schema,output_schema_bytes,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,org_id,CASE WHEN admitted THEN caller_user_id ELSE NULL END AS caller_user_id,caller_user_id_bytes,CASE WHEN admitted THEN caller_email ELSE NULL END AS caller_email,caller_email_bytes,CASE WHEN admitted THEN caller_name ELSE NULL END AS caller_name,caller_name_bytes,iterations_used,tokens_used,budget_max_iterations,budget_max_tokens,duration_ms,CASE WHEN admitted THEN llm_model ELSE NULL END AS llm_model,llm_model_bytes,CASE WHEN admitted THEN asked ELSE NULL END AS asked,asked_bytes,CASE WHEN admitted THEN did ELSE NULL END AS did,did_bytes,CASE WHEN admitted THEN answered ELSE NULL END AS answered,answered_bytes,CASE WHEN admitted THEN metadata ELSE NULL END AS metadata,metadata_bytes,confidence,CASE WHEN admitted THEN confidence_reason ELSE NULL END AS confidence_reason,confidence_reason_bytes,summary_generated_at,CASE WHEN admitted THEN summary_status ELSE NULL END AS summary_status,summary_status_bytes,summary_delivery_id,CASE WHEN admitted THEN summary_prompt_version ELSE NULL END AS summary_prompt_version,summary_prompt_version_bytes,started_at,completed_at,parent_run_id,error_absent,summary_error_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[RunRow],
            RunRow,
            1,
        ),
        "child_runs": _Group(
            "child_runs",
            "/* agent-reference:child_runs:metadata */ SELECT id AS id FROM agent_runs WHERE parent_run_id=$1::uuid ORDER BY id LIMIT 1",
            "/* agent-reference:child_runs:material */ WITH material AS (SELECT id AS id,parent_run_id AS parent_run_id FROM agent_runs WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,parent_run_id,(768) AS row_charge FROM material), admitted AS (SELECT id,parent_run_id,row_charge,(row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,parent_run_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[ChildRunMetadata],
            ChildRunMetadata,
            0,
        ),
        "steps": _Group(
            "steps",
            "/* agent-reference:steps:metadata */ SELECT id AS id,step_number AS step_number,type AS type,octet_length((type)::text) AS type_bytes,octet_length((content::text)::text) AS content_bytes FROM agent_run_steps WHERE run_id=$1::uuid ORDER BY step_number,id LIMIT 7",
            "/* agent-reference:steps:material */ WITH material AS (SELECT id AS id,run_id AS run_id,step_number AS step_number,type AS type,content::text AS content,tokens_used AS tokens_used,duration_ms AS duration_ms,created_at AS created_at FROM agent_run_steps WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,run_id,step_number,type,content,tokens_used,duration_ms,created_at,octet_length(type) AS type_bytes,octet_length(content) AS content_bytes,(1536+coalesce(octet_length(type),0)+coalesce(octet_length(content),0)) AS row_charge FROM material), admitted AS (SELECT id,run_id,step_number,type,content,tokens_used,duration_ms,created_at,type_bytes,content_bytes,row_charge,((coalesce(type_bytes,0)<=65536 AND coalesce(content_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,run_id,step_number,CASE WHEN admitted THEN type ELSE NULL END AS type,type_bytes,CASE WHEN admitted THEN content ELSE NULL END AS content,content_bytes,tokens_used,duration_ms,created_at,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (
                _Cell("step_number", "int", False),
                _Cell("type", "text", False),
            ),
            _COLUMNS[StepRow],
            StepRow,
            6,
        ),
        "execution": _Group(
            "execution",
            "/* agent-reference:execution:metadata */ SELECT id AS id,octet_length((workflow_name)::text) AS workflow_name_bytes,octet_length((executed_by_name)::text) AS executed_by_name_bytes,octet_length((parameters::text)::text) AS parameters_bytes,octet_length((result::text)::text) AS result_bytes,octet_length((result_type)::text) AS result_type_bytes,octet_length((execution_model)::text) AS execution_model_bytes,octet_length((runtime_mode)::text) AS runtime_mode_bytes,octet_length((runtime_evidence::text)::text) AS runtime_evidence_bytes,octet_length((runtime_evidence_hash)::text) AS runtime_evidence_hash_bytes,octet_length((dispatch_evidence::text)::text) AS dispatch_evidence_bytes,octet_length((dispatch_evidence_hash)::text) AS dispatch_evidence_hash_bytes,octet_length((retry_policy::text)::text) AS retry_policy_bytes,octet_length((attempt_tracking_version)::text) AS attempt_tracking_version_bytes,octet_length((execution_context::text)::text) AS execution_context_bytes,octet_length((status::text)::text) AS status_bytes FROM executions WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:execution:material */ WITH material AS (SELECT id AS id,workflow_id AS workflow_id,workflow_name AS workflow_name,organization_id AS organization_id,executed_by AS executed_by,executed_by_name AS executed_by_name,parameters::text AS parameters,result::text AS result,result_type AS result_type,started_at AS started_at,completed_at AS completed_at,duration_ms AS duration_ms,execution_model AS execution_model,form_id AS form_id,api_key_id AS api_key_id,session_id AS session_id,solution_deployment_id AS solution_deployment_id,runtime_mode AS runtime_mode,runtime_evidence::text AS runtime_evidence,runtime_evidence_hash AS runtime_evidence_hash,dispatch_evidence::text AS dispatch_evidence,dispatch_evidence_hash AS dispatch_evidence_hash,retry_policy::text AS retry_policy,attempt_tracking_version AS attempt_tracking_version,execution_context::text AS execution_context,status::text AS status,error_message IS NULL AS error_absent FROM executions WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,workflow_id,workflow_name,organization_id,executed_by,executed_by_name,parameters,result,result_type,started_at,completed_at,duration_ms,execution_model,form_id,api_key_id,session_id,solution_deployment_id,runtime_mode,runtime_evidence,runtime_evidence_hash,dispatch_evidence,dispatch_evidence_hash,retry_policy,attempt_tracking_version,execution_context,status,error_absent,octet_length(workflow_name) AS workflow_name_bytes,octet_length(executed_by_name) AS executed_by_name_bytes,octet_length(parameters) AS parameters_bytes,octet_length(result) AS result_bytes,octet_length(result_type) AS result_type_bytes,octet_length(execution_model) AS execution_model_bytes,octet_length(runtime_mode) AS runtime_mode_bytes,octet_length(runtime_evidence) AS runtime_evidence_bytes,octet_length(runtime_evidence_hash) AS runtime_evidence_hash_bytes,octet_length(dispatch_evidence) AS dispatch_evidence_bytes,octet_length(dispatch_evidence_hash) AS dispatch_evidence_hash_bytes,octet_length(retry_policy) AS retry_policy_bytes,octet_length(attempt_tracking_version) AS attempt_tracking_version_bytes,octet_length(execution_context) AS execution_context_bytes,octet_length(status) AS status_bytes,(3968+coalesce(octet_length(workflow_name),0)+coalesce(octet_length(executed_by_name),0)+coalesce(octet_length(parameters),0)+coalesce(octet_length(result),0)+coalesce(octet_length(result_type),0)+coalesce(octet_length(execution_model),0)+coalesce(octet_length(runtime_mode),0)+coalesce(octet_length(runtime_evidence),0)+coalesce(octet_length(runtime_evidence_hash),0)+coalesce(octet_length(dispatch_evidence),0)+coalesce(octet_length(dispatch_evidence_hash),0)+coalesce(octet_length(retry_policy),0)+coalesce(octet_length(attempt_tracking_version),0)+coalesce(octet_length(execution_context),0)+coalesce(octet_length(status),0)) AS row_charge FROM material), admitted AS (SELECT id,workflow_id,workflow_name,organization_id,executed_by,executed_by_name,parameters,result,result_type,started_at,completed_at,duration_ms,execution_model,form_id,api_key_id,session_id,solution_deployment_id,runtime_mode,runtime_evidence,runtime_evidence_hash,dispatch_evidence,dispatch_evidence_hash,retry_policy,attempt_tracking_version,execution_context,status,error_absent,workflow_name_bytes,executed_by_name_bytes,parameters_bytes,result_bytes,result_type_bytes,execution_model_bytes,runtime_mode_bytes,runtime_evidence_bytes,runtime_evidence_hash_bytes,dispatch_evidence_bytes,dispatch_evidence_hash_bytes,retry_policy_bytes,attempt_tracking_version_bytes,execution_context_bytes,status_bytes,row_charge,((coalesce(workflow_name_bytes,0)<=65536 AND coalesce(executed_by_name_bytes,0)<=65536 AND coalesce(parameters_bytes,0)<=65536 AND coalesce(result_bytes,0)<=65536 AND coalesce(result_type_bytes,0)<=65536 AND coalesce(execution_model_bytes,0)<=65536 AND coalesce(runtime_mode_bytes,0)<=65536 AND coalesce(runtime_evidence_bytes,0)<=65536 AND coalesce(runtime_evidence_hash_bytes,0)<=65536 AND coalesce(dispatch_evidence_bytes,0)<=65536 AND coalesce(dispatch_evidence_hash_bytes,0)<=65536 AND coalesce(retry_policy_bytes,0)<=65536 AND coalesce(attempt_tracking_version_bytes,0)<=65536 AND coalesce(execution_context_bytes,0)<=65536 AND coalesce(status_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,workflow_id,CASE WHEN admitted THEN workflow_name ELSE NULL END AS workflow_name,workflow_name_bytes,organization_id,executed_by,CASE WHEN admitted THEN executed_by_name ELSE NULL END AS executed_by_name,executed_by_name_bytes,CASE WHEN admitted THEN parameters ELSE NULL END AS parameters,parameters_bytes,CASE WHEN admitted THEN result ELSE NULL END AS result,result_bytes,CASE WHEN admitted THEN result_type ELSE NULL END AS result_type,result_type_bytes,started_at,completed_at,duration_ms,CASE WHEN admitted THEN execution_model ELSE NULL END AS execution_model,execution_model_bytes,form_id,api_key_id,session_id,solution_deployment_id,CASE WHEN admitted THEN runtime_mode ELSE NULL END AS runtime_mode,runtime_mode_bytes,CASE WHEN admitted THEN runtime_evidence ELSE NULL END AS runtime_evidence,runtime_evidence_bytes,CASE WHEN admitted THEN runtime_evidence_hash ELSE NULL END AS runtime_evidence_hash,runtime_evidence_hash_bytes,CASE WHEN admitted THEN dispatch_evidence ELSE NULL END AS dispatch_evidence,dispatch_evidence_bytes,CASE WHEN admitted THEN dispatch_evidence_hash ELSE NULL END AS dispatch_evidence_hash,dispatch_evidence_hash_bytes,CASE WHEN admitted THEN retry_policy ELSE NULL END AS retry_policy,retry_policy_bytes,CASE WHEN admitted THEN attempt_tracking_version ELSE NULL END AS attempt_tracking_version,attempt_tracking_version_bytes,CASE WHEN admitted THEN execution_context ELSE NULL END AS execution_context,execution_context_bytes,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,error_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[ExecutionRow],
            ExecutionRow,
            1,
        ),
        "parent_attempts": _Group(
            "parent_attempts",
            "/* agent-reference:parent_attempts:metadata */ SELECT id AS id,logical_job_type AS logical_job_type,logical_job_id AS logical_job_id,octet_length((logical_job_type)::text) AS logical_job_type_bytes,octet_length((status)::text) AS status_bytes,octet_length((policy_identifier)::text) AS policy_identifier_bytes,octet_length((workload_class)::text) AS workload_class_bytes,octet_length((admission_policy)::text) AS admission_policy_bytes,octet_length((mechanism)::text) AS mechanism_bytes,octet_length((queue_name)::text) AS queue_name_bytes,octet_length((message_id)::text) AS message_id_bytes,octet_length((worker_id)::text) AS worker_id_bytes FROM execution_attempts WHERE logical_job_type='agent_run' AND logical_job_id=$1::uuid ORDER BY attempt_number,id LIMIT 2",
            "/* agent-reference:parent_attempts:material */ WITH material AS (SELECT id AS id,logical_job_type AS logical_job_type,logical_job_id AS logical_job_id,organization_id AS organization_id,attempt_number AS attempt_number,status AS status,policy_identifier AS policy_identifier,workload_class AS workload_class,admission_policy AS admission_policy,mechanism AS mechanism,queue_name AS queue_name,message_id AS message_id,retry_count AS retry_count,replay_count AS replay_count,worker_id AS worker_id,process_id AS process_id,started_at AS started_at,completed_at AS completed_at,lease_token IS NULL AS lease_absent,failure_code IS NULL AS failure_code_absent,failure_message IS NULL AS failure_message_absent FROM execution_attempts WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,logical_job_type,logical_job_id,organization_id,attempt_number,status,policy_identifier,workload_class,admission_policy,mechanism,queue_name,message_id,retry_count,replay_count,worker_id,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,octet_length(logical_job_type) AS logical_job_type_bytes,octet_length(status) AS status_bytes,octet_length(policy_identifier) AS policy_identifier_bytes,octet_length(workload_class) AS workload_class_bytes,octet_length(admission_policy) AS admission_policy_bytes,octet_length(mechanism) AS mechanism_bytes,octet_length(queue_name) AS queue_name_bytes,octet_length(message_id) AS message_id_bytes,octet_length(worker_id) AS worker_id_bytes,(3200+coalesce(octet_length(logical_job_type),0)+coalesce(octet_length(status),0)+coalesce(octet_length(policy_identifier),0)+coalesce(octet_length(workload_class),0)+coalesce(octet_length(admission_policy),0)+coalesce(octet_length(mechanism),0)+coalesce(octet_length(queue_name),0)+coalesce(octet_length(message_id),0)+coalesce(octet_length(worker_id),0)) AS row_charge FROM material), admitted AS (SELECT id,logical_job_type,logical_job_id,organization_id,attempt_number,status,policy_identifier,workload_class,admission_policy,mechanism,queue_name,message_id,retry_count,replay_count,worker_id,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,logical_job_type_bytes,status_bytes,policy_identifier_bytes,workload_class_bytes,admission_policy_bytes,mechanism_bytes,queue_name_bytes,message_id_bytes,worker_id_bytes,row_charge,((coalesce(logical_job_type_bytes,0)<=65536 AND coalesce(status_bytes,0)<=65536 AND coalesce(policy_identifier_bytes,0)<=65536 AND coalesce(workload_class_bytes,0)<=65536 AND coalesce(admission_policy_bytes,0)<=65536 AND coalesce(mechanism_bytes,0)<=65536 AND coalesce(queue_name_bytes,0)<=65536 AND coalesce(message_id_bytes,0)<=65536 AND coalesce(worker_id_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN logical_job_type ELSE NULL END AS logical_job_type,logical_job_type_bytes,logical_job_id,organization_id,attempt_number,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,CASE WHEN admitted THEN policy_identifier ELSE NULL END AS policy_identifier,policy_identifier_bytes,CASE WHEN admitted THEN workload_class ELSE NULL END AS workload_class,workload_class_bytes,CASE WHEN admitted THEN admission_policy ELSE NULL END AS admission_policy,admission_policy_bytes,CASE WHEN admitted THEN mechanism ELSE NULL END AS mechanism,mechanism_bytes,CASE WHEN admitted THEN queue_name ELSE NULL END AS queue_name,queue_name_bytes,CASE WHEN admitted THEN message_id ELSE NULL END AS message_id,message_id_bytes,retry_count,replay_count,CASE WHEN admitted THEN worker_id ELSE NULL END AS worker_id,worker_id_bytes,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (
                _Cell("logical_job_type", "text", False),
                _Cell("logical_job_id", "uuid", False),
            ),
            _COLUMNS[ParentAttemptRow],
            ParentAttemptRow,
            1,
        ),
        "generic_run": _Group(
            "generic_run",
            "/* agent-reference:generic_run:metadata */ SELECT id AS id,logical_job_type AS logical_job_type,logical_job_id AS logical_job_id,octet_length((logical_job_type)::text) AS logical_job_type_bytes,octet_length((status)::text) AS status_bytes,octet_length((policy_identifier)::text) AS policy_identifier_bytes,octet_length((workload_class)::text) AS workload_class_bytes,octet_length((admission_policy)::text) AS admission_policy_bytes,octet_length((mechanism)::text) AS mechanism_bytes,octet_length((queue_name)::text) AS queue_name_bytes,octet_length((message_id)::text) AS message_id_bytes,octet_length((worker_id)::text) AS worker_id_bytes FROM execution_attempts WHERE logical_job_id=$1::uuid ORDER BY logical_job_id,logical_job_type,attempt_number,id LIMIT 2",
            "/* agent-reference:generic_run:material */ WITH material AS (SELECT id AS id,logical_job_type AS logical_job_type,logical_job_id AS logical_job_id,organization_id AS organization_id,attempt_number AS attempt_number,status AS status,policy_identifier AS policy_identifier,workload_class AS workload_class,admission_policy AS admission_policy,mechanism AS mechanism,queue_name AS queue_name,message_id AS message_id,retry_count AS retry_count,replay_count AS replay_count,worker_id AS worker_id,process_id AS process_id,started_at AS started_at,completed_at AS completed_at,lease_token IS NULL AS lease_absent,failure_code IS NULL AS failure_code_absent,failure_message IS NULL AS failure_message_absent FROM execution_attempts WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,logical_job_type,logical_job_id,organization_id,attempt_number,status,policy_identifier,workload_class,admission_policy,mechanism,queue_name,message_id,retry_count,replay_count,worker_id,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,octet_length(logical_job_type) AS logical_job_type_bytes,octet_length(status) AS status_bytes,octet_length(policy_identifier) AS policy_identifier_bytes,octet_length(workload_class) AS workload_class_bytes,octet_length(admission_policy) AS admission_policy_bytes,octet_length(mechanism) AS mechanism_bytes,octet_length(queue_name) AS queue_name_bytes,octet_length(message_id) AS message_id_bytes,octet_length(worker_id) AS worker_id_bytes,(3200+coalesce(octet_length(logical_job_type),0)+coalesce(octet_length(status),0)+coalesce(octet_length(policy_identifier),0)+coalesce(octet_length(workload_class),0)+coalesce(octet_length(admission_policy),0)+coalesce(octet_length(mechanism),0)+coalesce(octet_length(queue_name),0)+coalesce(octet_length(message_id),0)+coalesce(octet_length(worker_id),0)) AS row_charge FROM material), admitted AS (SELECT id,logical_job_type,logical_job_id,organization_id,attempt_number,status,policy_identifier,workload_class,admission_policy,mechanism,queue_name,message_id,retry_count,replay_count,worker_id,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,logical_job_type_bytes,status_bytes,policy_identifier_bytes,workload_class_bytes,admission_policy_bytes,mechanism_bytes,queue_name_bytes,message_id_bytes,worker_id_bytes,row_charge,((coalesce(logical_job_type_bytes,0)<=65536 AND coalesce(status_bytes,0)<=65536 AND coalesce(policy_identifier_bytes,0)<=65536 AND coalesce(workload_class_bytes,0)<=65536 AND coalesce(admission_policy_bytes,0)<=65536 AND coalesce(mechanism_bytes,0)<=65536 AND coalesce(queue_name_bytes,0)<=65536 AND coalesce(message_id_bytes,0)<=65536 AND coalesce(worker_id_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN logical_job_type ELSE NULL END AS logical_job_type,logical_job_type_bytes,logical_job_id,organization_id,attempt_number,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,CASE WHEN admitted THEN policy_identifier ELSE NULL END AS policy_identifier,policy_identifier_bytes,CASE WHEN admitted THEN workload_class ELSE NULL END AS workload_class,workload_class_bytes,CASE WHEN admitted THEN admission_policy ELSE NULL END AS admission_policy,admission_policy_bytes,CASE WHEN admitted THEN mechanism ELSE NULL END AS mechanism,mechanism_bytes,CASE WHEN admitted THEN queue_name ELSE NULL END AS queue_name,queue_name_bytes,CASE WHEN admitted THEN message_id ELSE NULL END AS message_id,message_id_bytes,retry_count,replay_count,CASE WHEN admitted THEN worker_id ELSE NULL END AS worker_id,worker_id_bytes,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (
                _Cell("logical_job_type", "text", False),
                _Cell("logical_job_id", "uuid", False),
            ),
            _COLUMNS[ParentAttemptRow],
            ParentAttemptRow,
            1,
        ),
        "generic_known": _Group(
            "generic_known",
            "/* agent-reference:generic_known:metadata */ SELECT id AS id,logical_job_type AS logical_job_type,logical_job_id AS logical_job_id,octet_length((logical_job_type)::text) AS logical_job_type_bytes,octet_length((status)::text) AS status_bytes,octet_length((policy_identifier)::text) AS policy_identifier_bytes,octet_length((workload_class)::text) AS workload_class_bytes,octet_length((admission_policy)::text) AS admission_policy_bytes,octet_length((mechanism)::text) AS mechanism_bytes,octet_length((queue_name)::text) AS queue_name_bytes,octet_length((message_id)::text) AS message_id_bytes,octet_length((worker_id)::text) AS worker_id_bytes FROM execution_attempts WHERE logical_job_id IN ($1::uuid,$2::uuid) ORDER BY logical_job_id,logical_job_type,attempt_number,id LIMIT 2",
            "/* agent-reference:generic_known:material */ WITH material AS (SELECT id AS id,logical_job_type AS logical_job_type,logical_job_id AS logical_job_id,organization_id AS organization_id,attempt_number AS attempt_number,status AS status,policy_identifier AS policy_identifier,workload_class AS workload_class,admission_policy AS admission_policy,mechanism AS mechanism,queue_name AS queue_name,message_id AS message_id,retry_count AS retry_count,replay_count AS replay_count,worker_id AS worker_id,process_id AS process_id,started_at AS started_at,completed_at AS completed_at,lease_token IS NULL AS lease_absent,failure_code IS NULL AS failure_code_absent,failure_message IS NULL AS failure_message_absent FROM execution_attempts WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,logical_job_type,logical_job_id,organization_id,attempt_number,status,policy_identifier,workload_class,admission_policy,mechanism,queue_name,message_id,retry_count,replay_count,worker_id,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,octet_length(logical_job_type) AS logical_job_type_bytes,octet_length(status) AS status_bytes,octet_length(policy_identifier) AS policy_identifier_bytes,octet_length(workload_class) AS workload_class_bytes,octet_length(admission_policy) AS admission_policy_bytes,octet_length(mechanism) AS mechanism_bytes,octet_length(queue_name) AS queue_name_bytes,octet_length(message_id) AS message_id_bytes,octet_length(worker_id) AS worker_id_bytes,(3200+coalesce(octet_length(logical_job_type),0)+coalesce(octet_length(status),0)+coalesce(octet_length(policy_identifier),0)+coalesce(octet_length(workload_class),0)+coalesce(octet_length(admission_policy),0)+coalesce(octet_length(mechanism),0)+coalesce(octet_length(queue_name),0)+coalesce(octet_length(message_id),0)+coalesce(octet_length(worker_id),0)) AS row_charge FROM material), admitted AS (SELECT id,logical_job_type,logical_job_id,organization_id,attempt_number,status,policy_identifier,workload_class,admission_policy,mechanism,queue_name,message_id,retry_count,replay_count,worker_id,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,logical_job_type_bytes,status_bytes,policy_identifier_bytes,workload_class_bytes,admission_policy_bytes,mechanism_bytes,queue_name_bytes,message_id_bytes,worker_id_bytes,row_charge,((coalesce(logical_job_type_bytes,0)<=65536 AND coalesce(status_bytes,0)<=65536 AND coalesce(policy_identifier_bytes,0)<=65536 AND coalesce(workload_class_bytes,0)<=65536 AND coalesce(admission_policy_bytes,0)<=65536 AND coalesce(mechanism_bytes,0)<=65536 AND coalesce(queue_name_bytes,0)<=65536 AND coalesce(message_id_bytes,0)<=65536 AND coalesce(worker_id_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN logical_job_type ELSE NULL END AS logical_job_type,logical_job_type_bytes,logical_job_id,organization_id,attempt_number,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,CASE WHEN admitted THEN policy_identifier ELSE NULL END AS policy_identifier,policy_identifier_bytes,CASE WHEN admitted THEN workload_class ELSE NULL END AS workload_class,workload_class_bytes,CASE WHEN admitted THEN admission_policy ELSE NULL END AS admission_policy,admission_policy_bytes,CASE WHEN admitted THEN mechanism ELSE NULL END AS mechanism,mechanism_bytes,CASE WHEN admitted THEN queue_name ELSE NULL END AS queue_name,queue_name_bytes,CASE WHEN admitted THEN message_id ELSE NULL END AS message_id,message_id_bytes,retry_count,replay_count,CASE WHEN admitted THEN worker_id ELSE NULL END AS worker_id,worker_id_bytes,process_id,started_at,completed_at,lease_absent,failure_code_absent,failure_message_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (
                _Cell("logical_job_type", "text", False),
                _Cell("logical_job_id", "uuid", False),
            ),
            _COLUMNS[ParentAttemptRow],
            ParentAttemptRow,
            1,
        ),
        "workflow_attempts": _Group(
            "workflow_attempts",
            "/* agent-reference:workflow_attempts:metadata */ SELECT id AS id,octet_length((status)::text) AS status_bytes,octet_length((phase)::text) AS phase_bytes,octet_length((worker_id)::text) AS worker_id_bytes,octet_length((process_id)::text) AS process_id_bytes,octet_length((runtime_mode)::text) AS runtime_mode_bytes,octet_length((runtime_evidence_hash)::text) AS runtime_evidence_hash_bytes,octet_length((dispatch_evidence_hash)::text) AS dispatch_evidence_hash_bytes,octet_length((policy_digest)::text) AS policy_digest_bytes,octet_length((policy_version)::text) AS policy_version_bytes FROM workflow_execution_attempts WHERE execution_id=$1::uuid ORDER BY attempt_number,id LIMIT 2",
            "/* agent-reference:workflow_attempts:material */ WITH material AS (SELECT id AS id,execution_id AS execution_id,attempt_number AS attempt_number,status AS status,phase AS phase,worker_id AS worker_id,worker_incarnation_id AS worker_incarnation_id,process_id AS process_id,runtime_mode AS runtime_mode,runtime_evidence_hash AS runtime_evidence_hash,dispatch_evidence_hash AS dispatch_evidence_hash,policy_digest AS policy_digest,policy_version AS policy_version,published_at AS published_at,claimed_at AS claimed_at,started_at AS started_at,heartbeat_at AS heartbeat_at,completed_at AS completed_at,claim_token IS NOT NULL AS claim_present,failure_phase IS NULL AS failure_phase_absent,failure_code IS NULL AS failure_code_absent FROM workflow_execution_attempts WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,execution_id,attempt_number,status,phase,worker_id,worker_incarnation_id,process_id,runtime_mode,runtime_evidence_hash,dispatch_evidence_hash,policy_digest,policy_version,published_at,claimed_at,started_at,heartbeat_at,completed_at,claim_present,failure_phase_absent,failure_code_absent,octet_length(status) AS status_bytes,octet_length(phase) AS phase_bytes,octet_length(worker_id) AS worker_id_bytes,octet_length(process_id) AS process_id_bytes,octet_length(runtime_mode) AS runtime_mode_bytes,octet_length(runtime_evidence_hash) AS runtime_evidence_hash_bytes,octet_length(dispatch_evidence_hash) AS dispatch_evidence_hash_bytes,octet_length(policy_digest) AS policy_digest_bytes,octet_length(policy_version) AS policy_version_bytes,(3200+coalesce(octet_length(status),0)+coalesce(octet_length(phase),0)+coalesce(octet_length(worker_id),0)+coalesce(octet_length(process_id),0)+coalesce(octet_length(runtime_mode),0)+coalesce(octet_length(runtime_evidence_hash),0)+coalesce(octet_length(dispatch_evidence_hash),0)+coalesce(octet_length(policy_digest),0)+coalesce(octet_length(policy_version),0)) AS row_charge FROM material), admitted AS (SELECT id,execution_id,attempt_number,status,phase,worker_id,worker_incarnation_id,process_id,runtime_mode,runtime_evidence_hash,dispatch_evidence_hash,policy_digest,policy_version,published_at,claimed_at,started_at,heartbeat_at,completed_at,claim_present,failure_phase_absent,failure_code_absent,status_bytes,phase_bytes,worker_id_bytes,process_id_bytes,runtime_mode_bytes,runtime_evidence_hash_bytes,dispatch_evidence_hash_bytes,policy_digest_bytes,policy_version_bytes,row_charge,((coalesce(status_bytes,0)<=65536 AND coalesce(phase_bytes,0)<=65536 AND coalesce(worker_id_bytes,0)<=65536 AND coalesce(process_id_bytes,0)<=65536 AND coalesce(runtime_mode_bytes,0)<=65536 AND coalesce(runtime_evidence_hash_bytes,0)<=65536 AND coalesce(dispatch_evidence_hash_bytes,0)<=65536 AND coalesce(policy_digest_bytes,0)<=65536 AND coalesce(policy_version_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,execution_id,attempt_number,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,CASE WHEN admitted THEN phase ELSE NULL END AS phase,phase_bytes,CASE WHEN admitted THEN worker_id ELSE NULL END AS worker_id,worker_id_bytes,worker_incarnation_id,CASE WHEN admitted THEN process_id ELSE NULL END AS process_id,process_id_bytes,CASE WHEN admitted THEN runtime_mode ELSE NULL END AS runtime_mode,runtime_mode_bytes,CASE WHEN admitted THEN runtime_evidence_hash ELSE NULL END AS runtime_evidence_hash,runtime_evidence_hash_bytes,CASE WHEN admitted THEN dispatch_evidence_hash ELSE NULL END AS dispatch_evidence_hash,dispatch_evidence_hash_bytes,CASE WHEN admitted THEN policy_digest ELSE NULL END AS policy_digest,policy_digest_bytes,CASE WHEN admitted THEN policy_version ELSE NULL END AS policy_version,policy_version_bytes,published_at,claimed_at,started_at,heartbeat_at,completed_at,claim_present,failure_phase_absent,failure_code_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[WorkflowAttemptRow],
            WorkflowAttemptRow,
            1,
        ),
        "agent_delivery": _Group(
            "agent_delivery",
            "/* agent-reference:agent_delivery:metadata */ SELECT id AS id,octet_length((queue_name)::text) AS queue_name_bytes,octet_length((message_id)::text) AS message_id_bytes,octet_length((encrypted_envelope)::text) AS encrypted_envelope_bytes,octet_length((status)::text) AS status_bytes FROM work_deliveries WHERE queue_name='agent-runs' AND message_id=$1::text ORDER BY id LIMIT 2",
            "/* agent-reference:agent_delivery:material */ WITH material AS (SELECT id AS id,queue_name AS queue_name,message_id AS message_id,encrypted_envelope AS encrypted_envelope,status AS status,available_at AS available_at,created_at AS created_at,started_at AS started_at,settled_at AS settled_at,claim_count AS claim_count,lease_owner IS NULL AS lease_owner_absent,lease_token IS NULL AS lease_token_absent,lease_expires_at IS NULL AS lease_expiry_absent FROM work_deliveries WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,queue_name,message_id,encrypted_envelope,status,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,octet_length(queue_name) AS queue_name_bytes,octet_length(message_id) AS message_id_bytes,octet_length(encrypted_envelope) AS encrypted_envelope_bytes,octet_length(status) AS status_bytes,(2176+coalesce(octet_length(queue_name),0)+coalesce(octet_length(message_id),0)+coalesce(octet_length(encrypted_envelope),0)+coalesce(octet_length(status),0)) AS row_charge FROM material), admitted AS (SELECT id,queue_name,message_id,encrypted_envelope,status,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,queue_name_bytes,message_id_bytes,encrypted_envelope_bytes,status_bytes,row_charge,((coalesce(queue_name_bytes,0)<=65536 AND coalesce(message_id_bytes,0)<=65536 AND coalesce(encrypted_envelope_bytes,0)<=131072 AND coalesce(status_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN queue_name ELSE NULL END AS queue_name,queue_name_bytes,CASE WHEN admitted THEN message_id ELSE NULL END AS message_id,message_id_bytes,CASE WHEN admitted THEN encrypted_envelope ELSE NULL END AS encrypted_envelope,encrypted_envelope_bytes,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[DeliveryRow],
            DeliveryRow,
            1,
        ),
        "workflow_delivery": _Group(
            "workflow_delivery",
            "/* agent-reference:workflow_delivery:metadata */ SELECT id AS id,octet_length((queue_name)::text) AS queue_name_bytes,octet_length((message_id)::text) AS message_id_bytes,octet_length((encrypted_envelope)::text) AS encrypted_envelope_bytes,octet_length((status)::text) AS status_bytes FROM work_deliveries WHERE queue_name='workflow-executions' AND message_id=$1::text ORDER BY id LIMIT 2",
            "/* agent-reference:workflow_delivery:material */ WITH material AS (SELECT id AS id,queue_name AS queue_name,message_id AS message_id,encrypted_envelope AS encrypted_envelope,status AS status,available_at AS available_at,created_at AS created_at,started_at AS started_at,settled_at AS settled_at,claim_count AS claim_count,lease_owner IS NULL AS lease_owner_absent,lease_token IS NULL AS lease_token_absent,lease_expires_at IS NULL AS lease_expiry_absent FROM work_deliveries WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,queue_name,message_id,encrypted_envelope,status,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,octet_length(queue_name) AS queue_name_bytes,octet_length(message_id) AS message_id_bytes,octet_length(encrypted_envelope) AS encrypted_envelope_bytes,octet_length(status) AS status_bytes,(2176+coalesce(octet_length(queue_name),0)+coalesce(octet_length(message_id),0)+coalesce(octet_length(encrypted_envelope),0)+coalesce(octet_length(status),0)) AS row_charge FROM material), admitted AS (SELECT id,queue_name,message_id,encrypted_envelope,status,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,queue_name_bytes,message_id_bytes,encrypted_envelope_bytes,status_bytes,row_charge,((coalesce(queue_name_bytes,0)<=65536 AND coalesce(message_id_bytes,0)<=65536 AND coalesce(encrypted_envelope_bytes,0)<=131072 AND coalesce(status_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN queue_name ELSE NULL END AS queue_name,queue_name_bytes,CASE WHEN admitted THEN message_id ELSE NULL END AS message_id,message_id_bytes,CASE WHEN admitted THEN encrypted_envelope ELSE NULL END AS encrypted_envelope,encrypted_envelope_bytes,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[DeliveryRow],
            DeliveryRow,
            1,
        ),
        "summary_delivery": _Group(
            "summary_delivery",
            "/* agent-reference:summary_delivery:metadata */ SELECT id AS id,octet_length((queue_name)::text) AS queue_name_bytes,octet_length((message_id)::text) AS message_id_bytes,octet_length((encrypted_envelope)::text) AS encrypted_envelope_bytes,octet_length((status)::text) AS status_bytes FROM work_deliveries WHERE queue_name='agent-summarization' AND message_id=$1::text ORDER BY id LIMIT 2",
            "/* agent-reference:summary_delivery:material */ WITH material AS (SELECT id AS id,queue_name AS queue_name,message_id AS message_id,encrypted_envelope AS encrypted_envelope,status AS status,available_at AS available_at,created_at AS created_at,started_at AS started_at,settled_at AS settled_at,claim_count AS claim_count,lease_owner IS NULL AS lease_owner_absent,lease_token IS NULL AS lease_token_absent,lease_expires_at IS NULL AS lease_expiry_absent FROM work_deliveries WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,queue_name,message_id,encrypted_envelope,status,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,octet_length(queue_name) AS queue_name_bytes,octet_length(message_id) AS message_id_bytes,octet_length(encrypted_envelope) AS encrypted_envelope_bytes,octet_length(status) AS status_bytes,(2176+coalesce(octet_length(queue_name),0)+coalesce(octet_length(message_id),0)+coalesce(octet_length(encrypted_envelope),0)+coalesce(octet_length(status),0)) AS row_charge FROM material), admitted AS (SELECT id,queue_name,message_id,encrypted_envelope,status,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,queue_name_bytes,message_id_bytes,encrypted_envelope_bytes,status_bytes,row_charge,((coalesce(queue_name_bytes,0)<=65536 AND coalesce(message_id_bytes,0)<=65536 AND coalesce(encrypted_envelope_bytes,0)<=131072 AND coalesce(status_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN queue_name ELSE NULL END AS queue_name,queue_name_bytes,CASE WHEN admitted THEN message_id ELSE NULL END AS message_id,message_id_bytes,CASE WHEN admitted THEN encrypted_envelope ELSE NULL END AS encrypted_envelope,encrypted_envelope_bytes,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,available_at,created_at,started_at,settled_at,claim_count,lease_owner_absent,lease_token_absent,lease_expiry_absent,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[DeliveryRow],
            DeliveryRow,
            1,
        ),
        "usage_run": _Group(
            "usage_run",
            "/* agent-reference:usage_run:metadata */ SELECT id AS id,execution_id AS execution_id,octet_length((provider)::text) AS provider_bytes,octet_length((model)::text) AS model_bytes FROM ai_usage WHERE agent_run_id=$1::uuid ORDER BY id LIMIT 4",
            "/* agent-reference:usage_run:material */ WITH material AS (SELECT id AS id,execution_id AS execution_id,conversation_id AS conversation_id,agent_run_id AS agent_run_id,message_id AS message_id,provider AS provider,model AS model,input_tokens AS input_tokens,output_tokens AS output_tokens,cache_read_tokens AS cache_read_tokens,cache_write_tokens AS cache_write_tokens,provider_cost AS provider_cost,cost AS cost,duration_ms AS duration_ms,timestamp AS timestamp,sequence AS sequence,organization_id AS organization_id,user_id AS user_id FROM ai_usage WHERE id=$1::int LIMIT 2), sized AS (SELECT id,execution_id,conversation_id,agent_run_id,message_id,provider,model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,provider_cost,cost,duration_ms,timestamp,sequence,organization_id,user_id,octet_length(provider) AS provider_bytes,octet_length(model) AS model_bytes,(2816+coalesce(octet_length(provider),0)+coalesce(octet_length(model),0)) AS row_charge FROM material), admitted AS (SELECT id,execution_id,conversation_id,agent_run_id,message_id,provider,model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,provider_cost,cost,duration_ms,timestamp,sequence,organization_id,user_id,provider_bytes,model_bytes,row_charge,((coalesce(provider_bytes,0)<=65536 AND coalesce(model_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,execution_id,conversation_id,agent_run_id,message_id,CASE WHEN admitted THEN provider ELSE NULL END AS provider,provider_bytes,CASE WHEN admitted THEN model ELSE NULL END AS model,model_bytes,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,provider_cost,cost,duration_ms,timestamp,sequence,organization_id,user_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (_Cell("execution_id", "uuid", True),),
            _COLUMNS[UsageRow],
            UsageRow,
            3,
        ),
        "usage_known": _Group(
            "usage_known",
            "/* agent-reference:usage_known:metadata */ SELECT id AS id,execution_id AS execution_id,octet_length((provider)::text) AS provider_bytes,octet_length((model)::text) AS model_bytes FROM ai_usage WHERE agent_run_id=$1::uuid OR execution_id=$2::uuid ORDER BY id LIMIT 4",
            "/* agent-reference:usage_known:material */ WITH material AS (SELECT id AS id,execution_id AS execution_id,conversation_id AS conversation_id,agent_run_id AS agent_run_id,message_id AS message_id,provider AS provider,model AS model,input_tokens AS input_tokens,output_tokens AS output_tokens,cache_read_tokens AS cache_read_tokens,cache_write_tokens AS cache_write_tokens,provider_cost AS provider_cost,cost AS cost,duration_ms AS duration_ms,timestamp AS timestamp,sequence AS sequence,organization_id AS organization_id,user_id AS user_id FROM ai_usage WHERE id=$1::int LIMIT 2), sized AS (SELECT id,execution_id,conversation_id,agent_run_id,message_id,provider,model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,provider_cost,cost,duration_ms,timestamp,sequence,organization_id,user_id,octet_length(provider) AS provider_bytes,octet_length(model) AS model_bytes,(2816+coalesce(octet_length(provider),0)+coalesce(octet_length(model),0)) AS row_charge FROM material), admitted AS (SELECT id,execution_id,conversation_id,agent_run_id,message_id,provider,model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,provider_cost,cost,duration_ms,timestamp,sequence,organization_id,user_id,provider_bytes,model_bytes,row_charge,((coalesce(provider_bytes,0)<=65536 AND coalesce(model_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,execution_id,conversation_id,agent_run_id,message_id,CASE WHEN admitted THEN provider ELSE NULL END AS provider,provider_bytes,CASE WHEN admitted THEN model ELSE NULL END AS model,model_bytes,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,provider_cost,cost,duration_ms,timestamp,sequence,organization_id,user_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (_Cell("execution_id", "uuid", True),),
            _COLUMNS[UsageRow],
            UsageRow,
            3,
        ),
        "workflows_selected": _Group(
            "workflows_selected",
            "/* agent-reference:workflows_selected:metadata */ SELECT id AS id,octet_length((path)::text) AS path_bytes,octet_length((function_name)::text) AS function_name_bytes,octet_length((name)::text) AS name_bytes,octet_length((description)::text) AS description_bytes,octet_length((category)::text) AS category_bytes,octet_length((tags::text)::text) AS tags_bytes,octet_length((type)::text) AS type_bytes,octet_length((tool_description)::text) AS tool_description_bytes,octet_length((display_name)::text) AS display_name_bytes,octet_length((parameters_schema::text)::text) AS parameters_schema_bytes,octet_length((execution_mode)::text) AS execution_mode_bytes,octet_length((retry_policy::text)::text) AS retry_policy_bytes,octet_length((access_level)::text) AS access_level_bytes,octet_length((allowed_methods::text)::text) AS allowed_methods_bytes FROM workflows WHERE id=ANY($1::uuid[]) ORDER BY id LIMIT 4",
            "/* agent-reference:workflows_selected:material */ WITH material AS (SELECT id AS id,solution_id AS solution_id,organization_id AS organization_id,path AS path,function_name AS function_name,name AS name,description AS description,category AS category,tags::text AS tags,type AS type,tool_description AS tool_description,display_name AS display_name,parameters_schema::text AS parameters_schema,execution_mode AS execution_mode,timeout_seconds AS timeout_seconds,cache_ttl_seconds AS cache_ttl_seconds,time_saved AS time_saved,value AS value,retry_policy::text AS retry_policy,access_level AS access_level,endpoint_enabled AS endpoint_enabled,public_endpoint AS public_endpoint,allowed_methods::text AS allowed_methods,disable_global_key AS disable_global_key,is_active AS is_active,is_orphaned AS is_orphaned FROM workflows WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,solution_id,organization_id,path,function_name,name,description,category,tags,type,tool_description,display_name,parameters_schema,execution_mode,timeout_seconds,cache_ttl_seconds,time_saved,value,retry_policy,access_level,endpoint_enabled,public_endpoint,allowed_methods,disable_global_key,is_active,is_orphaned,octet_length(path) AS path_bytes,octet_length(function_name) AS function_name_bytes,octet_length(name) AS name_bytes,octet_length(description) AS description_bytes,octet_length(category) AS category_bytes,octet_length(tags) AS tags_bytes,octet_length(type) AS type_bytes,octet_length(tool_description) AS tool_description_bytes,octet_length(display_name) AS display_name_bytes,octet_length(parameters_schema) AS parameters_schema_bytes,octet_length(execution_mode) AS execution_mode_bytes,octet_length(retry_policy) AS retry_policy_bytes,octet_length(access_level) AS access_level_bytes,octet_length(allowed_methods) AS allowed_methods_bytes,(3840+coalesce(octet_length(path),0)+coalesce(octet_length(function_name),0)+coalesce(octet_length(name),0)+coalesce(octet_length(description),0)+coalesce(octet_length(category),0)+coalesce(octet_length(tags),0)+coalesce(octet_length(type),0)+coalesce(octet_length(tool_description),0)+coalesce(octet_length(display_name),0)+coalesce(octet_length(parameters_schema),0)+coalesce(octet_length(execution_mode),0)+coalesce(octet_length(retry_policy),0)+coalesce(octet_length(access_level),0)+coalesce(octet_length(allowed_methods),0)) AS row_charge FROM material), admitted AS (SELECT id,solution_id,organization_id,path,function_name,name,description,category,tags,type,tool_description,display_name,parameters_schema,execution_mode,timeout_seconds,cache_ttl_seconds,time_saved,value,retry_policy,access_level,endpoint_enabled,public_endpoint,allowed_methods,disable_global_key,is_active,is_orphaned,path_bytes,function_name_bytes,name_bytes,description_bytes,category_bytes,tags_bytes,type_bytes,tool_description_bytes,display_name_bytes,parameters_schema_bytes,execution_mode_bytes,retry_policy_bytes,access_level_bytes,allowed_methods_bytes,row_charge,((coalesce(path_bytes,0)<=65536 AND coalesce(function_name_bytes,0)<=65536 AND coalesce(name_bytes,0)<=65536 AND coalesce(description_bytes,0)<=65536 AND coalesce(category_bytes,0)<=65536 AND coalesce(tags_bytes,0)<=65536 AND coalesce(type_bytes,0)<=65536 AND coalesce(tool_description_bytes,0)<=65536 AND coalesce(display_name_bytes,0)<=65536 AND coalesce(parameters_schema_bytes,0)<=65536 AND coalesce(execution_mode_bytes,0)<=65536 AND coalesce(retry_policy_bytes,0)<=65536 AND coalesce(access_level_bytes,0)<=65536 AND coalesce(allowed_methods_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,solution_id,organization_id,CASE WHEN admitted THEN path ELSE NULL END AS path,path_bytes,CASE WHEN admitted THEN function_name ELSE NULL END AS function_name,function_name_bytes,CASE WHEN admitted THEN name ELSE NULL END AS name,name_bytes,CASE WHEN admitted THEN description ELSE NULL END AS description,description_bytes,CASE WHEN admitted THEN category ELSE NULL END AS category,category_bytes,CASE WHEN admitted THEN tags ELSE NULL END AS tags,tags_bytes,CASE WHEN admitted THEN type ELSE NULL END AS type,type_bytes,CASE WHEN admitted THEN tool_description ELSE NULL END AS tool_description,tool_description_bytes,CASE WHEN admitted THEN display_name ELSE NULL END AS display_name,display_name_bytes,CASE WHEN admitted THEN parameters_schema ELSE NULL END AS parameters_schema,parameters_schema_bytes,CASE WHEN admitted THEN execution_mode ELSE NULL END AS execution_mode,execution_mode_bytes,timeout_seconds,cache_ttl_seconds,time_saved,value,CASE WHEN admitted THEN retry_policy ELSE NULL END AS retry_policy,retry_policy_bytes,CASE WHEN admitted THEN access_level ELSE NULL END AS access_level,access_level_bytes,endpoint_enabled,public_endpoint,CASE WHEN admitted THEN allowed_methods ELSE NULL END AS allowed_methods,allowed_methods_bytes,disable_global_key,is_active,is_orphaned,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[WorkflowRow],
            WorkflowRow,
            3,
        ),
        "workflows_solution": _Group(
            "workflows_solution",
            "/* agent-reference:workflows_solution:metadata */ SELECT id AS id,octet_length((path)::text) AS path_bytes,octet_length((function_name)::text) AS function_name_bytes,octet_length((name)::text) AS name_bytes,octet_length((description)::text) AS description_bytes,octet_length((category)::text) AS category_bytes,octet_length((tags::text)::text) AS tags_bytes,octet_length((type)::text) AS type_bytes,octet_length((tool_description)::text) AS tool_description_bytes,octet_length((display_name)::text) AS display_name_bytes,octet_length((parameters_schema::text)::text) AS parameters_schema_bytes,octet_length((execution_mode)::text) AS execution_mode_bytes,octet_length((retry_policy::text)::text) AS retry_policy_bytes,octet_length((access_level)::text) AS access_level_bytes,octet_length((allowed_methods::text)::text) AS allowed_methods_bytes FROM workflows WHERE solution_id=$1::uuid ORDER BY id LIMIT 4",
            "/* agent-reference:workflows_solution:material */ WITH material AS (SELECT id AS id,solution_id AS solution_id,organization_id AS organization_id,path AS path,function_name AS function_name,name AS name,description AS description,category AS category,tags::text AS tags,type AS type,tool_description AS tool_description,display_name AS display_name,parameters_schema::text AS parameters_schema,execution_mode AS execution_mode,timeout_seconds AS timeout_seconds,cache_ttl_seconds AS cache_ttl_seconds,time_saved AS time_saved,value AS value,retry_policy::text AS retry_policy,access_level AS access_level,endpoint_enabled AS endpoint_enabled,public_endpoint AS public_endpoint,allowed_methods::text AS allowed_methods,disable_global_key AS disable_global_key,is_active AS is_active,is_orphaned AS is_orphaned FROM workflows WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,solution_id,organization_id,path,function_name,name,description,category,tags,type,tool_description,display_name,parameters_schema,execution_mode,timeout_seconds,cache_ttl_seconds,time_saved,value,retry_policy,access_level,endpoint_enabled,public_endpoint,allowed_methods,disable_global_key,is_active,is_orphaned,octet_length(path) AS path_bytes,octet_length(function_name) AS function_name_bytes,octet_length(name) AS name_bytes,octet_length(description) AS description_bytes,octet_length(category) AS category_bytes,octet_length(tags) AS tags_bytes,octet_length(type) AS type_bytes,octet_length(tool_description) AS tool_description_bytes,octet_length(display_name) AS display_name_bytes,octet_length(parameters_schema) AS parameters_schema_bytes,octet_length(execution_mode) AS execution_mode_bytes,octet_length(retry_policy) AS retry_policy_bytes,octet_length(access_level) AS access_level_bytes,octet_length(allowed_methods) AS allowed_methods_bytes,(3840+coalesce(octet_length(path),0)+coalesce(octet_length(function_name),0)+coalesce(octet_length(name),0)+coalesce(octet_length(description),0)+coalesce(octet_length(category),0)+coalesce(octet_length(tags),0)+coalesce(octet_length(type),0)+coalesce(octet_length(tool_description),0)+coalesce(octet_length(display_name),0)+coalesce(octet_length(parameters_schema),0)+coalesce(octet_length(execution_mode),0)+coalesce(octet_length(retry_policy),0)+coalesce(octet_length(access_level),0)+coalesce(octet_length(allowed_methods),0)) AS row_charge FROM material), admitted AS (SELECT id,solution_id,organization_id,path,function_name,name,description,category,tags,type,tool_description,display_name,parameters_schema,execution_mode,timeout_seconds,cache_ttl_seconds,time_saved,value,retry_policy,access_level,endpoint_enabled,public_endpoint,allowed_methods,disable_global_key,is_active,is_orphaned,path_bytes,function_name_bytes,name_bytes,description_bytes,category_bytes,tags_bytes,type_bytes,tool_description_bytes,display_name_bytes,parameters_schema_bytes,execution_mode_bytes,retry_policy_bytes,access_level_bytes,allowed_methods_bytes,row_charge,((coalesce(path_bytes,0)<=65536 AND coalesce(function_name_bytes,0)<=65536 AND coalesce(name_bytes,0)<=65536 AND coalesce(description_bytes,0)<=65536 AND coalesce(category_bytes,0)<=65536 AND coalesce(tags_bytes,0)<=65536 AND coalesce(type_bytes,0)<=65536 AND coalesce(tool_description_bytes,0)<=65536 AND coalesce(display_name_bytes,0)<=65536 AND coalesce(parameters_schema_bytes,0)<=65536 AND coalesce(execution_mode_bytes,0)<=65536 AND coalesce(retry_policy_bytes,0)<=65536 AND coalesce(access_level_bytes,0)<=65536 AND coalesce(allowed_methods_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,solution_id,organization_id,CASE WHEN admitted THEN path ELSE NULL END AS path,path_bytes,CASE WHEN admitted THEN function_name ELSE NULL END AS function_name,function_name_bytes,CASE WHEN admitted THEN name ELSE NULL END AS name,name_bytes,CASE WHEN admitted THEN description ELSE NULL END AS description,description_bytes,CASE WHEN admitted THEN category ELSE NULL END AS category,category_bytes,CASE WHEN admitted THEN tags ELSE NULL END AS tags,tags_bytes,CASE WHEN admitted THEN type ELSE NULL END AS type,type_bytes,CASE WHEN admitted THEN tool_description ELSE NULL END AS tool_description,tool_description_bytes,CASE WHEN admitted THEN display_name ELSE NULL END AS display_name,display_name_bytes,CASE WHEN admitted THEN parameters_schema ELSE NULL END AS parameters_schema,parameters_schema_bytes,CASE WHEN admitted THEN execution_mode ELSE NULL END AS execution_mode,execution_mode_bytes,timeout_seconds,cache_ttl_seconds,time_saved,value,CASE WHEN admitted THEN retry_policy ELSE NULL END AS retry_policy,retry_policy_bytes,CASE WHEN admitted THEN access_level ELSE NULL END AS access_level,access_level_bytes,endpoint_enabled,public_endpoint,CASE WHEN admitted THEN allowed_methods ELSE NULL END AS allowed_methods,allowed_methods_bytes,disable_global_key,is_active,is_orphaned,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[WorkflowRow],
            WorkflowRow,
            3,
        ),
        "solution": _Group(
            "solution",
            "/* agent-reference:solution:metadata */ SELECT id AS id,octet_length((status)::text) AS status_bytes,octet_length((execution_runtime_mode)::text) AS execution_runtime_mode_bytes FROM solutions WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:solution:material */ WITH material AS (SELECT id AS id,organization_id AS organization_id,status AS status,active_deployment_id AS active_deployment_id,execution_runtime_mode AS execution_runtime_mode,allow_outbound_access AS allow_outbound_access FROM solutions WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,organization_id,status,active_deployment_id,execution_runtime_mode,allow_outbound_access,octet_length(status) AS status_bytes,octet_length(execution_runtime_mode) AS execution_runtime_mode_bytes,(1280+coalesce(octet_length(status),0)+coalesce(octet_length(execution_runtime_mode),0)) AS row_charge FROM material), admitted AS (SELECT id,organization_id,status,active_deployment_id,execution_runtime_mode,allow_outbound_access,status_bytes,execution_runtime_mode_bytes,row_charge,((coalesce(status_bytes,0)<=65536 AND coalesce(execution_runtime_mode_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,organization_id,CASE WHEN admitted THEN status ELSE NULL END AS status,status_bytes,active_deployment_id,CASE WHEN admitted THEN execution_runtime_mode ELSE NULL END AS execution_runtime_mode,execution_runtime_mode_bytes,allow_outbound_access,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[SolutionRow],
            SolutionRow,
            1,
        ),
        "deployments": _Group(
            "deployments",
            "/* agent-reference:deployments:metadata */ SELECT id AS id,octet_length((state)::text) AS state_bytes,octet_length((bundle_hash)::text) AS bundle_hash_bytes,octet_length((compiled_manifest::text)::text) AS compiled_manifest_bytes,octet_length((compiled_manifest_hash)::text) AS compiled_manifest_hash_bytes,octet_length((resolution_map::text)::text) AS resolution_map_bytes,octet_length((resolution_map_hash)::text) AS resolution_map_hash_bytes,octet_length((source_artifact_key)::text) AS source_artifact_key_bytes,octet_length((runtime_storage_prefix)::text) AS runtime_storage_prefix_bytes,octet_length((git_repository)::text) AS git_repository_bytes,octet_length((git_ref)::text) AS git_ref_bytes,octet_length((git_commit_sha)::text) AS git_commit_sha_bytes FROM solution_deployments WHERE id=ANY($1::uuid[]) ORDER BY id LIMIT 3",
            "/* agent-reference:deployments:material */ WITH material AS (SELECT id AS id,organization_id AS organization_id,solution_id AS solution_id,parent_deployment_id AS parent_deployment_id,base_deployment_id AS base_deployment_id,state AS state,bundle_hash AS bundle_hash,compiled_manifest::text AS compiled_manifest,compiled_manifest_hash AS compiled_manifest_hash,resolution_map::text AS resolution_map,resolution_map_hash AS resolution_map_hash,source_artifact_key AS source_artifact_key,runtime_storage_prefix AS runtime_storage_prefix,git_repository AS git_repository,git_ref AS git_ref,git_commit_sha AS git_commit_sha,activated_at AS activated_at,superseded_at AS superseded_at FROM solution_deployments WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,organization_id,solution_id,parent_deployment_id,base_deployment_id,state,bundle_hash,compiled_manifest,compiled_manifest_hash,resolution_map,resolution_map_hash,source_artifact_key,runtime_storage_prefix,git_repository,git_ref,git_commit_sha,activated_at,superseded_at,octet_length(state) AS state_bytes,octet_length(bundle_hash) AS bundle_hash_bytes,octet_length(compiled_manifest) AS compiled_manifest_bytes,octet_length(compiled_manifest_hash) AS compiled_manifest_hash_bytes,octet_length(resolution_map) AS resolution_map_bytes,octet_length(resolution_map_hash) AS resolution_map_hash_bytes,octet_length(source_artifact_key) AS source_artifact_key_bytes,octet_length(runtime_storage_prefix) AS runtime_storage_prefix_bytes,octet_length(git_repository) AS git_repository_bytes,octet_length(git_ref) AS git_ref_bytes,octet_length(git_commit_sha) AS git_commit_sha_bytes,(2816+coalesce(octet_length(state),0)+coalesce(octet_length(bundle_hash),0)+coalesce(octet_length(compiled_manifest),0)+coalesce(octet_length(compiled_manifest_hash),0)+coalesce(octet_length(resolution_map),0)+coalesce(octet_length(resolution_map_hash),0)+coalesce(octet_length(source_artifact_key),0)+coalesce(octet_length(runtime_storage_prefix),0)+coalesce(octet_length(git_repository),0)+coalesce(octet_length(git_ref),0)+coalesce(octet_length(git_commit_sha),0)) AS row_charge FROM material), admitted AS (SELECT id,organization_id,solution_id,parent_deployment_id,base_deployment_id,state,bundle_hash,compiled_manifest,compiled_manifest_hash,resolution_map,resolution_map_hash,source_artifact_key,runtime_storage_prefix,git_repository,git_ref,git_commit_sha,activated_at,superseded_at,state_bytes,bundle_hash_bytes,compiled_manifest_bytes,compiled_manifest_hash_bytes,resolution_map_bytes,resolution_map_hash_bytes,source_artifact_key_bytes,runtime_storage_prefix_bytes,git_repository_bytes,git_ref_bytes,git_commit_sha_bytes,row_charge,((coalesce(state_bytes,0)<=65536 AND coalesce(bundle_hash_bytes,0)<=65536 AND coalesce(compiled_manifest_bytes,0)<=65536 AND coalesce(compiled_manifest_hash_bytes,0)<=65536 AND coalesce(resolution_map_bytes,0)<=65536 AND coalesce(resolution_map_hash_bytes,0)<=65536 AND coalesce(source_artifact_key_bytes,0)<=65536 AND coalesce(runtime_storage_prefix_bytes,0)<=65536 AND coalesce(git_repository_bytes,0)<=65536 AND coalesce(git_ref_bytes,0)<=65536 AND coalesce(git_commit_sha_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,organization_id,solution_id,parent_deployment_id,base_deployment_id,CASE WHEN admitted THEN state ELSE NULL END AS state,state_bytes,CASE WHEN admitted THEN bundle_hash ELSE NULL END AS bundle_hash,bundle_hash_bytes,CASE WHEN admitted THEN compiled_manifest ELSE NULL END AS compiled_manifest,compiled_manifest_bytes,CASE WHEN admitted THEN compiled_manifest_hash ELSE NULL END AS compiled_manifest_hash,compiled_manifest_hash_bytes,CASE WHEN admitted THEN resolution_map ELSE NULL END AS resolution_map,resolution_map_bytes,CASE WHEN admitted THEN resolution_map_hash ELSE NULL END AS resolution_map_hash,resolution_map_hash_bytes,CASE WHEN admitted THEN source_artifact_key ELSE NULL END AS source_artifact_key,source_artifact_key_bytes,CASE WHEN admitted THEN runtime_storage_prefix ELSE NULL END AS runtime_storage_prefix,runtime_storage_prefix_bytes,CASE WHEN admitted THEN git_repository ELSE NULL END AS git_repository,git_repository_bytes,CASE WHEN admitted THEN git_ref ELSE NULL END AS git_ref,git_ref_bytes,CASE WHEN admitted THEN git_commit_sha ELSE NULL END AS git_commit_sha,git_commit_sha_bytes,activated_at,superseded_at,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[DeploymentRow],
            DeploymentRow,
            2,
        ),
        "edges": _Group(
            "edges",
            "/* agent-reference:edges:metadata */ SELECT deployment_id AS deployment_id,dependency_solution_id AS dependency_solution_id FROM solution_deployment_dependencies WHERE deployment_id=ANY($1::uuid[]) ORDER BY deployment_id,dependency_solution_id LIMIT 1",
            "/* agent-reference:edges:material */ WITH material AS (SELECT deployment_id AS deployment_id,dependency_solution_id AS dependency_solution_id,dependency_deployment_id AS dependency_deployment_id FROM solution_deployment_dependencies WHERE deployment_id=$1::uuid AND dependency_solution_id=$2::uuid LIMIT 2), sized AS (SELECT deployment_id,dependency_solution_id,dependency_deployment_id,(896) AS row_charge FROM material), admitted AS (SELECT deployment_id,dependency_solution_id,dependency_deployment_id,row_charge,(row_charge<=$3::bigint) AS admitted FROM sized) SELECT deployment_id,dependency_solution_id,dependency_deployment_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("deployment_id", "dependency_solution_id"),
            (),
            _COLUMNS[DeploymentEdgeRow],
            DeploymentEdgeRow,
            0,
        ),
        "profile": _Group(
            "profile",
            "/* agent-reference:profile:metadata */ SELECT id AS id,octet_length((name)::text) AS name_bytes,octet_length((model)::text) AS model_bytes,octet_length((openai_transport)::text) AS openai_transport_bytes,octet_length((capabilities::text)::text) AS capabilities_bytes FROM ai_model_profiles WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:profile:material */ WITH material AS (SELECT id AS id,name AS name,connection_id AS connection_id,model AS model,openai_transport AS openai_transport,default_max_tokens AS default_max_tokens,capabilities::text AS capabilities,enabled_for_chat AS enabled_for_chat,failover_profile_id AS failover_profile_id FROM ai_model_profiles WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,name,connection_id,model,openai_transport,default_max_tokens,capabilities,enabled_for_chat,failover_profile_id,octet_length(name) AS name_bytes,octet_length(model) AS model_bytes,octet_length(openai_transport) AS openai_transport_bytes,octet_length(capabilities) AS capabilities_bytes,(1664+coalesce(octet_length(name),0)+coalesce(octet_length(model),0)+coalesce(octet_length(openai_transport),0)+coalesce(octet_length(capabilities),0)) AS row_charge FROM material), admitted AS (SELECT id,name,connection_id,model,openai_transport,default_max_tokens,capabilities,enabled_for_chat,failover_profile_id,name_bytes,model_bytes,openai_transport_bytes,capabilities_bytes,row_charge,((coalesce(name_bytes,0)<=65536 AND coalesce(model_bytes,0)<=65536 AND coalesce(openai_transport_bytes,0)<=65536 AND coalesce(capabilities_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN name ELSE NULL END AS name,name_bytes,connection_id,CASE WHEN admitted THEN model ELSE NULL END AS model,model_bytes,CASE WHEN admitted THEN openai_transport ELSE NULL END AS openai_transport,openai_transport_bytes,default_max_tokens,CASE WHEN admitted THEN capabilities ELSE NULL END AS capabilities,capabilities_bytes,enabled_for_chat,failover_profile_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[ProfileRow],
            ProfileRow,
            1,
        ),
        "connection": _Group(
            "connection",
            "/* agent-reference:connection:metadata */ SELECT id AS id,octet_length((name)::text) AS name_bytes,octet_length((provider)::text) AS provider_bytes,octet_length((endpoint)::text) AS endpoint_bytes FROM ai_provider_connections WHERE id=$1::uuid ORDER BY id LIMIT 2",
            "/* agent-reference:connection:material */ WITH material AS (SELECT id AS id,name AS name,provider AS provider,endpoint AS endpoint FROM ai_provider_connections WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,name,provider,endpoint,octet_length(name) AS name_bytes,octet_length(provider) AS provider_bytes,octet_length(endpoint) AS endpoint_bytes,(1024+coalesce(octet_length(name),0)+coalesce(octet_length(provider),0)+coalesce(octet_length(endpoint),0)) AS row_charge FROM material), admitted AS (SELECT id,name,provider,endpoint,name_bytes,provider_bytes,endpoint_bytes,row_charge,((coalesce(name_bytes,0)<=65536 AND coalesce(provider_bytes,0)<=65536 AND coalesce(endpoint_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,CASE WHEN admitted THEN name ELSE NULL END AS name,name_bytes,CASE WHEN admitted THEN provider ELSE NULL END AS provider,provider_bytes,CASE WHEN admitted THEN endpoint ELSE NULL END AS endpoint,endpoint_bytes,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (),
            _COLUMNS[ConnectionRow],
            ConnectionRow,
            1,
        ),
        "assignments": _Group(
            "assignments",
            "/* agent-reference:assignments:metadata */ SELECT assignment_key AS assignment_key,octet_length((assignment_key)::text) AS assignment_key_bytes FROM ai_model_assignments WHERE TRUE ORDER BY assignment_key LIMIT 7",
            "/* agent-reference:assignments:material */ WITH material AS (SELECT assignment_key AS assignment_key,profile_id AS profile_id FROM ai_model_assignments WHERE assignment_key=$1::text LIMIT 2), sized AS (SELECT assignment_key,profile_id,octet_length(assignment_key) AS assignment_key_bytes,(768+coalesce(octet_length(assignment_key),0)) AS row_charge FROM material), admitted AS (SELECT assignment_key,profile_id,assignment_key_bytes,row_charge,((coalesce(assignment_key_bytes,0)<=65536) AND row_charge<=$2::bigint) AS admitted FROM sized) SELECT CASE WHEN admitted THEN assignment_key ELSE NULL END AS assignment_key,assignment_key_bytes,profile_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("assignment_key",),
            (),
            _COLUMNS[AssignmentRow],
            AssignmentRow,
            6,
        ),
        "solution_executions": _Group(
            "solution_executions",
            "/* agent-reference:solution_executions:metadata */ SELECT e.id AS id,e.workflow_id AS workflow_id FROM executions e JOIN workflows w ON e.workflow_id=w.id WHERE w.solution_id=$1::uuid ORDER BY e.id LIMIT 2",
            "/* agent-reference:solution_executions:material */ WITH material AS (SELECT e.id AS id,e.workflow_id AS workflow_id FROM executions e JOIN workflows w ON e.workflow_id=w.id WHERE e.id=$1::uuid LIMIT 2), sized AS (SELECT id,workflow_id,(768) AS row_charge FROM material), admitted AS (SELECT id,workflow_id,row_charge,(row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,workflow_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (_Cell("workflow_id", "uuid", True),),
            _COLUMNS[ExecutionMetadata],
            ExecutionMetadata,
            1,
        ),
        "selected_executions": _Group(
            "selected_executions",
            "/* agent-reference:selected_executions:metadata */ SELECT id AS id,workflow_id AS workflow_id FROM executions WHERE workflow_id=ANY($1::uuid[]) ORDER BY id LIMIT 2",
            "/* agent-reference:selected_executions:material */ WITH material AS (SELECT id AS id,workflow_id AS workflow_id FROM executions WHERE id=$1::uuid LIMIT 2), sized AS (SELECT id,workflow_id,(768) AS row_charge FROM material), admitted AS (SELECT id,workflow_id,row_charge,(row_charge<=$2::bigint) AS admitted FROM sized) SELECT id,workflow_id,row_charge,NOT admitted AS oversize FROM admitted",
            ("id",),
            (_Cell("workflow_id", "uuid", True),),
            _COLUMNS[ExecutionMetadata],
            ExecutionMetadata,
            1,
        ),
    }
)


class _FrozenArray(tuple[JsonValue, ...]):
    __slots__ = ()

    def __repr__(self) -> str:
        return "<private-json-array>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _FrozenObject(Mapping[str, JsonValue]):
    _data: Mapping[str, JsonValue]

    def __getitem__(self, key: str) -> JsonValue:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return "<private-json-object>"

    __str__ = __repr__


class _Fault(Exception):
    """Static internal classification, containing no input or driver exception."""

    def __init__(self, code: ReaderCode) -> None:
        super().__init__(code.value)
        self.code = code


def _pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _Fault(ReaderCode.MATERIAL_INVALID)
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise _Fault(ReaderCode.MATERIAL_INVALID)


def _freeze(value: object, depth: int = 0) -> JsonValue:
    if depth > MAX_JSON_DEPTH:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if value is None or type(value) in (bool, int):
        return cast(JsonValue, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise _Fault(ReaderCode.MATERIAL_INVALID)
        return value
    if type(value) is str:
        _utf8_length(value)
        return value
    if type(value) in (list, dict) and depth >= MAX_JSON_DEPTH:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if type(value) is list:
        return _FrozenArray(_freeze(item, depth + 1) for item in value)
    if type(value) is dict:
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _Fault(ReaderCode.MATERIAL_INVALID)
            _utf8_length(key)
            result[key] = _freeze(item, depth + 1)
        return _FrozenObject(MappingProxyType(result))
    raise _Fault(ReaderCode.MATERIAL_INVALID)


def _utf8_length(value: str) -> int:
    invalid = False
    length = 0
    try:
        length = len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        invalid = True
    if invalid:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    return length


def _parse_json(raw: str, kind: str) -> JsonValue:
    invalid = False
    value: object = None
    try:
        value = json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
    except (ValueError, UnicodeError, RecursionError, _Fault):
        invalid = True
    if invalid:
        # Outside exception scope: no body/decoder exception cause or context.
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    frozen = _freeze(value)
    if kind == "json_object" and not isinstance(frozen, Mapping):
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if kind in ("json_array", "json_strings") and not isinstance(frozen, _FrozenArray):
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if kind == "json_strings" and any(
        type(item) is not str for item in cast(tuple, frozen)
    ):
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if kind == "json_container" and not (
        isinstance(frozen, Mapping) or isinstance(frozen, _FrozenArray)
    ):
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    return frozen


def _native(value: object, cell: _Cell) -> object:
    if value is None:
        if cell.nullable:
            return None
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    expected = {
        "uuid": UUID,
        "bool": bool,
        "int": int,
        "float": float,
        "decimal": Decimal,
        "datetime": datetime,
        "text": str,
    }[cell.kind]
    if type(value) is not expected:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if cell.kind == "int" and not -(2**31) <= cast(int, value) < 2**31:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if cell.kind == "float" and not math.isfinite(cast(float, value)):
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if cell.kind == "decimal" and not cast(Decimal, value).is_finite():
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if cell.kind == "datetime":
        dt = cast(datetime, value)
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise _Fault(ReaderCode.MATERIAL_INVALID)
    if cell.kind == "text" and _utf8_length(cast(str, value)) > min(cell.cap, 255):
        # Only metadata selector/key text reaches _native. Private text has its
        # independently accounted per-cell cap below, not this selector limit.
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    return value


def _record(raw: object, expected: set[str]) -> Mapping[str, object]:
    invalid = False
    record: dict[str, object] = {}
    try:
        record = dict(cast(Mapping[str, object], raw))
        invalid = set(record) != expected
    except (TypeError, ValueError):
        invalid = True
    if invalid:
        raise _Fault(ReaderCode.SCHEMA_MISMATCH)
    return record


def _metadata(group: _Group, raw_rows: object) -> tuple[tuple[object, ...], ...]:
    if type(raw_rows) is not list:
        raise _Fault(ReaderCode.SCHEMA_MISMATCH)
    if len(raw_rows) > group.maximum:
        raise _Fault(ReaderCode.CARDINALITY_EXCESS)
    columns = {cell.name: cell for cell in group.cells}
    names = (
        set(group.keys)
        | {c.name for c in group.selectors}
        | {c.name + "_bytes" for c in group.cells if c.variable}
    )
    identities: list[tuple[object, ...]] = []
    for raw in raw_rows:
        record = _record(raw, names)
        identity = tuple(_native(record[key], columns[key]) for key in group.keys)
        if identity in identities:
            raise _Fault(ReaderCode.MATERIAL_INVALID)
        for cell in group.selectors:
            _native(record[cell.name], cell)
        for cell in group.cells:
            if cell.variable:
                size = record[cell.name + "_bytes"]
                if size is None:
                    if not cell.nullable:
                        raise _Fault(ReaderCode.MATERIAL_INVALID)
                elif type(size) is not int or size < 0:
                    raise _Fault(ReaderCode.MATERIAL_INVALID)
                elif size > cell.cap:
                    raise _Fault(ReaderCode.MATERIAL_OVERSIZE)
        identities.append(identity)
    return tuple(identities)


def _material(
    group: _Group, raw_rows: object, identity: tuple[object, ...], remaining: int
) -> tuple[object | None, int]:
    if type(raw_rows) is not list:
        raise _Fault(ReaderCode.SCHEMA_MISMATCH)
    if len(raw_rows) > 1:
        raise _Fault(ReaderCode.CARDINALITY_EXCESS)
    if not raw_rows:
        return None, 0
    names = (
        {c.name for c in group.cells}
        | {c.name + "_bytes" for c in group.cells if c.variable}
        | {"row_charge", "oversize"}
    )
    record = _record(raw_rows[0], names)
    if type(record["oversize"]) is not bool or type(record["row_charge"]) is not int:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if record["oversize"]:
        if any(record[c.name] is not None for c in group.cells if c.variable):
            raise _Fault(ReaderCode.MATERIAL_INVALID)
        raise _Fault(ReaderCode.MATERIAL_OVERSIZE)
    values: dict[str, object] = {}
    charge = group.fixed_charge
    for cell in group.cells:
        value = record[cell.name]
        if not cell.variable:
            values[cell.name] = _native(value, cell)
            continue
        size = record[cell.name + "_bytes"]
        if value is None:
            if not cell.nullable or size is not None:
                raise _Fault(ReaderCode.MATERIAL_INVALID)
            values[cell.name] = None
            continue
        if type(value) is not str or type(size) is not int or size < 0:
            raise _Fault(ReaderCode.MATERIAL_INVALID)
        actual_size = _utf8_length(value)
        if actual_size != size:
            raise _Fault(ReaderCode.MATERIAL_INVALID)
        if size > cell.cap:
            raise _Fault(ReaderCode.MATERIAL_OVERSIZE)
        charge += size
        values[cell.name] = (
            _parse_json(value, cell.kind) if cell.kind.startswith("json_") else value
        )
    if record["row_charge"] != charge:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if charge > remaining:
        raise _Fault(ReaderCode.MATERIAL_OVERSIZE)
    if tuple(values[k] for k in group.keys) != identity:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    return group.row_type(**values), charge


def _validate_ids(ids: SetupIds | RunIds) -> None:
    if type(ids) is RunIds:
        if type(ids.run_id) is not UUID or type(ids.setup) is not SetupIds:
            raise _Fault(ReaderCode.INVALID_INPUT)
        setup = ids.setup
    elif type(ids) is SetupIds:
        setup = ids
    else:
        raise _Fault(ReaderCode.INVALID_INPUT)
    values = (
        setup.user_id,
        setup.organization_id,
        setup.agent_id,
        setup.solution_id,
        setup.initial_deployment_id,
        setup.final_deployment_id,
        setup.role_id,
        setup.profile_id,
        setup.connection_id,
    )
    if any(type(value) is not UUID for value in values):
        raise _Fault(ReaderCode.INVALID_INPUT)
    if setup.initial_deployment_id == setup.final_deployment_id:
        raise _Fault(ReaderCode.INVALID_INPUT)


def _discover(steps: tuple[StepRow, ...]) -> UUID | None:
    selected = tuple(step for step in steps if step.step_number == 4)
    if not selected:
        return None
    if len(selected) != 1 or selected[0].type != "tool_result":
        raise _Fault(ReaderCode.DISCOVERY_INVALID)
    content = selected[0].content
    if not isinstance(content, Mapping) or type(content.get("execution_id")) is not str:
        raise _Fault(ReaderCode.DISCOVERY_INVALID)
    raw = cast(str, content["execution_id"])
    parsed = None
    try:
        parsed = UUID(raw)
    except ValueError:
        pass
    if parsed is None or str(parsed) != raw:
        raise _Fault(ReaderCode.DISCOVERY_INVALID)
    return parsed


class _Read:
    def __init__(self, connection: Connection, end_ns: int) -> None:
        self.connection = connection
        self.end_ns = end_ns
        # Two fixed metadata fetches and retained acquisition scalars share this
        # existing admission budget; this does not bound backend allocation.
        self.remaining_bytes = MAX_SNAPSHOT_BYTES - ACQUISITION_RESERVE_BYTES
        self.admissions: list[
            tuple[_Group, tuple[object, ...], tuple[tuple[object, ...], ...]]
        ] = []
        self.changed = False

    def timeout(self) -> float:
        remaining = self.end_ns - monotonic_ns()
        if remaining <= 0:
            raise _Fault(ReaderCode.SNAPSHOT_TIMEOUT)
        return remaining / 1_000_000_000

    async def fetch(self, sql: str, args: tuple[object, ...]) -> object:
        # All callers pass only frozen module SQL, never user SQL selectors.
        return await self.connection.fetch(sql, *args, timeout=self.timeout())

    async def group(self, name: str, *args: object) -> tuple:
        group = _GROUPS[name]
        binds = tuple(args)
        identities = _metadata(group, await self.fetch(group.metadata_sql, binds))
        self.admissions.append((group, binds, identities))
        rows: list[object] = []
        for identity in identities:
            if self.remaining_bytes < group.fixed_charge:
                raise _Fault(ReaderCode.MATERIAL_OVERSIZE)
            raw = await self.fetch(
                group.material_sql, (*identity, self.remaining_bytes)
            )
            row, charge = _material(group, raw, identity, self.remaining_bytes)
            self.remaining_bytes -= charge
            if row is None:
                self.changed = True
            else:
                rows.append(row)
        return tuple(rows)

    async def recheck(self) -> None:
        for group, args, before in self.admissions:
            after = _metadata(group, await self.fetch(group.metadata_sql, args))
            if after != before:
                self.changed = True


_SETTINGS_SQL = (
    "SELECT current_setting('transaction_isolation') AS isolation, "
    "current_setting('transaction_read_only') AS read_only"
)
_BEGIN_POLL = "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY"
_BEGIN_FINAL = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
_TRANSACTION_IDENTITY_SQL = """SELECT pg_catalog.pg_backend_pid() AS backend_pid,
       pg_catalog.pg_postmaster_start_time() AS postmaster_started_at,
       l.virtualtransaction AS virtual_transaction,
       l.virtualxid AS own_virtual_xid,
       l.mode AS own_lock_mode,
       l.granted AS own_lock_granted
FROM pg_catalog.pg_locks AS l
WHERE l.pid = pg_catalog.pg_backend_pid()
  AND l.locktype = 'virtualxid'
  AND l.mode = 'ExclusiveLock'
  AND l.granted IS TRUE
  AND l.virtualxid = l.virtualtransaction
LIMIT 2"""


def _transaction_identity(raw_rows: object) -> TransactionIdentity:
    if type(raw_rows) is not list:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if len(raw_rows) > 1:
        raise _Fault(ReaderCode.CARDINALITY_EXCESS)
    if not raw_rows:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    expected = {
        "backend_pid",
        "postmaster_started_at",
        "virtual_transaction",
        "own_virtual_xid",
        "own_lock_mode",
        "own_lock_granted",
    }
    record: dict[str, object] = {}
    malformed = False
    try:
        record = dict(cast(Mapping[str, object], raw_rows[0]))
    except (TypeError, ValueError):
        malformed = True
    if malformed:
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    if set(record) != expected:
        raise _Fault(ReaderCode.SCHEMA_MISMATCH)
    pid = record["backend_pid"]
    epoch = record["postmaster_started_at"]
    vxid = record["virtual_transaction"]
    own_vxid = record["own_virtual_xid"]
    if (
        type(pid) is not int
        or not 0 < pid <= 2147483647
        or type(epoch) is not datetime
        or epoch.tzinfo is None
        or epoch.utcoffset() is None
        or type(vxid) is not str
        or type(own_vxid) is not str
        or re.fullmatch(r"[1-9][0-9]{0,9}/[1-9][0-9]{0,9}", vxid, re.ASCII) is None
        or any(int(component) > 4294967295 for component in vxid.split("/"))
        or vxid != own_vxid
        or type(record["own_lock_mode"]) is not str
        or record["own_lock_mode"] != "ExclusiveLock"
        or record["own_lock_granted"] is not True
    ):
        raise _Fault(ReaderCode.MATERIAL_INVALID)
    return TransactionIdentity(epoch, pid, vxid)


def _one(rows: tuple) -> object | None:
    return rows[0] if rows else None


async def _setup(read: _Read, ids: SetupIds, facts: TransactionFacts) -> SetupSnapshot:
    user = await read.group("user", ids.user_id)
    organization = await read.group("organization", ids.organization_id)
    roles = await read.group("user_roles", ids.user_id)
    agent = await read.group("agent", ids.agent_id)
    tools = await read.group("agent_tools", ids.agent_id)
    agent_roles = await read.group("agent_roles", ids.agent_id)
    delegations = await read.group("agent_delegations", ids.agent_id)
    mcp = await read.group("agent_mcp", ids.agent_id)
    workflows = await read.group("workflows_selected", WORKFLOW_IDS)
    solution_workflows = await read.group("workflows_solution", ids.solution_id)
    workflow_roles = await read.group("workflow_roles", WORKFLOW_IDS)
    solution = await read.group("solution", ids.solution_id)
    deployments = await read.group(
        "deployments", (ids.initial_deployment_id, ids.final_deployment_id)
    )
    edges = await read.group(
        "edges", (ids.initial_deployment_id, ids.final_deployment_id)
    )
    profile = await read.group("profile", ids.profile_id)
    connection = await read.group("connection", ids.connection_id)
    assignments = await read.group("assignments")
    solution_executions = await read.group("solution_executions", ids.solution_id)
    selected_executions = await read.group("selected_executions", WORKFLOW_IDS)
    return SetupSnapshot(
        ids,
        facts,
        PrincipalRows(
            cast(UserRow | None, _one(user)),
            cast(OrganizationRow | None, _one(organization)),
            roles,
        ),
        cast(AgentRow | None, _one(agent)),
        tools,
        agent_roles,
        delegations,
        mcp,
        workflows,
        solution_workflows,
        workflow_roles,
        cast(SolutionRow | None, _one(solution)),
        deployments,
        edges,
        ModelSetupRows(
            cast(ProfileRow | None, _one(profile)),
            cast(ConnectionRow | None, _one(connection)),
            assignments,
        ),
        solution_executions,
        selected_executions,
    )


async def _run(
    read: _Read, ids: RunIds, stage: RunReadStage, facts: TransactionFacts
) -> RunSnapshot:
    setup = await _setup(read, ids.setup, facts)
    run = await read.group("run", ids.run_id)
    children = await read.group("child_runs", ids.run_id)
    steps = await read.group("steps", ids.run_id)
    execution_id = _discover(steps)
    parent_attempts = await read.group("parent_attempts", ids.run_id)
    agent_delivery = await read.group("agent_delivery", str(ids.run_id))
    summary_delivery = await read.group("summary_delivery", str(ids.run_id))
    executions: tuple[ExecutionRow, ...] = ()
    workflow_attempts: tuple[WorkflowAttemptRow, ...] = ()
    workflow_delivery: tuple[DeliveryRow, ...] = ()
    if execution_id is None:
        generic = await read.group("generic_run", ids.run_id)
        usage = await read.group("usage_run", ids.run_id)
        deferred = tuple(DeferredChildGroup)
    else:
        executions = await read.group("execution", execution_id)
        workflow_attempts = await read.group("workflow_attempts", execution_id)
        workflow_delivery = await read.group("workflow_delivery", str(execution_id))
        generic = await read.group("generic_known", ids.run_id, execution_id)
        usage = await read.group("usage_known", ids.run_id, execution_id)
        deferred = ()
    # Selected zero-child-usage collection is a structural admission guard.
    if any(row.execution_id is not None for row in usage):
        raise _Fault(ReaderCode.CARDINALITY_EXCESS)
    return RunSnapshot(
        ids,
        stage,
        facts,
        setup,
        cast(RunRow | None, _one(run)),
        children,
        steps,
        parent_attempts,
        generic,
        agent_delivery,
        summary_delivery,
        usage,
        execution_id,
        executions,
        workflow_attempts,
        workflow_delivery,
        deferred,
    )


def _classify(error: Exception) -> ReaderCode:
    if isinstance(error, _Fault):
        return error.code
    if isinstance(error, TimeoutError):
        return ReaderCode.SNAPSHOT_TIMEOUT
    if type(error).__name__ in {
        "UndefinedColumnError",
        "UndefinedTableError",
        "InvalidSchemaNameError",
    }:
        return ReaderCode.SCHEMA_MISMATCH
    return ReaderCode.CONNECTION_FAILURE


def _terminate(connection: Connection) -> None:
    # Termination is private observation custody only, never a server cleanup proof.
    # Even a termination exception cannot disclose raw driver state or allow reuse.
    try:
        connection.terminate()
    except Exception:
        pass


async def _observe(
    connection: Connection,
    ids: SetupIds | RunIds,
    stage: RunReadStage,
    deadline_ns: int,
) -> ReadResult:
    code: ReaderCode | None = None
    try:
        _validate_ids(ids)
        if (
            type(stage) is not RunReadStage
            or type(deadline_ns) is not int
            or deadline_ns <= 0
        ):
            raise _Fault(ReaderCode.INVALID_INPUT)
    except _Fault as error:
        code = error.code
    if code is not None:
        return ReadResult(ReadKind.FAILED, code)
    now = monotonic_ns()
    if deadline_ns <= now:
        return ReadResult(ReadKind.FAILED, ReaderCode.DEADLINE_EXPIRED)
    active: object = None
    try:
        active = connection.is_in_transaction()
    except Exception:
        code = ReaderCode.CONNECTION_FAILURE
    if code is not None:
        return ReadResult(ReadKind.FAILED, code)
    if active is not False:
        return ReadResult(
            ReadKind.FAILED,
            ReaderCode.ACTIVE_TRANSACTION
            if active is True
            else ReaderCode.CONNECTION_FAILURE,
        )
    end_ns = min(deadline_ns, now + MAX_SNAPSHOT_SECONDS * 1_000_000_000)
    read = _Read(connection, end_ns)
    isolation = "repeatable read" if stage is RunReadStage.FINAL else "read committed"
    control_uncertain = False
    snapshot: SetupSnapshot | RunSnapshot | None = None
    acquisition: ReadAcquisition | None = None
    try:
        async with asyncio.timeout(read.timeout()):
            begin_timeout = read.timeout()
            started_ns = monotonic_ns()
            control_uncertain = True
            if type(started_ns) is not int or not 0 < started_ns <= end_ns:
                raise _Fault(ReaderCode.MATERIAL_INVALID)
            await connection.execute(
                _BEGIN_FINAL if stage is RunReadStage.FINAL else _BEGIN_POLL,
                timeout=begin_timeout,
            )
            control_uncertain = False
            settings = await read.fetch(_SETTINGS_SQL, ())
            if type(settings) is not list or len(settings) != 1:
                raise _Fault(ReaderCode.ISOLATION_MISMATCH)
            settings_row = _record(settings[0], {"isolation", "read_only"})
            if (
                type(settings_row["isolation"]) is not str
                or type(settings_row["read_only"]) is not str
            ):
                raise _Fault(ReaderCode.ISOLATION_MISMATCH)
            if (
                settings_row["isolation"] != isolation
                or settings_row["read_only"] != "on"
            ):
                raise _Fault(ReaderCode.ISOLATION_MISMATCH)
            identity = _transaction_identity(
                await read.fetch(_TRANSACTION_IDENTITY_SQL, ())
            )
            facts = TransactionFacts(isolation, True, identity)
            snapshot = (
                await _run(read, ids, stage, facts)
                if type(ids) is RunIds
                else await _setup(read, ids, facts)
            )
            await read.recheck()
            after_identity = _transaction_identity(
                await read.fetch(_TRANSACTION_IDENTITY_SQL, ())
            )
            if after_identity != identity:
                raise _Fault(ReaderCode.MATERIAL_INVALID)
            read.timeout()
            control_uncertain = True
            await connection.execute("COMMIT", timeout=read.timeout())
            if connection.is_in_transaction() is not False:
                raise _Fault(ReaderCode.CONNECTION_TAINTED)
            read.timeout()
            completed_ns = monotonic_ns()
            if (
                type(completed_ns) is not int
                or not 0 < started_ns <= completed_ns <= end_ns
            ):
                raise _Fault(ReaderCode.MATERIAL_INVALID)
            acquisition = ReadAcquisition(started_ns, completed_ns, identity)
            control_uncertain = False
    except asyncio.CancelledError:
        _terminate(connection)
        raise
    except Exception as error:
        code = _classify(error)
    if code is None and (snapshot is None or acquisition is None):
        _terminate(connection)
        return ReadResult(ReadKind.FAILED, ReaderCode.CONNECTION_TAINTED)
    if code is None:
        return ReadResult(
            ReadKind.CHANGED if read.changed else ReadKind.OBSERVED,
            ReaderCode.SNAPSHOT_CHANGED if read.changed else None,
            snapshot,
            acquisition,
        )
    if (
        control_uncertain
        or code in (ReaderCode.SNAPSHOT_TIMEOUT, ReaderCode.CONNECTION_TAINTED)
        or monotonic_ns() >= end_ns
    ):
        _terminate(connection)
        return ReadResult(ReadKind.FAILED, ReaderCode.CONNECTION_TAINTED)
    rollback_failed = False
    try:
        async with asyncio.timeout(read.timeout()):
            await connection.execute("ROLLBACK", timeout=read.timeout())
            rollback_failed = connection.is_in_transaction() is not False
            read.timeout()
    except asyncio.CancelledError:
        _terminate(connection)
        raise
    except Exception:
        rollback_failed = True
    if rollback_failed:
        _terminate(connection)
        return ReadResult(ReadKind.FAILED, ReaderCode.CONNECTION_TAINTED)
    return ReadResult(ReadKind.FAILED, code)


async def read_setup(
    connection: Connection, ids: SetupIds, *, deadline_ns: int
) -> ReadResult:
    """Read bounded setup material; observed never means eligible or accepted."""
    if type(ids) is not SetupIds:
        return ReadResult(ReadKind.FAILED, ReaderCode.INVALID_INPUT)
    return await _observe(connection, ids, RunReadStage.POLL, deadline_ns)


async def read_run(
    connection: Connection, ids: RunIds, *, stage: RunReadStage, deadline_ns: int
) -> ReadResult:
    """Read committed run material; incomplete final reads retain deferred groups."""
    if type(ids) is not RunIds:
        return ReadResult(ReadKind.FAILED, ReaderCode.INVALID_INPUT)
    return await _observe(connection, ids, stage, deadline_ns)
