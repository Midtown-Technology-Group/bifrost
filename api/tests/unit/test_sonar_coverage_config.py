"""Sonar reports retain real hits and unexecuted authored source across mounts."""

from configparser import ConfigParser
from pathlib import Path
import shlex
import subprocess
import sys
from xml.etree import ElementTree

import coverage
import pytest


API_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = "def choose(flag):\n    if flag:\n        return 1\n    return 2\n\nchoose(True)\n"


def test_sonar_collection_and_reporting_keep_the_same_exclusions():
    collect = ConfigParser()
    collect.read(API_ROOT / ".coveragerc.sonar")
    report = ConfigParser()
    report.read(API_ROOT / ".coveragerc.sonar-report")

    assert collect.get("run", "omit") == report.get("run", "omit")
    assert collect.getboolean("run", "branch")
    assert report.getboolean("run", "branch")
    assert not collect.getboolean("run", "relative_files")
    assert report.getboolean("run", "relative_files")
    assert report.getboolean("report", "include_namespace_packages")


def test_sonar_maps_mounts_without_losing_hits_or_unimported_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app = tmp_path / "app"
    repo = tmp_path / "repo"
    data_file = tmp_path / ".coverage"
    measured = [
        "api/src/routers/example.py",
        "api/scripts/example.py",
        "doc_renderer_service/rendering.py",
        "scripts/example.py",
        ".claude/skills/example/helper.py",
    ]
    unimported = [
        "api/bifrost/cli.py",
        "api/src/worker/main.py",
        "api/src/services/execution/example.py",
        "api/alembic/versions/example.py",
        "api/bifrost.py",
        "api/_bifrost_workspace_effects.py",
        "scripts/unimported.py",
        "docs/fixtures/authored_helper.py",
    ]
    excluded = [
        "api/tests/unit/test_example.py",
        "scripts/test_example.py",
        "scripts/example_test.py",
        "scripts/conftest.py",
        "client/node_modules/vendor/helper.py",
        ".agents/skills/example/helper.py",
        "plugins/bifrost/skills/example/helper.py",
    ]
    for filename in measured + unimported + excluded:
        path = repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SAMPLE)
        if filename.startswith("api/"):
            mounted = app / filename.removeprefix("api/")
        elif filename.startswith(("doc_renderer_service/", ".claude/")):
            mounted = app / filename
        else:
            continue
        mounted.parent.mkdir(parents=True, exist_ok=True)
        mounted.write_text(SAMPLE)

    configs = {}
    for name in (".coveragerc.sonar", ".coveragerc.sonar-report"):
        config = tmp_path / name
        config.write_text(
            (API_ROOT / name)
            .read_text()
            .replace("/app", str(app))
            .replace("/repo", str(repo))
        )
        configs[name] = config

    monkeypatch.chdir(app)
    collector = coverage.Coverage(
        config_file=str(configs[".coveragerc.sonar"]), data_file=str(data_file)
    )
    collector.start()
    try:
        for filename in measured:
            if filename.startswith("api/"):
                path = app / filename.removeprefix("api/")
            elif filename.startswith(("doc_renderer_service/", ".claude/")):
                path = app / filename
            else:
                path = repo / filename
            exec(compile(path.read_text(), str(path), "exec"), {})
    finally:
        collector.stop()
        collector.save()

    monkeypatch.chdir(repo)
    report_path = tmp_path / "coverage.xml"
    workflow_path = next(
        root / ".github/workflows/sonar-coverage.yml"
        for root in (API_ROOT, *API_ROOT.parents)
        if (root / ".github/workflows/sonar-coverage.yml").is_file()
    )
    report_commands = [
        shlex.split(line.strip())
        for line in workflow_path.read_text().splitlines()
        if line.strip().startswith("coverage xml ")
    ]
    assert len(report_commands) == 1, "Expected one workflow XML export command"
    # Exercise the workflow's actual CLI flags, substituting only fixture paths.
    # Calling Coverage.xml_report directly would miss unsupported CLI options.
    command = report_commands[0]
    for original, replacement in (
        ("/app/.coveragerc.sonar-report", str(configs[".coveragerc.sonar-report"])),
        ("/tmp/bifrost/.coverage.sonar", str(data_file)),
        ("/tmp/bifrost/coverage-sonar.xml", str(report_path)),
    ):
        command = [argument.replace(original, replacement) for argument in command]
    result = subprocess.run(
        [sys.executable, "-m", *command],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = ElementTree.parse(report_path)
    classes = report.findall(".//class")
    by_filename = {item.attrib["filename"]: item for item in classes}

    assert len(classes) == len(by_filename), "Container aliases must not duplicate files"
    assert set(by_filename) == set(measured + unimported)
    for filename in measured:
        assert float(by_filename[filename].attrib["line-rate"]) > 0
        assert float(by_filename[filename].attrib["branch-rate"]) == 0.5
    for filename in unimported:
        assert float(by_filename[filename].attrib["line-rate"]) == 0
        assert float(by_filename[filename].attrib["branch-rate"]) == 0
