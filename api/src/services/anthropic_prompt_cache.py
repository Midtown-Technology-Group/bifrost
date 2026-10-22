"""Adaptive Anthropic prompt caching for compatible provider endpoints."""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from src.services.llm.base import LLMConfig

logger = logging.getLogger(__name__)
ANTHROPIC_CACHE_SETTING_KEYS = frozenset(
    {
        "anthropic_cache",
        "anthropic_cache_instructions",
        "anthropic_cache_messages",
        "anthropic_cache_tool_definitions",
    }
)
T = TypeVar("T")


def without_prompt_cache_settings(settings: dict[str, object]) -> dict[str, object]:
    """Return settings with every Anthropic cache control removed."""
    return {key: value for key, value in settings.items() if key not in ANTHROPIC_CACHE_SETTING_KEYS}


def is_prompt_cache_rejection(error: ModelHTTPError) -> bool:
    """Recognize only explicit pre-generation cache-control validation errors."""
    if error.status_code not in (400, 422) or not isinstance(error.body, dict):
        return False
    detail = error.body.get("error")
    if not isinstance(detail, dict) or not isinstance(detail.get("message"), str):
        return False
    message = detail["message"].casefold()
    return bool(
        re.search(r"\bcache[_ -]?control\b", message)
        and re.search(
            r"\b(?:unsupported|not supported|unrecognized|not recognized|unknown)\b",
            message,
        )
    )


async def persist_prompt_cache_support(config: LLMConfig, supported: bool) -> None:
    """Persist only if the profile still names the probed model and connection."""
    if (
        config.model_profile_id is None
        or config.provider_connection_id is None
        or config.provider_connection_updated_at is None
    ):
        return
    try:
        from src.core.database import get_session_factory
        from src.models.orm.ai_models import AIModelProfile, AIProviderConnection

        session_factory = get_session_factory()
        async with session_factory() as session:
            profile = (
                await session.execute(
                    select(
                        AIModelProfile.id,
                        AIModelProfile.connection_id,
                        AIModelProfile.anthropic_prompt_cache_supported,
                    )
                    .where(
                        AIModelProfile.id == config.model_profile_id,
                        AIModelProfile.model == config.model,
                        AIModelProfile.connection_id
                        == config.provider_connection_id,
                    )
                    .with_for_update(of=AIModelProfile, nowait=True)
                )
            ).one_or_none()
            if profile is None:
                return

            current_connection_revision = await session.scalar(
                select(AIProviderConnection.updated_at).where(
                    AIProviderConnection.id == profile.connection_id
                )
            )
            if current_connection_revision != config.provider_connection_updated_at:
                return

            if supported:
                # An explicit rejection wins over a concurrent successful probe.
                if profile.anthropic_prompt_cache_supported is not None:
                    return
            await session.execute(
                update(AIModelProfile)
                .where(AIModelProfile.id == profile.id)
                .values(
                    anthropic_prompt_cache_supported=supported,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            logger.debug(
                "Skipped contended Anthropic prompt-cache capability for model profile %s",
                config.model_profile_id,
            )
            return
        logger.warning(
            "Could not persist Anthropic prompt-cache capability for model profile %s",
            config.model_profile_id,
            exc_info=True,
        )
    except Exception:
        logger.warning(
            "Could not persist Anthropic prompt-cache capability for model profile %s",
            config.model_profile_id,
            exc_info=True,
        )


async def request_with_prompt_cache_fallback(
    send: Callable[[dict[str, object]], Awaitable[T]],
    model_settings: dict[str, object],
    config: LLMConfig,
    *,
    effective_model: str | None = None,
) -> T:
    """Send once with caching, retrying only an explicit cache rejection."""
    resolved_model = effective_model or config.model
    configured_model = resolved_model == config.model
    stored_support = (
        config.anthropic_prompt_cache_supported if configured_model else None
    )
    initial_settings = (
        without_prompt_cache_settings(model_settings)
        if stored_support is False
        else dict(model_settings)
    )
    attempted_cache = bool(ANTHROPIC_CACHE_SETTING_KEYS & initial_settings.keys())

    try:
        result = await send(initial_settings)
    except ModelHTTPError as error:
        if not attempted_cache or not is_prompt_cache_rejection(error):
            raise
        result = await send(without_prompt_cache_settings(initial_settings))
        if configured_model:
            config.anthropic_prompt_cache_supported = False
            if config.model_profile_id is not None:
                await persist_prompt_cache_support(config, False)
        return result

    if attempted_cache and configured_model and stored_support is None:
        config.anthropic_prompt_cache_supported = True
        if config.model_profile_id is not None:
            await persist_prompt_cache_support(config, True)
    return result

