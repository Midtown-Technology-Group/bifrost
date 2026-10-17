from copy import deepcopy

import pytest

from src.routers import websocket as ws_mod


@pytest.mark.parametrize("include_sensitive", [False, True])
def test_agent_run_message_projection_matches_raw_data_grant(
    include_sensitive: bool,
) -> None:
    payload = {
        "type": "agent_run_step",
        "input": {"customer": "private"},
        "output": {"answer": "private"},
        "summary_error": "provider traceback",
        "step": {
            "type": "tool_result",
            "content": {"token": "secret"},
            "tokens_used": 4,
        },
    }
    original = deepcopy(payload)

    projected = ws_mod._project_agent_run_message(
        payload,
        include_sensitive=include_sensitive,
    )

    assert payload == original
    if include_sensitive:
        assert projected == original
    else:
        assert projected["input"] is None
        assert projected["output"] is None
        assert projected["summary_error"] is None
        assert projected["step"]["content"] is None
        assert projected["step"]["tokens_used"] == 4
