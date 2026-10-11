"""Path-scoped CORS for MCP OAuth discovery and provider endpoints."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.cors import CORSMiddleware


Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_MCP_OAUTH_EXACT_PATHS = frozenset(
    {
        "/authorize",
        "/mcp/callback",
        "/register",
        "/token",
    }
)
_MCP_OAUTH_DISCOVERY_PATHS = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
)


def _is_mcp_oauth_path(path: str) -> bool:
    if path in _MCP_OAUTH_EXACT_PATHS:
        return True
    return any(
        path == discovery_path or path.startswith(f"{discovery_path}/")
        for discovery_path in _MCP_OAUTH_DISCOVERY_PATHS
    )


class MCPAuthCORSMiddleware:
    """Apply permissive CORS only to the MCP OAuth protocol surface."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.cors_app = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _is_mcp_oauth_path(scope["path"]):
            await self.cors_app(scope, receive, send)
            return
        await self.app(scope, receive, send)
