"""Workspace promotion MCP Tools — thin wrappers around the REST API.

Implements ``retire_workspace_release`` as a thin HTTP bridge tool (no ORM, no
repositories, no ``AsyncSession``). Omitted identity fields are resolved from
``GET /api/workspace-promotions/live`` before calling the retirement endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest

logger = logging.getLogger(__name__)

RETIRE_LIVE_ACKNOWLEDGEMENT = "retire-live-workspace-release"


async def retire_workspace_release(
    context: Any,
    reason: str,
    acknowledgement: str,
    expected_release_id: str | None = None,
    expected_artifact_id: str | None = None,
    governed_manifest_id: str | None = None,
) -> ToolResult:
    """Retire the global Live Workspace release — ``POST /live/retire``.

    ``acknowledgement`` must be exactly ``retire-live-workspace-release``.
    Omitted identity fields are resolved from the Live status endpoint.
    """
    logger.info("MCP retire_workspace_release (HTTP bridge)")
    if not reason:
        return error_result("reason is required")
    if acknowledgement != RETIRE_LIVE_ACKNOWLEDGEMENT:
        return error_result(
            f"acknowledgement must be exactly {RETIRE_LIVE_ACKNOWLEDGEMENT}"
        )
    if not expected_release_id or not expected_artifact_id or not governed_manifest_id:
        status_code, live = await call_rest(
            context, "GET", "/api/workspace-promotions/live"
        )
        if status_code != 200 or not isinstance(live, dict):
            return error_result(
                f"retire_workspace_release failed: HTTP {status_code}",
                {"body": live},
            )
        active = live.get("active_release")
        if not isinstance(active, dict):
            return error_result("there is no Live Workspace release to retire")
        expected_release_id = expected_release_id or str(
            active.get("release_id") or ""
        )
        expected_artifact_id = expected_artifact_id or str(
            active.get("artifact_id") or ""
        )
        runtime = active.get("runtime") or {}
        challenge = runtime.get("activation_authorization") or {}
        governed_manifest_id = governed_manifest_id or str(
            challenge.get("governed_manifest_id") or ""
        )
    if not expected_release_id or not expected_artifact_id or not governed_manifest_id:
        return error_result("could not resolve the Live release identity")

    status_code, body = await call_rest(
        context,
        "POST",
        "/api/workspace-promotions/live/retire",
        json_body={
            "expected_release_id": expected_release_id,
            "expected_artifact_id": expected_artifact_id,
            "governed_manifest_id": governed_manifest_id,
            "reason": reason,
            "acknowledgement": acknowledgement,
        },
    )
    if status_code not in (200, 201):
        return error_result(
            f"retire_workspace_release failed: HTTP {status_code}", {"body": body}
        )
    release_id = body.get("release_id") if isinstance(body, dict) else None
    return success_result(
        f"Retired Workspace release: {release_id}",
        body if isinstance(body, dict) else {"body": body},
    )


TOOLS = [
    (
        "retire_workspace_release",
        "Retire Workspace Release",
        "Retire the immutable global Live Workspace release.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register the workspace promotion parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {"retire_workspace_release": retire_workspace_release}

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "RETIRE_LIVE_ACKNOWLEDGEMENT",
    "TOOLS",
    "register_tools",
    "retire_workspace_release",
]
