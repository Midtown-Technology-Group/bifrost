from __future__ import annotations

import pytest
from src.services.decision import DecisionValidationError, Rule, RuleProvider


@pytest.mark.asyncio
async def test_boolean_rule_returns_value_and_one_hot_scores() -> None:
    provider = RuleProvider(
        {"retry?": Rule("boolean", lambda ctx: ctx["attempts"] < 3)}
    )

    result = await provider.boolean("retry?", {"attempts": 1})

    assert result.value is True
    assert result.scores == {"true": 1.0, "false": 0.0}
    assert result.confidence is None


@pytest.mark.asyncio
async def test_boolean_false_rule_scores() -> None:
    provider = RuleProvider(
        {"retry?": Rule("boolean", lambda ctx: ctx["attempts"] < 3)}
    )

    result = await provider.boolean("retry?", {"attempts": 3})

    assert result.value is False
    assert result.scores == {"true": 0.0, "false": 1.0}


@pytest.mark.asyncio
async def test_choice_rule_returns_value_and_one_hot_scores() -> None:
    provider = RuleProvider(
        {"route": Rule("choice", lambda ctx: "network" if ctx["port"] == 22 else "app")}
    )

    result = await provider.choice(
        "route", ["app", "network"], {"port": 22}
    )

    assert result.value == "network"
    assert result.scores == {"app": 0.0, "network": 1.0}


@pytest.mark.asyncio
async def test_score_rule_returns_bounded_score() -> None:
    provider = RuleProvider(
        {"risk": Rule("score", lambda ctx: ctx["severity"] / 10.0)}
    )

    result = await provider.score("risk", {"severity": 7})

    assert result.value == 0.7
    assert result.scores == {"score": 0.7}


@pytest.mark.asyncio
async def test_rank_rule_returns_permutation_with_positional_scores() -> None:
    provider = RuleProvider(
        {"order": Rule("rank", lambda ctx: list(ctx["preferred"]))}
    )

    result = await provider.rank(
        "order", ["a", "b", "c"], {"preferred": ["b", "a", "c"]}
    )

    assert result.value == ["b", "a", "c"]
    assert result.scores["b"] == 1.0
    assert result.scores["a"] == pytest.approx(1.0 - 1 / 3)
    assert result.scores["c"] == pytest.approx(1.0 - 2 / 3)


@pytest.mark.asyncio
async def test_unknown_question_fails_closed() -> None:
    provider = RuleProvider({})

    with pytest.raises(DecisionValidationError, match="no rule registered"):
        await provider.boolean("missing", {})


@pytest.mark.asyncio
async def test_kind_mismatch_fails_closed() -> None:
    provider = RuleProvider({"q": Rule("boolean", lambda ctx: True)})

    with pytest.raises(DecisionValidationError, match="kind 'boolean', not 'choice'"):
        await provider.choice("q", ["a", "b"], {})


@pytest.mark.asyncio
async def test_boolean_rule_with_non_boolean_result_fails_closed() -> None:
    provider = RuleProvider({"q": Rule("boolean", lambda ctx: "yes")})

    with pytest.raises(DecisionValidationError, match="non-boolean"):
        await provider.boolean("q", {})


@pytest.mark.asyncio
async def test_choice_rule_with_non_string_result_fails_closed() -> None:
    provider = RuleProvider({"q": Rule("choice", lambda ctx: 1)})

    with pytest.raises(DecisionValidationError, match="non-string"):
        await provider.choice("q", ["a"], {})


@pytest.mark.asyncio
async def test_score_rule_out_of_range_fails_closed() -> None:
    provider = RuleProvider({"q": Rule("score", lambda ctx: 1.5)})

    with pytest.raises(DecisionValidationError, match=r"outside \[0.0, 1.0\]"):
        await provider.score("q", {})


@pytest.mark.asyncio
async def test_score_rule_with_non_numeric_result_fails_closed() -> None:
    provider = RuleProvider({"q": Rule("score", lambda ctx: "high")})

    with pytest.raises(DecisionValidationError, match="non-numeric"):
        await provider.score("q", {})


@pytest.mark.asyncio
async def test_rank_rule_with_non_list_result_fails_closed() -> None:
    provider = RuleProvider({"q": Rule("rank", lambda ctx: "a")})

    with pytest.raises(DecisionValidationError, match="non-list rank"):
        await provider.rank("q", ["a"], {})


def test_provider_info_identifies_deterministic_rules() -> None:
    provider = RuleProvider({})

    assert provider.info.provider == "rule"
    assert provider.info.inference_method == "deterministic_rules"
    assert provider.info.model is None
    assert provider.info.model_revision is None
