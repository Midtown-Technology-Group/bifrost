import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def _load_execute_workflow_sync():
    path = Path(__file__).parents[1] / "e2e" / "conftest.py"
    spec = importlib.util.spec_from_file_location("e2e_conftest_timeout_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.execute_workflow_sync


def _client():
    response = SimpleNamespace(
        status_code=200,
        text="",
        json=lambda: {
            "execution_id": "exec-1",
            "status": "Success",
            "result": {},
        },
    )
    return SimpleNamespace(post=MagicMock(return_value=response))


def test_execute_helper_uses_client_timeout_when_no_override_is_supplied() -> None:
    execute_workflow_sync = _load_execute_workflow_sync()
    client = _client()

    execute_workflow_sync(client, {}, "wf-1")

    assert "timeout" not in client.post.call_args.kwargs


def test_execute_helper_forwards_explicit_timeout_override() -> None:
    execute_workflow_sync = _load_execute_workflow_sync()
    client = _client()

    execute_workflow_sync(client, {}, "wf-1", request_timeout=12.5)

    assert client.post.call_args.kwargs["timeout"] == 12.5
