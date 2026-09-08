"""Safe SDK reads retry connection establishment without replaying writes."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

client_module = import_module("bifrost.client")


def response(status):
    return httpx.Response(
        status, request=httpx.Request("POST", "https://example.test/")
    )


@pytest.mark.asyncio
async def test_safe_read_connect_timeout_then_success(monkeypatch):
    send = AsyncMock(side_effect=[httpx.ConnectTimeout("TLS handshake"), response(200)])
    sleep = AsyncMock()
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    result = await client_module._send_with_5xx_retry(
        "GET", send, retry_connect_timeout=True
    )
    assert result.status_code == 200
    assert send.await_count == 2
    sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_mixed_timeouts_and503_share_six_attempt_budget(monkeypatch):
    send = AsyncMock(side_effect=[response(503), httpx.ConnectTimeout("TLS")] * 3)
    sleep = AsyncMock()
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    with pytest.raises(httpx.ConnectTimeout):
        await client_module._send_with_5xx_retry(
            "GET", send, retry_connect_timeout=True
        )
    assert send.await_count == 6
    assert [call.args[0] for call in sleep.await_args_list] == list(
        client_module.SDK_RETRY_BACKOFF_SECONDS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "PUT", "DELETE"])
async def test_no_transport_retry_without_explicit_opt_in(method):
    send = AsyncMock(side_effect=httpx.ConnectTimeout("TLS"))
    with pytest.raises(httpx.ConnectTimeout):
        await client_module._send_with_5xx_retry(method, send)
    assert send.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectError]
)
async def test_other_transport_failures_are_not_replayed(error):
    send = AsyncMock(side_effect=error("uncertain"))
    with pytest.raises(error):
        await client_module._send_with_5xx_retry(
            "GET", send, retry_connect_timeout=True
        )
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_concrete_safe_post_propagates_opt_in(monkeypatch):
    transport = SimpleNamespace(
        post=AsyncMock(side_effect=[httpx.ConnectTimeout("TLS"), response(200)])
    )
    concrete = object.__new__(client_module.BifrostClient)
    concrete._access_token = "test-token"
    concrete._get_async_client = lambda: transport
    monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
    assert (
        await concrete.post("/api/sdk/integrations/get", retry_safe=True)
    ).status_code == 200
    assert transport.post.await_count == 2


@pytest.mark.asyncio
async def test_workflow_cancellation_stops_read_retry(monkeypatch):
    import asyncio

    send = AsyncMock(side_effect=httpx.ConnectTimeout("TLS"))
    monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await client_module._send_with_5xx_retry("GET", send, retry_connect_timeout=True)
    assert send.await_count == 1
