from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.mcp_oauth_cors import MCPAuthCORSMiddleware


def _client(*, allowed_origins: list[str] | None = None) -> TestClient:
    app = FastAPI()

    @app.get("/{path:path}")
    async def get_path(path: str):
        return {"path": path}

    app.add_middleware(
        MCPAuthCORSMiddleware,
        allowed_origins=allowed_origins or [],
    )
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


def test_regular_api_preflight_denies_unconfigured_origins():
    response = _client().options(
        "/api/users",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_configured_rest_origin_retains_direct_browser_access():
    client = _client(allowed_origins=["https://console.example"])

    response = client.get(
        "/api/branding",
        headers={"Origin": "https://console.example"},
    )
    preflight = client.options(
        "/auth/status",
        headers={
            "Origin": "https://console.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://console.example"
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://console.example"


def test_untrusted_rest_origin_remains_denied_with_explicit_allowlist():
    client = _client(allowed_origins=["https://console.example"])

    response = client.get(
        "/api/branding",
        headers={"Origin": "https://untrusted.example"},
    )
    preflight = client.options(
        "/auth/status",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers


def test_legacy_wildcard_does_not_reopen_regular_rest_routes():
    client = _client(
        allowed_origins=["*", "https://console.example"],
    )

    untrusted = client.get(
        "/api/branding",
        headers={"Origin": "https://untrusted.example"},
    )
    trusted = client.get(
        "/api/branding",
        headers={"Origin": "https://console.example"},
    )

    assert "access-control-allow-origin" not in untrusted.headers
    assert trusted.headers["access-control-allow-origin"] == "https://console.example"


def test_mcp_transport_keeps_its_separate_origin_policy():
    response = _client(
        allowed_origins=["https://console.example"],
    ).get(
        "/mcp/agent-123",
        headers={"Origin": "https://console.example"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_api_route_ending_in_provider_path_does_not_match():
    response = _client().get(
        "/api/auth/register",
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
