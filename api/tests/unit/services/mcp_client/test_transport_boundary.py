from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.external_mcp import (
    MCPConnectionCreate,
    MCPConnectionUpdate,
    MCPServerCreate,
    MCPServerUpdate,
)
from src.routers.mcp_connections import (
    MCPConnectionCreateRequest,
    MCPConnectionUpdateRequest,
)
from src.routers.mcp_servers import MCPServerDiscoverRequest
from src.services.mcp_client import client as mcp_client


@pytest.mark.asyncio
async def test_open_client_constructs_explicit_streamable_http_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(
        id="connection-id",
        server_url_override="https://regional.example/mcp",
        server=SimpleNamespace(server_url="https://default.example/mcp"),
    )
    constructed: dict[str, Any] = {}

    class FakeTransport:
        def __init__(self, url: str, *, auth: str) -> None:
            constructed.update(url=url, auth=auth)

    class FakeClient:
        initialize_result = None
        protocol_version = "2026-07-28"

        def __init__(self, transport: Any, **kwargs: Any) -> None:
            constructed.update(transport=transport, client_kwargs=kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("fastmcp.Client", FakeClient)
    monkeypatch.setattr(
        "fastmcp.client.transports.StreamableHttpTransport",
        FakeTransport,
    )

    async with mcp_client.open_client(connection, "secret-token"):
        pass

    assert isinstance(constructed["transport"], FakeTransport)
    assert constructed == {
        "url": "https://regional.example/mcp",
        "auth": "secret-token",
        "transport": constructed["transport"],
        "client_kwargs": {"mode": "auto"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_url",
    [
        "stdio://python server.py",
        "file:///tmp/server.py",
        "./server.py",
    ],
)
async def test_open_client_rejects_non_http_transport_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_url: str,
) -> None:
    connection = SimpleNamespace(
        id="connection-id",
        server_url_override=unsafe_url,
        server=SimpleNamespace(server_url="https://default.example/mcp"),
    )
    constructed = False

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal constructed
            constructed = True

    monkeypatch.setattr("fastmcp.Client", FakeClient)

    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        async with mcp_client.open_client(connection, "secret-token"):
            pass

    assert constructed is False


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        pytest.param(
            MCPServerCreate,
            {"name": "Unsafe", "server_url": "file:///tmp/server.py"},
            id="server-create-contract",
        ),
        pytest.param(
            MCPServerUpdate,
            {"server_url": "file:///tmp/server.py"},
            id="server-update-contract",
        ),
        pytest.param(
            MCPConnectionCreate,
            {
                "server_id": uuid4(),
                "organization_id": uuid4(),
                "client_id": "client",
                "encrypted_client_secret": "encrypted",
                "server_url_override": "file:///tmp/server.py",
            },
            id="connection-create-contract",
        ),
        pytest.param(
            MCPConnectionUpdate,
            {"server_url_override": "file:///tmp/server.py"},
            id="connection-update-contract",
        ),
        pytest.param(
            MCPServerDiscoverRequest,
            {"server_url": "file:///tmp/server.py"},
            id="server-discovery-request",
        ),
        pytest.param(
            MCPConnectionCreateRequest,
            {
                "server_id": uuid4(),
                "organization_id": uuid4(),
                "client_id": "client",
                "client_secret": "secret",
                "server_url_override": "file:///tmp/server.py",
            },
            id="connection-create-request",
        ),
        pytest.param(
            MCPConnectionUpdateRequest,
            {"server_url_override": "file:///tmp/server.py"},
            id="connection-update-request",
        ),
    ],
)
def test_mcp_url_ingress_rejects_non_http_transport(
    model: type,
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="HTTP or HTTPS"):
        model(**payload)
