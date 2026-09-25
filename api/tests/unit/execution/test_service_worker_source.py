"""Service children obey the fork's pinned workspace source boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.execution import engine, virtual_import, worker, workspace_modules


@pytest.mark.asyncio
async def test_service_rejects_changed_source_before_execution(monkeypatch):
    monkeypatch.setattr(
        workspace_modules,
        "clear_workspace_modules",
        lambda: SimpleNamespace(generation="current-generation"),
    )
    monkeypatch.setattr(virtual_import, "get_virtual_finder", lambda: None)
    monkeypatch.setattr(virtual_import, "install_virtual_import_hook", lambda: None)
    removed = []
    monkeypatch.setattr(
        virtual_import, "remove_virtual_import_hook", lambda: removed.append(True)
    )
    loaded = []

    def load_source(**kwargs):
        loaded.append(kwargs)
        return lambda: None, None, None, "actual source"

    monkeypatch.setattr(worker, "_load_workspace_workflow", load_source)
    monkeypatch.setattr(
        "bifrost._service_runtime.install_service_credentials", lambda _token: None
    )
    execute = AsyncMock()
    monkeypatch.setattr(engine, "execute", execute)

    result = await worker.run_service(
        {"attempt_id": "attempt-1", "token": "token"},
        {
            "execution_id": "attempt-1",
            "function_name": "main",
            "file_path": "workflows/service.py",
            "content_hash": "wrong-revision",
            "caller": {
                "user_id": "00000000-0000-0000-0000-000000000001",
                "email": "service@bifrost.internal",
                "name": "Service",
            },
        },
    )

    assert loaded[0]["workspace_generation"] == "current-generation"
    assert result["status"] == "Failed"
    assert result["error_type"] == "RuntimeError"
    assert "source integrity mismatch" in result["error_message"]
    execute.assert_not_awaited()
    assert removed == [True]
