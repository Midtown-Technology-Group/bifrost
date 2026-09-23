"""
RuleProvider

Deterministic rule-based DecisionProvider. Serves as the baseline adapter
and the test double for the decision surface: rules are registered per
question and evaluated against the caller-supplied context. No model is
involved; scores are one-hot / positional encodings of the rule outcome.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.services.decision.base import (
    DecisionPrimitive,
    DecisionProvider,
    DecisionProviderInfo,
    DecisionValidationError,
    ProviderDecision,
)

RuleFn = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class Rule:
    """A deterministic decision function bound to one primitive."""

    kind: DecisionPrimitive
    fn: RuleFn


class RuleProvider(DecisionProvider):
    """Serves decisions from registered rules; unknown questions fail closed."""

    def __init__(self, rules: Mapping[str, Rule]):
        self._rules = dict(rules)

    @property
    def info(self) -> DecisionProviderInfo:
        return DecisionProviderInfo(
            provider="rule",
            inference_method="deterministic_rules",
        )

    async def boolean(
        self,
        question: str,
        context: Mapping[str, Any],
    ) -> ProviderDecision[bool]:
        value = self._run(question, "boolean", context)
        if not isinstance(value, bool):
            raise DecisionValidationError(
                f"rule for {question!r} returned non-boolean: {value!r}"
            )
        scores = {"true": 1.0, "false": 0.0} if value else {"true": 0.0, "false": 1.0}
        return ProviderDecision(value=value, scores=scores)

    async def choice(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any],
    ) -> ProviderDecision[str]:
        value = self._run(question, "choice", context)
        if not isinstance(value, str):
            raise DecisionValidationError(
                f"rule for {question!r} returned non-string: {value!r}"
            )
        scores = {opt: 1.0 if opt == value else 0.0 for opt in options}
        return ProviderDecision(value=value, scores=scores)

    async def score(
        self,
        question: str,
        context: Mapping[str, Any],
    ) -> ProviderDecision[float]:
        value = self._run(question, "score", context)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DecisionValidationError(
                f"rule for {question!r} returned non-numeric score: {value!r}"
            )
        numeric = float(value)
        if not 0.0 <= numeric <= 1.0:
            raise DecisionValidationError(
                f"rule for {question!r} returned score outside [0.0, 1.0]: {value!r}"
            )
        return ProviderDecision(value=numeric, scores={"score": numeric})

    async def rank(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any],
    ) -> ProviderDecision[list[str]]:
        value = self._run(question, "rank", context)
        if not isinstance(value, (list, tuple)):
            raise DecisionValidationError(
                f"rule for {question!r} returned non-list rank: {value!r}"
            )
        ranked = list(value)
        count = len(ranked)
        scores = {
            opt: 1.0 - (index / count) for index, opt in enumerate(ranked)
        }
        return ProviderDecision(value=ranked, scores=scores)

    def _run(
        self,
        question: str,
        kind: DecisionPrimitive,
        context: Mapping[str, Any],
    ) -> Any:
        rule = self._rules.get(question)
        if rule is None:
            raise DecisionValidationError(
                f"no rule registered for question {question!r}"
            )
        if rule.kind != kind:
            raise DecisionValidationError(
                f"rule for {question!r} is kind {rule.kind!r}, not {kind!r}"
            )
        return rule.fn(context)
