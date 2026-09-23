from __future__ import annotations

from types import SimpleNamespace

from src.config import Settings
from src.services.decision import RuleProvider, create_decision_engine, factory


def test_settings_default_is_off() -> None:
    assert Settings.model_fields["decision_inference_mode"].default == "off"


def test_factory_defaults_to_settings_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(decision_inference_mode="shadow"),
    )

    engine = create_decision_engine(RuleProvider({}))

    assert engine.mode == "shadow"


def test_factory_explicit_mode_overrides_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(decision_inference_mode="off"),
    )

    engine = create_decision_engine(RuleProvider({}), mode="enforce")

    assert engine.mode == "enforce"
