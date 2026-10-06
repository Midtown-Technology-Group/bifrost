"""Fork invariants retained at the worker-local SDK transport boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.mark.parametrize("outcome", ["missing", "wrong-generation", "unavailable"])
def test_local_module_fetch_never_uses_network_or_storage(monkeypatch, outcome):
    from src.core import module_cache_sync as cache

    redis = MagicMock()
    redis.get.return_value = None
    monkeypatch.setattr(cache, "_get_sync_redis", lambda: redis)
    monkeypatch.setattr(cache, "workspace_generation_for_import", lambda: "pinned")
    engine = MagicMock()
    if outcome == "unavailable":
        engine.engine_request_sync.side_effect = httpx.ConnectError("socket gone")
    else:
        engine.engine_request_sync.return_value = SimpleNamespace(
            status_code=404 if outcome == "missing" else 200,
            json=lambda: {"content": "pass", "hash": "h", "generation": "new"},
        )
    monkeypatch.setattr(cache, "_get_engine_client", lambda: engine)
    network = MagicMock(side_effect=AssertionError("must stay on worker socket"))
    storage = MagicMock(side_effect=AssertionError("child has no storage access"))
    monkeypatch.setattr(cache, "_fetch_module_from_api", network)
    monkeypatch.setattr(cache, "_object_storage_cached_module", storage)
    if outcome == "missing":
        assert cache.get_module_sync("missing.py") is None
    else:
        expected = cache.WorkspaceGenerationChangedError if outcome == "wrong-generation" else cache.ModuleResolutionError
        with pytest.raises(expected):
            cache.get_module_sync("missing.py")
    network.assert_not_called()
    storage.assert_not_called()
    redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_process_token_renewal_stays_on_socket(monkeypatch):
    from bifrost import client as sdk

    sdk._refresh_coordinators.clear()
    api_url = "https://unreachable.invalid"
    monkeypatch.setenv("BIFROST_API_URL", api_url)
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "expired")
    monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "expired")
    monkeypatch.setattr(sdk, "_engine_socket_path", "/private/worker.sock")
    requests = []

    def reply(request):
        requests.append(request)
        if request.url.path == "/auth/refresh":
            return httpx.Response(200, json={"access_token": "renewed", "refresh_token": "renewed"})
        return httpx.Response(200 if request.headers["authorization"] == "Bearer renewed" else 401)

    def local_transport(*, uds):
        assert uds == "/private/worker.sock"
        return httpx.MockTransport(reply)

    monkeypatch.setattr(sdk.httpx, "AsyncHTTPTransport", local_transport)
    client = sdk.BifrostClient(api_url, "expired")
    try:
        response = await client.engine_request("GET", "/api/sdk/context")
        assert response.status_code == 200
        assert [r.url.path for r in requests] == ["/api/sdk/context", "/auth/refresh", "/api/sdk/context"]
        assert all(r.url.host == "bifrost-engine" for r in requests)
        assert requests[-1].headers["authorization"] == "Bearer renewed"
    finally:
        await client.close()
        sdk._refresh_coordinators.clear()
