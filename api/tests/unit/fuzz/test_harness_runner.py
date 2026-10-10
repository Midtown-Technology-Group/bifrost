from pathlib import Path

import pytest

from fuzz.harnesses import HARNESS_TARGETS, run_harness
from fuzz.runner import iter_corpus_cases
from src.services.webhooks.protocol import WebhookRequest


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
