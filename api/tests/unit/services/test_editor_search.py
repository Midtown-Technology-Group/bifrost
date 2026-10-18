"""Retained MTG structural safety requirements on the unified search matcher."""

import os
import pytest
from hypothesis import given, settings, strategies as st

from src.services.editor.search import _validate_regex_pattern
from src.services.source_search.matching import build_matcher


@pytest.mark.parametrize("pattern", ["(a+)+$", "(a|a)+", "a" * 513])
def test_risky_regex_is_rejected_before_engine_compilation(pattern, monkeypatch):
    import src.services.source_search.matching as matching

    def compile_forbidden(*args, **kwargs):
        raise AssertionError("unsafe pattern reached engine")

    monkeypatch.setattr(matching.regex, "compile", compile_forbidden)
    with pytest.raises(ValueError, match="nested quantifiers|exceeds"):
        build_matcher(pattern, True, False)


@pytest.mark.parametrize("pattern", [
    r"(?:async\s+)?def", r"(\d+)?", r"(http|https)?", r"(?:foo|bar)+", r"(?:[a-z]+\d+)?",
])
def test_benign_grouped_patterns_are_accepted(pattern):
    _validate_regex_pattern(pattern)
    build_matcher(pattern, True, False)


@pytest.mark.property
@settings(max_examples=int(os.environ.get("BIFROST_REGEX_VALIDATION_FUZZ_EXAMPLES", "100")), deadline=200)
@given(pattern=st.text(max_size=80))
def test_generated_patterns_have_controlled_rejection(pattern):
    try:
        build_matcher(pattern, True, False)
    except ValueError as exc:
        assert any(reason in str(exc) for reason in ("nested quantifiers", "exceeds", "Invalid regex"))
