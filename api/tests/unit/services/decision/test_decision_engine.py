from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from src.services.decision import (
    DecisionEngine,
    DecisionEngineDisabled,
    DecisionProvider,
    DecisionProviderInfo,
    DecisionValidationError,
    ProviderDecision,
    Rule,
    RuleProvider,
)


class StubProvider(DecisionProvider):
    """Provider returning a fixed ProviderDecision from every primitive."""

    def __init__(self, result: ProviderDecision[Any]):
        self._result = result

    @property
    def info(self) -> DecisionProviderInfo:
        return DecisionProviderInfo(
            provider="stub",
            inference_method="stub",
            model="stub-model",
            model_revision="rev-1",
        )

    async def boolean(
        self, question: str, context: Mapping[str, Any]
    ) -> ProviderDecision[bool]:
        return self._result

    async def choice(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any],
    ) -> ProviderDecision[str]:
        return self._result

    async def score(
        self, question: str, context: Mapping[str, Any]
    ) -> ProviderDecision[float]:
        return self._result

    async def rank(
        self,
        question: str,
        options: Sequence[str],
        context: Mapping[str, Any],
    ) -> ProviderDecision[list[str]]:
        return self._result


def rule_engine(*, mode: str = "shadow") -> DecisionEngine:
    provider = RuleProvider(
        {
            "retry?": Rule("boolean", lambda ctx: ctx["attempts"] < 3),
            "route": Rule("choice", lambda ctx: "network"),
            "risk": Rule("score", lambda ctx: 0.4),
            "order": Rule("rank", lambda ctx: ["b", "a"]),
        }
    )
    return DecisionEngine(provider, mode=mode)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_off_mode_rejects_every_primitive() -> None:
    engine = rule_engine(mode="off")

    with pytest.raises(DecisionEngineDisabled):
        await engine.boolean("retry?", {"attempts": 1})
    with pytest.raises(DecisionEngineDisabled):
        await engine.choice("route", ["app", "network"], {})
    with pytest.raises(DecisionEngineDisabled):
        await engine.score("risk", {})
    with pytest.raises(DecisionEngineDisabled):
        await engine.rank("order", ["a", "b"], {})


@pytest.mark.asyncio
async def test_shadow_mode_results_are_not_authoritative() -> None:
    engine = rule_engine(mode="shadow")

    result = await engine.boolean("retry?", {"attempts": 1})

    assert result.value is True
    assert result.authoritative is False


@pytest.mark.asyncio
async def test_enforce_mode_results_are_authoritative() -> None:
    engine = rule_engine(mode="enforce")

    result = await engine.choice("route", ["app", "network"], {})

    assert result.value == "network"
    assert result.authoritative is True


@pytest.mark.asyncio
async def test_result_meta_carries_provenance() -> None:
    engine = rule_engine()

    result = await engine.score("risk", {"severity": 4})

    meta = result.meta
    assert meta.provider == "rule"
    assert meta.inference_method == "deterministic_rules"
    assert meta.model is None
    assert meta.model_revision is None
    assert meta.latency_ms >= 0.0
    assert len(meta.input_hash) == 64
    int(meta.input_hash, 16)


@pytest.mark.asyncio
async def test_stub_provider_metadata_surfaces_on_result() -> None:
    engine = DecisionEngine(
        StubProvider(ProviderDecision(value=0.5)), mode="shadow"
    )

    result = await engine.score("q", {})

    assert result.meta.provider == "stub"
    assert result.meta.model == "stub-model"
    assert result.meta.model_revision == "rev-1"


@pytest.mark.asyncio
async def test_input_hash_is_stable_and_input_sensitive() -> None:
    engine = rule_engine()

    first = await engine.boolean("retry?", {"attempts": 1})
    second = await engine.boolean("retry?", {"attempts": 1})
    different = await engine.boolean("retry?", {"attempts": 2})

    assert first.meta.input_hash == second.meta.input_hash
    assert first.meta.input_hash != different.meta.input_hash


@pytest.mark.asyncio
async def test_choice_hash_includes_options() -> None:
    engine = rule_engine()

    with_a = await engine.choice("route", ["app", "network"], {})
    without_app = await engine.choice("route", ["network"], {})

    assert with_a.meta.input_hash != without_app.meta.input_hash


@pytest.mark.asyncio
async def test_empty_question_fails_closed() -> None:
    engine = rule_engine()

    with pytest.raises(DecisionValidationError, match="question"):
        await engine.boolean("   ", {})


@pytest.mark.asyncio
async def test_empty_options_fail_closed() -> None:
    engine = rule_engine()

    with pytest.raises(DecisionValidationError, match="non-empty"):
        await engine.choice("route", [], {})


@pytest.mark.asyncio
async def test_duplicate_options_fail_closed() -> None:
    engine = rule_engine()

    with pytest.raises(DecisionValidationError, match="unique"):
        await engine.choice("route", ["a", "a"], {})


@pytest.mark.asyncio
async def test_string_options_fail_closed() -> None:
    engine = rule_engine()

    with pytest.raises(DecisionValidationError, match="sequence"):
        await engine.choice("route", "ab", {})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_provider_boolean_non_bool_fails_closed() -> None:
    engine = DecisionEngine(
        StubProvider(ProviderDecision(value="yes")), mode="shadow"
    )

    with pytest.raises(DecisionValidationError, match="non-boolean"):
        await engine.boolean("q", {})


@pytest.mark.asyncio
async def test_provider_choice_outside_options_fails_closed() -> None:
    engine = DecisionEngine(
        StubProvider(ProviderDecision(value="nope")), mode="shadow"
    )

    with pytest.raises(DecisionValidationError, match="outside the allowed set"):
        await engine.choice("q", ["a", "b"], {})


@pytest.mark.asyncio
async def test_provider_score_out_of_range_fails_closed() -> None:
    engine = DecisionEngine(
        StubProvider(ProviderDecision(value=1.5)), mode="shadow"
    )

    with pytest.raises(DecisionValidationError, match=r"outside \[0.0, 1.0\]"):
        await engine.score("q", {})


@pytest.mark.asyncio
async def test_provider_confidence_out_of_range_fails_closed() -> None:
    engine = DecisionEngine(
        StubProvider(ProviderDecision(value=0.5, confidence=1.2)),
        mode="shadow",
    )

    with pytest.raises(DecisionValidationError, match="confidence"):
        await engine.score("q", {})


@pytest.mark.asyncio
async def test_provider_rank_non_permutation_fails_closed() -> None:
    engine = DecisionEngine(
        StubProvider(ProviderDecision(value=["a"])), mode="shadow"
    )

    with pytest.raises(DecisionValidationError, match="permutation"):
        await engine.rank("q", ["a", "b"], {})


def test_unknown_mode_fails_closed() -> None:
    with pytest.raises(DecisionValidationError, match="unknown decision mode"):
        DecisionEngine(RuleProvider({}), mode="enforce-turbo")  # type: ignore[arg-type]


def test_engine_exposes_mode_and_provider() -> None:
    provider = RuleProvider({})

    engine = DecisionEngine(provider, mode="shadow")

    assert engine.mode == "shadow"
    assert engine.provider is provider
