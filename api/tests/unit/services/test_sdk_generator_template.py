"""Tests for SDK generator template rendering."""

import ast
from collections.abc import Iterator

import pytest
import requests
from jinja2.sandbox import SandboxedEnvironment

from src.services import sdk_generator


class _SequenceSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self._responses: Iterator[requests.Response] = iter(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        self.calls.append((method, url, kwargs))
        return next(self._responses)


def _response(status_code: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = b"{}"
    return response


def _generated_client(session: _SequenceSession):
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Retry API"},
        "paths": {
            "/items": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "post": {"responses": {"200": {"description": "OK"}}},
                "put": {"responses": {"200": {"description": "OK"}}},
                "patch": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }
    code, _ = sdk_generator.generate_sdk(spec, "retry", "bearer")
    namespace: dict[str, object] = {}
    exec(compile(code, "<generated-sdk>", "exec"), namespace)
    client_type = namespace["_RetryAPIClient"]
    client = client_type("https://api.example.test", session, base_backoff=0)
    return client, namespace["SDKError"]


def test_generate_sdk_renders_python_source_without_html_escaping(monkeypatch) -> None:
    sandbox_calls = []

    def tracking_sandboxed_environment(*args, **kwargs):
        sandbox_calls.append((args, kwargs))
        return SandboxedEnvironment(*args, **kwargs)

    monkeypatch.setattr(
        sdk_generator,
        "SandboxedEnvironment",
        tracking_sandboxed_environment,
    )
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Quotes API"},
        "paths": {
            "/items": {
                "get": {
                    "summary": "Return \"quoted\" values",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    code, module_name = sdk_generator.generate_sdk(spec, "quotes", "bearer")

    assert sandbox_calls
    assert module_name == "quotes_api"
    tree = ast.parse(code)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "list_items"
    )
    assert ast.get_docstring(method) == 'Return "quoted" values'
    assert "&quot;" not in code


@pytest.mark.parametrize(
    ("method_name", "status_code"),
    [("create_items", 429), ("patch_items", 503)],
)
def test_generated_sdk_does_not_retry_non_idempotent_methods(
    method_name: str,
    status_code: int,
) -> None:
    session = _SequenceSession([_response(status_code), _response(200)])
    client, sdk_error = _generated_client(session)

    with pytest.raises(sdk_error, match=f"HTTP {status_code}"):
        getattr(client, method_name)({"name": "new"})

    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("method_name", "status_code"),
    [("list_items", 429), ("update_items", 503)],
)
def test_generated_sdk_retries_idempotent_methods(
    method_name: str,
    status_code: int,
) -> None:
    session = _SequenceSession([_response(status_code), _response(200)])
    client, _ = _generated_client(session)

    if method_name == "update_items":
        result = client.update_items({"name": "replacement"})
    else:
        result = client.list_items()

    assert result == {}
    assert len(session.calls) == 2
