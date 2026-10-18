from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.services.mcp_server import server


def _workflow_tool() -> server.WorkflowTool:
    return server.WorkflowTool(
        name="bounded_result",
        description="Return bounded workflow data",
        workflow_id=str(uuid4()),
        workflow_name="Bounded Result",
        parameters={"type": "object", "properties": {}},
    )


@pytest.fixture
def workflow_context(monkeypatch: pytest.MonkeyPatch) -> None:
    context = server.MCPContext(user_id=uuid4(), org_id=uuid4())
    monkeypatch.setattr(server, "_get_context_from_token", lambda: context)


@pytest.mark.asyncio
async def test_workflow_json_result_parsing_runs_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    workflow_context: None,
) -> None:
    monkeypatch.setattr(
        server,
        "_execute_workflow_tool_impl",
        AsyncMock(return_value='{"ok": true}'),
    )
    calls = []

    async def run_in_thread(function, *args):
        calls.append((function, args))
        return function(*args)

    monkeypatch.setattr(server.asyncio, "to_thread", run_in_thread)

    result = await _workflow_tool().run({})

    assert result.structured_content == {"ok": True}
    assert calls == [(server._parse_bounded_workflow_result_json, ('{"ok": true}',))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "oversized",
    [
        "[" + ("0," * server._MAX_WORKFLOW_RESULT_JSON_BYTES) + "]",
        '"' + ("💥" * 62_501) + '"',
    ],
)
async def test_workflow_json_result_rejects_oversized_input_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    workflow_context: None,
    oversized: str,
) -> None:
    monkeypatch.setattr(
        server,
        "_execute_workflow_tool_impl",
        AsyncMock(return_value=oversized),
    )

    result = await _workflow_tool().run({})

    assert result.structured_content is not None
    assert "250,000-byte parsing limit" in result.structured_content["error"]
    assert oversized not in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ("[" * 66 + "0" + "]" * 66, "64-level nesting limit"),
        ("[" + ",".join("0" for _ in range(50_000)) + "]", "50,000-node parsing limit"),
    ],
)
async def test_workflow_json_result_rejects_excessive_structure(
    monkeypatch: pytest.MonkeyPatch,
    workflow_context: None,
    payload: str,
    expected_error: str,
) -> None:
    monkeypatch.setattr(
        server,
        "_execute_workflow_tool_impl",
        AsyncMock(return_value=payload),
    )

    result = await _workflow_tool().run({})

    assert result.structured_content is not None
    assert expected_error in result.structured_content["error"]
