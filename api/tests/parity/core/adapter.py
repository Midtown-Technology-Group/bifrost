"""Backend-neutral actual HTTP adapter and bounded committed-state barrier."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from tests.parity.core.capture import CapturedStep, ScopedReferenceCapture, now
from tests.parity.harness import Observation


class ReferenceAdapter:
    def __init__(
        self,
        base_url: str,
        capture: ScopedReferenceCapture,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.capture = capture
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            follow_redirects=False,
            timeout=60,
        )
        self.polls: list[dict[str, Any]] = []

    async def close(self):
        await self.client.aclose()

    async def request(
        self,
        step: str,
        method: str,
        path: str,
        *,
        json=None,
        headers=None,
        settled: bool = False,
        before=None,
    ) -> CapturedStep:
        before = before or now()
        response = await self.client.request(method, path, json=json, headers=headers)
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = {"raw_body": response.text}
        if settled:
            deadline = time.monotonic() + 30
            while not await self.capture.settled():
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        "Owned PostgreSQL workflow delivery did not settle"
                    )
                await asyncio.sleep(0.05)
            await self.capture.wait_terminal_events()
        database = await self.capture.snapshot()
        events = await self.capture.drain_events()
        transport = await self.capture.drain_requests()
        return CapturedStep(
            Observation(
                step, response.status_code, body, database, events, before, now()
            ),
            transport,
        )

    async def observe_until(
        self, step: str, path: str, predicate, *, before, deadline: float
    ) -> CapturedStep:
        events = []
        requests = {"sdk_requests": [], "model_requests": [], "vendor_requests": []}
        while True:
            captured = await self.request(step, "GET", path, before=before)
            events.extend(captured.observation.events)
            captured.observation.events = list(events)
            for plane, accumulated in requests.items():
                accumulated.extend(getattr(captured.transport, plane))
                setattr(captured.transport, plane, list(accumulated))
            self.polls.append(
                {
                    "status": captured.observation.status,
                    "body": captured.observation.body,
                }
            )
            if predicate(captured):
                return captured
            if time.monotonic() >= deadline:
                raise AssertionError("Reference observation predicate did not complete")
            await asyncio.sleep(0.05)


class ResponseDriftTransport(httpx.AsyncBaseTransport):
    """Actual upstream response mutation before the adapter parses its body."""

    def __init__(self, plane: str):
        assert plane in {"status", "body"}
        self.plane = plane
        self.upstream = httpx.AsyncHTTPTransport(retries=0)
        self.original: tuple[int, Any] | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.upstream.handle_async_request(request)
        try:
            await response.aread()
            body = response.json()
            self.original = (response.status_code, body)
            assert response.status_code == 200 and isinstance(body["result"], dict)
            if self.plane == "status":
                return httpx.Response(202, json=body, request=request)
            changed = {**body, "result": {**body["result"], "ready": False}}
            return httpx.Response(response.status_code, json=changed, request=request)
        finally:
            await response.aclose()

    async def aclose(self):
        await self.upstream.aclose()
