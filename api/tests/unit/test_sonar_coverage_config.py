"""Sonar reports retain real hits and unexecuted authored source across mounts."""

from configparser import ConfigParser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from xml.etree import ElementTree

import coverage
import pytest


API_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = "def choose(flag):\n    if flag:\n        return 1\n    return 2\n\nchoose(True)\n"


def _filter_module():
    spec = importlib.util.spec_from_file_location(
        "sonar_coverage_filter_under_test", API_ROOT / "scripts/filter_sonar_coverage.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    data_file = artifacts / ".coverage.sonar"
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
    template_name = "api/src/services/templates/sdk.py.j2"
    template = repo / template_name
    template.parent.mkdir(parents=True)
    template.write_text("{{ generated_python }}\n")
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
        # Real unit tests execute nonexistent virtual workflow filenames. They
        # must not make XML export fail or hide missing repository source.
        exec(compile(SAMPLE, str(app / "features/archive/new_helper.py"), "exec"), {})
        # Jinja records generated Python against the original template name;
        # those line numbers are not template-source coverage.
        exec(compile(SAMPLE, str(app / template_name.removeprefix("api/")), "exec"), {})
        # Both container aliases can measure the same tracked source.
        duplicate = repo / measured[0]
        exec(compile(duplicate.read_text(), str(duplicate), "exec"), {})
    finally:
        collector.stop()
        collector.save()

    monkeypatch.chdir(repo)
    report_path = tmp_path / "coverage.xml"
    filtered_path = artifacts / ".coverage.sonar-tracked"
    listing = repo / ".sonar-evidence/raw/tracked-files.nul"
    listing.parent.mkdir(parents=True)
    listing.write_bytes(("\0".join(measured + unimported + excluded + [template_name]) + "\0").encode())
    workflow_path = next(
        root / ".github/workflows/sonar-coverage.yml"
        for root in (API_ROOT, *API_ROOT.parents)
        if (root / ".github/workflows/sonar-coverage.yml").is_file()
    )
    filter_commands = [
        shlex.split(line.strip())
        for line in workflow_path.read_text().splitlines()
        if line.strip().startswith("python /repo/api/scripts/filter_sonar_coverage.py")
    ]
    assert filter_commands == [["python", "/repo/api/scripts/filter_sonar_coverage.py"]]
    module = _filter_module()
    assert (module.REPOSITORY_ROOT, module.API_ROOT, module.ARTIFACT_ROOT) == (
        Path("/repo"), Path("/app"), Path("/tmp/bifrost")
    )
    # Fixture-only injection; production accepts no CLI/environment overrides.
    module.REPOSITORY_ROOT, module.API_ROOT, module.ARTIFACT_ROOT = repo, app, artifacts
    assert module.main(filter_commands[0][2:]) == 0
    audit = json.loads((artifacts / module.AUDIT_NAME).read_text())
    assert filtered_path.stat().st_mode & 0o777 == 0o600
    assert (artifacts / module.AUDIT_NAME).stat().st_mode & 0o777 == 0o600
    assert audit["input_sha256"] == hashlib.sha256(data_file.read_bytes()).hexdigest()
    assert audit["output_sha256"] == hashlib.sha256(filtered_path.read_bytes()).hexdigest()
    assert audit["tracked_inventory_sha256"] == hashlib.sha256(listing.read_bytes()).hexdigest()
    assert audit["discarded_runtime_only_files"] == ["api/features/archive/new_helper.py"]
    assert audit["discarded_compiled_template_source_sha256"] == {
        template_name: hashlib.sha256(template.read_bytes()).hexdigest()
    }
    assert audit["retained_canonical_files"] == len(measured + unimported)
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
        ("/tmp/bifrost/.coverage.sonar-tracked", str(filtered_path)),
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
    assert set(by_filename) == set(measured + unimported), set(by_filename)
    for filename in measured:
        assert float(by_filename[filename].attrib["line-rate"]) > 0
        assert float(by_filename[filename].attrib["branch-rate"]) == 0.5
    for filename in unimported:
        assert float(by_filename[filename].attrib["line-rate"]) == 0
        assert float(by_filename[filename].attrib["branch-rate"]) == 0


def test_sonar_inventory_rejects_missing_or_unsafe_tracked_source(tmp_path: Path):
    module = _filter_module()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "source.py").write_text(SAMPLE)
    listing = tmp_path / "tracked.nul"
    for raw in (b"", b"source.py", b"source.py\0source.py\0", b"../source.py\0",
                b"/source.py\0", b"a\\b.py\0", b"a\nb.py\0", b"\0", b"only.json\0",
                b"missing.py\0"):
        listing.write_bytes(raw)
        with pytest.raises(ValueError):
            module.tracked_python(root, listing.read_bytes())
    (root / "linked.py").symlink_to(root / "source.py")
    listing.write_bytes(b"linked.py\0")
    with pytest.raises(ValueError, match="Missing or unsafe"):
        module.tracked_python(root, listing.read_bytes())
    listing.write_bytes(b"source.py\0")
    assert module.tracked_python(root, listing.read_bytes())[0] == {"source.py"}


def test_sonar_compiled_template_audit_rejects_unsafe_source_or_tracer(tmp_path: Path):
    module = _filter_module()
    root = tmp_path / "repo"
    template_name = "api/src/services/templates/sdk.py.j2"
    template = root / template_name
    template.parent.mkdir(parents=True)
    source = root / "source.py"
    source.write_text(SAMPLE)
    listing = tmp_path / "tracked.nul"
    listing.write_bytes(f"source.py\0{template_name}\0".encode())
    data_file = tmp_path / "data"
    data = coverage.CoverageData(basename=str(data_file))
    data.add_arcs({str(source): {(1, 2)}, str(template): {(1, 2)}})
    data.write()
    for linked in (False, True):
        if linked:
            template.symlink_to(source)
        with pytest.raises(OSError):
            module.filter_data(root, tmp_path / "app", listing.read_bytes(), data)
    template.unlink()
    template.write_text("{{ generated_python }}\n")
    data.add_file_tracers({str(template): "unreviewed-template-plugin"})
    data.write()
    with pytest.raises(ValueError, match="Custom template file tracers"):
        module.filter_data(root, tmp_path / "app", listing.read_bytes(), data)


def test_sonar_measured_paths_do_not_escape_mounts(tmp_path: Path):
    module = _filter_module()
    repo, app = tmp_path / "repo", tmp_path / "app"
    assert module.repository_path(str(repo / "scripts/helper.py"), repo, app) == "scripts/helper.py"
    assert module.repository_path(str(app / "src/a.py"), repo, app) == "api/src/a.py"
    assert module.repository_path(str(app / "doc_renderer_service/a.py"), repo, app) == "doc_renderer_service/a.py"
    assert module.repository_path(str(app / ".claude/a.py"), repo, app) == ".claude/a.py"
    assert module.repository_path(str(tmp_path / "other.py"), repo, app) is None
    for filename in ("relative.py", str(app / "../outside.py")):
        with pytest.raises(ValueError):
            module.repository_path(filename, repo, app)


def test_sonar_filter_preserves_source_and_rejects_invalid_data(tmp_path: Path):
    module = _filter_module()
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "source.py"
    source.write_text(SAMPLE)
    inventory = b"source.py\0metadata.json\0other.py.j2\0"
    data = coverage.CoverageData(no_disk=True)
    data.add_arcs({str(source): {(1, 2)}, str(tmp_path / "outside.py"): {(1, 2)}})
    arcs, audit = module.filter_data(root, tmp_path / "app", inventory, data)
    assert arcs == {str(source): {(1, 2)}}
    assert audit["retained_canonical_files"] == 1
    assert audit["discarded_outside_source_roots"] == 1
    source.unlink()
    with pytest.raises(ValueError, match="Missing or unsafe tracked"):
        module.filter_data(root, tmp_path / "app", inventory, data)
    source.write_text(SAMPLE)
    for name, records, tracer in (
        ("virtual", {str(root / "virtual.py"): {(1, 2)}}, False),
        ("nonpython", {str(root / "metadata.json"): {(1, 2)}}, False),
        ("other-template", {str(root / "other.py.j2"): {(1, 2)}}, False),
        ("tracer", {str(source): {(1, 2)}}, True),
    ):
        value = coverage.CoverageData(no_disk=True)
        value.add_arcs(records)
        if tracer:
            value.add_file_tracers({str(source): "unreviewed-plugin"})
        with pytest.raises(ValueError):
            module.filter_data(root, tmp_path / "app", inventory, value)
    lines = coverage.CoverageData(no_disk=True)
    lines.add_lines({str(source): {1, 2}})
    with pytest.raises(ValueError, match="branch coverage"):
        module.filter_data(root, tmp_path / "app", inventory, lines)


def test_sonar_artifact_io_cannot_traverse_follow_links_or_overwrite(tmp_path: Path):
    module = _filter_module()
    root = tmp_path / "artifacts"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    secret = tmp_path / "outside"
    secret.write_bytes(b"outside artifact boundary")
    module.write_fresh(root, "nested/valid", b"valid")
    assert module.read_bounded(root, "nested/valid") == b"valid"
    old_umask = os.umask(0o077)
    try:
        module.write_fresh(root, "private-report", b"report")
    finally:
        os.umask(old_umask)
    assert (root / "private-report").stat().st_mode & 0o777 == 0o600
    for name in ("../outside", str(secret), "a\\b", "a\nb", "", "."):
        with pytest.raises(ValueError):
            module.read_bounded(root, name)
        with pytest.raises(ValueError):
            module.write_fresh(root, name, b"invalid")
    (root / "leaf-link").symlink_to(secret)
    (root / "parent-link").symlink_to(nested, target_is_directory=True)
    linked_root = tmp_path / "root-link"
    linked_root.symlink_to(root, target_is_directory=True)
    for directory, name in ((root, "leaf-link"), (root, "parent-link/valid"),
                            (linked_root, "nested/valid")):
        with pytest.raises(OSError):
            module.read_bounded(directory, name)
        with pytest.raises(OSError):
            module.write_fresh(directory, name, b"invalid")
    with pytest.raises(FileExistsError):
        module.write_fresh(root, "nested/valid", b"replacement")
    assert secret.read_bytes() == b"outside artifact boundary"
    assert module.read_bounded(root, "nested/valid") == b"valid"
    (root / "empty").touch()
    os.mkfifo(root / "fifo")
    for name in ("empty", "fifo", "nested"):
        with pytest.raises(ValueError):
            module.read_bounded(root, name)
    with pytest.raises(FileNotFoundError):
        module.read_bounded(root, "missing")
    module.MAX_ARTIFACT_BYTES = 3
    with pytest.raises(ValueError, match="bounded regular file"):
        module.read_bounded(root, "nested/valid")


def test_sonar_cli_rejects_all_artifact_path_overrides(tmp_path: Path):
    module = _filter_module()
    for option in ("--repository-root", "--api-root", "--tracked-files", "--input", "--output", "--audit"):
        with pytest.raises(SystemExit) as error:
            module.main([option, str(tmp_path / "untrusted")])
        assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []
