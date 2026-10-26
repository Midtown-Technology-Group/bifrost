"""Anthropic prompt-cache settings and adaptive endpoint behavior."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from sqlalchemy.dialects import postgresql

from src.services.agent_runtime.model_factory import (
    agent_model_settings,
    anthropic_prompt_cache_settings,
    create_agent_model,
)
from src.services.anthropic_prompt_cache import (
    is_prompt_cache_rejection,
    persist_prompt_cache_support,
    request_with_prompt_cache_fallback,
)
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient


def _config(provider: str, endpoint: str | None = None) -> LLMConfig:
    return LLMConfig(provider=provider, model="m", api_key="k", endpoint=endpoint)


def test_anthropic_agent_settings_enable_prompt_cache():
    settings = agent_model_settings(_config("anthropic"), max_tokens=1000, session_id="s")
    assert settings["anthropic_cache"] is True
    assert settings["anthropic_cache_instructions"] is True
    assert settings["anthropic_cache_tool_definitions"] is True


def test_openai_agent_settings_untouched():
    settings = agent_model_settings(_config("openai"), max_tokens=1000, session_id="s")
    assert "anthropic_cache" not in settings
    assert settings["openai_store"] is False


def test_known_unsupported_endpoint_omits_cache_settings():
    config = _config("anthropic")
    config.anthropic_prompt_cache_supported = False

    settings = agent_model_settings(config, max_tokens=1000, session_id="s")

    assert not set(anthropic_prompt_cache_settings()) & settings.keys()

    direct_settings = PydanticAIClient(config)._model_settings(1000)
    assert not set(anthropic_prompt_cache_settings()) & direct_settings.keys()


def test_anthropic_messages_through_openrouter_keeps_cache_settings():
    config = _config("anthropic", "https://openrouter.ai/api")

    agent_settings = agent_model_settings(
        config, max_tokens=1000, session_id="cache-probe"
    )
    direct_settings = PydanticAIClient(config)._model_settings(1000)

    assert agent_settings["extra_body"] == {"session_id": "cache-probe"}
    assert agent_settings["anthropic_cache"] is True
    assert "anthropic_cache" in direct_settings


@pytest.mark.asyncio
async def test_native_anthropic_model_uses_adaptive_request_path():
    calls = []

    async def fake_create(
        self, messages, stream, model_settings, model_request_parameters
    ):
        calls.append(model_settings)
        if len(calls) == 1:
            raise ModelHTTPError(
                400,
                "claude",
                {"error": {"message": "cache_control unsupported"}},
            )
        return "ok"

    config = _config("anthropic")
    with patch.object(AnthropicModel, "_messages_create", fake_create):
        model = create_agent_model(config)
        result = await model._messages_create(
            [],
            False,
            anthropic_prompt_cache_settings(),
            ModelRequestParameters(),
        )

    assert result == "ok"
    assert len(calls) == 2
    assert "anthropic_cache" in calls[0]
    assert "anthropic_cache" not in calls[1]


@pytest.mark.parametrize("status_code", [400, 422])
def test_cache_control_validation_error_is_retryable(status_code):
    error = ModelHTTPError(
        status_code,
        "claude",
        {"error": {"message": "cache_control is not supported"}},
    )
    assert is_prompt_cache_rejection(error) is True


@pytest.mark.parametrize(
    "error",
    [
        ModelHTTPError(400, "claude", {"error": {"message": "invalid model"}}),
        ModelHTTPError(
            400,
            "claude",
            {"error": {"message": "invalid model; cache_control was included"}},
        ),
        ModelHTTPError(401, "claude", {"error": {"message": "cache_control denied"}}),
        ModelHTTPError(429, "claude", {"error": {"message": "cache_control limited"}}),
        ModelHTTPError(503, "claude", {"error": {"message": "cache_control unavailable"}}),
    ],
)
def test_ambiguous_or_non_validation_errors_are_not_cache_rejections(error):
    assert is_prompt_cache_rejection(error) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("revision_matches", [True, False])
async def test_persistence_locks_profile_before_fencing_connection_revision(
    revision_matches,
):
    profile_id = uuid4()
    connection_id = uuid4()
    connection_updated_at = datetime.now(UTC)
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        model_profile_id=profile_id,
        provider_connection_id=connection_id,
        provider_connection_updated_at=connection_updated_at,
    )

    class Session:
        def __init__(self):
            self.statements = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def execute(self, statement):
            self.statements.append(statement)
            return SimpleNamespace(one_or_none=lambda: profile)

        async def scalar(self, statement):
            self.statements.append(statement)
            return (
                connection_updated_at
                if revision_matches
                else connection_updated_at + timedelta(seconds=1)
            )

        async def commit(self):
            self.committed = True

    profile = SimpleNamespace(
        id=profile_id,
        connection_id=connection_id,
        anthropic_prompt_cache_supported=None,
    )
    session = Session()
    with patch(
        "src.core.database.get_session_factory",
        return_value=lambda: session,
    ):
        await persist_prompt_cache_support(config, False)

    assert len(session.statements) == (3 if revision_matches else 2)
    profile_query = session.statements[0].compile(dialect=postgresql.dialect())
    sql = " ".join(str(profile_query).split())
    params = profile_query.params
    assert "ai_model_profiles.model =" in sql
    assert "ai_model_profiles.connection_id =" in sql
    assert "FOR UPDATE" in sql
    assert "NOWAIT" in sql
    assert profile_id in params.values()
    assert connection_id in params.values()
    revision_query = " ".join(str(session.statements[1].compile()).split())
    assert "ai_provider_connections.updated_at" in revision_query
    assert "ai_provider_connections.id =" in revision_query
    if revision_matches:
        update_query = session.statements[2].compile()
        assert "UPDATE ai_model_profiles SET" in " ".join(str(update_query).split())
        assert False in update_query.params.values()
    assert session.committed is revision_matches


@pytest.mark.asyncio
async def test_unknown_endpoint_falls_back_once_and_remembers_unsupported():
    profile_id = uuid4()
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        model_profile_id=profile_id,
        provider_connection_id=uuid4(),
        provider_connection_updated_at=datetime.now(UTC),
    )
    send = AsyncMock(
        side_effect=[
            ModelHTTPError(400, "claude", {"error": {"message": "cache_control unsupported"}}),
            "ok",
        ]
    )

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist:
        result = await request_with_prompt_cache_fallback(
            send, {**anthropic_prompt_cache_settings(), "max_tokens": 10}, config
        )

    assert result == "ok"
    assert send.await_count == 2
    assert send.await_args_list[0].args[0]["anthropic_cache"] is True
    assert not set(anthropic_prompt_cache_settings()) & send.await_args_list[1].args[0].keys()
    assert config.anthropic_prompt_cache_supported is False
    persist.assert_awaited_once_with(config, False)


@pytest.mark.asyncio
async def test_unknown_endpoint_success_remembers_supported():
    profile_id = uuid4()
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        model_profile_id=profile_id,
        provider_connection_id=uuid4(),
        provider_connection_updated_at=datetime.now(UTC),
    )
    send = AsyncMock(return_value="ok")

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist:
        result = await request_with_prompt_cache_fallback(
            send, anthropic_prompt_cache_settings(), config
        )

    assert result == "ok"
    assert config.anthropic_prompt_cache_supported is True
    persist.assert_awaited_once_with(config, True)


@pytest.mark.asyncio
async def test_failed_uncached_fallback_does_not_poison_profile_capability():
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        model_profile_id=uuid4(),
    )
    cache_error = ModelHTTPError(
        400,
        "claude",
        {"error": {"message": "cache_control unsupported"}},
    )
    fallback_error = ModelHTTPError(
        400,
        "claude",
        {"error": {"message": "invalid model"}},
    )
    send = AsyncMock(side_effect=[cache_error, fallback_error])

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist, pytest.raises(ModelHTTPError) as raised:
        await request_with_prompt_cache_fallback(
            send, anthropic_prompt_cache_settings(), config
        )

    assert raised.value is fallback_error
    assert config.anthropic_prompt_cache_supported is None
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_override_cannot_read_or_mutate_profile_capability():
    profile_id = uuid4()
    config = LLMConfig(
        provider="anthropic",
        model="configured-claude",
        api_key="key",
        model_profile_id=profile_id,
        anthropic_prompt_cache_supported=False,
    )
    send = AsyncMock(
        side_effect=[
            ModelHTTPError(
                400,
                "override-claude",
                {"error": {"message": "cache_control unsupported"}},
            ),
            "ok",
        ]
    )

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist:
        result = await request_with_prompt_cache_fallback(
            send,
            anthropic_prompt_cache_settings(),
            config,
            effective_model="override-claude",
        )

    assert result == "ok"
    assert send.await_args_list[0].args[0]["anthropic_cache"] is True
    assert config.anthropic_prompt_cache_supported is False
    override_settings = PydanticAIClient(config)._model_settings(100, "override-claude")
    assert dict(override_settings)["anthropic_cache"] is True
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_validation_error_is_not_retried_or_persisted():
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        model_profile_id=uuid4(),
    )
    error = ModelHTTPError(400, "claude", {"error": {"message": "invalid model"}})
    send = AsyncMock(side_effect=error)

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist:
        with pytest.raises(ModelHTTPError) as raised:
            await request_with_prompt_cache_fallback(
                send, anthropic_prompt_cache_settings(), config
            )

    assert raised.value is error
    assert send.await_count == 1
    persist.assert_not_awaited()
