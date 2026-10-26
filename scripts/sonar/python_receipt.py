#!/usr/bin/env python3
"""Reuse only original comprehensive Python evidence; otherwise collect afresh.

This is report transport, never a transferred CI or Sonar status. All consumers
still perform their own preflight, analysis, required checks and admission.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

SPEC = importlib.util.spec_from_file_location("main_receipt", Path(__file__).with_name("main_receipt.py"))
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)

# Whole source/workflow trees additionally bind the actual commands, complete
# test inventory, locks, include/exclude rules and runtime instrumentation.
CONTRACT = {
    "schema": 1, "mode": "comprehensive", "branch": True,
    "slow_units": True, "repository_tools": True,
    "runtime": ["api", "api-replica", "worker", "scheduler", "scheduler-fixtures"],
    "config": "api/.coveragerc.sonar", "compose": "docker-compose.sonar.yml",
}
STEPS = (
    "Collect repository Python tool coverage", "Collect comprehensive Python coverage",
    "Validate reports and source inventory", "Retain original producer and toolchain identity",
    "Tear down task-owned stack", "Retain exact-source evidence",
)
FILES = {"producer.json", "manifest.json", "python.xml", "client.lcov", "node.lcov"}


def workflow_tree(sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise transport.ReuseRefused("Exact workflow commit required")
    try:
        return transport.command(["git", "rev-parse", sha + "^{tree}"])
    except transport.ReuseRefused:
        # Exact-head checkouts need not contain GitHub's synthetic PR workflow
        # commit. Fetch that one immutable object from the configured public
        # repository, never a receipt-supplied URL or mutable ref.
        try:
            transport.command(["git", "fetch", "--no-tags", "--depth=1",
                               "https://github.com/" + transport.REPOSITORY + ".git", sha])
        except subprocess.TimeoutExpired as exc:
            raise transport.ReuseRefused("Workflow object fetch timed out") from exc
        return transport.command(["git", "rev-parse", sha + "^{tree}"])


def record(root: Path, output: Path) -> None:
    # Retain the existing schema for exact-main tag consumers. The additional
    # contract is mandatory only for this new Python-only transport.
    transport.record(root, output)
    receipt = json.loads((output / "producer.json").read_bytes())
    if workflow_tree(receipt["workflow_sha"]) != receipt["tree"]:
        raise transport.ReuseRefused("Original workflow tree differs from collection source")
    receipt["python_collection"] = CONTRACT
    (output / "producer.json").write_bytes(transport.preflight.json_bytes(receipt))


def choose_run(head: str, branch: str) -> dict:
    runs = transport.pages(f"actions/workflows/ci.yml/runs?head_sha={head}", "workflow_runs")
    current_id = int(os.environ["GITHUB_RUN_ID"])
    candidates = [run for run in runs if run.get("id") != current_id
                  and run.get("head_sha") == head and run.get("head_branch") == branch
                  and run.get("event") in {"pull_request", "workflow_dispatch"}
                  and run.get("path") == transport.WORKFLOW
                  and run.get("head_repository", {}).get("full_name") == transport.REPOSITORY]
    if not candidates:
        raise transport.ReuseRefused("No exact-source Python producer")
    # Never look past a newer failed/running candidate or a superseding attempt.
    run = max(candidates, key=lambda row: row["id"])
    if (run.get("status") != "completed" or run.get("conclusion") != "success"
            or type(run.get("run_attempt")) is not int or run["run_attempt"] < 1):
        raise transport.ReuseRefused("Latest Python producer is not complete/successful")
    return run


def reuse(root: Path, output: Path) -> None:
    if (os.environ.get("GITHUB_REPOSITORY") != transport.REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") not in {"pull_request", "workflow_dispatch"}):
        raise transport.ReuseRefused("Python reuse requires a same-repository PR or manual branch run")
    branch = os.environ.get("GITHUB_HEAD_REF") if os.environ["GITHUB_EVENT_NAME"] == "pull_request" else os.environ.get("GITHUB_REF", "").removeprefix("refs/heads/")
    if not branch or not re.fullmatch(r"[A-Za-z0-9_./-]+", branch) or ".." in branch:
        raise transport.ReuseRefused("Exact branch required")
    if os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch" and not os.environ.get("GITHUB_REF", "").startswith("refs/heads/"):
        raise transport.ReuseRefused("Manual Python reuse requires a branch")
    head = transport.exact_head()
    base = os.environ.get("EXPECTED_BASE", "")
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise transport.ReuseRefused("Exact comparison base required")
    tree = transport.command(["git", "rev-parse", "HEAD^{tree}"])
    if workflow_tree(os.environ.get("GITHUB_WORKFLOW_SHA", "")) != tree:
        raise transport.ReuseRefused("Consumer workflow tree differs from collection source")
    run = choose_run(head, branch)
    jobs = transport.pages(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
    job = transport.successful_job(jobs, transport.EVIDENCE_JOB)
    for name in STEPS:
        steps = [step for step in job.get("steps", []) if step.get("name") == name]
        if len(steps) != 1 or steps[0].get("conclusion") != "success":
            raise transport.ReuseRefused("Original comprehensive Python collection/export required")
    artifacts = transport.pages(f"actions/runs/{run['id']}/artifacts", "artifacts")
    matches = [item for item in artifacts if item.get("name") == f"sonar-evidence-{head}-{run['run_attempt']}"]
    if len(matches) != 1:
        raise transport.ReuseRefused("One exact-attempt Python evidence artifact required")
    artifact = matches[0]
    files = transport.archive_files(artifact, FILES, job, run["id"])
    producer = json.loads(files["producer.json"])
    # Reuse the established toolchain/image validation without claiming a main
    # event. Only these two independently verified event fields are adapted.
    event, ref = producer.get("event"), producer.get("ref")
    expected_ref = "refs/heads/" + branch
    if (event != run["event"] or (event == "workflow_dispatch" and ref != expected_ref)
            or (event == "pull_request" and not re.fullmatch(r"refs/pull/[1-9][0-9]*/merge", ref or ""))):
        raise transport.ReuseRefused("Producer event/ref mismatch")
    if workflow_tree(producer.get("workflow_sha", "")) != tree:
        raise transport.ReuseRefused("Producer workflow tree differs from collection source")
    normalized = producer | {"event": "push", "ref": "refs/heads/main", "workflow_sha": head}
    transport.validate_producer(normalized, run, head, root)
    manifest = json.loads(files["manifest.json"])
    digest = hashlib.sha256(files["manifest.json"]).hexdigest()
    if (producer.get("python_collection") != CONTRACT or producer.get("base") != base
            or manifest.get("base") != base or producer.get("manifest_sha256") != digest
            or producer.get("config_sha256") != manifest.get("config_sha256")):
        raise transport.ReuseRefused("Python collection/base/config/manifest mismatch")
    owned = [output / name for name in FILES]
    destination = output / "raw/python.xml"
    provenance = Path(str(destination) + ".provenance.json")
    origin = output / "python-origin"
    if output.is_symlink() or (output / "raw").is_symlink() or any(path.exists() or path.is_symlink() for path in [*owned, destination, provenance, origin]):
        raise transport.ReuseRefused("Python reuse requires empty owned destinations")
    output.mkdir(parents=True, exist_ok=True)
    try:
        for name, data in files.items():
            (output / name).write_bytes(data)
        # The complete existing verifier checks original report bytes, inventory,
        # source/base and config before Python is admitted to current preflight.
        transport.preflight.verify(root, head, base, output, digest)
        current = transport.api(f"actions/runs/{run['id']}")
        if any(current.get(key) != run.get(key) for key in ("id", "head_sha", "head_branch", "event", "path", "head_repository", "run_attempt", "status", "conclusion")):
            raise transport.ReuseRefused("Producer changed during Python evidence retrieval")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(files["python.xml"])
        transport.preflight.stamp_report(root, head, base, destination, "sonar-project.properties")
        origin.mkdir()
        for name, data in files.items():
            (origin / name).write_bytes(data)
        (origin / "reuse.json").write_bytes(transport.preflight.json_bytes({
            "schema": 1, "kind": "exact-source-comprehensive-python", "producer": producer,
            "artifact_id": artifact["id"], "artifact_digest": artifact["digest"], "job_id": job["id"],
            "consumer_run": os.environ["GITHUB_RUN_ID"], "consumer_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        }))
    except Exception:
        destination.unlink(missing_ok=True)
        provenance.unlink(missing_ok=True)
        if origin.is_dir():
            for path in origin.iterdir():
                path.unlink()
            origin.rmdir()
        raise
    finally:
        for path in owned:
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "reuse"))
    args = parser.parse_args()
    root = Path.cwd()
    if args.action == "record":
        record(root, root / ".sonar-evidence")
        return 0
    try:
        reuse(root, root / ".sonar-evidence")
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        reason = str(exc) if type(exc) is transport.ReuseRefused else "Python evidence unavailable or incompatible"
        transport.summary(f"Python evidence not reused: {reason}; comprehensive collection remains enabled.")
        return 0
    except Exception:
        transport.summary("Python reuse failed unexpectedly; stopped without claiming reuse.")
        return 1
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write("reused=true\n")
    transport.summary("Original successful comprehensive Python evidence reused; current preflight, analysis and all CI/admission checks remain required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
