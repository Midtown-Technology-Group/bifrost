"""The CI benchmark fixtures must exercise valid execution response contracts."""

from benchmarks.test_issue_890_macro import (
    test_bench_execution_list_page as run_list_fixture,
    test_bench_nested_execution_result as run_nested_fixture,
)


def _once(function, *args):
    return function(*args)


def test_macro_fixtures_validate_and_serialize() -> None:
    run_list_fixture(_once)
    run_nested_fixture(_once)
