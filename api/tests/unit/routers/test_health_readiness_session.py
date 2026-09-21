import importlib.util
from pathlib import Path

import pytest

from src.core import database


_HEALTH_PATH = Path(__file__).parents[3] / "src" / "routers" / "health.py"
_SPEC = importlib.util.spec_from_file_location("health_router_for_session_test", _HEALTH_PATH)
health = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(health)


class FailingSession:
    def __init__(self):
        self.commit_called = False
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        self.closed = True

    async def execute(self, _statement):
        raise TimeoutError("database checkout timed out")

    async def commit(self):
        self.commit_called = True
        raise AssertionError("readiness teardown must not commit")


@pytest.mark.asyncio
async def test_readiness_database_timeout_closes_without_commit(monkeypatch):
    session = FailingSession()
    monkeypatch.setattr(database, "get_session_factory", lambda: lambda: session)

    dependency = database.get_read_only_db()
    db = await dependency.__anext__()
    name, component = await health.check_database(db)

    # Normal dependency exhaustion runs the generator's post-yield teardown.
    # This would call get_db's commit and fail if either readiness route were
    # accidentally switched back to the write-oriented dependency.
    with pytest.raises(StopAsyncIteration):
        await dependency.__anext__()

    assert name == "database"
    assert component == {
        "status": "unhealthy",
        "type": "postgresql",
        "error": "TimeoutError",
    }
    assert session.closed
    assert not session.commit_called


def test_readiness_routes_use_read_only_database_dependency():
    for path in ("/health/ready", "/health/detailed"):
        route = next(route for route in health.router.routes if route.path == path)
        assert any(
            dependency.call is database.get_read_only_db
            for dependency in route.dependant.dependencies
        )
