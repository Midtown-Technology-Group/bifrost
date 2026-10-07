"""Sonar reports retain real hits and unexecuted authored source across mounts."""

import hashlib
import importlib.util
import json
import os
import runpy
import shlex
import subprocess
import sys
import textwrap
from configparser import ConfigParser
from pathlib import Path
from xml.etree import ElementTree

import coverage
import pytest
from coverage.exceptions import CoverageException

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
    assert collect.getboolean("run", "sigterm")
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
        Path("/repo"), Path("/app"), Path("/bifrost-results")
    )
    compose = workflow_path.parents[2] / "docker-compose.test.yml"
    model = compose.read_text()
    # Both fixed aliases refer to the same task-owned host results directory.
    assert "- ${LOG_DIR:-/tmp/bifrost}:/tmp/bifrost" in model
    assert "- ${LOG_DIR:-/tmp/bifrost}:/bifrost-results" in model
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
        ("/bifrost-results/.coverage.sonar-tracked", str(filtered_path)),
        ("/bifrost-results/coverage-sonar.xml", str(report_path)),
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
            module.tracked_python(root, raw)
    (root / "linked.py").symlink_to(root / "source.py")
    listing.write_bytes(b"linked.py\0")
    raw = listing.read_bytes()
    with pytest.raises(ValueError, match="Missing or unsafe"):
        module.tracked_python(root, raw)
    listing.write_bytes(b"source.py\0")
    raw = listing.read_bytes()
    assert module.tracked_python(root, raw)[0] == {"source.py"}


def test_sonar_compiled_template_audit_rejects_unsafe_source_or_tracer(tmp_path: Path):
    module = _filter_module()
    root = tmp_path / "repo"
    template_name = "api/src/services/templates/sdk.py.j2"
    template = root / template_name
    template.parent.mkdir(parents=True)
    source = root / "source.py"
    source.write_text(SAMPLE)
    listing = tmp_path / "tracked.nul"
    inventory = f"source.py\0{template_name}\0".encode()
    listing.write_bytes(inventory)
    app = tmp_path / "app"
    data_file = tmp_path / "data"
    data = coverage.CoverageData(basename=str(data_file))
    data.add_arcs({str(source): {(1, 2)}, str(template): {(1, 2)}})
    data.write()
    for linked in (False, True):
        if linked:
            template.symlink_to(source)
        with pytest.raises(OSError):
            module.filter_data(root, app, inventory, data)
    template.unlink()
    template.write_text("{{ generated_python }}\n")
    data.add_file_tracers({str(template): "unreviewed-template-plugin"})
    data.write()
    with pytest.raises(ValueError, match="Custom template file tracers"):
        module.filter_data(root, app, inventory, data)


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


def test_sonar_private_artifacts_transfer_on_export_failure(tmp_path: Path):
    workflow_path = next(
        root / ".github/workflows/sonar-coverage.yml"
        for root in (API_ROOT, *API_ROOT.parents)
        if (root / ".github/workflows/sonar-coverage.yml").is_file()
    )
    workflow = workflow_path.read_text()
    start = workflow.index("      - name: Preserve Python diagnostics after teardown")
    end = workflow.index("      - name:", start + 1)
    block = workflow[start:end]
    assert "if: always() && env.SONAR_LOG_DIR != ''" in block
    assert start > workflow.index("      - name: Tear down task-owned stack")
    assert workflow.count("sudo chown --no-dereference") == 1
    start = block.index("          for private_file in ")
    end = block.index("          done\n", start) + len("          done\n")
    script = textwrap.dedent(block[start:end])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "calls"
    sudo = bin_dir / "sudo"
    sudo.write_text("#!/bin/sh\nprintf '%s\\0' \"$@\" >> \"$CAPTURE\"\n")
    sudo.chmod(0o700)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    environment = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                       SONAR_LOG_DIR=str(artifacts), CAPTURE=str(capture), REPORT_STATUS="failure")
    audit = artifacts / "sonar-runtime-coverage.json"
    audit.write_text("{}")
    audit.chmod(0o600)
    for files in ((audit,), (artifacts / ".coverage.sonar-tracked", audit)):
        for path in files:
            path.touch()
            path.chmod(0o600)
        capture.unlink(missing_ok=True)
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], env=environment,
                                cwd=tmp_path, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        arguments = capture.read_bytes().decode().split("\0")[:-1]
        expected = []
        for path in files:
            expected += ["chown", "--no-dereference", f"{os.getuid()}:{os.getgid()}", str(path)]
            assert path.stat().st_mode & 0o777 == 0o600
        assert arguments == expected
    target = artifacts / ".coverage.sonar-tracked"
    target.unlink()
    target.symlink_to(audit)
    capture.unlink()
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], env=environment,
                            cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "Unsafe private Sonar diagnostic artifact" in result.stderr
    assert not capture.exists()


def _runtime_combiner(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "sonar_runtime_combiner_under_test", API_ROOT / "scripts/combine_sonar_coverage.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RUNTIME_DIR = tmp_path / "runtime"
    module.RUNTIME_DIR.mkdir()
    module.DATA_FILE = tmp_path / ".coverage.sonar"
    module.RC_FILE = tmp_path / "sonar.rc"
    module.RC_FILE.write_text("[run]\nbranch = True\n")
    return module


def test_sonar_combines_real_unit_and_runtime_branch_hits(tmp_path: Path) -> None:
    module = _runtime_combiner(tmp_path)
    source = tmp_path / "sample.py"
    source.write_text("def choose(flag):\n    if flag:\n        return 1\n    return 2\n")
    for flag in (True, False):
        data_file = module.DATA_FILE if flag else module.RUNTIME_DIR / ".coverage.sonar"
        collector = coverage.Coverage(
            config_file=False, branch=True, data_file=str(data_file), data_suffix=not flag,
            source=[str(tmp_path)],
        )
        collector.start()
        try:
            namespace = runpy.run_path(str(source))
            assert namespace["choose"](flag) == (1 if flag else 2)
        finally:
            collector.stop()
            collector.save()
    assert module.main() == 0
    report = coverage.Coverage(config_file=False, branch=True, data_file=str(module.DATA_FILE))
    report.load()
    xml = tmp_path / "combined.xml"
    report.xml_report(outfile=str(xml))
    measured = ElementTree.parse(xml).find(".//class")
    assert measured is not None
    assert measured.attrib["line-rate"] == "1"
    assert measured.attrib["branch-rate"] == "1"
    assert len(list(module.RUNTIME_DIR.glob(".coverage.sonar.*"))) == 1


def test_sonar_runtime_absence_fails_even_with_unit_data(tmp_path: Path) -> None:
    module = _runtime_combiner(tmp_path)
    data = coverage.CoverageData(basename=str(module.DATA_FILE))
    data.add_arcs({str(tmp_path / "sample.py"): [(1, 2)]})
    data.write()
    original = module.DATA_FILE.read_bytes()
    with pytest.raises(ValueError, match="actual runtime"):
        module.main()
    assert module.DATA_FILE.read_bytes() == original


def test_sonar_runtime_mount_and_workflow_use_the_tested_combiner() -> None:
    root = API_ROOT
    compose = (root / "docker-compose.sonar.yml").read_text()
    runner = compose.split("  test-runner:\n", 1)[1]
    assert "- test-coverage:/coverage" in runner
    workflow = (root / ".github/workflows/sonar-coverage.yml").read_text()
    assert "python /app/scripts/combine_sonar_coverage.py" in workflow
    assert "coverage combine --strict" not in workflow


@pytest.mark.parametrize("invalid", ["corrupt", "incompatible"])
def test_sonar_invalid_report_is_not_skipped_when_another_report_is_valid(
    tmp_path: Path, invalid: str
) -> None:
    module = _runtime_combiner(tmp_path)
    source = str(tmp_path / "sample.py")
    unit = coverage.CoverageData(basename=str(module.DATA_FILE))
    unit.add_arcs({source: [(1, 2)]})
    unit.write()
    original = module.DATA_FILE.read_bytes()
    valid = coverage.CoverageData(basename=str(module.RUNTIME_DIR / ".coverage.sonar.valid"))
    valid.add_arcs({source: [(2, 3)]})
    valid.write()
    invalid_path = module.RUNTIME_DIR / ".coverage.sonar.invalid"
    if invalid == "corrupt":
        invalid_path.write_bytes(b"not a coverage database")
    else:
        incompatible = coverage.CoverageData(basename=str(invalid_path))
        incompatible.add_lines({source: [1]})
        incompatible.write()
    with pytest.raises((ValueError, CoverageException)):
        module.main()
    assert module.DATA_FILE.read_bytes() == original
    assert invalid_path.exists()
