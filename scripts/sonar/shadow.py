"""Manual-only coverage experiment receipts and fail-closed equivalence checks.

The authoritative Sonar path is never replaced by this tool. SQLite/coverage
inputs are data, not executable artifacts. No credentials are read here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

REPOSITORY = "Midtown-Technology-Group/bifrost"
LANES = ("baseline", "unit", "supplement", "e2e-1", "e2e-2", "e2e-3", "e2e-4")
CONFIGS = (
    "api/.coveragerc.sonar", "api/.coveragerc.sonar-report", "api/pytest.ini",
    "docker-compose.test.yml", "docker-compose.sonar.yml", "test.sh",
    ".github/workflows/ci.yml", ".github/workflows/sonar-coverage.yml",
    "scripts/sonar/shadow.py", "scripts/sonar/shadow.sh",
    "api/scripts/filter_sonar_coverage.py", "api/scripts/combine_sonar_coverage.py",
    "requirements.lock",
)
CONTEXT_FILE = "context.json"
MANIFEST_FILE = "manifest.json"
COVERAGE_FILE = "python.coverage"
COMPARISON_JOB = "Coverage Shadow Comparison"
MAX_BYTES = 128 * 1024 * 1024
JOB_NAMES = {"baseline": "sonar-coverage / Sonar Coverage Evidence", "unit": "Unit Tests",
             "supplement": "Coverage Shadow Supplement",
             **{f"e2e-{i}": f"E2E Tests (shard {i}/4)" for i in range(1, 5)}}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_file(path: Path, *, allow_empty: bool = False) -> bytes:
    """Read bounded regular files without traversing symlink components."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Symlink in evidence path")
    minimum = 0 if allow_empty else 1
    if not path.is_file() or not minimum <= path.stat().st_size <= MAX_BYTES:
        raise ValueError(f"Missing, empty or oversized evidence: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Evidence grew beyond its bound")
    return raw


def unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    return json.loads(read_file(path), object_pairs_hook=unique_object)


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["/usr/bin/git", "-C", str(root), *args], text=True).strip()


def context(root: Path, image: str) -> dict:
    """Bind a non-main manual run to its exact checkout, attempt and configs."""
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("Shadow collection is manual-only")
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Unexpected repository")
    ref = os.environ.get("GITHUB_REF", "")
    if not ref.startswith("refs/heads/") or ref == "refs/heads/main":
        raise ValueError("Use an isolated branch, never main or a production ref")
    head = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[a-f0-9]{40}", head) or git(root, "rev-parse", "HEAD") != head:
        raise ValueError("Exact checkout mismatch")
    if os.environ.get("GITHUB_WORKFLOW_SHA") != head:
        raise ValueError("Workflow definition differs from the tested commit")
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        if not re.fullmatch(r"[1-9]\d*", os.environ.get(name, "")):
            raise ValueError(f"Missing run identity: {name}")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("Missing exact test image identity")
    git(root, "ls-files", "--error-unmatch", "--", *CONFIGS)
    subprocess.run(["/usr/bin/git", "-C", str(root), "diff", "--exit-code", "HEAD", "--"],
                   check=True, stdout=subprocess.DEVNULL)
    return {"schema": 1, "repository": REPOSITORY, "event": "workflow_dispatch",
            "head": head, "tree": git(root, "rev-parse", "HEAD^{tree}"),
            "run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
            "workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"],
            "image": image, "configs": {p: digest(read_file(root / p)) for p in CONFIGS}}


def phase_names(lane: str) -> list[str]:
    if lane == "baseline":
        return ["unit", "e2e", "tools"]
    if lane == "supplement":
        return ["unit", "tools"]
    return ["e2e"] if lane.startswith("e2e-") else ["unit"]


def snapshot(artifacts: Path, runtime: Path, lane: str, context_raw: bytes) -> None:
    """Copy raw data before combining or deleting any task-owned volume."""
    target = artifacts / f"coverage-shadow-{lane}"
    target.mkdir(mode=0o700)
    # The host owns the private context. Explicit stdin transport lets the
    # container retain the same bytes without widening filesystem access.
    with (target / CONTEXT_FILE).open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(context_raw)
    raw = target / "runtime"
    raw.mkdir(mode=0o700)
    sources = [(path, raw / path.name) for path in sorted(runtime.glob(".coverage.sonar.*"))]
    sources += [(artifacts / ".coverage.sonar", target / "test-runner.coverage"),
                (artifacts / "sonar-runtime-producers.txt", target / "producers.txt")]
    sources += [(artifacts / f"junit-{phase}.xml", target / f"junit-{phase}.xml") for phase in phase_names(lane)]
    failures = []
    for source, destination in sources:
        try:
            # Empty bytes are useful diagnostics, never qualifying coverage.
            destination.write_bytes(read_file(source, allow_empty=True))
        except (ValueError, OSError) as error:
            failures.append({"file": source.name, "error": str(error)})
    if failures:
        write_json(target / "snapshot-failures.json", {"failures": failures})
        raise ValueError("Incomplete snapshot; retained available evidence and diagnostics")


def database(path: Path, *, allow_empty: bool = False) -> tuple[str, dict[str, set[tuple[int, int]]]]:
    """Read actual branch arcs and zero-hit file records from Coverage.py SQLite."""
    read_file(path)
    uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        meta = dict(connection.execute("select key, value from meta"))
        if meta.get("has_arcs") != "1":
            raise ValueError("Line-only coverage is incompatible")
        files = dict(connection.execute("select id, path from file"))
        arcs = {name: set() for name in files.values()}
        for file_id, start, end in connection.execute("select file_id, fromno, tono from arc"):
            arcs[files[file_id]].add((start, end))
    if not arcs and not allow_empty:
        raise ValueError("Empty runtime coverage database")
    return meta["version"], arcs


def validate_runtime(directory: Path, lane: str) -> dict:
    producers = {}
    for line in read_file(directory / "producers.txt").decode().splitlines():
        role, container = line.split()
        producers[container[:12]] = role
    observed = set()
    versions = set()
    files = sorted((directory / "runtime").iterdir())
    if not files:
        raise ValueError("Missing runtime reports")
    runtime_required = lane == "baseline" or lane.startswith("e2e-")
    empty_reports = []
    for path in files:
        version, measured = database(path, allow_empty=not runtime_required)
        versions.add(version)
        hostname = path.name.removeprefix(".coverage.sonar.").split(".")[0]
        if hostname not in producers:
            raise ValueError("Runtime report has no attributable container")
        observed.add(producers[hostname])
        if not measured:
            empty_reports.append(path.name)
    required = {"api", "api-replica"}
    if lane == "baseline" or lane.startswith("e2e-"):
        required |= {"worker", "scheduler", "scheduler-fixtures"}
    if not required <= observed or len(versions) != 1:
        raise ValueError("Incomplete or incompatible runtime producer evidence")
    return {"roles": sorted(observed), "version": versions.pop(), "reports": len(files),
            "empty_reports": empty_reports}


def seal(root: Path, artifacts: Path, lane: str) -> None:
    directory = artifacts / f"coverage-shadow-{lane}"
    (directory / COVERAGE_FILE).write_bytes(read_file(artifacts / ".coverage.sonar-tracked"))
    (directory / "python.xml").write_bytes(read_file(artifacts / "coverage-sonar.xml"))
    version, _ = database(directory / COVERAGE_FILE)
    runtime = validate_runtime(directory, lane)
    if version != runtime["version"]:
        raise ValueError("Test-runner and runtime versions differ")
    for phase in phase_names(lane):
        junit(directory / f"junit-{phase}.xml")
    identity = read_json(directory / CONTEXT_FILE)
    files = {p.relative_to(directory).as_posix(): digest(read_file(p))
             for p in directory.rglob("*") if p.is_file()}
    write_json(directory / MANIFEST_FILE, {"identity": identity, "lane": lane,
               "status": "exported", "runtime": runtime, "files": files,
               "started_at": (root / ".sonar-evidence/shadow-context.json").stat().st_mtime, "sealed_at": time.time()})


def xml_bytes(path: Path) -> bytes:
    raw = read_file(path)
    text = raw.decode("utf-8")
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Unsafe XML")
    return raw


def junit(path: Path) -> Counter:
    raw = xml_bytes(path)
    result = Counter()
    for case in ElementTree.fromstring(raw).iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError("Failed tests cannot establish equivalence")
        result[(case.attrib["classname"], case.attrib["name"],
                "skipped" if case.find("skipped") is not None else "passed")] += 1
    if not result:
        raise ValueError("Empty test execution inventory")
    return result


def load_bundle(directory: Path, expected: dict) -> dict:
    manifest = read_json(directory / MANIFEST_FILE)
    if manifest.get("identity") != expected or manifest.get("status") != "exported":
        raise ValueError("Stale, mixed or incomplete producer identity")
    if read_json(directory / CONTEXT_FILE) != expected:
        raise ValueError("Payload context differs from its manifest")
    lane = manifest["lane"]
    if lane not in LANES:
        raise ValueError("Unexpected shard")
    listed = set(manifest["files"])
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    if actual != listed | {MANIFEST_FILE}:
        raise ValueError("Missing or unexpected artifact files")
    for name, checksum in manifest["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Unsafe artifact member")
        if digest(read_file(directory / name)) != checksum:
            raise ValueError("Corrupt artifact")
    validate_runtime(directory, lane)
    return {"manifest": manifest, "directory": directory}


def require_jobs(payload: dict, expected: dict) -> None:
    jobs = payload["jobs"]
    if payload.get("total_count") != len(jobs):
        raise ValueError("Incomplete provider job inventory")
    for name in JOB_NAMES.values():
        matching = [job for job in jobs if job["name"] == name]
        if len(matching) != 1 or matching[0]["conclusion"] != "success":
            raise ValueError(f"Missing successful producer job: {name}")
        job = matching[0]
        if (str(job["run_id"]) != expected["run_id"]
                or str(job["run_attempt"]) != expected["attempt"] or job["head_sha"] != expected["head"]):
            raise ValueError("Provider job identity differs from the artifact")


def test_equivalence(bundles: dict) -> dict:
    baseline = bundles["baseline"]["directory"]
    for phase, lanes in (("unit", ("unit", "supplement")),
                         ("e2e", tuple(f"e2e-{i}" for i in range(1, 5))),
                         ("tools", ("supplement",))):
        expected = junit(baseline / f"junit-{phase}.xml")
        actual = Counter()
        for lane in lanes:
            actual.update(junit(bundles[lane]["directory"] / f"junit-{phase}.xml"))
        if actual != expected:
            raise ValueError(f"Test execution differs: {phase}; missing={sum((expected-actual).values())}, extra={sum((actual-expected).values())}")
    return {"test_inventories": "identical"}


def combine(root: Path, bundles: dict, output: Path) -> Path:
    import coverage  # Available in the exact CI test image, not required on the operator host.

    data = output / ".coverage.combined"
    collector = coverage.Coverage(config_file=str(root / "api/.coveragerc.sonar-report"), data_file=str(data))
    paths = [str(bundles[lane]["directory"] / COVERAGE_FILE) for lane in LANES if lane != "baseline"]
    collector.combine(data_paths=paths, strict=True, keep=True)
    collector.save()
    collector.xml_report(outfile=str(output / "shadow.xml"))
    return data


def canonical_arcs(measured: dict) -> dict:
    result = {}
    for name, arcs in measured.items():
        path = name.removeprefix("/repo/")
        if Path(path).is_absolute() or ".." in Path(path).parts or "\\" in path:
            raise ValueError("Unexpected measured source root")
        result.setdefault(path, set()).update(arcs)
    return result


def coverage_equivalence(baseline: Path, candidate: Path) -> dict:
    old_version, old = database(baseline)
    new_version, new = database(candidate)
    if old_version != new_version:
        raise ValueError("Coverage tool versions differ")
    old, new = canonical_arcs(old), canonical_arcs(new)
    if old.keys() != new.keys():
        raise ValueError("Authored source/zero-hit file inventories differ")
    missing = sum(len(arcs - new[name]) for name, arcs in old.items())
    if missing:
        raise ValueError(f"Lost baseline branch arcs: {missing}")
    return {"files": len(old), "lost_arcs": 0,
            "additional_arcs": sum(len(arcs - old[name]) for name, arcs in new.items())}


def xml_denominator(path: Path, root: Path, tracked: dict) -> dict:
    from scripts.sonar import preflight

    normalized, _ = preflight.parse_python(xml_bytes(path), root, tracked)
    result = {}
    for node in ElementTree.fromstring(normalized).iter("class"):
        result[node.attrib["filename"]] = {
            int(line.attrib["number"]): line.get("condition-coverage", "(0/0)").split("/")[-1].rstrip(")")
            for line in node.findall("./lines/line")}
    return result


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timing evidence must include a timezone")
    return parsed.timestamp()


def seconds(start: str, end: str) -> float:
    elapsed = timestamp(end) - timestamp(start)
    if elapsed < 0:
        raise ValueError("Timing evidence runs backwards")
    return elapsed


def named_job(payload: dict, name: str) -> dict:
    matches = [job for job in payload["jobs"] if job["name"] == name]
    if len(matches) != 1 or matches[0]["conclusion"] != "success":
        raise ValueError(f"Missing completed timing producer: {name}")
    return matches[0]


def test_step_duration(job: dict) -> float:
    prefix = "Run unit tests" if job["name"] == "Unit Tests" else "Run E2E tests (shard "
    steps = [step for step in job["steps"] if step["name"].startswith(prefix)]
    if len(steps) != 1 or steps[0]["conclusion"] != "success":
        raise ValueError("Missing successful timed test step")
    duration = seconds(steps[0]["started_at"], steps[0]["completed_at"])
    if duration <= 0:
        raise ValueError("Test-step duration must be positive")
    extra = [step for step in job["steps"] if step["name"].startswith(
        ("Prepare opt-in shadow", "Export opt-in", "Clean up opt-in", "Own opt-in", "Retain opt-in"))]
    return duration + sum(seconds(step["started_at"], step["completed_at"]) for step in extra
                          if step["conclusion"] == "success")


def validate_control(expected: dict, control: dict, control_jobs: dict) -> None:
    if (control["head_sha"] != expected["head"] or control["event"] != "workflow_dispatch"
            or control["conclusion"] != "success" or str(control["id"]) == expected["run_id"]):
        raise ValueError("Timing control must be a successful separate manual run on this exact source")
    if control.get("repository", {}).get("full_name") != REPOSITORY or control.get("path") != ".github/workflows/ci.yml":
        raise ValueError("Unexpected timing-control repository or workflow")
    if control_jobs["total_count"] != len(control_jobs["jobs"]):
        raise ValueError("Incomplete timing-control jobs")
    for name in ("Coverage Shadow Supplement", COMPARISON_JOB):
        jobs = [job for job in control_jobs["jobs"] if job["name"] == name]
        if len(jobs) != 1 or jobs[0]["conclusion"] != "skipped":
            raise ValueError("Timing control was instrumented or has unknown pilot scope")


def timing_model(expected: dict, current: dict, control: dict, control_jobs: dict,
                 combine_seconds: float) -> dict:
    validate_control(expected, control, control_jobs)
    names = [JOB_NAMES[lane] for lane in LANES if lane in ("unit", "e2e-1", "e2e-2", "e2e-3", "e2e-4")]
    pairs = [(named_job(current, name), named_job(control_jobs, name)) for name in names]
    for _, before in pairs:
        if (before["head_sha"] != expected["head"] or before["run_id"] != control["id"]
                or before["run_attempt"] != control["run_attempt"]):
            raise ValueError("Mixed timing-control producer identity")
    overhead = sum(test_step_duration(now) - test_step_duration(before) for now, before in pairs)
    normal_seconds = sum(test_step_duration(before) for _, before in pairs)
    baseline = named_job(current, JOB_NAMES["baseline"])
    serial = next(step for step in baseline["steps"] if step["name"] == "Collect comprehensive Python coverage")
    serial_seconds = seconds(serial["started_at"], serial["completed_at"])
    supplement = named_job(current, JOB_NAMES["supplement"])
    supplement_seconds = seconds(supplement["started_at"], supplement["completed_at"])
    comparison = [job for job in current["jobs"] if job["name"] == COMPARISON_JOB]
    if len(comparison) != 1:
        raise ValueError("Missing comparison-job timing")
    comparison_seconds = max(combine_seconds, time.time() - timestamp(comparison[0]["started_at"]))
    extra_jobs = supplement_seconds + comparison_seconds
    shadow_end = max([now["completed_at"] for now, _ in pairs] + [supplement["completed_at"]])
    excluded = {*JOB_NAMES.values(), COMPARISON_JOB}
    unaffected = [timestamp(job["completed_at"]) for job in current["jobs"]
                  if job["name"] not in excluded and not job["name"].startswith("sonar-coverage /") and job["completed_at"]]
    unaffected_end = max([timestamp(serial["started_at"]), *unaffected])
    current_end = max(timestamp(serial["completed_at"]), unaffected_end)
    proposed_end = max(timestamp(shadow_end) + comparison_seconds, unaffected_end)
    wall_saved = current_end - proposed_end
    return {"control_run": control["id"], "serial_python_seconds": serial_seconds,
            "comparison_elapsed_seconds": comparison_seconds,
            "test_step_overhead_seconds": overhead, "test_step_overhead_fraction": overhead / normal_seconds,
            "largest_step_overhead_fraction": max(test_step_duration(now) / test_step_duration(before) - 1 for now, before in pairs),
            "counterfactual_runner_seconds_saved": serial_seconds - overhead - extra_jobs,
            "counterfactual_wall_seconds_saved": wall_saved,
            "limitations": ["Shadow run executes both paths; these savings are counterfactual, not realized.",
                            "Timing-control image digests need independent comparison of completed pull logs before timing acceptance.",
                            "Runner setup/cache/contention vary; repeat before making a sustained-capacity claim.",
                            "Comparison cleanup/upload after this snapshot can reduce the estimated saving."]}


def compare(root: Path, inputs: Path, jobs: Path, image: str, output: Path,
            control_run: Path, control_jobs: Path) -> None:
    started = time.monotonic()
    expected = context(root, image)
    provider = read_json(jobs)
    require_jobs(provider, expected)
    bundles = {}
    for directory in sorted(inputs.iterdir()):
        bundle = load_bundle(directory, expected)
        lane = bundle["manifest"]["lane"]
        if lane in bundles:
            raise ValueError("Duplicate shard")
        bundles[lane] = bundle
    if set(bundles) != set(LANES):
        raise ValueError("Missing or unexpected producer set")
    output.mkdir(mode=0o700)
    tests = test_equivalence(bundles)
    combined = combine(root, bundles, output)
    baseline = bundles["baseline"]["directory"]
    arcs = coverage_equivalence(baseline / COVERAGE_FILE, combined)
    from scripts.sonar import preflight

    tracked = {item["path"]: item for item in preflight.inventory(root, expected["head"], expected["head"])}
    if xml_denominator(baseline / "python.xml", root, tracked) != xml_denominator(output / "shadow.xml", root, tracked):
        raise ValueError("Source line/branch denominators differ")
    timing = timing_model(expected, provider, read_json(control_run),
                          read_json(control_jobs), time.monotonic() - started)
    write_json(output / "comparison.json", {"status": "equivalent", "identity": expected, **tests, **arcs,
               "timing": timing,
               "limitation": "Offline shadow comparison only; no Sonar submission, admission or rollout authority"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("context", "snapshot", "seal", "compare"))
    parser.add_argument("--lane", choices=LANES)
    parser.add_argument("--image")
    args = parser.parse_args()
    root = Path.cwd()
    os.umask(0o077)
    if args.action == "context":
        write_json(root / ".sonar-evidence/shadow-context.json", context(root, args.image))
    elif args.action == "snapshot":
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Oversized context input")
        snapshot(Path("/bifrost-results"), Path("/coverage"), args.lane, raw)
    elif args.action == "seal":
        seal(root, Path("/bifrost-results"), args.lane)
    else:
        output = root / ".sonar-evidence/shadow-comparison"
        try:
            # Evidence locations are fixed by the workflow, never CLI-controlled.
            evidence = root / ".sonar-evidence"
            compare(root, evidence / "shadow-inputs", evidence / "shadow-jobs.json", args.image,
                    output, evidence / "control-run.json", evidence / "control-jobs.json")
        except Exception as error:
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "failure.json", {"status": "failed", "error": str(error)})
            raise


if __name__ == "__main__":
    main()
