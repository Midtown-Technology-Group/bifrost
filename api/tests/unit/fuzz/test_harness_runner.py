import subprocess
import sys
from pathlib import Path

import pytest

from fuzz.harnesses import HARNESS_TARGETS, run_harness
from fuzz.runner import iter_corpus_cases
from src.services.editor.search import _search_content
from src.services.webhooks.protocol import WebhookRequest


def test_search_output_budget_corpus_preserves_resource_rejection():
    case = next(case for case in iter_corpus_cases() if case.path.name == "output-budget.bin")
    text = case.data.decode("utf-8", errors="replace")
    with pytest.raises(ValueError, match="Search exceeded the output character budget"):
        _search_content(text[64:], "fuzz.txt", text[:64], case_sensitive=False, is_regex=True)
    run_harness(case.target, case.data)


def test_search_harness_does_not_mask_unexpected_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("Unexpected search failure")

    monkeypatch.setattr("src.services.editor.search._search_content", fail)
    with pytest.raises(ValueError, match="Unexpected search failure"):
        run_harness("editor-search", b"payload")


def test_registered_harnesses_have_seed_corpus_cases():
    cases_by_target = {}
    for case in iter_corpus_cases():
        cases_by_target.setdefault(case.target, []).append(case)

    assert set(cases_by_target) == set(HARNESS_TARGETS)
    assert all(cases for cases in cases_by_target.values())


@pytest.mark.parametrize("case", list(iter_corpus_cases()), ids=lambda case: case.id)
def test_seed_corpus_cases_do_not_crash(case):
    run_harness(case.target, case.data)


def test_unknown_harness_target_is_rejected():
    with pytest.raises(ValueError, match="Unknown fuzz harness"):
        run_harness("missing-target", b"payload")


@pytest.mark.parametrize(
    ("body", "expected"),
    [(b"4", 4), (b"[]", []), (b'"value"', "value"), (b"true", True), (b"null", None)],
)
def test_webhook_harness_preserves_non_object_json_parsing(body, expected):
    request = WebhookRequest(method="POST", path="/", headers={}, query_params={}, body=body)
    assert request.json_body == expected
    assert request.text_body == body.decode()
    run_harness("webhook-request", body)


def test_corpus_case_ids_are_stable_relative_paths():
    for case in iter_corpus_cases():
        assert case.id == str(Path(case.target) / case.path.name)
        assert case.path.is_file()


@pytest.mark.parametrize("target", ["cron-parser", "webhook-request"])
def test_small_targets_do_not_load_editor_or_orm_dependencies(target):
    script = (
        "import sys; from fuzz.harnesses import run_harness; "
        f"run_harness({target!r}, b''); "
        "assert 'src.services.editor.search' not in sys.modules; "
        "assert 'src.models.orm' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[3],
        check=True,
        capture_output=True,
        text=True,
    )
