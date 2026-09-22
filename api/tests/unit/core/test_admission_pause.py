"""Maintenance closes external ingress without stranding accepted engine work."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.core.admission_pause import AdmissionPauseMiddleware, admission_tracker
from src.core.security import ENGINE_USER_ID, create_access_token, create_refresh_token
from src.services.runtime_maintenance import RuntimeMaintenanceState


async def open_state() -> RuntimeMaintenanceState:
    return RuntimeMaintenanceState()


async def draining_state() -> RuntimeMaintenanceState:
    return RuntimeMaintenanceState(phase="draining")


def client(*, static_paused: bool = True, state_reader=open_state) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        AdmissionPauseMiddleware,
        static_paused=static_paused,
        state_reader=state_reader,
    )

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def target(path: str):
        if path == "forbidden":
            raise HTTPException(status_code=403)
        if path == "api/platform/runtime-maintenance":
            raise HTTPException(status_code=401)
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


@pytest.mark.asyncio
async def test_existing_websocket_is_closed_without_blocking_finite_request_drain():
    started = asyncio.Event()
    sent = []

    async def app(scope, receive, send):
        started.set()
        assert await receive() == {"type": "websocket.disconnect", "code": 1013}

    async def receive():
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        sent.append(message)

    middleware = AdmissionPauseMiddleware(app, state_reader=open_state)
    scope = {
        "type": "websocket",
        "path": "/ws/connect",
        "headers": [],
        "query_string": b"",
        "scheme": "wss",
        "server": ("test", 443),
        "client": ("test", 1),
        "subprotocols": [],
    }
    task = asyncio.create_task(middleware(scope, receive, send))
    await started.wait()
    assert admission_tracker.ordinary_inflight == 0
    assert admission_tracker.websocket_senders

    async with admission_tracker.lock:
        await admission_tracker.close_websockets()

    assert sent == [{"type": "websocket.close", "code": 1013}]
    assert admission_tracker.websocket_senders == {}
    assert admission_tracker.ordinary_inflight == 0
    await task


@pytest.mark.asyncio
async def test_websocket_message_handler_counts_as_finite_inflight_work():
    started = asyncio.Event()
    handled = asyncio.Event()
    release = asyncio.Event()
    messages = asyncio.Queue()
    messages.put_nowait({"type": "websocket.receive", "text": "mutate"})

    async def app(scope, receive, send):
        assert (await receive())["text"] == "mutate"
        started.set()
        await release.wait()
        assert await receive() == {"type": "websocket.disconnect", "code": 1013}
        handled.set()

    async def receive():
        return await messages.get()

    async def send(message):
        return None

    middleware = AdmissionPauseMiddleware(app, state_reader=open_state)
    scope = {
        "type": "websocket",
        "path": "/ws/connect",
        "headers": [],
        "query_string": b"",
        "scheme": "wss",
        "server": ("test", 443),
        "client": ("test", 1),
        "subprotocols": [],
    }
    task = asyncio.create_task(middleware(scope, receive, send))
    await started.wait()
    assert admission_tracker.ordinary_inflight == 1

    async with admission_tracker.lock:
        await admission_tracker.close_websockets()
    assert admission_tracker.ordinary_inflight == 1

    release.set()
    await task
    assert handled.is_set()
    assert admission_tracker.ordinary_inflight == 0


@pytest.mark.asyncio
async def test_existing_websocket_rechecks_durable_gate_before_each_message():
    closed = RuntimeMaintenanceState()
    entered = asyncio.Event()
    message_ready = asyncio.Event()
    sent = []

    async def state_reader():
        return closed

    async def app(scope, receive, send):
        entered.set()
        assert await receive() == {"type": "websocket.disconnect", "code": 1013}

    async def receive():
        await message_ready.wait()
        return {"type": "websocket.receive", "text": "mutate"}

    async def send(message):
        sent.append(message)

    middleware = AdmissionPauseMiddleware(app, state_reader=state_reader)
    scope = {
        "type": "websocket",
        "path": "/ws/connect",
        "headers": [],
        "query_string": b"",
        "scheme": "wss",
        "server": ("test", 443),
        "client": ("test", 1),
        "subprotocols": [],
    }
    task = asyncio.create_task(middleware(scope, receive, send))
    await entered.wait()
    closed = RuntimeMaintenanceState(generation=None, phase="draining")
    message_ready.set()
    await task

    assert sent == [{"type": "websocket.close", "code": 1013}]
    assert admission_tracker.ordinary_inflight == 0


@pytest.mark.asyncio
async def test_maintenance_entry_rejects_unverified_multi_process_api(monkeypatch):
    from src.routers.platform import runtime_maintenance

    monkeypatch.setattr(
        runtime_maintenance,
        "get_settings",
        lambda: SimpleNamespace(
            runtime_maintenance_single_api_process=False,
            work_delivery_backend="postgres",
        ),
    )
    request = runtime_maintenance.MaintenanceEnterRequest(
        generation=uuid4(), reason="release"
    )

    with pytest.raises(HTTPException) as error:
        await runtime_maintenance.enter_maintenance(None, None, request)  # type: ignore[arg-type]

    assert error.value.status_code == 409
    assert "single-API-process" in str(error.value.detail)


def test_dynamic_gate_closes_and_reopens_without_rebuilding_app():
    state = RuntimeMaintenanceState()

    async def reader() -> RuntimeMaintenanceState:
        return state

    http = client(static_paused=False, state_reader=reader)
    with http:
        assert http.get("/ordinary").status_code == 200
        state = RuntimeMaintenanceState(phase="draining")
        assert http.get("/ordinary").status_code == 503
        state = RuntimeMaintenanceState()
        assert http.get("/ordinary").status_code == 200


def test_authenticated_maintenance_route_reaches_normal_authorization_layer():
    with client(static_paused=False, state_reader=draining_state) as http:
        assert http.get("/api/platform/runtime-maintenance").status_code == 401
        assert http.get("/api/platform/runtime-maintenance-extra").status_code == 503


def test_real_maintenance_route_rejects_unauthenticated_request(monkeypatch):
    from src.main import create_app, get_settings
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from src.core import entity_change_hook

    monkeypatch.setattr(get_settings(), "admissions_paused", False)
    listeners = (
        ("before_flush", entity_change_hook._before_flush_workflow_catalog_revision),
        ("after_flush", entity_change_hook._after_flush),
        ("do_orm_execute", entity_change_hook._track_bulk_workflow_change),
        ("after_commit", entity_change_hook._after_commit),
        ("after_rollback", entity_change_hook._after_rollback),
    )
    already_registered = {
        (name, listener): event.contains(Session, name, listener)
        for name, listener in listeners
    }
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as http:
            assert http.get("/api/platform/runtime-maintenance").status_code == 401
    finally:
        for name, listener in listeners:
            if not already_registered[(name, listener)] and event.contains(
                Session, name, listener
            ):
                event.remove(Session, name, listener)


def test_api_always_registers_dynamic_gate_and_preserves_static_pause(monkeypatch):
    from src.main import create_app, get_settings

    monkeypatch.setattr(get_settings(), "admissions_paused", True)
    paused = create_app()
    assert paused.user_middleware[0].cls is AdmissionPauseMiddleware
    http = TestClient(paused, raise_server_exceptions=False)
    assert http.post("/api/hooks/not-a-real-source").status_code == 503
    monkeypatch.setattr(get_settings(), "admissions_paused", False)
    opened = create_app()
    assert opened.user_middleware[0].cls is AdmissionPauseMiddleware
    assert opened.user_middleware[0].kwargs["static_paused"] is False
