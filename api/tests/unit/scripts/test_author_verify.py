import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
    docs = affected.AffectedPlan("docs-only", "docs", ())
    with pytest.raises(ValueError, match="unsupported"):
        author.selection(docs, [], ["--ignore=tests"])


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
    monkeypatch.setenv("BIFROST_AUTHOR_REPORT_DIR", str(tmp_path / "bifrost-fixture"))
    result, _ = author.run_command(
        ["./test.sh", "tests/unit/test_boundary.py"], tmp_path, 0, "fixture"
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

    monkeypatch.setenv("BIFROST_AUTHOR_REPORT_DIR", str(tmp_path / "bifrost-fixture"))
    report = tmp_path / "bifrost-fixture" / "test-results.xml"
    report.parent.mkdir()
    report.write_text('<testsuite><testcase classname="old" name="old"/></testsuite>')
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

    monkeypatch.setenv("BIFROST_AUTHOR_REPORT_DIR", str(tmp_path / "bifrost-fixture"))
    report = tmp_path / "bifrost-fixture" / "test-results.xml"
    report.parent.mkdir()

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


@pytest.mark.parametrize("prefix", [None, "", "author-review"])
def test_receipt_reads_junit_from_configured_project(monkeypatch, tmp_path, prefix):
    import hashlib
    import json
    import sys
    from types import SimpleNamespace

    if prefix is None:
        monkeypatch.delenv("BIFROST_PROJECT_PREFIX", raising=False)
    else:
        monkeypatch.setenv("BIFROST_PROJECT_PREFIX", prefix)
    monkeypatch.setattr(author, "ROOT", tmp_path)
    target = "tests/unit/test_boundary.py"
    source = tmp_path / "api" / target
    source.parent.mkdir(parents=True)
    source.touch()
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(
        author,
        "git",
        lambda *args: str(evidence) if args[-1] == "author-verification" else "base",
    )
    monkeypatch.setattr(
        author, "identity", lambda _base: {"head_sha": "head", "base_sha": "base"}
    )
    monkeypatch.setattr(author, "changes", lambda _base: ())
    monkeypatch.setattr(
        affected,
        "plan_changes",
        lambda _changes: affected.AffectedPlan("docs-only", "fixture", ()),
    )
    monkeypatch.setattr(
        sys, "argv", ["author", "--test", target, "--boundary", "mocked runner"]
    )
    expected_project = (
        (prefix or "bifrost-test")
        + "-"
        + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:8]
    )
    monkeypatch.setenv(
        "BIFROST_AUTHOR_REPORT_DIR", str(tmp_path / ("bifrost-" + expected_project))
    )
    report = tmp_path / ("bifrost-" + expected_project) / "test-results.xml"
    report.parent.mkdir()

    def run(*args, **kwargs):
        report.write_text(
            '<testsuite><testcase classname="boundary" name="persisted"/></testsuite>'
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(author.subprocess, "run", run)
    try:
        assert author.main() == 0
        receipt = json.loads(next(evidence.glob("*/receipt.json")).read_text())
        assert receipt["compose_project"] == expected_project
        assert receipt["commands"][0]["cases"][0]["name"] == "boundary::persisted"
    finally:
        report.unlink(missing_ok=True)
        report.parent.rmdir()


@pytest.mark.parametrize(
    "ref", ["--output=receipt", "-h", "main;touch sentinel", "", "main\nHEAD"]
)
def test_base_operand_rejects_options_and_untrusted_syntax(monkeypatch, ref):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid base reached Git")

    monkeypatch.setattr(author.subprocess, "check_output", forbidden)
    with pytest.raises(ValueError, match="base must"):
        author.resolve_base(ref)


def test_identity_tracks_git_changes_content_modes_and_symlinks(monkeypatch, tmp_path):
    monkeypatch.setattr(author, "ROOT", tmp_path)
    author.git("init", "-b", "main")
    tracked = tmp_path / "tracked.py"
    deleted = tmp_path / "deleted.py"
    tracked.write_text("before")
    deleted.write_text("keep")
    author.git("add", ".")
    author.git(
        "-c",
        "user.name=Test Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    base = author.resolve_base("HEAD")
    assert author.identity(base)["dirty"] is False
    tracked.write_text("after")
    deleted.unlink()
    staged = tmp_path / "staged.py"
    staged.touch()
    author.git("add", "staged.py")
    untracked = tmp_path / "untracked.py"
    untracked.write_text("untracked")
    link = tmp_path / "link.py"
    link.symlink_to("tracked.py")
    changes = {change.path: change.status for change in author.changes(base)}
    assert changes == {
        "tracked.py": "M",
        "deleted.py": "D",
        "staged.py": "A",
        "untracked.py": "A",
        "link.py": "A",
    }
    before = author.identity(base)
    assert before["head_sha"] == base
    assert before["dirty"] is True
    untracked.write_text("changed untracked bytes")
    content = author.identity(base)
    assert content["changed_content_sha256"] != before["changed_content_sha256"]
    tracked.chmod(0o755)
    mode = author.identity(base)
    assert mode["changed_content_sha256"] != content["changed_content_sha256"]
    link.unlink()
    link.symlink_to("missing.py")
    assert (
        author.identity(base)["changed_content_sha256"]
        != mode["changed_content_sha256"]
    )


def test_client_consumers_and_native_report_gap_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(author, "ROOT", tmp_path)
    plan = affected.AffectedPlan(
        "affected", "client", ("client/src/widget.test.ts", "client/e2e/widget.spec.ts")
    )
    (tmp_path / "client/src").mkdir(parents=True)
    (tmp_path / "client/e2e").mkdir()
    (tmp_path / "client/src/widget.test.ts").touch()
    (tmp_path / "client/e2e/widget.spec.ts").touch()
    selected = author.selection(
        plan, [], ["client/src/consumer.test.ts", "client/e2e/consumer.spec.ts"]
    )
    assert selected["commands"] == [
        ["./test.sh", "client", "unit", "src/consumer.test.ts", "src/widget.test.ts"],
        ["./test.sh", "client", "e2e", "e2e/consumer.spec.ts", "e2e/widget.spec.ts"],
    ]
    monkeypatch.setattr(
        author.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    for command in selected["commands"]:
        result, gaps = author.run_command(command, tmp_path, 0, "fixture")
        assert result["outcome"] == "passed"
        assert result["cases"] == []
        assert "native client report" in gaps[0]


@pytest.mark.parametrize(
    "unsafe", ["directory-link", "report-link", "hard-link", "writable", "fifo"]
)
def test_report_rejects_substituted_links_and_unsafe_files(
    monkeypatch, tmp_path, unsafe
):
    monkeypatch.setenv("BIFROST_AUTHOR_REPORT_DIR", str(tmp_path / "bifrost-fixture"))
    directory = tmp_path / "bifrost-fixture"
    directory.mkdir()
    report = directory / "test-results.xml"
    original = tmp_path / "original.xml"
    original.write_text('<testsuite><testcase name="untrusted"/></testsuite>')
    if unsafe == "directory-link":
        directory.rmdir()
        directory.symlink_to(tmp_path, target_is_directory=True)
    elif unsafe == "report-link":
        report.symlink_to(original)
    elif unsafe == "hard-link":
        os.link(original, report)
    elif unsafe == "writable":
        report.write_text(original.read_text())
        report.chmod(0o666)
    else:
        os.mkfifo(report)
    with pytest.raises(OSError):
        author.read_runner_report("fixture")


def test_report_rejects_unsafe_project_and_handles_absent_report(monkeypatch, tmp_path):
    monkeypatch.setenv("BIFROST_AUTHOR_REPORT_DIR", str(tmp_path / "bifrost-fixture"))
    with pytest.raises(ValueError, match="unsafe Compose"):
        author.read_runner_report("../../elsewhere")
    assert author.read_runner_report("fixture") is None
    (tmp_path / "bifrost-fixture").mkdir()
    assert author.read_runner_report("fixture") is None
    assert author.junit_cases(tmp_path / "missing.xml") == []


@pytest.fixture
def receipt_cli(monkeypatch, tmp_path):
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(author, "ROOT", tmp_path)
    monkeypatch.setattr(author, "resolve_base", lambda _ref: "base")
    monkeypatch.setattr(author, "git", lambda *_args: str(evidence))
    monkeypatch.setattr(
        author, "identity", lambda _base: {"head_sha": "head", "base_sha": "base"}
    )
    monkeypatch.setattr(author, "changes", lambda _base: ())
    monkeypatch.setattr(
        affected,
        "plan_changes",
        lambda _changes: affected.AffectedPlan("affected", "fixture", ()),
    )
    commands = [
        ["./test.sh", "tests/unit/first.py"],
        ["./test.sh", "tests/unit/second.py"],
    ]
    monkeypatch.setattr(
        author,
        "selection",
        lambda *_args: {"commands": commands, "deferred": [], "risks": []},
    )
    monkeypatch.setattr(sys, "argv", ["author"])
    return evidence, commands


@pytest.mark.parametrize(
    "outcome,exit_code",
    [("failed", 7), ("incomplete", 0), ("missing-test-evidence", 0)],
)
def test_receipt_stops_after_nonpassing_command_and_names_unrun(
    receipt_cli, monkeypatch, outcome, exit_code
):
    evidence, commands = receipt_cli
    calls = []

    def run(command, *_args):
        calls.append(command)
        return {
            "argv": command,
            "outcome": outcome,
            "exit_code": exit_code,
            "cases": [],
        }, ["acceptance missing"]

    monkeypatch.setattr(author, "run_command", run)
    assert author.main() == (exit_code or 1)
    receipt = json.loads(next(evidence.glob("*/receipt.json")).read_text())
    assert calls == commands[:1]
    assert receipt["unrun_commands"] == commands[1:]
    assert receipt["outcome"] == "failed"
    assert "Mocked/real boundaries not identified" in receipt["acceptance_gaps"]


@pytest.mark.parametrize(
    "error",
    [
        KeyboardInterrupt(),
        OSError("runner stopped"),
        ValueError("invalid report"),
        author.ET.ParseError("bad report"),
    ],
)
def test_receipt_preserves_interruption_and_pending_commands(
    receipt_cli, monkeypatch, error
):
    evidence, commands = receipt_cli

    def run(*_args):
        raise error

    monkeypatch.setattr(author, "run_command", run)
    assert author.main() == 1
    receipt = json.loads(next(evidence.glob("*/receipt.json")).read_text())
    assert receipt["outcome"] == "interrupted-or-error"
    assert receipt["commands"][0]["outcome"] == "interrupted-or-error"
    assert receipt["unrun_commands"] == commands[1:]


def test_receipt_rejects_source_changes_during_verification(receipt_cli, monkeypatch):
    evidence, _ = receipt_cli
    identities = iter([{"head_sha": "before"}, {"head_sha": "after"}])
    monkeypatch.setattr(author, "identity", lambda _base: next(identities))
    monkeypatch.setattr(
        author,
        "run_command",
        lambda command, *_args: (
            {"argv": command, "outcome": "passed", "exit_code": 0, "cases": []},
            [],
        ),
    )
    assert author.main() == 1
    receipt = json.loads(next(evidence.glob("*/receipt.json")).read_text())
    assert (
        "Source changed during verification; evidence is invalid"
        in receipt["acceptance_gaps"]
    )


def test_plan_only_creates_no_receipt_or_execution(receipt_cli, monkeypatch, capsys):
    evidence, _ = receipt_cli
    monkeypatch.setattr(sys, "argv", ["author", "--plan"])
    assert author.main() == 0
    assert json.loads(capsys.readouterr().out)["identity"]["head_sha"] == "head"
    assert not evidence.exists()


@pytest.mark.parametrize("scope,expected", [("affected", 2), ("docs-only", 0)])
def test_empty_selection_cannot_credit_non_docs_behavior(
    receipt_cli, monkeypatch, scope, expected
):
    evidence, commands = receipt_cli
    commands.clear()
    monkeypatch.setattr(
        affected,
        "plan_changes",
        lambda _changes: affected.AffectedPlan(scope, "fixture", ()),
    )
    assert author.main() == expected
    receipt = json.loads(next(evidence.glob("*/receipt.json")).read_text())
    assert receipt["commands"] == []


@pytest.mark.parametrize("fixed", ["failure", "error", "skipped"])
def test_regression_rejects_nonpassing_fixed_case(tmp_path, fixed):
    old, new = _reports(tmp_path, '<failure message="expected sentinel"/>')
    new.write_text(
        f'<testsuite><testcase classname="test.boundary" name="test_contract"><{fixed}/></testcase></testsuite>'
    )
    with pytest.raises(ValueError, match="fixed implementation must pass"):
        regression.check(old, new, "test.boundary::test_contract", "expected sentinel")


def test_regression_rejects_duplicate_case_names(tmp_path):
    old, new = _reports(tmp_path, '<failure message="expected sentinel"/>')
    old.write_text(
        "<testsuite>"
        + '<testcase classname="test.boundary" name="test_contract"/>' * 2
        + "</testsuite>"
    )
    with pytest.raises(ValueError, match="found 2"):
        regression.check(old, new, "test.boundary::test_contract", "expected sentinel")


@pytest.mark.parametrize(
    "expected,exit_code", [("expected sentinel", 0), ("wrong behavior", 1)]
)
def test_regression_cli_reports_observed_behavior_or_rejects(
    tmp_path, monkeypatch, capsys, expected, exit_code
):
    old, new = _reports(tmp_path, '<failure message="expected sentinel"/>')
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "regression",
            "--red",
            str(old),
            "--green",
            str(new),
            "--test",
            "test.boundary::test_contract",
            "--expected",
            expected,
        ],
    )
    if exit_code:
        with pytest.raises(SystemExit) as raised:
            regression.main()
        assert raised.value.code == exit_code
        assert "rejected" in capsys.readouterr().err
    else:
        assert regression.main() == 0
        assert "Behavioral red/green observed" in capsys.readouterr().out


@pytest.mark.parametrize("owner_boundary", ["directory", "report"])
def test_report_rejects_unexpected_owner(monkeypatch, tmp_path, owner_boundary):
    directory = tmp_path / "bifrost-fixture"
    directory.mkdir()
    (directory / "test-results.xml").write_text("<testsuite/>")
    monkeypatch.setenv("BIFROST_AUTHOR_REPORT_DIR", str(directory))
    original_fstat = os.fstat

    def fstat(descriptor):
        info = original_fstat(descriptor)
        at_directory = author.stat.S_ISDIR(info.st_mode)
        if at_directory == (owner_boundary == "directory"):
            return SimpleNamespace(
                st_uid=98765, st_mode=info.st_mode, st_nlink=info.st_nlink
            )
        return info

    monkeypatch.setattr(author.os, "fstat", fstat)
    with pytest.raises(PermissionError, match="owner"):
        author.read_runner_report("fixture")


def test_report_requires_matching_runner_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("BIFROST_AUTHOR_REPORT_DIR", raising=False)
    with pytest.raises(OSError, match="supplied by test.sh"):
        author.read_runner_report("fixture")
    monkeypatch.setenv("BIFROST_AUTHOR_REPORT_DIR", str(tmp_path / "bifrost-other"))
    with pytest.raises(OSError, match="does not match"):
        author.read_runner_report("fixture")
