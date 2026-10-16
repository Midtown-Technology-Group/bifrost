"""Path-scoped MCP OAuth CORS plus explicit-origin REST compatibility."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
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


def _is_mcp_transport_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


class MCPAuthCORSMiddleware:
    """Keep OAuth discovery public while honoring explicit REST origins."""

    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.oauth_cors_app = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )
        # A legacy "*" configuration must not re-expand the ordinary REST
        # attack surface. Explicit origins retain the documented direct-API
        # deployment path; same-origin clients need no CORS response headers.
        explicit_origins = sorted(
            {
                origin.strip()
                for origin in allowed_origins
                if origin.strip() and origin.strip() != "*"
            }
        )
        self.rest_cors_app = CORSMiddleware(
            app,
            allow_origins=explicit_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _is_mcp_oauth_path(scope["path"]):
            await self.oauth_cors_app(scope, receive, send)
            return
        if scope["type"] == "http" and _is_mcp_transport_path(scope["path"]):
            # The mounted FastMCP app has a stricter origin policy that also
            # incorporates public_url. Do not intercept its preflights here.
            await self.app(scope, receive, send)
            return
        await self.rest_cors_app(scope, receive, send)
