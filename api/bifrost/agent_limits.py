"""Shared safety limits for autonomous agent execution."""

from __future__ import annotations

DEFAULT_AGENT_RUN_TIMEOUT_SECONDS = 30 * 60
MIN_AGENT_RUN_TIMEOUT_SECONDS = 1
MAX_AGENT_RUN_TIMEOUT_SECONDS = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS


def validate_agent_run_timeout(value: object) -> int | None:
    """Validate a configured timeout without accepting coercible input."""
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("max_run_timeout must be an integer number of seconds")
    if not MIN_AGENT_RUN_TIMEOUT_SECONDS <= value <= MAX_AGENT_RUN_TIMEOUT_SECONDS:
        raise ValueError(
            "max_run_timeout must be between "
            f"{MIN_AGENT_RUN_TIMEOUT_SECONDS} and {MAX_AGENT_RUN_TIMEOUT_SECONDS} seconds"
        )
    return value


def bounded_agent_run_timeout(
    value: object,
    *,
    default: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
) -> float:
    """Return a positive run timeout that cannot exceed the hard safety cap.

    Manifest validation prevents new invalid values. This runtime boundary also
    protects executions that read legacy or independently written database rows.
    """
    if type(default) not in (int, float) or default <= 0:
        default = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
    default = min(default, MAX_AGENT_RUN_TIMEOUT_SECONDS)
    if type(value) is not int or value < MIN_AGENT_RUN_TIMEOUT_SECONDS:
        return default
    return min(value, MAX_AGENT_RUN_TIMEOUT_SECONDS)
