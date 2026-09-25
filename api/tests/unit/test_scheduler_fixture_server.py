import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from scripts.scheduler_fixture_server import (
    FixtureHandler,
    chat_completion_payload,
    chat_completion_stream_events,
    encode_sse_events,
)


def test_issue_890_external_http_fixture_validates_and_echoes() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/issue-890/external-http"
        valid = Request(url, data=b'{"value":7,"delay_ms":0}', method="POST")
        with urlopen(valid, timeout=5) as response:
            assert json.loads(response.read()) == {"value": 7, "source": "fixture"}
        invalid = Request(url, data=b'{"value":7,"delay_ms":1001}', method="POST")
        try:
            urlopen(invalid, timeout=5)
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("fixture accepted an unbounded delay")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_chat_completion_non_stream_contract_remains_openai_compatible_json() -> None:
    payload = chat_completion_payload({"model": "fixture-chat"})

    assert ChatCompletion.model_validate(payload).choices[0].message.content == "ok"
    assert payload == {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 0,
        "model": "fixture-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def test_issue_890_agent_fixture_calls_one_tool_then_finishes() -> None:
    request = {
        "model": "issue-890-agent",
        "tools": [{"function": {"name": "issue_890_agent_tool"}}],
        "messages": [{"role": "user", "content": "run"}],
    }
    first = ChatCompletion.model_validate(chat_completion_payload(request))
    assert first.choices[0].finish_reason == "tool_calls"
    assert first.choices[0].message.tool_calls[0].function.name == "issue_890_agent_tool"

    request["messages"].append({"role": "tool", "content": '{"ok":true}'})
    second = ChatCompletion.model_validate(chat_completion_payload(request))
    assert second.choices[0].finish_reason == "stop"
    assert second.choices[0].message.content == "ok"

    summary = chat_completion_payload({
        "model": "issue-890-agent",
        "messages": [{"role": "system", "content": "You summarize what an AI agent did"}],
    })
    content = ChatCompletion.model_validate(summary).choices[0].message.content
    assert json.loads(content or "")["answered"] == "ok"


def test_chat_completion_stream_contract_is_openai_compatible_sse() -> None:
    events = chat_completion_stream_events({"model": "fixture-chat", "stream": True})
    encoded = encode_sse_events(events).decode()
    frames = [frame for frame in encoded.split("\n\n") if frame]

    assert frames[-1] == "data: [DONE]"
    chunks = [json.loads(frame.removeprefix("data: ")) for frame in frames[:-1]]
    validated = [ChatCompletionChunk.model_validate(chunk) for chunk in chunks]
    assert [chunk.object for chunk in validated] == [
        "chat.completion.chunk",
        "chat.completion.chunk",
        "chat.completion.chunk",
    ]
    assert chunks[0]["choices"][0] == {
        "index": 0,
        "delta": {"role": "assistant", "content": ""},
        "finish_reason": None,
    }
    assert chunks[1]["choices"][0] == {
        "index": 0,
        "delta": {"content": "ok"},
        "finish_reason": None,
    }
    assert chunks[2]["choices"][0] == {
        "index": 0,
        "delta": {},
        "finish_reason": "stop",
    }
    assert chunks[2]["usage"] == {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
