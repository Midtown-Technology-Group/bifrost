from __future__ import annotations

import pytest

import bifrost


def test_compute_version_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIFROST_VERSION", "2026.7.5")
    monkeypatch.setattr(
        bifrost._importlib_metadata,
        "version",
        lambda _name: pytest.fail("metadata lookup should not run"),
    )

    assert bifrost._compute_version() == "2026.7.5"


def test_compute_version_uses_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIFROST_VERSION", raising=False)
    monkeypatch.setattr(
        bifrost._importlib_metadata,
        "version",
        lambda name: "1.2.3" if name == "bifrost" else "unexpected",
    )

    assert bifrost._compute_version() == "1.2.3"


def test_compute_version_falls_back_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIFROST_VERSION", raising=False)

    def missing_package(_name: str) -> str:
        raise bifrost._importlib_metadata.PackageNotFoundError

    monkeypatch.setattr(bifrost._importlib_metadata, "version", missing_package)

    assert bifrost._compute_version() == "unknown"


def test_unknown_lazy_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'not_real'"):
        bifrost.__getattr__("not_real")


def test_public_exports_include_sdk_modules_models_decorators_and_errors() -> None:
    expected = {
        "agents",
        "api",
        "ai",
        "config",
        "ExecutionContext",
        "ExecutionStatus",
        "ConfigType",
        "FormFieldType",
        "UserError",
        "WorkflowError",
        "ConfigurationError",
        "workflow",
        "data_provider",
        "tool",
    }

    assert expected.issubset(set(bifrost.__all__))
    assert bifrost.ExecutionStatus.SUCCESS.value == "Success"
    assert bifrost.ConfigType.SECRET.value == "secret"
    assert bifrost.FormFieldType.MULTI_SELECT.value == "multi_select"
