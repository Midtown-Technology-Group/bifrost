"""
Decision Inference Abstraction

Provider-neutral bounded-decision surface (issue #806). Callers use the
boolean / choice / score / rank primitives without knowing which provider,
model, or runtime served the call. Deterministic authorization and policy
enforcement stay outside the model.

Usage:
    from src.services.decision import Rule, RuleProvider, create_decision_engine

    engine = create_decision_engine(
        RuleProvider({"retry?": Rule("boolean", lambda ctx: ctx["attempts"] < 3)}),
    )
    result = await engine.boolean("retry?", {"attempts": 1})
"""

from src.services.decision.base import (
    DecisionEngineDisabled,
    DecisionError,
    DecisionMeta,
    DecisionMode,
    DecisionPrimitive,
    DecisionProvider,
    DecisionProviderInfo,
    DecisionResult,
    DecisionValidationError,
    ProviderDecision,
)
from src.services.decision.engine import DecisionEngine, compute_input_hash
from src.services.decision.factory import create_decision_engine
from src.services.decision.rule_provider import Rule, RuleProvider

__all__ = [
    "DecisionEngine",
    "DecisionEngineDisabled",
    "DecisionError",
    "DecisionMeta",
    "DecisionMode",
    "DecisionPrimitive",
    "DecisionProvider",
    "DecisionProviderInfo",
    "DecisionResult",
    "DecisionValidationError",
    "ProviderDecision",
    "Rule",
    "RuleProvider",
    "compute_input_hash",
    "create_decision_engine",
]
