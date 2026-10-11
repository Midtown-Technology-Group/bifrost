from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import _http_bridge, data_providers, sdk


def _context(**overrides):
    ctx = SimpleNamespace(
        user_id=uuid4(),
        org_id=uuid4(),
        user_email="user@example.test",
        user_name="Unit User",
        is_platform_admin=False,
        is_external=True,
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


@pytest.mark.asyncio
async def test_call_rest_parses_json_text_and_no_content_responses():
    class Response:
        def __init__(self, status_code, json_value=None, text=""):
            self.status_code = status_code
            self._json_value = json_value
            self.text = text

        def json(self):
            if isinstance(self._json_value, Exception):
                raise self._json_value
            return self._json_value

    class Client:
        def __init__(self):
            self.calls = []
            self.responses = [
                Response(200, {"ok": True}),
                Response(204),
                Response(500, ValueError("not json"), "plain failure"),
            ]

        async def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return self.responses.pop(0)

    client = Client()

    @asynccontextmanager
    async def fake_rest_client(_context):
        yield client

    with patch.object(_http_bridge, "rest_client", fake_rest_client):
        json_result = await _http_bridge.call_rest(
            _context(),
            "post",
            "/api/things",
            json_body={"name": "thing"},
            params={"q": "x"},
        )
        empty_result = await _http_bridge.call_rest(_context(), "delete", "/api/things/1")
        text_result = await _http_bridge.call_rest(_context(), "get", "/api/error")

    assert json_result == (200, {"ok": True})
    assert empty_result == (204, None)
    assert text_result == (500, "plain failure")
    assert client.calls[0] == (
        "POST",
        "/api/things",
        {"json": {"name": "thing"}, "params": {"q": "x"}},
    )


@pytest.mark.asyncio
async def test_call_rest_forwards_operation_identity_to_mutating_rest_calls():
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}

    class Client:
        def __init__(self):
            self.calls = []

        async def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return Response()

    client = Client()

    @asynccontextmanager
    async def fake_rest_client(_context):
        yield client

    with patch.object(_http_bridge, "rest_client", fake_rest_client):
        await _http_bridge.call_rest(
            _context(operation_id="stable-op-42"),
            "POST",
            "/api/things",
            json_body={"name": "thing"},
        )
        await _http_bridge.call_rest(
            _context(operation_id="stable-op-42"),
            "GET",
            "/api/things/1",
        )

    assert client.calls[0][2]["headers"] == {"Idempotency-Key": "stable-op-42"}
    assert "headers" not in client.calls[1][2]


def test_token_from_context_reuses_non_mcp_fastmcp_token():
    token = SimpleNamespace(token="live-token")

    with (
        patch("fastmcp.server.dependencies.get_access_token", return_value=token),
        patch("src.core.security.decode_token", return_value={"sub": "user"}),
        patch("src.core.security.create_access_token") as create_access_token,
    ):
        result = _http_bridge._token_from_context(_context())

    assert result == "live-token"
    create_access_token.assert_not_called()


def test_token_from_context_exchanges_verified_mcp_token():
    resource = "https://example.test/mcp"
    token = SimpleNamespace(
        token="mcp-resource-token",
        claims={"mcp": True, "scope": "mcp:access", "resource": resource},
    )
    payload = {
        "sub": str(uuid4()),
        "email": "mcp-user@example.test",
        "name": "MCP User",
        "is_superuser": False,
        "is_provider_org": True,
        "is_external": False,
        "org_id": str(uuid4()),
        "mcp": True,
        "scope": "mcp:access",
        "resource": resource,
    }

    with (
        patch("fastmcp.server.dependencies.get_access_token", return_value=token),
        patch("src.core.security.decode_token", side_effect=[None, payload]) as decode,
        patch(
            "src.core.security.create_access_token", return_value="rest-token"
        ) as create,
    ):
        result = _http_bridge._token_from_context(_context())

    assert result == "rest-token"
    decode.assert_any_call(
        "mcp-resource-token", expected_type="access", audience=resource
    )
    claims = create.call_args.kwargs["data"]
    assert claims["sub"] == payload["sub"]
    assert claims["is_provider_org"] is True
    assert "mcp" not in claims
    assert "resource" not in claims


def test_token_from_context_rejects_unverified_mcp_token():
    token = SimpleNamespace(
        token="wrong-resource-token",
        claims={
            "mcp": True,
            "scope": "mcp:access",
            "resource": "https://example.test/mcp",
        },
    )

    with (
        patch("fastmcp.server.dependencies.get_access_token", return_value=token),
        patch("src.core.security.decode_token", side_effect=[None, None]),
        patch("src.core.security.create_access_token") as create,
        pytest.raises(PermissionError, match="verified MCP access token"),
    ):
        _http_bridge._token_from_context(_context())

    create.assert_not_called()


def test_token_from_context_fails_closed_without_authenticated_token():
    ctx = MCPContext(user_id=uuid4(), is_platform_admin=True, org_id=None)

    with (
        patch("fastmcp.server.dependencies.get_access_token", side_effect=RuntimeError),
        patch("src.core.security.create_access_token") as create,
        pytest.raises(PermissionError, match="Authenticated MCP access token required"),
    ):
        _http_bridge._token_from_context(ctx)

    create.assert_not_called()


def test_token_from_context_mints_only_for_internal_execution_context():
    ctx = MCPContext.for_internal_execution(
        user_id=uuid4(),
        org_id=None,
        user_email="user@example.test",
        user_name="Unit User",
        is_platform_admin=True,
        is_external=True,
    )

    with (
        patch("fastmcp.server.dependencies.get_access_token", side_effect=RuntimeError),
        patch("src.core.security.create_access_token", return_value="minted") as create,
    ):
        result = _http_bridge._token_from_context(ctx)

    assert result == "minted"
    claims = create.call_args.kwargs["data"]
    assert claims["sub"] == str(ctx.user_id)
    assert claims["email"] == "user@example.test"
    assert claims["name"] == "Unit User"
    assert claims["is_superuser"] is True
    assert claims["is_external"] is True
    assert claims["org_id"] is None


@pytest.mark.asyncio
async def test_rest_client_uses_override_base_url_and_auth_headers(monkeypatch):
    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setenv(_http_bridge._BRIDGE_URL_ENV, "http://api.test/")

    with (
        patch.object(_http_bridge, "_token_from_context", return_value="token-1"),
        patch("httpx.AsyncClient", FakeClient),
    ):
        async with _http_bridge.rest_client(_context()) as client:
            assert isinstance(client, FakeClient)

    assert created["base_url"] == "http://api.test"
    assert created["headers"]["Authorization"] == "Bearer token-1"
    assert created["headers"]["Content-Type"] == "application/json"
    assert created["timeout"] == 30.0


def test_sdk_annotation_and_method_docs_cover_generics_defaults_and_docstrings():
    class Example:
        """Example SDK module."""

        def _private(self):
            return None

        async def fetch(
            self,
            name: str,
            tags: list[str] | None = None,
        ) -> dict[str, int]:
            """Fetch a thing.

            More detail that should stay in the docstring payload.
            """
            return {"count": 1}

    methods = sdk._extract_class_methods(Example)
    docs = sdk._generate_module_docs("example", Example)

    assert [method["name"] for method in methods] == ["fetch"]
    assert methods[0]["params"][0] == {"name": "name", "type": "str"}
    assert methods[0]["params"][1]["name"] == "tags"
    assert methods[0]["params"][1]["default"] == "None"
    assert methods[0]["return_type"] == "dict"
    assert methods[0]["summary"] == "Fetch a thing."
    assert "### example" in docs
    assert "Example SDK module." in docs
    assert "**`example.fetch(" in docs


@pytest.mark.asyncio
async def test_get_data_provider_schema_combines_model_and_usage_docs():
    with patch(
        "src.services.mcp_server.schema_utils.models_to_markdown",
        return_value="# Model docs\n",
    ) as models_to_markdown:
        result = await data_providers.get_data_provider_schema(_context())

    assert result.structured_content["schema"].startswith("# Model docs\n")
    assert "Data providers must return a list" in result.structured_content["schema"]
    assert "Use `list_workflows` with type filter" in result.structured_content["schema"]
    model_names = [name for _model, name in models_to_markdown.call_args.args[0]]
    assert model_names == [
        "DataProviderMetadata",
        "WorkflowParameter (input parameters)",
    ]
    assert models_to_markdown.call_args.args[1] == "Data Provider Schema Documentation"


@pytest.mark.asyncio
async def test_get_sdk_schema_reports_import_errors():
    with patch.dict("sys.modules", {"bifrost": None}):
        result = await sdk.get_sdk_schema(_context())

    assert "Error generating SDK documentation" in result.structured_content["error"]
