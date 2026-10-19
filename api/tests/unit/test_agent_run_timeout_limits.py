"""Safety-bound tests for configured autonomous agent run timeouts."""

import pytest
from pydantic import ValidationError

from bifrost.agent_limits import (
    DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
    MAX_AGENT_RUN_TIMEOUT_SECONDS,
    bounded_agent_run_timeout,
)
from bifrost.manifest import ManifestAgent


@pytest.mark.parametrize("value", [1, 600, MAX_AGENT_RUN_TIMEOUT_SECONDS])
def test_manifest_agent_accepts_bounded_run_timeout(value: int) -> None:
    agent = ManifestAgent(id="agent-id", max_run_timeout=value)
    assert agent.max_run_timeout == value


@pytest.mark.parametrize("value", [0, -1, 1801, True, "600", 1.5])
def test_manifest_agent_rejects_invalid_run_timeout(value: object) -> None:
    with pytest.raises(ValidationError, match="max_run_timeout"):
        ManifestAgent(id="agent-id", max_run_timeout=value)


@pytest.mark.parametrize("value", [None, 0, -1, True, "600", 1.5])
def test_runtime_timeout_defaults_for_invalid_or_legacy_values(value: object) -> None:
    assert bounded_agent_run_timeout(value) == DEFAULT_AGENT_RUN_TIMEOUT_SECONDS


def test_runtime_timeout_caps_legacy_oversize_value() -> None:
    assert bounded_agent_run_timeout(10_000) == MAX_AGENT_RUN_TIMEOUT_SECONDS
    assert (
        bounded_agent_run_timeout(None, default=10_000) == MAX_AGENT_RUN_TIMEOUT_SECONDS
    )
