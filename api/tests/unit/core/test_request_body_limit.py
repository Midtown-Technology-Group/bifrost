"""Early route body limits run before Starlette parses multipart uploads."""
from __future__ import annotations

import pytest


async def _run(
    middleware,
    *,
    headers=None,
    chunks=(b"",),
    path="/api/solutions/import-workspace/preview",
) -> list[dict]:
    sent: list[dict] = []
    messages = iter(chunks)

    async def receive():
        body = next(messages)
        return {"type": "http.request", "body": body, "more_body": body != chunks[-1]}

    async def send(message):
        sent.append(message)

    await middleware(
        {
            "type": "http", "method": "POST", "path": path,
            "headers": headers or [],
        },
        receive,
        send,
    )
    return sent


@pytest.mark.asyncio
async def test_route_body_limit_rejects_declared_oversize_before_multipart_parser_runs() -> None:
    from src.core.request_body_limit import RouteBodyLimitMiddleware

    invoked = False

    async def app(_scope, _receive, _send):
        nonlocal invoked
        invoked = True

    middleware = RouteBodyLimitMiddleware(
        app, limits={("POST", "/api/solutions/import-workspace/preview"): 10},
    )
    sent = await _run(middleware, headers=[(b"content-length", b"11")])

    assert invoked is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_route_body_limit_rejects_chunked_oversize_before_uploadfile_spooling_completes() -> None:
    from src.core.request_body_limit import RouteBodyLimitMiddleware

    received: list[bytes] = []

    async def app(_scope, receive, _send):
        while True:
            message = await receive()
            received.append(message.get("body", b""))
            if not message.get("more_body"):
                return

    middleware = RouteBodyLimitMiddleware(
        app, limits={("POST", "/api/solutions/import-workspace/preview"): 10},
    )
    sent = await _run(middleware, chunks=(b"12345", b"678901"))

    assert received == [b"12345"]
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_route_body_limit_uses_route_specific_safe_error_detail() -> None:
    from src.core.request_body_limit import RouteBodyLimitMiddleware

    async def app(_scope, _receive, _send):
        raise AssertionError("oversized request reached the application")

    path = "/api/codex-gateway/oauth/import-auth-cache"
    middleware = RouteBodyLimitMiddleware(
        app,
        limits={("POST", path): 10},
        detail="Codex auth cache request exceeds the import limit.",
    )
    sent = await _run(
        middleware,
        headers=[(b"content-length", b"11")],
        path=path,
    )

    assert sent[0]["status"] == 413
    assert b"Codex auth cache request exceeds the import limit." in sent[1]["body"]
