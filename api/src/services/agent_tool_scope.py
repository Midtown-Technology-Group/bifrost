"""Shared validation for workflow tools attached to agents."""

from typing import Any


def agent_scope_allows_tool(
    agent: Any,
    tool: Any,
    *,
    allow_cross_org: bool = False,
) -> bool:
    """Return whether a stored association is a usable tool for an agent."""
    return bool(
        tool.is_active
        and tool.type == "tool"
        and (
            allow_cross_org
            or tool.organization_id is None
            or tool.organization_id == agent.organization_id
        )
    )
