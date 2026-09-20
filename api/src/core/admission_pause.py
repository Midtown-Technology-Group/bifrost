"""Temporary external admission gate; normal endpoint authorization still runs."""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.security import ENGINE_USER_ID, decode_token


class AdmissionPauseMiddleware:
    """Installed only for an explicitly paused API process."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] in {
            "/health", "/health/live", "/health/ready",
        }:
            await self.app(scope, receive, send)
            return

        scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
        payload = decode_token(token, expected_type="access") if scheme.lower() == "bearer" else None
        if payload and payload.get("engine") is True and payload.get("sub") == ENGINE_USER_ID:
            # This bypasses maintenance only, never the route's authentication,
            # tenant checks, delegation policy, or execution-attempt fencing.
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"detail": "New requests are paused for maintenance. Retry shortly."},
            status_code=503,
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
