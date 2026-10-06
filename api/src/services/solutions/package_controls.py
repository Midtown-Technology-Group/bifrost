"""Read installed package controls without export side effects or secret values."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.workspace_release import canonical_digest
from src.models.orm.base import Base

# Explicit fields: timestamps, runtime pointers, credentials, documents and file
# contents are not part of a portable control snapshot. Do not use capture's
# connection fallback here: it can create declarations while reading.
CONTROL_FIELDS: dict[str, tuple[str, ...]] = {
    "solutions": ("id", "slug", "organization_id", "global_repo_access", "allow_inbound_access"),
    "workflows": (
        "id", "solution_id", "organization_id", "name", "function_name", "type", "path",
        "is_active", "endpoint_enabled", "allowed_methods", "public_endpoint", "disable_global_key",
        "execution_mode", "timeout_seconds", "retry_policy", "access_level", "cache_ttl_seconds",
        "api_key_enabled", "api_key_expires_at",
    ),
    "applications": ("id", "solution_id", "organization_id", "slug", "app_model", "access_level"),
    "tables": ("id", "solution_id", "organization_id", "name", "schema", "access"),
    "forms": (
        "id", "solution_id", "organization_id", "access_level", "is_active", "workflow_id",
        "launch_workflow_id", "default_launch_params", "allowed_query_params", "workflow_path",
        "workflow_function_name", "confirmation_markdown",
    ),
    "agents": (
        "id", "solution_id", "organization_id", "access_level", "is_active", "owner_user_id",
        "channels", "knowledge_sources", "system_tools", "llm_profile_id", "llm_max_tokens",
        "max_iterations", "max_token_budget", "max_run_timeout",
    ),
    "custom_claims": ("id", "solution_id", "organization_id", "name", "type", "query"),
    "event_sources": ("id", "solution_id", "organization_id", "source_type", "event_type", "is_active"),
    "event_subscriptions": (
        "id", "solution_id", "event_source_id", "workflow_id", "agent_id", "target_type",
        "event_type", "criteria", "input_mapping", "is_active",
    ),
    "file_policies": ("id", "solution_id", "organization_id", "location", "path", "policies"),
}

# Child rows are grouped by parent, so adding/removing a grant on an existing
# entity is a control change, including MCP grants omitted by INSTALL exports.
CHILD_FIELDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "workflow_roles": ("workflows", "workflow_id", ("role_id",)),
    "app_roles": ("applications", "app_id", ("role_id",)),
    "form_roles": ("forms", "form_id", ("role_id",)),
    "form_fields": ("forms", "form_id", (
        "id", "name", "type", "required", "position", "default_value", "options",
        "data_provider_id", "data_provider_inputs", "visibility_expression", "validation",
        "allowed_types", "multiple", "max_size_mb", "allow_as_query_param", "auto_fill",
    )),
    "agent_roles": ("agents", "agent_id", ("role_id",)),
    "agent_tools": ("agents", "agent_id", ("workflow_id",)),
    "agent_delegations": ("agents", "parent_agent_id", ("child_agent_id",)),
    "agent_mcp_connections": ("agents", "agent_id", ("connection_id",)),
    "schedule_sources": ("event_sources", "event_source_id", ("cron_expression", "timezone", "enabled", "overlap_policy")),
    "webhook_sources": ("event_sources", "event_source_id", (
        "adapter_name", "integration_id", "config", "rate_limit_per_minute", "rate_limit_window_seconds", "rate_limit_enabled",
    )),
}

# Declarations retain natural identity. A reviewed initial package may add new
# resources, while an existing declaration's binding/policy cannot be replaced.
DECLARATION_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "solution_file_locations": ("location", ("location", "position")),
    "solution_connection_schema": ("integration_name", ("integration_name", "template", "position")),
    "solution_config_schema": ("key", ("key", "type", "required", "position")),
}


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return value


async def capture_package_controls(db: AsyncSession, solution_id: UUID) -> dict[str, Any]:
    """Capture only explicit scoped columns, including empty child grant sets."""
    result: dict[str, Any] = {}
    for name, fields in CONTROL_FIELDS.items():
        table = Base.metadata.tables[name]
        scope = table.c.id == solution_id if name == "solutions" else table.c.solution_id == solution_id
        rows = (await db.execute(select(*(table.c[field] for field in fields)).where(scope))).mappings()
        result[name] = {str(row["id"]): _json_value(dict(row)) for row in rows}
    if str(solution_id) not in result["solutions"]:
        raise ValueError("Package target is missing")
    for name, (parent_name, parent_column, fields) in CHILD_FIELDS.items():
        table = Base.metadata.tables[name]
        parents = result[parent_name]
        children: dict[str, list[Any]] = {key: [] for key in parents}
        if parents:
            rows = (await db.execute(select(table.c[parent_column], *(table.c[field] for field in fields)).where(
                table.c[parent_column].in_([UUID(key) for key in parents])
            ))).mappings()
            for row in rows:
                children[str(row[parent_column])].append(_json_value({field: row[field] for field in fields}))
        result[name] = {
            key: sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
            for key, items in children.items()
        }
    for name, (identity_field, fields) in DECLARATION_FIELDS.items():
        table = Base.metadata.tables[name]
        rows = (await db.execute(select(*(table.c[field] for field in fields)).where(
            table.c.solution_id == solution_id
        ))).mappings()
        result[name] = {str(row[identity_field]): _json_value(dict(row)) for row in rows}
    return result


def require_preserved_package_controls(before: dict[str, Any], after: dict[str, Any]) -> None:
    """New reviewed entities are allowed; existing controls/resources cannot drift."""
    for kind, entities in before.items():
        current = after.get(kind, {})
        for identity, expected in entities.items():
            if identity not in current or canonical_digest(current[identity]) != canonical_digest(expected):
                raise ValueError(f"Package changes installed controls: {kind}/{identity}")
