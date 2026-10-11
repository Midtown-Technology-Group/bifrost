"""Detect and report the usable OpenAI-compatible inference transport."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from openai import APIStatusError, AsyncOpenAI

from src.services.agent_runtime.retry_transport import get_ai_retry_http_client

OpenAITransport = Literal["responses", "chat_completions"]


@dataclass(frozen=True)
class OpenAITransportProbe:
    """One logical provider request made while detecting a model transport."""

    model: str
    transport: OpenAITransport
    input_tokens: int
    output_tokens: int
    duration_ms: int
    succeeded: bool


def _tokens(response: object | None, *, chat: bool) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    input_name = "prompt_tokens" if chat else "input_tokens"
    output_name = "completion_tokens" if chat else "output_tokens"
    return int(getattr(usage, input_name, 0) or 0), int(
        getattr(usage, output_name, 0) or 0
    )


async def _observed_request(
    request: Callable[[], Awaitable[object]],
    *,
    model: str,
    transport: OpenAITransport,
    before_request: Callable[[], None] | None,
    record_probe: Callable[[OpenAITransportProbe], None] | None,
) -> object:
    if before_request is not None:
        before_request()
    started = perf_counter()
    response = None
    try:
        response = await request()
        return response
    finally:
        if record_probe is not None:
            input_tokens, output_tokens = _tokens(
                response, chat=transport == "chat_completions"
            )
            record_probe(
                OpenAITransportProbe(
                    model=model,
                    transport=transport,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=max(0, int((perf_counter() - started) * 1000)),
                    succeeded=response is not None,
                )
            )


def _responses_are_unsupported(error: APIStatusError) -> bool:
    if error.status_code in (404, 405, 501):
        return True
    if error.status_code != 400:
        return False
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "model not supported",
            "does not support the responses api",
            "responses api is not supported",
            "unsupported endpoint",
            "unknown endpoint",
        )
    )


async def detect_openai_transport(
    *,
    api_key: str,
    endpoint: str | None,
    model: str,
    before_request: Callable[[], None] | None = None,
    record_probe: Callable[[OpenAITransportProbe], None] | None = None,
) -> OpenAITransport:
    """Probe Responses first and use Chat only for a definite unsupported error."""

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        http_client=get_ai_retry_http_client(),
        max_retries=0,
    )
    try:
        await _observed_request(
            lambda: client.responses.create(
                model=model,
                input="Reply with OK.",
                max_output_tokens=64,
                store=False,
            ),
            model=model,
            transport="responses",
            before_request=before_request,
            record_probe=record_probe,
        )
        return "responses"
    except APIStatusError as error:
        if not _responses_are_unsupported(error):
            raise ValueError(
                f"Could not verify model '{model}' through the Responses API "
                f"(HTTP {error.status_code}); transport was not changed."
            ) from error

    try:
        await _observed_request(
            lambda: client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                max_completion_tokens=64,
            ),
            model=model,
            transport="chat_completions",
            before_request=before_request,
            record_probe=record_probe,
        )
    except APIStatusError as error:
        raise ValueError(
            f"Model '{model}' is unavailable through both Responses and Chat "
            f"Completions (Chat HTTP {error.status_code})."
        ) from error
    return "chat_completions"
