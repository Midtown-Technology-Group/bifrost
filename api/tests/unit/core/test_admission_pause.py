"""Maintenance closes external ingress without stranding accepted engine work."""

from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.core.admission_pause import AdmissionPauseMiddleware
from src.core.security import ENGINE_USER_ID, create_access_token, create_refresh_token


def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(AdmissionPauseMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def target(path: str):
        if path == "forbidden":
            raise HTTPException(status_code=403)
        return {"reached": path}

    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/hooks/source", "/api/workflows/execute", "/api/packages/install", "/mcp", "/auth/login"])
def test_external_admissions_are_rejected_before_routes(path):
    with client() as http:
        response = http.post(path)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/health", "/health/live", "/health/ready"])
def test_only_exact_health_gets_remain_public(path):
    with client() as http:
        assert http.get(path).status_code == 200
        assert http.post(path).status_code == 503
        assert http.get(path + "/execute").status_code == 503


def test_signed_engine_can_finish_sdk_calls_and_nested_work_without_bypassing_auth():
    token = create_access_token({"sub": ENGINE_USER_ID, "engine": True})
    with client() as http:
        headers = {"Authorization": f"Bearer {token}"}
        assert http.get("/api/sdk/modules", headers=headers).status_code == 200
        assert http.post("/api/workflows/execute", headers=headers).status_code == 200
        assert http.get("/forbidden", headers=headers).status_code == 403


@pytest.mark.parametrize("kind", ["user", "admin", "string-claim", "wrong-subject", "expired", "wrong-audience", "refresh", "forged"])
def test_untrusted_or_invalid_credentials_cannot_bypass_pause(kind):
    claims = {"sub": ENGINE_USER_ID, "engine": True}
    if kind == "user":
        claims = {"sub": "user"}
    elif kind == "admin":
        claims = {"sub": ENGINE_USER_ID, "is_superuser": True}
    elif kind == "string-claim":
        claims["engine"] = "true"
    elif kind == "wrong-subject":
        claims["sub"] = "user"
    token = create_access_token(
        claims,
        expires_delta=timedelta(seconds=-1) if kind == "expired" else None,
        audience="other-service" if kind == "wrong-audience" else None,
    )
    if kind == "refresh":
        token, _ = create_refresh_token(claims)
    elif kind == "forged":
        head, signature = token.rsplit(".", 1)
        token = head + "." + ("a" if signature[0] != "a" else "b") + signature[1:]
    with client() as http:
        assert http.post("/api/workflows/execute", headers={"Authorization": f"Bearer {token}"}).status_code == 503


def test_new_websocket_admissions_are_closed():
    with client() as http:
        with pytest.raises(WebSocketDisconnect) as error:
            with http.websocket_connect("/ws/connect"):
                pytest.fail("maintenance admitted a new websocket")
    assert error.value.code == 1013


def test_api_registers_pause_only_when_enabled(monkeypatch):
    from src.main import create_app, get_settings

    monkeypatch.setattr(get_settings(), "admissions_paused", True)
    paused = create_app()
    assert paused.user_middleware[0].cls is AdmissionPauseMiddleware
    http = TestClient(paused, raise_server_exceptions=False)
    assert http.post("/api/hooks/not-a-real-source").status_code == 503
    monkeypatch.setattr(get_settings(), "admissions_paused", False)
    assert all(m.cls is not AdmissionPauseMiddleware for m in create_app().user_middleware)
