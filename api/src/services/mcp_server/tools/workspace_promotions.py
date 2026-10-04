"""Read-only Workspace release inventory wrapper."""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest


async def inspect_workspace_release_retirement(context: Any) -> ToolResult:
    """Read native retirement inventory; external caller review remains required."""
    status_code, body = await call_rest(context, "GET",
        "/api/workspace-promotions/live/retirement-inventory")
    if status_code != 200 or not isinstance(body, dict):
        return error_result(f"retirement inventory failed: HTTP {status_code}", {"body": body})
    return success_result("Live retirement inventory (read only)", body)


TOOLS = [
    ("inspect_workspace_release_retirement", "Inspect Live Retirement", "Read the Live retirement inventory."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register the read-only retained release inventory tool with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {"inspect_workspace_release_retirement": inspect_workspace_release_retirement}

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "register_tools",
    "inspect_workspace_release_retirement",
]
