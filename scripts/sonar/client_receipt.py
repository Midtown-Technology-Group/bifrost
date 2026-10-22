#!/usr/bin/env python3
"""Transport comprehensive client LCOV within one CI run; otherwise collect.

The existing archive verifier and final Sonar preflight remain authoritative.
No report from an affected/failed/foreign-tree producer can replace collection.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import zipfile

SPEC = importlib.util.spec_from_file_location("main_receipt", Path(__file__).with_name("main_receipt.py"))
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


def context() -> dict:
    if os.environ.get("GITHUB_REPOSITORY") != transport.REPOSITORY:
        raise transport.ReuseRefused("Client coverage requires the configured repository")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event not in {"pull_request", "merge_group", "push", "workflow_dispatch"}:
        raise transport.ReuseRefused("Unsupported client coverage event")
    return {
        "schema": 1, "repository": transport.REPOSITORY, "event": event,
        "ref": os.environ["GITHUB_REF"], "source": os.environ["GITHUB_SHA"],
        "workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    }


def source_identity() -> dict:
    # Whole-tree equality binds all configuration, lockfiles, source, fixtures
    # and workflow callers, rather than maintaining an incomplete file allowlist.
    tests = transport.command(["git", "ls-files", "client/src"]).splitlines()
    tests = [name for name in tests if name.endswith((".test.ts", ".test.tsx"))]
    if not tests:
        raise transport.ReuseRefused("Complete client test inventory missing")
    return {"checkout": transport.command(["git", "rev-parse", "HEAD"]),
            "tree": transport.command(["git", "rev-parse", "HEAD^{tree}"]),
            "tests": tests,
            "node": transport.command(["node", "--version"]),
            "npm": transport.command(["npm", "--version"])}


def record(root: Path) -> None:
    report = root / "client/coverage/lcov.info"
    if report.is_symlink() or not report.is_file():
        raise transport.ReuseRefused("Complete client report missing")
    data = report.read_bytes()
    if not data or len(data) > transport.preflight.MAX_REPORT_BYTES:
        raise transport.ReuseRefused("Complete client report empty or oversized")
    receipt = context() | source_identity() | {"mode": "comprehensive", "command": "npm run test:coverage",
                                               "report_sha256": hashlib.sha256(data).hexdigest()}
    destination = root / ".client-coverage"
    destination.mkdir(exist_ok=False)
    (destination / "client.lcov").write_bytes(data)
    (destination / "producer.json").write_bytes(transport.preflight.json_bytes(receipt))


def reuse(root: Path) -> None:
    expected = context()
    head = transport.exact_head()
    artifact_id = os.environ.get("CLIENT_ARTIFACT_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", artifact_id):
        raise transport.ReuseRefused("Exact client artifact ID required")
    run_id, attempt = expected["run_id"], expected["run_attempt"]
    run = transport.api(f"actions/runs/{run_id}")
    def validate_run(current):
        if (current.get("id") != run_id or current.get("run_attempt") != attempt
                or current.get("head_sha") != head
                or current.get("event") != expected["event"] or current.get("path") != transport.WORKFLOW
                or current.get("head_repository", {}).get("full_name") != transport.REPOSITORY):
            raise transport.ReuseRefused("Client producer run/trust/attempt mismatch")
    validate_run(run)
    jobs = transport.pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    job = transport.successful_job(jobs, "Client Unit Tests")
    for name in ("Vitest", "Bind complete client coverage", "Retain complete client coverage"):
        steps = [step for step in job.get("steps", []) if step.get("name") == name]
        if len(steps) != 1 or steps[0].get("conclusion") != "success":
            raise transport.ReuseRefused("Client producer did not complete tests and export")
    artifact = transport.api(f"actions/artifacts/{artifact_id}")
    if (artifact.get("id") != int(artifact_id)
            or artifact.get("name") != f"client-coverage-{run_id}-{attempt}"):
        raise transport.ReuseRefused("Client artifact identity mismatch")
    files = transport.archive_files(artifact, {"client.lcov", "producer.json"}, job, run_id)
    receipt = json.loads(files["producer.json"])
    identity = source_identity()
    if (any(receipt.get(key) != value for key, value in expected.items())
            or receipt.get("mode") != "comprehensive"
            or receipt.get("command") != "npm run test:coverage"
            or any(receipt.get(key) != identity[key] for key in ("tree", "tests", "node", "npm"))
            or receipt.get("checkout") != expected["source"]
            or not re.fullmatch(r"[0-9a-f]{40}", receipt.get("checkout", ""))
            or identity["checkout"] != head
            or not files["client.lcov"]
            or receipt.get("report_sha256") != hashlib.sha256(files["client.lcov"]).hexdigest()):
        raise transport.ReuseRefused("Client coverage source/tree/config/toolchain/report mismatch")
    validate_run(transport.api(f"actions/runs/{run_id}"))
    destination = root / ".sonar-evidence/raw/client.lcov"
    if destination.exists() or destination.is_symlink():
        raise transport.ReuseRefused("Client reuse requires an empty report destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Write only owned fixed files; a failed write cannot contaminate fallback.
    receipt_path = root / ".sonar-evidence/client-producer.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise transport.ReuseRefused("Client reuse requires an empty receipt destination")
    try:
        receipt_path.write_bytes(files["producer.json"])
        destination.write_bytes(files["client.lcov"])
    except Exception:
        receipt_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "reuse"))
    args = parser.parse_args()
    root = Path.cwd()
    if args.action == "record":
        record(root)
        return 0
    try:
        reuse(root)
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
        # Do not expose raw provider/subprocess/foreign exception values.
        reason = str(exc) if type(exc) is transport.ReuseRefused else "Client evidence unavailable or incompatible"
        transport.summary(f"Client evidence not reused: {reason}. Existing comprehensive collection remains enabled.")
        return 0
    except Exception:
        transport.summary("Client reuse failed unexpectedly; stopped without claiming reuse.")
        return 1
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write("reused=true\n")
    transport.summary("Client tests succeeded; complete same-run LCOV reused. Final report validation remains required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
