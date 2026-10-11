from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.mcp_oauth_cors import MCPAuthCORSMiddleware


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/{path:path}")
    async def get_path(path: str):
        return {"path": path}

    app.add_middleware(MCPAuthCORSMiddleware)
    return TestClient(app)


def test_mcp_oauth_provider_preflight_allows_browser_clients():
    response = _client().options(
        "/token",
        headers={
            "Origin": "https://mcp-client.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_mcp_oauth_discovery_suffix_allows_browser_clients():
    response = _client().get(
        "/.well-known/oauth-protected-resource/mcp/agent-123",
        headers={"Origin": "https://mcp-client.example"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_regular_api_response_does_not_allow_cross_origin_reads():
    response = _client().get(
        "/api/users",
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_regular_api_preflight_is_not_handled_by_mcp_cors():
    response = _client().options(
        "/api/users",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 405
    assert "access-control-allow-origin" not in response.headers


def test_api_route_ending_in_provider_path_does_not_match():
    response = _client().get(
        "/api/auth/register",
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
