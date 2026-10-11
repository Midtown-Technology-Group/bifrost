import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import BadRequestError, RateLimitError

from src.services.openai_transport_detection import (
    OpenAITransportProbe,
    detect_openai_transport,
)


def _status_error(error_type, status: int, message: str):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://models.example.test/v1/responses"),
    )
    return error_type(message, response=response, body={"message": message})


def _client(
    *,
    responses_result=None,
    responses_error=None,
    chat_result=None,
    chat_error=None,
):
    return SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=responses_result,
                side_effect=responses_error,
            )
        ),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=chat_result or MagicMock(),
                    side_effect=chat_error,
                )
            )
        ),
    )


@pytest.mark.asyncio
async def test_detect_openai_transport_prefers_responses() -> None:
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=2))
    client = _client(responses_result=response)
    before_request = MagicMock()
    probes: list[OpenAITransportProbe] = []

    with patch(
        "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
    ):
        transport = await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
            before_request=before_request,
            record_probe=probes.append,
        )

    assert transport == "responses"
    before_request.assert_called_once_with()
    assert probes == [
        OpenAITransportProbe(
            model="test-model",
            transport="responses",
            input_tokens=7,
            output_tokens=2,
            duration_ms=probes[0].duration_ms,
            succeeded=True,
        )
    ]
    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_openai_transport_falls_back_for_unsupported_model() -> None:
    chat_response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1)
    )
    client = _client(
        responses_error=_status_error(
            BadRequestError, 400, "Model not supported for the Responses API"
        ),
        chat_result=chat_response,
    )
    before_request = MagicMock()
    probes: list[OpenAITransportProbe] = []

    with patch(
        "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
    ):
        transport = await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
            before_request=before_request,
            record_probe=probes.append,
        )

    assert transport == "chat_completions"
    assert before_request.call_count == 2
    observed = [
        (probe.transport, probe.input_tokens, probe.output_tokens, probe.succeeded)
        for probe in probes
    ]
    assert observed == [
        ("responses", 0, 0, False),
        ("chat_completions", 5, 1, True),
    ]
    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_openai_transport_fails_when_both_transports_are_unavailable() -> (
    None
):
    client = _client(
        responses_error=_status_error(
            BadRequestError, 400, "Model not supported for the Responses API"
        ),
        chat_error=_status_error(BadRequestError, 400, "Unknown deployment"),
    )

    with (
        patch(
            "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
        ),
        pytest.raises(ValueError, match="unavailable through both"),
    ):
        await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
        )

    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_openai_transport_does_not_fallback_for_rate_limits() -> None:
    client = _client(
        responses_error=_status_error(RateLimitError, 429, "Rate limit exceeded")
    )

    with (
        patch(
            "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
        ),
        pytest.raises(ValueError, match="transport was not changed"),
    ):
        await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
        )

    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_openai_transport_bounds_the_complete_probe_sequence() -> None:
    async def never_returns(**_kwargs):
        await asyncio.Event().wait()

    client = _client()
    client.responses.create = AsyncMock(side_effect=never_returns)
    probes: list[OpenAITransportProbe] = []

    with (
        patch(
            "src.services.openai_transport_detection.AsyncOpenAI",
            return_value=client,
        ),
        patch(
            "src.services.openai_transport_detection.OPENAI_TRANSPORT_DETECTION_TIMEOUT_SECONDS",
            0.01,
        ),
        pytest.raises(TimeoutError),
    ):
        await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
            record_probe=probes.append,
        )

    assert len(probes) == 1
    assert probes[0].transport == "responses"
    assert probes[0].succeeded is False
    client.chat.completions.create.assert_not_awaited()
