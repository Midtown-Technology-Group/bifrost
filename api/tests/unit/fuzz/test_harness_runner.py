from pathlib import Path

import pytest

from fuzz import cfl_entrypoint
from fuzz.harnesses import HARNESS_TARGETS, run_harness
from fuzz.runner import iter_corpus_cases
from src.services.editor.search import _search_content


def test_search_output_budget_corpus_preserves_resource_rejection():
    case = next(case for case in iter_corpus_cases() if case.path.name == "output-budget.bin")
    text = case.data.decode("utf-8", errors="replace")
    with pytest.raises(ValueError, match="Search exceeded the output character budget"):
        _search_content(text[64:], "fuzz.txt", text[:64], case_sensitive=False, is_regex=True)
    run_harness(case.target, case.data)


def test_search_harness_does_not_mask_unexpected_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("Unexpected search failure")

    monkeypatch.setattr("fuzz.harnesses._search_content", fail)
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


def test_corpus_case_ids_are_stable_relative_paths():
    for case in iter_corpus_cases():
        assert case.id == str(Path(case.target) / case.path.name)
        assert case.path.is_file()


def test_cfl_entrypoint_runs_file_and_directory_inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    standalone_seed = tmp_path / "standalone.txt"
    standalone_seed.write_bytes(b"standalone")

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    first_seed = corpus_dir / "01.txt"
    second_seed = corpus_dir / "02.txt"
    first_seed.write_bytes(b"first")
    second_seed.write_bytes(b"second")

    calls = []
    monkeypatch.setattr(
        cfl_entrypoint,
        "run_harness",
        lambda target, data: calls.append((target, data)),
    )

    result = cfl_entrypoint.main(
        [
            "cfl_entrypoint.py",
            "cron_parser_fuzzer",
            "-runs=1",
            standalone_seed.name,
            corpus_dir.name,
        ]
    )

    assert result == 0
    assert calls == [
        ("cron-parser", b"standalone"),
        ("cron-parser", b"first"),
        ("cron-parser", b"second"),
    ]


def test_cfl_entrypoint_runs_empty_payload_when_no_corpus(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cfl_entrypoint,
        "run_harness",
        lambda target, data: calls.append((target, data)),
    )

    assert cfl_entrypoint.main(["cfl_entrypoint.py", "webhook_request_fuzzer"]) == 0
    assert calls == [("webhook-request", b"")]


def test_cfl_entrypoint_rejects_unknown_fuzzer():
    with pytest.raises(SystemExit, match="Unknown fuzzer 'missing_fuzzer'"):
        cfl_entrypoint.main(["cfl_entrypoint.py", "missing_fuzzer"])
