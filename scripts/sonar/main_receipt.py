#!/usr/bin/env python3
"""Reuse complete exact-main Sonar evidence on tags; otherwise collect normally.

This transports the existing preflight contract, not a Sonar result producer.
The original head/base, scanner task, toolchain and run/attempt remain intact.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

SPEC = importlib.util.spec_from_file_location("sonar_preflight", Path(__file__).with_name("preflight.py"))
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
REPOSITORY = "Midtown-Technology-Group/bifrost"
WORKFLOW = ".github/workflows/ci.yml"
PROJECT = "Midtown-Technology-Group_bifrost"
EVIDENCE_JOB = "sonar-coverage / Sonar Coverage Evidence"
SCAN_JOB = "sonar-coverage / Sonar CI Analysis"
MAX_ARCHIVE = 200 * 1024 * 1024


def command(args: list[str], *, binary: bool = False):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(args, capture_output=True, env=environment, check=False, timeout=120)
    if result.returncode:
        # Never copy credential-bearing child stderr to receipts or logs.
        raise ValueError("Required read-only evidence command failed")
    return result.stdout if binary else result.stdout.decode().strip()


def api(path: str, *, archive: bool = False):
    if not re.fullmatch(r"[A-Za-z0-9_./?=&%-]+", path) or ".." in path:
        raise ValueError("Invalid repository-scoped API path")
    data = command(["gh", "api", f"repos/{REPOSITORY}/{path}"], binary=True)
    return data if archive else json.loads(data)


def pages(path: str, key: str) -> list[dict]:
    rows = []
    for page in range(1, 101):
        result = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        batch = result[key]
        if not isinstance(batch, list):
            raise ValueError("Malformed paginated evidence")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise ValueError("Evidence pagination exceeded bound")


def exact_head() -> str:
    head = os.environ.get("EXPECTED_HEAD", "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Exact source SHA required")
    if command(["git", "rev-parse", "HEAD"]) != head:
        raise ValueError("Checkout differs from requested source")
    return head


def record(root: Path, output: Path) -> None:
    head = exact_head()
    manifest = json.loads((output / "manifest.json").read_bytes())
    digest = hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    preflight.verify(root, head, manifest["base"], output, digest)
    images = json.loads(command(["docker", "image", "inspect", "bifrost-test-api-dev:latest",
                                 "bifrost-test-client-dev:latest"]))
    containers = command(["docker", "compose", "-f", os.environ["COMPOSE_FILE"], "ps", "-aq"]).splitlines()
    if not containers or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in containers):
        raise ValueError("Original test stack identity unavailable")
    runtime_images = sorted(set(command(["docker", "inspect", "--format", "{{.Image}}", *containers]).splitlines()))
    if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in runtime_images):
        raise ValueError("Original runtime image identity unavailable")
    receipt = {
        "schema": 1, "repository": os.environ["GITHUB_REPOSITORY"],
        "event": os.environ["GITHUB_EVENT_NAME"], "ref": os.environ["GITHUB_REF"],
        "head": head, "base": manifest["base"],
        "tree": command(["git", "rev-parse", "HEAD^{tree}"]),
        "workflow": WORKFLOW, "workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "manifest_sha256": digest, "config_sha256": manifest["config_sha256"],
        "toolchain": {
            "python": sys.version, "node": command(["node", "--version"]),
            "npm": command(["npm", "--version"]),
            "docker": command(["docker", "--version"]),
            "compose": command(["docker", "compose", "version"]),
            "runner": {key: os.environ.get(key) for key in ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion")},
            "images": [{"id": image["Id"], "repo_digests": image.get("RepoDigests", [])}
                       for image in images],
            "runtime_images": runtime_images,
        },
    }
    (output / "producer.json").write_bytes(preflight.json_bytes(receipt))


def successful_job(jobs: list[dict], name: str) -> dict:
    matches = [job for job in jobs if job.get("name") == name]
    if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
        raise ValueError(f"One complete successful {name} required")
    return matches[0]


def archive_files(artifact: dict, required: set[str], job: dict, run_id: int) -> dict[str, bytes]:
    if (artifact.get("expired") is not False
            or artifact.get("workflow_run", {}).get("id") != run_id
            or type(artifact.get("id")) is not int or artifact["id"] < 1
            or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE
            or not job["started_at"] <= artifact["created_at"] <= job["completed_at"]):
        raise ValueError("Artifact does not belong to the exact successful producer job")
    data = api(f"actions/artifacts/{artifact['id']}/zip", archive=True)
    if len(data) > MAX_ARCHIVE or artifact.get("digest") != "sha256:" + hashlib.sha256(data).hexdigest():
        raise ValueError("Artifact archive digest/size mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or not required <= set(names):
            raise ValueError("Artifact has duplicate or missing required files")
        result = {}
        for name in required:
            item = bundle.getinfo(name)
            if item.file_size > preflight.MAX_REPORT_BYTES or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Oversized or symlink evidence")
            # Only fixed basenames are read; nothing is extracted from the zip.
            result[name] = bundle.read(item)
        return result


def validate_producer(receipt: dict, run: dict, head: str, root: Path) -> None:
    expected = {"schema": 1, "repository": REPOSITORY, "event": "push", "ref": "refs/heads/main",
                "head": head, "tree": command(["git", "rev-parse", "HEAD^{tree}"]),
                "workflow": WORKFLOW, "workflow_sha": head,
                "run_id": run["id"], "run_attempt": run["run_attempt"]}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("Producer source/workflow/run/attempt mismatch")
    toolchain = receipt.get("toolchain", {})
    if any(not isinstance(toolchain.get(key), str) or not toolchain[key] for key in ("python", "node", "npm", "docker", "compose")):
        raise ValueError("Original toolchain evidence missing")
    images = toolchain.get("images", [])
    if len(images) != 2 or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("id", "")) for image in images):
        raise ValueError("Original test-image identities missing")
    runtime_images = toolchain.get("runtime_images", [])
    if not runtime_images or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in runtime_images):
        raise ValueError("Original runtime-image identities missing")
    # The entire source tree, including lockfiles, image recipes, workflow and
    # preflight, is exact. Toolchain observations describe the original producer;
    # no claim is made that the tag runner executes those tools again.


def choose_run(head: str) -> dict:
    runs = pages(f"actions/workflows/ci.yml/runs?branch=main&event=push&head_sha={head}", "workflow_runs")
    candidates = [run for run in runs if run.get("head_sha") == head and run.get("head_branch") == "main"
                  and run.get("event") == "push" and run.get("path") == WORKFLOW
                  and run.get("head_repository", {}).get("full_name") == REPOSITORY]
    if not candidates:
        raise ValueError("No exact-main producer")
    # A later failed/running attempt supersedes earlier green evidence.
    run = max(candidates, key=lambda row: row["id"])
    if (run.get("status") != "completed" or run.get("conclusion") != "success"
            or type(run.get("run_attempt")) is not int or run["run_attempt"] < 1):
        raise ValueError("Latest exact-main producer is not complete/successful")
    return run


def reuse(root: Path, output: Path) -> dict:
    head = exact_head()
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or not os.environ.get("GITHUB_REF", "").startswith("refs/tags/v"):
        raise ValueError("Reuse is limited to this repository's version tags")
    run = choose_run(head)
    jobs = pages(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
    evidence_job, scan_job = successful_job(jobs, EVIDENCE_JOB), successful_job(jobs, SCAN_JOB)
    for name in ("Analyze exact source and wait for its quality gate", "Verify evidence after analysis"):
        steps = [step for step in scan_job.get("steps", []) if step.get("name") == name]
        if len(steps) != 1 or steps[0].get("conclusion") != "success":
            raise ValueError("Original analysis and post-analysis verification must have run")
    artifacts = pages(f"actions/runs/{run['id']}/artifacts", "artifacts")
    def artifact(prefix):
        matches = [item for item in artifacts if item.get("name") == f"{prefix}-{head}-{run['run_attempt']}"]
        if len(matches) != 1:
            raise ValueError("One exact-attempt artifact required")
        return matches[0]
    evidence_artifact, task_artifact = artifact("sonar-evidence"), artifact("sonar-task")
    files = archive_files(evidence_artifact, {"manifest.json", "producer.json", "python.xml", "client.lcov", "node.lcov"}, evidence_job, run["id"])
    task = archive_files(task_artifact, {"report-task.txt"}, scan_job, run["id"])["report-task.txt"]
    properties = dict(line.split("=", 1) for line in task.decode().splitlines() if "=" in line)
    task_id = properties.get("ceTaskId", "")
    if (properties.get("projectKey") != PROJECT or properties.get("serverUrl") != "https://sonarcloud.io"
            or not re.fullmatch(r"[A-Za-z0-9_-]+", task_id)
            or properties.get("ceTaskUrl") != "https://sonarcloud.io/api/ce/task?id=" + task_id):
        raise ValueError("Original scanner project/server/task provenance missing")
    producer = json.loads(files["producer.json"])
    validate_producer(producer, run, head, root)
    manifest = json.loads(files["manifest.json"])
    if producer.get("base") != manifest.get("base") or producer.get("config_sha256") != manifest.get("config_sha256"):
        raise ValueError("Original base/configuration mismatch")
    if hashlib.sha256(files["manifest.json"]).hexdigest() != producer.get("manifest_sha256"):
        raise ValueError("Original manifest digest mismatch")
    # Keep the original manifest paths and bytes. Only fixed basenames are
    # written; on verification failure remove them before fresh collection.
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or any((output / name).exists() or (output / name).is_symlink()
                                  for name in (*files, "original-report-task.txt", "reuse.json")):
        raise ValueError("Reuse requires an empty evidence destination")
    try:
        for name, data in files.items():
            (output / name).write_bytes(data)
        preflight.verify(root, head, producer["base"], output, producer["manifest_sha256"])
        current = api(f"actions/runs/{run['id']}")
        if any(current.get(key) != run.get(key) for key in ("id", "head_sha", "run_attempt", "status", "conclusion")):
            raise ValueError("Producer changed during evidence retrieval")
        (output / "original-report-task.txt").write_bytes(task)
        receipt = {"schema": 1, "kind": "exact-main-evidence-reuse", "producer": producer,
                   "evidence_artifact_id": evidence_artifact["id"], "task_artifact_id": task_artifact["id"],
                   "evidence_job_id": evidence_job["id"], "analysis_job_id": scan_job["id"],
                   "original_ce_task_id": task_id, "consumer_run": os.environ["GITHUB_RUN_ID"],
                   "consumer_attempt": os.environ["GITHUB_RUN_ATTEMPT"], "consumer_ref": os.environ["GITHUB_REF"],
                   "tag_analysis": "not_submitted; original successful main analysis retained"}
        (output / "reuse.json").write_bytes(preflight.json_bytes(receipt))
        return producer
    except (ValueError, OSError, KeyError, TypeError):
        for name in (*files, "original-report-task.txt", "reuse.json"):
            (output / name).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "reuse"))
    args = parser.parse_args()
    root, output = Path.cwd(), Path.cwd() / ".sonar-evidence"
    if args.action == "record":
        record(root, output)
        return 0
    try:
        producer = reuse(root, output)
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(f"Exact-main reuse unavailable ({type(exc).__name__}); collecting fresh evidence.")
        return 0
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"reused=true\nbase={producer['base']}\nmanifest_sha256={producer['manifest_sha256']}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
        stream.write(f"Reused original main coverage and analysis provenance from run {producer['run_id']}, attempt {producer['run_attempt']}. No tag analysis was submitted.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
