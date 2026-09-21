from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from src.core.auth import get_current_superuser
from src.routers.platform import app_service as app_service_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(app_service_router.router)
    return TestClient(app)


def test_metrics_route_rejects_anonymous_before_azure(monkeypatch):
    called = False

    async def should_not_query(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(app_service_router, "get_app_service_metrics", should_not_query)
    response = _client().get("/api/platform/app-service/metrics")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert called is False


def test_metrics_route_rejects_non_admin_before_azure(monkeypatch):
    called = False

    async def should_not_query(*args, **kwargs):
        nonlocal called
        called = True

    async def non_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privileges required",
        )

    monkeypatch.setattr(app_service_router, "get_app_service_metrics", should_not_query)
    app = FastAPI()
    app.include_router(app_service_router.router)
    app.dependency_overrides[get_current_superuser] = non_admin
    response = TestClient(app).get("/api/platform/app-service/metrics")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert called is False
