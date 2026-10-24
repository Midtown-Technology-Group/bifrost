"""Fault probes for manual coverage receipts, combination and equivalence."""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from scripts.sonar import shadow


def sqlite_data(path, files=None, version="7.13.5", branch=True):
    files = files if files is not None else {"/repo/api/src/service.py": {(-1, 1), (1, 2), (2, -1)}}
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("create table meta(key text, value text); create table file(id integer, path text); "
                                 "create table arc(file_id integer, fromno integer, tono integer);")
        connection.executemany("insert into meta values (?,?)", [("version", version), ("has_arcs", str(int(branch)))])
        for number, (name, arcs) in enumerate(files.items(), 1):
            connection.execute("insert into file values (?,?)", (number, name))
            connection.executemany("insert into arc values (?,?,?)", [(number, start, end) for start, end in arcs])
        connection.commit()


def junit_bytes(names, status="passed"):
    outcome = "" if status == "passed" else f"<{status}/>"
    return ("<testsuite>" + "".join(f'<testcase classname="tests.test_example" name="{name}">{outcome}</testcase>'
                                  for name in names) + "</testsuite>").encode()


def xml_bytes():
    return (b'<coverage lines-valid="2" lines-covered="2" branches-valid="0" branches-covered="0">'
            b'<sources><source>.</source></sources><packages><package><classes>'
            b'<class filename="api/src/service.py"><lines><line number="1" hits="1"/>'
            b'<line number="2" hits="1"/></lines></class></classes></package></packages></coverage>')


class ShadowFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in shadow.CONFIGS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        source = self.root / "api/src/service.py"
        source.parent.mkdir(parents=True)
        source.write_text("answer = 1\nother = 2\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c",
                 "commit.gpgsign=false", "commit", "-qm", "fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": shadow.REPOSITORY,
                    "GITHUB_REF": "refs/heads/pilot", "GITHUB_SHA": self.head, "GITHUB_WORKFLOW_SHA": self.head,
                    "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.image = "sha256:" + "a" * 64
        self.identity = shadow.context(self.root, self.image)
        (self.root / ".sonar-evidence").mkdir()
        shadow.write_json(self.root / ".sonar-evidence/shadow-context.json", self.identity)

    def git(self, *args):
        return subprocess.check_output(["/usr/bin/git", "-C", str(self.root), *args], text=True).strip()

    def bundle(self, parent, lane):
        directory = parent / lane
        directory.mkdir()
        (directory / "runtime").mkdir()
        roles = ["api", "api-replica", "worker", "scheduler", "scheduler-fixtures"]
        rows = []
        for index, role in enumerate(roles, 1):
            host = f"{index:012x}"
            rows.append(f"{role} {host}")
            sqlite_data(directory / "runtime" / f".coverage.sonar.{host}.1.random")
        (directory / "producers.txt").write_text("\n".join(rows) + "\n")
        (directory / "context.json").write_text(json.dumps(self.identity))
        sqlite_data(directory / "python.coverage")
        (directory / "python.xml").write_bytes(xml_bytes())
        phases = {"unit": ["fast", "slow"], "tools": ["tool"], "e2e": [f"e{i}" for i in range(1, 5)]}
        if lane == "unit":
            phases["unit"] = ["fast"]
        elif lane == "supplement":
            phases["unit"] = ["slow"]
        elif lane.startswith("e2e-"):
            phases["e2e"] = ["e" + lane[-1]]
        for phase in shadow.phase_names(lane):
            (directory / f"junit-{phase}.xml").write_bytes(junit_bytes(phases[phase]))
        files = {p.relative_to(directory).as_posix(): shadow.digest(p.read_bytes())
                 for p in directory.rglob("*") if p.is_file()}
        shadow.write_json(directory / "manifest.json", {"identity": self.identity, "status": "exported",
                          "lane": lane, "files": files, "runtime": shadow.validate_runtime(directory, lane)})
        return directory

    def jobs(self, control=False):
        result = []
        names = list(shadow.JOB_NAMES.values())
        names += ["Coverage Shadow Comparison"]
        for name in names:
            duration = 60 if control else 66
            result.append({"name": name, "conclusion": "success", "run_id": 30 if control else 42,
                           "run_attempt": 1, "head_sha": self.head,
                           "started_at": "2026-10-19T01:00:00Z", "completed_at": "2026-10-19T01:05:00Z",
                           "steps": [{"name": "Run unit tests" if name == "Unit Tests" else "Run " + name.replace("Tests", "tests"),
                                      "conclusion": "success", "started_at": "2026-10-19T01:00:00Z",
                                      "completed_at": f"2026-10-19T01:01:{duration-60:02d}Z"}]})
            if not control and name == "Coverage Shadow Comparison":
                result[-1]["conclusion"] = None
                result[-1]["started_at"] = "2026-10-19T01:05:00Z"
                result[-1]["completed_at"] = None
            if control and name.startswith("Coverage Shadow"):
                result[-1]["conclusion"] = "skipped"
            if name == shadow.JOB_NAMES["baseline"]:
                result[-1]["steps"] = [{"name": "Collect comprehensive Python coverage", "conclusion": "success",
                                        "started_at": "2026-10-19T01:00:00Z", "completed_at": "2026-10-19T01:45:00Z"}]
        return {"total_count": len(result), "jobs": result}

    def control_receipt(self):
        return {"head_sha": self.head, "event": "workflow_dispatch", "conclusion": "success", "id": 30,
                "run_attempt": 1, "repository": {"full_name": shadow.REPOSITORY}, "path": ".github/workflows/ci.yml"}


class IdentityTests(ShadowFixture):
    def test_context_binds_tree_config_image_and_attempt(self):
        self.assertEqual(self.identity["tree"], self.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(self.identity["configs"]["test.sh"], shadow.digest(b"fixture\n"))
        self.assertEqual(self.identity["image"], self.image)
        self.assertEqual(self.identity["attempt"], "1")

    def test_wrong_event_ref_repo_head_workflow_or_attempt_is_rejected(self):
        for key, value in [("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REF", "refs/heads/main"),
                           ("GITHUB_REF", "refs/tags/v1"), ("GITHUB_REPOSITORY", "other/repo"),
                           ("GITHUB_SHA", "a" * 40), ("GITHUB_WORKFLOW_SHA", "b" * 40),
                           ("GITHUB_RUN_ID", "../1"), ("GITHUB_RUN_ATTEMPT", "0")]:
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                shadow.context(self.root, self.image)

    def test_dirty_source_or_missing_image_is_rejected(self):
        with self.assertRaises(ValueError):
            shadow.context(self.root, "latest")
        (self.root / "test.sh").write_text("changed\n")
        with self.assertRaises(subprocess.CalledProcessError):
            shadow.context(self.root, self.image)

    def test_private_bounded_regular_files_and_no_overwrite(self):
        path = self.root / "receipt.json"
        shadow.write_json(path, {"proof": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        context_path = self.root / ".sonar-evidence/shadow-context.json"
        self.assertEqual(context_path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            shadow.write_json(path, {})
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            shadow.read_file(link)
        empty = self.root / "empty"
        empty.touch()
        with self.assertRaises(ValueError):
            shadow.read_file(empty)
        with empty.open("wb") as stream:
            stream.truncate(shadow.MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            shadow.read_file(empty)


class DataTests(ShadowFixture):
    def test_canonical_union_preserves_zero_hit_source_and_branches(self):
        old = self.root / "old"
        new = self.root / "new"
        sqlite_data(old, {"/repo/api/src/service.py": {(1, 2)}, "/repo/api/zero.py": set()})
        sqlite_data(new, {"api/src/service.py": {(1, 2), (2, -1)}, "api/zero.py": set()})
        self.assertEqual(shadow.coverage_equivalence(old, new), {"files": 2, "lost_arcs": 0, "additional_arcs": 1})

    def test_missing_arcs_source_or_incompatible_versions_fail(self):
        for replacement, version in [({"api/src/service.py": set()}, "7.13.5"),
                                     ({"api/other.py": {(1, 2)}}, "7.13.5"),
                                     ({"api/src/service.py": {(1, 2)}}, "7.12.0")]:
            with self.subTest(replacement=replacement, version=version), tempfile.TemporaryDirectory() as directory:
                old, new = Path(directory) / "old", Path(directory) / "new"
                sqlite_data(old, {"/repo/api/src/service.py": {(1, 2)}})
                sqlite_data(new, replacement, version)
                with self.assertRaises(ValueError):
                    shadow.coverage_equivalence(old, new)

    def test_line_only_empty_or_corrupt_databases_fail(self):
        for files, branch in [({}, True), ({"/repo/api/src/service.py": {(1, 2)}}, False)]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "data"
                sqlite_data(path, files, branch=branch)
                with self.assertRaises(ValueError):
                    shadow.database(path)
        path = self.root / "corrupt"
        path.write_bytes(b"not sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            shadow.database(path)

    def test_uninitialized_runtime_database_is_diagnostic_only(self):
        path = self.root / "uninitialized.coverage"
        sqlite_data(path, {})
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("delete from meta where key = 'has_arcs'")
            connection.execute("create table line_bits(file_id integer, context_id integer, numbits blob)")
            connection.commit()
        self.assertEqual(shadow.database(path, allow_empty=True), ("7.13.5", {}))
        with self.assertRaises(ValueError):
            shadow.database(path)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("insert into line_bits values (1, 1, X'01')")
            connection.commit()
        with self.assertRaises(ValueError):
            shadow.database(path, allow_empty=True)

    def test_measured_line_only_data_is_not_an_empty_diagnostic(self):
        path = self.root / "line-only.coverage"
        sqlite_data(path, {"/repo/api/src/service.py": set()}, branch=False)
        with self.assertRaises(ValueError):
            shadow.database(path, allow_empty=True)

    def test_unit_runtime_sidecars_may_be_empty_but_e2e_runtime_may_not(self):
        parent = self.root / "inputs"
        parent.mkdir()
        unit = self.bundle(parent, "unit")
        api_hosts = {line.split()[1] for line in (unit / "producers.txt").read_text().splitlines()
                     if line.split()[0] in {"api", "api-replica"}}
        for path in (unit / "runtime").iterdir():
            hostname = path.name.removeprefix(".coverage.sonar.").split(".")[0]
            if hostname in api_hosts:
                path.unlink()
                sqlite_data(path, {})
        runtime = shadow.validate_runtime(unit, "unit")
        self.assertCountEqual(runtime["empty_reports"], [
            path.name for path in (unit / "runtime").iterdir()
            if path.name.removeprefix(".coverage.sonar.").split(".")[0] in api_hosts
        ])

        e2e = self.bundle(parent, "e2e-1")
        path = next(path for path in (e2e / "runtime").iterdir()
                    if path.name.removeprefix(".coverage.sonar.").split(".")[0] in api_hosts)
        path.unlink()
        sqlite_data(path, {})
        with self.assertRaisesRegex(ValueError, "Empty runtime coverage database"):
            shadow.validate_runtime(e2e, "e2e-1")

    def test_unit_and_supplement_exports_skip_shared_runtime_combiner(self):
        script = Path(__file__).with_name("shadow.sh")
        helper = self.root / "scripts/lib/test_helpers.sh"
        helper.parent.mkdir(parents=True)
        helper.write_text('compute_project_name() { printf "shadow-test-%s" "$SHADOW_TEST_ID"; }\n')
        (self.root / ".sonar-evidence").mkdir(exist_ok=True)
        (self.root / ".sonar-evidence/shadow-context.json").write_text("{}\n")
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text(
            '''#!/bin/bash
printf '%s\\n' "$*" >> "$DOCKER_TRACE"
if [[ "$*" == *'ps -a'* ]]; then
  printf 'api 111111111111\\napi-replica 222222222222\\n'
fi
'''
        )
        docker.chmod(0o755)
        local_script = self.root / "scripts/sonar/shadow.sh"
        local_script.parent.mkdir(parents=True, exist_ok=True)
        local_script.write_bytes(script.read_bytes())
        env = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
               "DOCKER_TRACE": str(self.root / "docker.trace"), "COMPOSE_FILE": "fixture.yml"}
        run_id = uuid4().hex
        for lane in ("unit", "supplement", "e2e-1"):
            env["SHADOW_TEST_ID"] = f"{run_id}-{lane}"
            log_dir = Path(f"/tmp/bifrost-shadow-test-{run_id}-{lane}")
            log_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(["bash", str(local_script), "export", lane], cwd=self.root,
                                    env=env, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = (self.root / "docker.trace").read_text()
            self.assertEqual("combine_sonar_coverage.py" in commands, lane.startswith("e2e-"))
            (self.root / "docker.trace").unlink()
            shutil.rmtree(log_dir)

    def test_unexpected_source_roots_fail(self):
        for name in ["/app/src/file.py", "../outside.py", "a\\b.py"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                shadow.canonical_arcs({name: set()})

    def test_junit_preserves_skip_disposition_and_rejects_failure_empty_and_entities(self):
        path = self.root / "junit.xml"
        path.write_bytes(junit_bytes(["one"], "skipped"))
        self.assertEqual(sum(shadow.junit(path).values()), 1)
        for raw in [junit_bytes(["one"], "failure"), junit_bytes(["one"], "error"), b"<testsuite/>",
                    b'<!DOCTYPE x><testsuite/>']:
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                shadow.junit(path)

    def test_xml_denominator_uses_existing_source_aware_validator(self):
        from scripts.sonar import preflight
        tracked = {item["path"]: item for item in preflight.inventory(self.root, self.head, self.head)}
        path = self.root / "report.xml"
        path.write_bytes(xml_bytes())
        self.assertEqual(shadow.xml_denominator(path, self.root, tracked), {"api/src/service.py": {1: "0", 2: "0"}})
        for raw in [xml_bytes().replace(b'lines-valid="2"', b'lines-valid="3"'),
                    xml_bytes().replace(b'number="2"', b'number="999"'), b'<!ENTITY x><coverage/>']:
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                shadow.xml_denominator(path, self.root, tracked)


class BundleTests(ShadowFixture):
    def test_all_shards_tools_and_slow_tests_exactly_match_baseline(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        bundles = {lane: shadow.load_bundle(self.bundle(inputs, lane), self.identity) for lane in shadow.LANES}
        self.assertEqual(shadow.test_equivalence(bundles), {"test_inventories": "identical"})
        (bundles["supplement"]["directory"] / "junit-unit.xml").write_bytes(junit_bytes(["different"]))
        with self.assertRaises(ValueError):
            shadow.test_equivalence(bundles)

    def test_missing_stale_mixed_corrupt_or_unknown_bundle_fails(self):
        parent = self.root / "inputs"
        parent.mkdir()
        directory = self.bundle(parent, "unit")
        for field, value in [("head", "b" * 40), ("attempt", "2"), ("image", "sha256:" + "b" * 64),
                             ("configs", {}), ("run_id", "99")]:
            identity = self.identity | {field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                shadow.load_bundle(directory, identity)
        (directory / "python.xml").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            shadow.load_bundle(directory, self.identity)

    def test_missing_or_unattributable_runtime_reports_fail(self):
        parent = self.root / "inputs"
        parent.mkdir()
        directory = self.bundle(parent, "baseline")
        path = next((directory / "runtime").iterdir())
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            shadow.validate_runtime(directory, "baseline")
        path.write_bytes(original)
        (directory / "producers.txt").write_text("other aaaaaaaaaaaa\n")
        with self.assertRaises(ValueError):
            shadow.validate_runtime(directory, "baseline")

    def test_snapshot_retains_raw_bytes_before_missing_junit_failure(self):
        results = self.root / "results"
        runtime = self.root / "runtime"
        results.mkdir()
        runtime.mkdir()
        sqlite_data(results / ".coverage.sonar")
        raw = runtime / ".coverage.sonar.hostname.1.random"
        raw.write_bytes(b"corrupt-but-preserved")
        empty = runtime / ".coverage.sonar.empty.2.random"
        empty.touch()
        (results / "sonar-runtime-producers.txt").write_text("api hostname\n")
        context_raw = json.dumps(self.identity).encode()
        with self.assertRaises(ValueError):
            shadow.snapshot(results, runtime, "unit", context_raw)
        retained = results / "coverage-shadow-unit"
        self.assertEqual((retained / "runtime" / raw.name).read_bytes(), raw.read_bytes())
        self.assertTrue((retained / "test-runner.coverage").is_file())
        self.assertEqual((retained / "runtime" / empty.name).read_bytes(), b"")
        self.assertTrue((retained / "producers.txt").is_file())
        self.assertTrue((retained / "snapshot-failures.json").is_file())
        self.assertFalse((retained / "manifest.json").exists())

    def test_sealed_snapshot_keeps_originals_and_verified_manifest(self):
        parent = self.root / "input"
        parent.mkdir()
        source = self.bundle(parent, "unit")
        results = self.root / "results"
        results.mkdir()
        for old, name in [("python.coverage", ".coverage.sonar"),
                          ("python.coverage", ".coverage.sonar-tracked"),
                          ("python.xml", "coverage-sonar.xml"),
                          ("junit-unit.xml", "junit-unit.xml"), ("producers.txt", "sonar-runtime-producers.txt")]:
            shutil.copyfile(source / old, results / name)
        context_raw = json.dumps(self.identity).encode()
        shadow.snapshot(results, source / "runtime", "unit", context_raw)
        self.assertEqual((results / "coverage-shadow-unit/context.json").read_bytes(), context_raw)
        self.assertEqual((results / "coverage-shadow-unit/context.json").stat().st_mode & 0o777, 0o600)
        shadow.seal(self.root, results, "unit")
        loaded = shadow.load_bundle(results / "coverage-shadow-unit", self.identity)
        self.assertEqual(loaded["manifest"]["status"], "exported")
        self.assertTrue((results / ".coverage.sonar").exists())

    def test_corrupt_context_retains_raw_data_but_cannot_be_sealed(self):
        parent = self.root / "input"
        parent.mkdir()
        source = self.bundle(parent, "unit")
        results = self.root / "results"
        results.mkdir()
        for old, name in [("python.coverage", ".coverage.sonar"),
                          ("python.coverage", ".coverage.sonar-tracked"),
                          ("python.xml", "coverage-sonar.xml"),
                          ("junit-unit.xml", "junit-unit.xml"), ("producers.txt", "sonar-runtime-producers.txt")]:
            shutil.copyfile(source / old, results / name)
        shadow.snapshot(results, source / "runtime", "unit", b"corrupt stdin")
        with self.assertRaises(ValueError):
            shadow.seal(self.root, results, "unit")
        directory = results / "coverage-shadow-unit"
        self.assertTrue(list((directory / "runtime").iterdir()))
        self.assertEqual((directory / "context.json").read_bytes(), b"corrupt stdin")
        self.assertFalse((directory / "manifest.json").exists())

    def test_missing_extra_unknown_status_and_traversing_members_fail(self):
        parent = self.root / "input"
        parent.mkdir()
        directory = self.bundle(parent, "unit")
        manifest_path = directory / "manifest.json"
        original = json.loads(manifest_path.read_bytes())
        for update in [{"lane": "e2e-5"}, {"status": "pending"}, {"files": {"../outside": "fake"}}]:
            manifest_path.write_text(json.dumps(original | update))
            with self.subTest(update=update), self.assertRaises(ValueError):
                shadow.load_bundle(directory, self.identity)
        manifest_path.write_text(json.dumps(original))
        extra = directory / "unexpected"
        extra.write_text("extra")
        with self.assertRaises(ValueError):
            shadow.load_bundle(directory, self.identity)
        extra.unlink()
        (directory / "python.xml").unlink()
        with self.assertRaises(ValueError):
            shadow.load_bundle(directory, self.identity)

    def test_offline_compare_requires_every_producer_and_preserves_denominators(self):
        parent = self.root / "inputs"
        parent.mkdir()
        for lane in shadow.LANES:
            self.bundle(parent, lane)
        jobs, control, control_jobs = (self.root / name for name in ("jobs.json", "control.json", "control-jobs.json"))
        for path, value in [(jobs, self.jobs()), (control, self.control_receipt()), (control_jobs, self.jobs(True))]:
            shadow.write_json(path, value)

        def combine(_root, _bundles, output):
            data = output / ".coverage.combined"
            sqlite_data(data, {"api/src/service.py": {(-1, 1), (1, 2), (2, -1)}})
            (output / "shadow.xml").write_bytes(xml_bytes())
            return data

        output = self.root / "comparison"
        with patch.object(shadow, "combine", side_effect=combine):
            shadow.compare(self.root, parent, jobs, self.image, output, control, control_jobs)
        report = json.loads((output / "comparison.json").read_bytes())
        self.assertEqual(report["status"], "equivalent")
        self.assertEqual(report["lost_arcs"], 0)
        self.assertEqual(report["timing"]["control_run"], 30)
        for mode in ("missing", "duplicate"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as staging:
                staged = Path(staging) / "inputs"
                shutil.copytree(parent, staged)
                if mode == "missing":
                    shutil.rmtree(staged / "e2e-4")
                else:
                    shutil.copytree(staged / "unit", staged / "extra")
                with self.assertRaises(ValueError):
                    shadow.compare(self.root, staged, jobs, self.image, Path(staging) / "out", control, control_jobs)

    def test_provider_inventory_requires_complete_successful_exact_attempt(self):
        shadow.require_jobs(self.jobs(), self.identity)
        for field, value in [("conclusion", "failure"), ("run_attempt", 2), ("run_id", 99), ("head_sha", "a" * 40)]:
            jobs = self.jobs()
            jobs["jobs"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                shadow.require_jobs(jobs, self.identity)
        jobs = self.jobs()
        jobs["total_count"] += 1
        with self.assertRaises(ValueError):
            shadow.require_jobs(jobs, self.identity)

    def test_timing_requires_normal_successful_same_revision_control(self):
        with patch.object(shadow.time, "time", return_value=shadow.timestamp("2026-10-19T01:05:10Z")):
            result = shadow.timing_model(self.identity, self.jobs(), self.control_receipt(), self.jobs(True), 10)
        self.assertEqual(result["serial_python_seconds"], 2700)
        self.assertEqual(result["test_step_overhead_seconds"], 30)
        self.assertEqual(result["counterfactual_runner_seconds_saved"], 2360)
        for field, value in [("head_sha", "a" * 40), ("conclusion", "failure"), ("event", "pull_request"),
                             ("id", 42), ("path", ".github/workflows/other.yml")]:
            provider = self.jobs()
            control = self.control_receipt() | {field: value}
            control_jobs = self.jobs(True)
            with self.subTest(field=field), self.assertRaises(ValueError):
                shadow.timing_model(self.identity, provider, control, control_jobs, 10)

    def test_ambiguous_json_and_invalid_timing_fail(self):
        path = self.root / "ambiguous.json"
        path.write_text('{"head":"old","head":"new"}')
        with self.assertRaises(ValueError):
            shadow.read_json(path)
        with self.assertRaises(ValueError):
            shadow.timestamp("2026-10-19T01:00:00")
        with self.assertRaises(ValueError):
            shadow.seconds("2026-10-19T02:00:00Z", "2026-10-19T01:00:00Z")
        for mode in ("missing", "zero"):
            job = self.jobs()["jobs"][1]
            if mode == "missing":
                job["steps"] = []
            else:
                job["steps"][0]["completed_at"] = job["steps"][0]["started_at"]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                shadow.test_step_duration(job)

    def test_cli_context_is_fresh_and_comparison_failure_is_recorded(self):
        previous = Path.cwd()
        previous_mask = os.umask(0o077)
        try:
            os.chdir(self.root)
            (self.root / ".sonar-evidence/shadow-context.json").unlink()
            with patch.object(sys, "argv", ["shadow.py", "context", "--image", self.image]):
                shadow.main()
            self.assertEqual(shadow.read_json(self.root / ".sonar-evidence/shadow-context.json"), self.identity)
            with (patch.object(sys, "argv", ["shadow.py", "compare"]),
                  patch.object(shadow, "compare", side_effect=ValueError("missing shard")),
                  self.assertRaises(ValueError)):
                shadow.main()
            report = shadow.read_json(self.root / ".sonar-evidence/shadow-comparison/failure.json")
            self.assertEqual(report, {"status": "failed", "error": "missing shard"})
        finally:
            os.chdir(previous)
            os.umask(previous_mask)

    def test_cli_rejects_caller_selected_evidence_paths(self):
        for option in ("--inputs", "--jobs", "--control-run", "--control-jobs"):
            argv = ["shadow.py", "compare", option, "../../private.json"]
            with (self.subTest(option=option), patch.object(sys, "argv", argv),
                  patch.object(sys, "stderr", io.StringIO()),
                  patch.object(shadow, "compare") as comparison,
                  self.assertRaises(SystemExit)):
                shadow.main()
            comparison.assert_not_called()

    def test_cli_comparison_uses_only_fixed_checkout_evidence(self):
        previous_mask = os.umask(0o077)
        root = Path.cwd()
        evidence = root / ".sonar-evidence"
        try:
            with (patch.object(sys, "argv", ["shadow.py", "compare", "--image", self.image]),
                  patch.object(shadow, "compare") as comparison):
                shadow.main()
            comparison.assert_called_once_with(
                root, evidence / "shadow-inputs", evidence / "shadow-jobs.json", self.image,
                evidence / "shadow-comparison", evidence / "control-run.json", evidence / "control-jobs.json")
        finally:
            os.umask(previous_mask)

    def test_cli_snapshot_consumes_bounded_private_stdin(self):
        previous_mask = os.umask(0o077)
        try:
            raw = json.dumps(self.identity).encode()
            with (patch.object(sys, "argv", ["shadow.py", "snapshot", "--lane", "unit"]),
                  patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw))),
                  patch.object(shadow, "snapshot") as capture):
                shadow.main()
                capture.assert_called_once_with(Path("/bifrost-results"), Path("/coverage"), "unit", raw)
            with (patch.object(sys, "argv", ["shadow.py", "snapshot", "--lane", "unit"]),
                  patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"oversized"))),
                  patch.object(shadow, "MAX_BYTES", 2), patch.object(shadow, "snapshot") as capture,
                  self.assertRaisesRegex(ValueError, "Oversized context")):
                shadow.main()
            capture.assert_not_called()
        finally:
            os.umask(previous_mask)

    def test_combine_keeps_original_databases_and_excludes_serial_baseline(self):
        class Recorder:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def combine(self, **kwargs):
                self.inputs = kwargs
            def save(self):
                pass
            def xml_report(self, **kwargs):
                self.report = kwargs
        collector = Recorder()
        module = type("CoverageModule", (), {"Coverage": lambda **kwargs: collector})
        bundles = {lane: {"directory": self.root / lane} for lane in shadow.LANES}
        with patch.dict(sys.modules, {"coverage": module}):
            shadow.combine(self.root, bundles, self.root)
        self.assertTrue(collector.inputs["keep"])
        self.assertTrue(collector.inputs["strict"])
        self.assertEqual(len(collector.inputs["data_paths"]), 6)
        self.assertFalse(any("baseline" in path for path in collector.inputs["data_paths"]))


if __name__ == "__main__":
    unittest.main()
