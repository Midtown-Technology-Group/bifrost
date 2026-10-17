from pathlib import Path

import pytest

from scripts import author_verify as author
from scripts import check_regression_evidence as regression
from scripts import plan_affected_tests as affected


def test_changed_helper_selects_direct_consumers_without_weakening_broad_plan(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(author, "ROOT", tmp_path)
    for path in [
        "tests/unit/test_helper.py",
        "tests/unit/test_preview.py",
        "tests/unit/test_rename.py",
    ]:
        target = tmp_path / "api" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    plan = affected.AffectedPlan(
        "affected",
        "graph",
        ("api/src/helper.py",),
        python=affected.SurfacePlan(
            unit_tests=("tests/unit/test_helper.py", "tests/unit/test_preview.py")
        ),
    )
    selected = author.selection(plan, [], ["tests/unit/test_rename.py"])
    assert selected["commands"] == [
        [
            "./test.sh",
            "tests/unit/test_helper.py",
            "tests/unit/test_preview.py",
            "tests/unit/test_rename.py",
            "-v",
            "--durations=0",
        ]
    ]
    broad = affected.AffectedPlan("comprehensive", "migration", ("test.sh",))
    selected = author.selection(broad, [], ["tests/unit/test_helper.py"])
    assert len(selected["deferred"]) == 7
    assert broad.scope == "comprehensive"


def test_missing_mapping_or_unsafe_selector_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(author, "ROOT", tmp_path)
    plan = affected.AffectedPlan(
        "affected", "sdk", ("api/src/services/file_storage/azure_blob_client.py",)
    )
    with pytest.raises(ValueError, match="missing or unsafe"):
        author.selection(plan, [], [])
    with pytest.raises(ValueError, match="unsupported"):
        author.selection(
            affected.AffectedPlan("docs-only", "docs", ()), [], ["--ignore=tests"]
        )


def test_junit_preserves_skip_reason_and_distinguishes_errors(tmp_path):
    report = tmp_path / "cases.xml"
    report.write_text(
        '<testsuite><testcase classname="test" name="ok"/><testcase classname="test" name="optional"><skipped message="missing provider key"/></testcase><testcase classname="test" name="broken"><error message="fixture signature"/></testcase></testsuite>'
    )
    cases = author.junit_cases(report)
    assert [(c["name"], c["outcome"], c["reason"]) for c in cases] == [
        ("test::ok", "passed", ""),
        ("test::optional", "skipped", "missing provider key"),
        ("test::broken", "error", "fixture signature"),
    ]


@pytest.mark.parametrize(
    "red",
    [
        '<error message="fixture"/>',
        '<skipped message="key"/>',
        "",
        '<failure message="ImportError: expected sentinel"/>',
        '<failure message="different behavior"/>',
    ],
)
def test_regression_rejects_nonbehavioral_or_wrong_failure(tmp_path, red):
    old, new = _reports(tmp_path, red)
    with pytest.raises(ValueError):
        regression.check(old, new, "test.boundary::test_contract", "expected sentinel")


def _reports(tmp_path: Path, failure: str):
    old, new = tmp_path / "red.xml", tmp_path / "green.xml"
    old.write_text(
        f'<testsuite><testcase classname="test.boundary" name="test_contract">{failure}</testcase></testsuite>'
    )
    new.write_text(
        '<testsuite><testcase classname="test.boundary" name="test_contract"/></testsuite>'
    )
    return old, new


def test_regression_accepts_expected_failure_and_same_case_success(tmp_path):
    old, new = _reports(
        tmp_path, '<failure message="AssertionError: expected sentinel"/>'
    )
    regression.check(old, new, "test.boundary::test_contract", "expected sentinel")
    with pytest.raises(ValueError, match="exactly one"):
        regression.check(old, new, "test.boundary::absent", "expected sentinel")


def test_comprehensive_plan_still_runs_changed_regression(monkeypatch, tmp_path):
    monkeypatch.setattr(author, "ROOT", tmp_path)
    path = tmp_path / "api/tests/unit/test_new.py"
    path.parent.mkdir(parents=True)
    path.touch()
    plan = affected.AffectedPlan(
        "comprehensive", "selector changed", ("test.sh", "api/tests/unit/test_new.py")
    )
    selected = author.selection(plan, [], [])
    assert selected["commands"] == [
        ["./test.sh", "tests/unit/test_new.py", "-v", "--durations=0"]
    ]
    assert selected["deferred"]


def test_runner_does_not_credit_stale_junit_or_successful_exit_without_cases(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        author.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result, _ = author.run_command(
        ["./test.sh", "tests/unit/test_boundary.py"],
        tmp_path,
        0,
        f"absent-{tmp_path.name}",
    )
    assert result["outcome"] == "missing-test-evidence"
    assert result["cases"] == []


def test_unit_neighbors_and_integration_have_separate_runner_lifecycles(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(author, "ROOT", tmp_path)
    targets = [
        "tests/unit/test_a.py",
        "tests/unit/test_b.py",
        "tests/e2e/test_transaction.py",
        "tests/contract/test_sdk.py",
    ]
    for target in targets:
        path = tmp_path / "api" / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    plan = affected.AffectedPlan(
        "affected",
        "graph",
        (),
        python=affected.SurfacePlan(
            unit_tests=tuple(targets[:2]), e2e_tests=(targets[2],)
        ),
    )
    commands = author.selection(plan, [], [targets[3]])["commands"]
    assert commands == [
        ["./test.sh", *targets[:2], "-v", "--durations=0"],
        ["./test.sh", *sorted(targets[2:]), "-v", "--durations=0"],
    ]


def test_runner_rejects_stale_report_and_failed_case_even_with_exit_zero(
    monkeypatch, tmp_path
):
    import os
    from types import SimpleNamespace

    report = tmp_path / "stale.xml"
    report.write_text('<testsuite><testcase classname="old" name="old"/></testsuite>')
    monkeypatch.setattr(author, "Path", lambda _path: report)
    monkeypatch.setattr(
        author.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    command = ["./test.sh", "tests/unit/test_boundary.py"]
    result, _ = author.run_command(command, tmp_path, 0, "fixture")
    assert result["outcome"] == "missing-test-evidence"

    def run(*args, **kwargs):
        previous = report.stat().st_mtime_ns
        report.write_text(
            '<testsuite><testcase classname="new" name="boundary"><failure message="persistence absent"/></testcase></testsuite>'
        )
        os.utime(report, ns=(previous + 1, previous + 1))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(author.subprocess, "run", run)
    result, _ = author.run_command(command, tmp_path, 1, "fixture")
    assert result["outcome"] == "failed"
    assert result["cases"][0]["name"] == "new::boundary"


@pytest.mark.parametrize(
    "changed",
    [
        "client/src/lib/app-sdk/index.v2.ts",
        "client/src/lib/v1.d.ts",
        "api/src/services/operation_catalog.py",
        "api/src/models/contracts/agents.py",
    ],
)
def test_source_contracts_always_select_generated_checks(
    monkeypatch, tmp_path, changed
):
    monkeypatch.setattr(author, "ROOT", tmp_path)
    monkeypatch.setitem(author.RISK_TESTS, "contracts", [])
    plan = affected.AffectedPlan("comprehensive", "contract", (changed,))
    assert ["./test.sh", "author-generated"] in author.selection(plan, [], [])[
        "commands"
    ]


def test_runner_marks_provider_key_skip_incomplete_even_with_exit_zero(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    report = tmp_path / "skipped.xml"
    monkeypatch.setattr(author, "Path", lambda _path: report)

    def run(*args, **kwargs):
        report.write_text(
            '<testsuite><testcase classname="authorization" name="required"><skipped message="missing provider key"/></testcase></testsuite>'
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(author.subprocess, "run", run)
    result, gaps = author.run_command(
        ["./test.sh", "tests/unit/test_boundary.py"], tmp_path, 0, "fixture"
    )
    assert result["outcome"] == "incomplete"
    assert result["cases"][0]["reason"] == "missing provider key"
    assert gaps
