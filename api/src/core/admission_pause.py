"""Runtime admission gate; normal endpoint authorization still runs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.security import ENGINE_USER_ID, decode_token
from src.services.runtime_maintenance import (
    MAINTENANCE_ROUTE_PREFIX,
    RuntimeMaintenanceState,
    read_cached_runtime_maintenance_state,
)


class AdmissionTracker:
    """Serialize maintenance entry with finite requests and open sockets."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.ordinary_inflight = 0
        self.websocket_senders: dict[int, tuple[Send, asyncio.Event]] = {}

    async def close_websockets(self) -> None:
        """Close sockets admitted before maintenance without waiting for clients."""
        sockets = list(self.websocket_senders.values())
        self.websocket_senders.clear()
        for _, closed in sockets:
            closed.set()
        if sockets:
            await asyncio.gather(
                *(
                    send({"type": "websocket.close", "code": 1013})
                    for send, _ in sockets
                ),
                return_exceptions=True,
            )

admission_tracker = AdmissionTracker()


class AdmissionPauseMiddleware:
    """Close ordinary admissions from either static or durable maintenance."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        static_paused: bool = False,
        state_reader: Callable[[], Awaitable[RuntimeMaintenanceState]] | None = None,
    ) -> None:
        self.app = app
        self.static_paused = static_paused
        self.state_reader = state_reader or read_cached_runtime_maintenance_state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if scope["type"] == "http" and scope["method"] == "GET" and path in {
            "/health", "/health/live", "/health/ready",
        }:
            await self.app(scope, receive, send)
            return

        # These exact admin routes remain reachable while paused, but they do
        # not bypass their normal CurrentSuperuser dependency.
        if scope["type"] == "http" and (
            path == MAINTENANCE_ROUTE_PREFIX
            or path.startswith(MAINTENANCE_ROUTE_PREFIX + "/")
        ):
            await self.app(scope, receive, send)
            return

        scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
        payload = decode_token(token, expected_type="access") if scheme.lower() == "bearer" else None
        if payload and payload.get("engine") is True and payload.get("sub") == ENGINE_USER_ID:
            # This bypasses maintenance only, never the route's authentication,
            # tenant checks, delegation policy, or execution-attempt fencing.
            await self.app(scope, receive, send)
            return

        try:
            async with admission_tracker.lock:
                paused = self.static_paused or (await self.state_reader()).active
                if not paused and scope["type"] == "http":
                    admission_tracker.ordinary_inflight += 1
                elif not paused:
                    socket_closed = asyncio.Event()
                    admission_tracker.websocket_senders[id(send)] = (
                        send,
                        socket_closed,
                    )
        except Exception:  # noqa: BLE001 -- an unreadable durable gate fails closed
            # Unknown durable state must never be interpreted as an open gate.
            paused = True

        if not paused and scope["type"] == "websocket":
            message_inflight = False

            async def gated_receive() -> Message:
                nonlocal message_inflight
                async with admission_tracker.lock:
                    if message_inflight:
                        admission_tracker.ordinary_inflight -= 1
                        message_inflight = False
                    if socket_closed.is_set():
                        return {"type": "websocket.disconnect", "code": 1013}

                receive_task = asyncio.create_task(receive())
                closed_task = asyncio.create_task(socket_closed.wait())
                done, pending = await asyncio.wait(
                    {receive_task, closed_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if closed_task in done and socket_closed.is_set():
                    return {"type": "websocket.disconnect", "code": 1013}

                message = receive_task.result()
                if message["type"] == "websocket.receive":
                    async with admission_tracker.lock:
                        try:
                            active = (await self.state_reader()).active
                        except Exception:  # noqa: BLE001 -- unreadable gate fails closed
                            active = True
                        if active:
                            socket_closed.set()
                            await send({"type": "websocket.close", "code": 1013})
                        if socket_closed.is_set():
                            return {"type": "websocket.disconnect", "code": 1013}
                        admission_tracker.ordinary_inflight += 1
                        message_inflight = True
                return message

            try:
                await self.app(scope, gated_receive, send)
            finally:
                async with admission_tracker.lock:
                    if message_inflight:
                        admission_tracker.ordinary_inflight -= 1
                    admission_tracker.websocket_senders.pop(id(send), None)
            return

        if not paused:
            try:
                await self.app(scope, receive, send)
            finally:
                async with admission_tracker.lock:
                    admission_tracker.ordinary_inflight -= 1
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return

        response = JSONResponse(
            {"detail": "New requests are paused for maintenance. Retry shortly."},
            status_code=503,
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
