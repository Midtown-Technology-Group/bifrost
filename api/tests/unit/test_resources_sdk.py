from unittest.mock import AsyncMock

import httpx
import pytest

from bifrost import resources


@pytest.mark.asyncio
async def test_sdk_resource_read_has_no_solution_deployment_or_org_selectors(monkeypatch):
    import importlib

    module = importlib.import_module("bifrost.resources")
    response = httpx.Response(200, content=b'{"rate":42}', request=httpx.Request("GET", "https://example.invalid"))
    client = AsyncMock()
    client.get.return_value = response
    monkeypatch.setattr(module, "get_client", lambda: client)
    assert await resources.read("data/rates reviewed.json") == '{"rate":42}'
    client.get.assert_awaited_once_with("/api/sdk/resources/data/rates%20reviewed.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../secret", "/root/file", "data\\rates.json", "data//rates.json", "data/./rates.json", "data/null\0.json"])
async def test_sdk_rejects_unsafe_paths_before_request(path):
    with pytest.raises(ValueError):
        await resources.read_bytes(path)
