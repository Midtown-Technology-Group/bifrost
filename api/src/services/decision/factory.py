"""
Decision Engine Factory

Builds a mode-gated DecisionEngine. The provider is supplied explicitly
(Phase 1 ships only RuleProvider); the mode defaults to the deploy-time
Settings gate until the runtime feature-flag service lands.
"""

from __future__ import annotations

from src.config import get_settings
from src.services.decision.base import DecisionMode, DecisionProvider
from src.services.decision.engine import DecisionEngine


def create_decision_engine(
    provider: DecisionProvider,
    *,
    mode: DecisionMode | None = None,
) -> DecisionEngine:
    """Create a DecisionEngine for the given provider.

    Args:
        provider: Provider adapter that will serve the bounded decisions.
        mode: off / shadow / enforce. Defaults to
            ``Settings.decision_inference_mode``.
    """
    resolved: DecisionMode = (
        mode if mode is not None else get_settings().decision_inference_mode
    )
    return DecisionEngine(provider, mode=resolved)
