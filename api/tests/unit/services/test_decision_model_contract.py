"""Spike: Pydantic AI ``DecisionModel`` / TypeSafe (Jev) contract (MIDT-274).

Establishes the actual ``DecisionModel`` contract against the pinned
``pydantic-ai-slim`` using an injected fake backend (no network, no API key,
no customer data). The next child building the BiFrost integration should
read this file as the tested contract; live TypeSafe checks remain opt-in
and are NOT covered here (no ``TYPESAFE_API_KEY`` in CI).

Verified against ``pydantic-ai-slim[typesafe]==2.53.0`` (``DecisionModel`` /
``TypeSafeModel`` shipped in 2.45.0; ``typesafe`` extra pulls
``typesafe-sdk>=0.6.0``).
"""

from datetime import datetime
from enum import Enum, IntEnum
from typing import Literal

import pytest
from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, UseEnumMemberDocstrings
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UserError
from pydantic_ai.models.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionModel,
    DecisionRequest,
    DecisionResponse,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
)
from pydantic_ai.models.typesafe import TypeSafeModel


class FakeDecisionModel(DecisionModel[None]):
    """Injectable fake backend: answers every question deterministically.

    Mirrors the documented ``decide`` contract: one ``DecisionRequest`` in,
    one same-named answer of matching kind per question out.
    """

    max_choice_options = 255
    max_score_levels = 10

    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []
        self.seen_settings: list[dict] = []
        self.noul = 0.9
        self.score = 3.4
        self.fail_with: Exception | None = None

    @property
    def model_name(self) -> str:
        return "fake-spike"

    @property
    def system(self) -> str:
        return "test"

    @property
    def base_url(self) -> str:
        return "https://decisions.example.invalid"

    async def decide(self, request: DecisionRequest, model_settings) -> DecisionResponse:
        self.requests.append(request)
        self.seen_settings.append(dict(model_settings))
        if self.fail_with is not None:
            raise self.fail_with
        answers = {}
        for name, question in request.questions.items():
            if isinstance(question, NoulQuestion):
                answers[name] = NoulAnswer(noul=self.noul)
            elif isinstance(question, ChoiceQuestion):
                options = list(question.criteria)
                answers[name] = ChoiceAnswer(
                    choice=options[0],
                    confidence=0.8,
                    probabilities={
                        option: (0.7 if option == options[0] else 0.3 / (len(options) - 1))
                        for option in options
                    },
                )
            else:
                answers[name] = ScoreAnswer(
                    score=self.score,
                    confidence=0.6,
                    probabilities={level: 0.2 for level in range(len(question.criteria))},
                )
        return DecisionResponse(answers=answers, model_name="fake-spike-v1")


class Area(str, Enum):
    BILLING = "billing"
    BUG = "bug"


class Clarity(UseEnumMemberDocstrings, IntEnum):
    OPAQUE = 0
    """The request is unreadable."""
    MUDDLED = 1
    """The request is confusing."""
    WORKABLE = 2
    """The request can be acted on with effort."""
    CLEAR = 3
    """The request is clear."""
    ACTIONABLE = 4
    """A reader could act on it immediately."""


class Ticket(BaseModel):
    """Triage a support ticket."""

    urgent: bool = Field(description="Needs a reply within the hour?")
    area: Area = Field(description="Which team owns it?")
    label: Literal["billing", "bug"] = Field(description="Routing label?")
    churn: float = Field(ge=0, le=1, description="Churn probability?")
    clarity: Clarity = Field(description="How actionable is the request?")


async def test_multi_field_output_is_one_aggregated_request() -> None:
    """Each output field becomes one question answered in a single request."""
    model = FakeDecisionModel()
    result = await Agent(model, output_type=Ticket).run(
        "My invoice lists a plan I never signed up for."
    )
    assert len(model.requests) == 1
    questions = model.requests[0].questions
    assert sorted(questions) == ["area", "churn", "clarity", "label", "urgent"]
    assert isinstance(questions["urgent"], NoulQuestion)
    assert isinstance(questions["churn"], NoulQuestion)
    assert isinstance(questions["area"], ChoiceQuestion)
    assert isinstance(questions["label"], ChoiceQuestion)
    # Described IntEnum levels form a rubric, asked as one score question.
    assert type(questions["clarity"]).__name__ == "ScoreQuestion"
    assert result.output.urgent is True
    assert result.output.area == Area.BILLING
    assert result.output.label == "billing"
    assert result.output.churn == pytest.approx(0.9)
    # Score 3.4 rounds to the nearest level (half up).
    assert result.output.clarity == Clarity.CLEAR


async def test_response_carries_metadata_confidence_and_raw_scores() -> None:
    """Model version, per-field confidence, and unrounded rubric positions."""
    model = FakeDecisionModel()
    result = await Agent(model, output_type=Ticket).run("My invoice is wrong.")
    assert result.response.model_name == "fake-spike-v1"
    details = result.response.provider_details
    assert details is not None
    assert details["confidence"] == {
        "urgent": pytest.approx(0.8),
        "area": pytest.approx(0.8),
        "label": pytest.approx(0.8),
        "clarity": pytest.approx(0.6),
    }
    assert details["probabilities"]["area"] == {"billing": 0.7, "bug": 0.3}
    assert details["scores"] == {"clarity": pytest.approx(3.4)}
    # A bounded float IS the probability: no confidence entry is reported.
    assert "churn" not in details["confidence"]


async def test_plain_int_enum_without_descriptions_is_a_pick_one() -> None:
    """Undescribed whole numbers are labels, not rubric levels."""

    class Codes(IntEnum):
        OK = 200
        MISSING = 404
        BROKEN = 500

    class Report(BaseModel):
        """Classify a status."""

        code: Codes = Field(description="Which status?")

    model = FakeDecisionModel()
    result = await Agent(model, output_type=Report).run("Page is gone.")
    assert isinstance(model.requests[0].questions["code"], ChoiceQuestion)
    assert result.output.code == Codes.OK


async def test_nested_model_is_asked_as_dotted_fields() -> None:
    """Nested shapes ARE supported: fields are asked as ``outer.inner``."""

    class Address(BaseModel):
        city: Literal["a", "b"] = Field(description="Which city?")

    class Route(BaseModel):
        """Route it."""

        origin: Address
        flag: bool = Field(description="Is it urgent?")

    model = FakeDecisionModel()
    result = await Agent(model, output_type=Route).run("Ship to a.")
    assert sorted(model.requests[0].questions) == ["flag", "origin.city"]
    assert result.output.origin.city == "a"
    assert result.output.flag is True


async def test_list_and_mapping_options_fan_out_as_yes_no_questions() -> None:
    """Each available option gets one question in the same request."""

    class Selections(BaseModel):
        tags: list[Literal["billing", "bug"]] = Field(description="Applicable tags?")
        votes: dict[Literal["billing", "bug"], bool] = Field(description="Applicable votes?")

    model = FakeDecisionModel()
    result = await Agent(model, output_type=Selections).run("Synthetic example.")
    assert len(model.requests) == 1
    assert set(model.requests[0].questions) == {
        "tags.billing", "tags.bug", "votes.billing", "votes.bug"
    }
    assert all(isinstance(q, NoulQuestion) for q in model.requests[0].questions.values())
    assert result.output.tags == ["billing", "bug"]
    assert result.output.votes == {"billing": True, "bug": True}


async def test_plain_str_output_is_refused_before_any_request() -> None:
    """A decision model cannot write text: refused with UserError, no call."""
    model = FakeDecisionModel()
    with pytest.raises(UserError):
        await Agent(model, output_type=str).run("Hello.")
    assert model.requests == []


@pytest.mark.parametrize("field_type", [int, float, datetime, str | int])
async def test_unsupported_unbounded_fields_are_refused(field_type: type) -> None:
    """Unbounded values and a union field cannot be reduced to decisions."""
    report = create_model("Report", value=(field_type, Field(description="Value?")))
    model = FakeDecisionModel()
    with pytest.raises(UserError):
        await Agent(model, output_type=report).run("Synthetic example.")
    assert model.requests == []


async def test_pick_one_over_backend_cap_is_refused() -> None:
    """Options beyond ``max_choice_options`` fail before a request is sent."""
    model = FakeDecisionModel()
    model.max_choice_options = 2
    with pytest.raises(UserError, match="at most 2 options"):
        await Agent(model, output_type=Literal["a", "b", "c"]).run("Pick one.")
    assert model.requests == []


async def test_missing_answer_is_unexpected_model_behavior() -> None:
    """Every question needs a same-named answer of the matching kind."""

    class Silent(FakeDecisionModel):
        async def decide(self, request: DecisionRequest, model_settings) -> DecisionResponse:
            return DecisionResponse(answers={}, model_name="silent")

    with pytest.raises(UnexpectedModelBehavior):
        await Agent(Silent(), output_type=Ticket).run("My invoice is wrong.")


async def test_wrong_answer_kind_is_unexpected_model_behavior() -> None:
    """A score cannot answer a yes/no question."""

    class WrongKind(FakeDecisionModel):
        async def decide(self, request: DecisionRequest, model_settings) -> DecisionResponse:
            return DecisionResponse(
                answers={
                    name: ScoreAnswer(score=1.0, confidence=0.8, probabilities={0: 1.0})
                    for name in request.questions
                },
                model_name="wrong-kind",
            )

    with pytest.raises(UnexpectedModelBehavior):
        await Agent(WrongKind(), instructions="Is it urgent?", output_type=bool).run("Yes.")


async def test_backend_failure_surfaces_for_fallback() -> None:
    """Backend errors propagate so a FallbackModel can take over."""
    model = FakeDecisionModel()
    model.fail_with = ModelAPIError("fake-spike", "backend exploded")
    with pytest.raises(ModelAPIError):
        await Agent(model, output_type=Ticket).run("My invoice is wrong.")


async def test_timeout_setting_reaches_the_backend() -> None:
    """``timeout`` is forwarded through ``model_settings`` to ``decide``."""
    model = FakeDecisionModel()
    await Agent(model, instructions="Is it urgent?", output_type=bool).run(
        "Ship it yesterday.", model_settings={"timeout": 7, "decision_route_threshold": 0.7}
    )
    assert model.seen_settings[0].get("timeout") == 7
    assert model.seen_settings[0].get("decision_route_threshold") == 0.7


async def test_boolean_threshold_decides_true() -> None:
    """``decision_boolean_threshold`` moves the yes/no bar."""
    model = FakeDecisionModel()
    agent = Agent(model, instructions="Is it urgent?", output_type=bool)
    assert (await agent.run("Ship it.", model_settings={"decision_boolean_threshold": 0.95})).output is False
    assert (await agent.run("Ship it.", model_settings={"decision_boolean_threshold": 0.5})).output is True


async def test_no_get_score_api() -> None:
    """There is no ``get_score`` entry point: scores come from rubric fields.

    Raw rubric positions are read from ``provider_details['scores']`` (see
    ``test_response_carries_metadata_confidence_and_raw_scores``).
    """
    import pydantic_ai.models.decision as decision_module

    assert not hasattr(TypeSafeModel, "get_score")
    assert not hasattr(decision_module, "get_score")


async def test_typesafe_model_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``TYPESAFE_API_KEY`` construction fails fast (no network)."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(UserError, match="TYPESAFE_API_KEY"):
        TypeSafeModel("jev-latest")
