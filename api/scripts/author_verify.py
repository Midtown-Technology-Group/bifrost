"""Focused author feedback, using the CI graph without changing CI acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

if __package__:
    from . import plan_affected_tests as affected
else:
    import plan_affected_tests as affected

ROOT = Path(__file__).resolve().parents[2]
CLIENT_PREFIX = "client/"
TEST_RUNNER = "./test.sh"

# Only invisible/high-value boundary edges belong here. Ordinary imports remain
# owned by plan_affected_tests. Missing targets fail; mappings must stay reviewable.
RISK_TESTS = {
    "sdk": [
        "tests/unit/services/test_azure_sdk_boundary.py",
        "tests/unit/services/test_azure_blob_lifecycle.py",
        "tests/unit/services/file_storage/test_azure_blob_client.py",
    ],
    "database": ["tests/e2e/platform/test_authoring_postgres.py"],
    "concurrency": [
        "tests/e2e/platform/test_authoring_concurrency.py",
        "tests/unit/test_service_lifecycle.py",
        "tests/unit/test_service_claim.py",
    ],
    "security": [
        "tests/unit/test_agent_run_authorization.py",
        "tests/unit/test_cli_sdk_external.py",
        "tests/e2e/api/test_authoring_authorization.py",
    ],
    "deadline": [
        "tests/unit/sdk/test_sdk_timeout_plumbing.py",
        "tests/unit/sdk/test_authoring_deadline.py",
    ],
    "contracts": [
        "tests/unit/test_contracts_parity.py",
        "tests/unit/test_contract_version.py",
        "tests/unit/services/test_operation_inventory.py",
    ],
    "consumers": [
        "tests/unit/services/test_app_storage.py",
        "tests/unit/test_app_compiler.py",
        "tests/unit/test_app_bundler.py",
    ],
}
RISK_PATHS = {
    "sdk": ("api/src/services/file_storage/azure_blob_client.py",),
    "database": ("api/alembic/", "api/src/models/orm/"),
    "concurrency": (
        "api/src/services/service_lifecycle.py",
        "api/src/services/service_claim.py",
    ),
    "security": (
        "api/src/core/auth",
        "api/src/services/knowledge",
        "api/src/services/agent_run_access.py",
    ),
    "deadline": ("api/bifrost/agents.py",),
    "contracts": (
        "api/src/models/contracts/",
        "api/shared/",
        "api/bifrost/",
        "api/src/routers/",
        "client/src/lib/app-sdk/",
        "client/src/lib/v1.d.ts",
        "api/src/services/operation_catalog",
        "api/src/services/operation_inventory.py",
        "api/scripts/operation_catalog/",
        "api/scripts/skill-truth/",
    ),
    "consumers": ("api/src/services/app_compiler/", "api/src/services/app_storage.py"),
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def resolve_base(ref: str) -> str:
    # Accept local refs and ordinary ancestry expressions, never Git options or
    # rev-parse modes. End option parsing even after validating the operand.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./~^{}@-]*", ref):
        raise ValueError("base must be a local ref or commit ancestry expression")
    return git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}")


def changes(base: str) -> tuple[affected.GitChange, ...]:
    # Include staged, unstaged and untracked author work. Disable rename folding:
    # a removal remains a broad-impact signal in the trusted planner.
    raw = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-status", "-z", base, "--"], cwd=ROOT
    ).decode()
    fields = raw.split("\0")
    result = [
        affected.GitChange(fields[i], fields[i + 1])
        for i in range(0, len(fields) - 1, 2)
    ]
    result.extend(
        affected.GitChange("A", p)
        for p in git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
        if p
    )
    return tuple(result)


def identity(base: str) -> dict:
    paths = sorted({c.path for c in changes(base)})
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.encode() + b"\0")
        target = ROOT / path
        if target.exists() or target.is_symlink():
            digest.update(str(target.lstat().st_mode).encode() + b"\0")
        if target.is_symlink():
            digest.update(os.readlink(target).encode())
        else:
            digest.update(target.read_bytes() if target.is_file() else b"<deleted>")
    return {
        "head_sha": git("rev-parse", "HEAD"),
        "base_sha": base,
        "dirty": bool(git("status", "--porcelain", "--untracked-files=all")),
        "changed_content_sha256": digest.hexdigest(),
    }


def add_extra_tests(
    extra: list[str], backend: set[str], client_unit: set[str], client_e2e: set[str]
) -> None:
    for target in extra:
        if target.startswith("tests/"):
            backend.add(target)
        elif target.startswith("client/e2e/"):
            client_e2e.add(target.removeprefix(CLIENT_PREFIX))
        elif target.startswith("client/src/") and ".test." in target:
            client_unit.add(target.removeprefix(CLIENT_PREFIX))
        else:
            raise ValueError(f"unsupported test selector: {target}")


def test_commands(
    active: list[str], backend: set[str], client_unit: set[str], client_e2e: set[str]
) -> list[list[str]]:
    commands = []
    if set(active) & {"security", "deadline", "concurrency", "contracts"}:
        commands.append([TEST_RUNNER, "quality", "api"])
    if "contracts" in active:
        commands.append([TEST_RUNNER, "author-generated"])
    # Unit neighbors share one process with workers paused; integration tests
    # retain their normal service lifecycle in a separate batch.
    unit = sorted(target for target in backend if target.startswith("tests/unit/"))
    integration = sorted(backend - set(unit))
    for targets in (unit, integration):
        if targets:
            commands.append([TEST_RUNNER, *targets, "-v", "--durations=0"])
    if client_unit:
        commands.append([TEST_RUNNER, "client", "unit", *sorted(client_unit)])
    if client_e2e:
        commands.append([TEST_RUNNER, "client", "e2e", *sorted(client_e2e)])
    return commands


def selection(plan: affected.AffectedPlan, risks: list[str], extra: list[str]) -> dict:
    active = sorted(
        set(risks)
        | {
            r
            for r, prefixes in RISK_PATHS.items()
            if any(p.startswith(prefixes) for p in plan.changed_paths)
        }
    )
    backend = set(plan.python.unit_tests) | set(plan.python.e2e_tests)
    backend.update(
        path.removeprefix("api/")
        for path in plan.changed_paths
        if affected._is_python_test(path) and (ROOT / path).is_file()
    )
    for risk in active:
        backend.update(RISK_TESTS[risk])
    client_unit = set(plan.client.unit_tests)
    client_e2e = set(plan.client.e2e_tests)
    client_unit.update(
        path.removeprefix(CLIENT_PREFIX)
        for path in plan.changed_paths
        if affected._is_client_unit(path) and (ROOT / path).is_file()
    )
    client_e2e.update(
        path.removeprefix(CLIENT_PREFIX)
        for path in plan.changed_paths
        if affected._is_client_e2e(path) and (ROOT / path).is_file()
    )
    add_extra_tests(extra, backend, client_unit, client_e2e)
    for target in backend:
        path = (ROOT / "api" / target.split("::")[0]).resolve()
        if not path.is_relative_to(ROOT / "api/tests") or not path.is_file():
            raise ValueError(f"missing or unsafe backend target: {target}")
    commands = test_commands(active, backend, client_unit, client_e2e)
    deferred = [
        f"{lane}: comprehensive validation remains required"
        for lane, mode in plan.to_dict()["lanes"].items()
        if mode == "comprehensive"
    ]
    if plan.scope != "docs-only" and not (backend or client_unit or client_e2e):
        deferred.append(
            "No focused behavioral tests found; supply --test after tracing callers"
        )
    return {"risks": active, "commands": commands, "deferred": deferred}


def junit_cases(path: Path) -> list[dict]:
    if not path.exists():
        return []
    cases = []
    for case in ET.parse(path).iter("testcase"):
        outcome = "passed"
        reason = ""
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome, reason = tag, node.get("message", "")
                break
        cases.append(
            {
                "name": f"{case.get('classname')}::{case.get('name')}",
                "outcome": outcome,
                "reason": reason,
                "seconds": float(case.get("time", "0")),
            }
        )
    return cases


def read_runner_report(project: str) -> tuple[tuple[int, int, int], bytes] | None:
    """Read the existing runner's report without following attacker-owned links.

    test.sh shares a writable results directory with its uid-1000 container.
    Pin that directory by descriptor and accept only a singly linked, regular,
    non-group/world-writable file owned by the host user or the pinned runner.
    Other users may remove a shared report, but cannot supply credited evidence.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("unsafe Compose project name")
    supplied = os.environ.get("BIFROST_AUTHOR_REPORT_DIR")
    if not supplied:
        raise OSError("runner report directory must be supplied by test.sh author")
    directory = Path(supplied)
    if not directory.is_absolute() or directory.name != "bifrost-" + project:
        raise OSError("runner report directory does not match the Compose project")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        directory_fd = os.open(directory, flags | os.O_DIRECTORY)
    except FileNotFoundError:
        return None
    try:
        if os.fstat(directory_fd).st_uid not in {os.getuid(), 1000}:
            raise PermissionError("runner report directory has an unexpected owner")
        try:
            report_fd = os.open("test-results.xml", flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(report_fd, "rb") as report:
            info = os.fstat(report.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid not in {os.getuid(), 1000}
                or info.st_mode & 0o022
            ):
                raise PermissionError("unsafe runner report ownership, links or mode")
            return (info.st_ino, info.st_mtime_ns, info.st_size), report.read()
    finally:
        os.close(directory_fd)


def run_command(
    command: list[str], evidence: Path, index: int, project: str
) -> tuple[dict, list[str]]:
    backend = command[1].startswith("tests/")
    prior_xml = read_runner_report(project) if backend else None
    start = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    result = {
        "argv": command,
        "exit_code": completed.returncode,
        "seconds": round(time.monotonic() - start, 3),
        "outcome": "passed" if completed.returncode == 0 else "failed",
        "cases": [],
    }
    gaps = []
    if backend:
        current_xml = read_runner_report(project)
        if current_xml is not None and current_xml != prior_xml:
            copied = evidence / f"{index}-junit.xml"
            copied.write_bytes(current_xml[1])
            result["cases"] = junit_cases(copied)
        if not result["cases"]:
            result["outcome"] = "missing-test-evidence"
        elif any(case["outcome"] in {"failure", "error"} for case in result["cases"]):
            result["outcome"] = "failed"
        if any(case["outcome"] == "skipped" for case in result["cases"]):
            if result["outcome"] == "passed":
                result["outcome"] = "incomplete"
            gaps.append(f"command {index} skipped tests; see named cases/reasons")
    elif command[1:3] in (["client", "e2e"], ["client", "unit"]):
        gaps.append(
            f"command {index}: consult native client report for named tests/skips"
        )
    return result, gaps


def execute_commands(
    receipt: dict, evidence: Path, project: str, save: Callable[[], None]
) -> int:
    for index, command in enumerate(receipt["selection"]["commands"]):
        receipt["commands"].append({"argv": command, "outcome": "running", "cases": []})
        save()
        result, gaps = run_command(command, evidence, index, project)
        receipt["commands"][-1] = result
        receipt["acceptance_gaps"].extend(gaps)
        save()
        if result["outcome"] != "passed":
            return result["exit_code"] or 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="local base ref; fetch explicitly before final evidence",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="print selection only; no commands or evidence credited",
    )
    parser.add_argument(
        "--risk", action="append", choices=sorted(RISK_TESTS), default=[]
    )
    parser.add_argument("--test", action="append", default=[])
    parser.add_argument(
        "--boundary",
        action="append",
        default=[],
        help="name real and mocked boundaries",
    )
    parser.add_argument("--gap", action="append", default=[])
    args = parser.parse_args()
    base = resolve_base(args.base)
    before = identity(base)
    plan = affected.plan_changes(changes(base))
    selected = selection(plan, args.risk, args.test)
    if args.plan:
        print(
            json.dumps(
                {"identity": before, "plan": plan.to_dict(), **selected}, indent=2
            )
        )
        return 0
    evidence = (
        Path(
            git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "author-verification",
            )
        )
        / uuid.uuid4().hex
    )
    evidence.mkdir(parents=True)
    prefix = os.environ.get("BIFROST_PROJECT_PREFIX") or "bifrost-test"
    project = prefix + "-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]
    receipt = {
        "schema": "bifrost.author-verification/v1",
        **before,
        "host": socket.gethostname(),
        "worktree": str(ROOT),
        "compose_project": project,
        "plan": plan.to_dict(),
        "selection": selected,
        "boundaries": args.boundary
        or ["UNSPECIFIED: author must identify mocked and real boundaries"],
        "acceptance_gaps": [*selected["deferred"], *args.gap],
        "commands": [],
        "outcome": "running",
    }
    if not args.boundary:
        receipt["acceptance_gaps"].append("Mocked/real boundaries not identified")
    receipt_path = evidence / "receipt.json"

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    print(f"Author receipt: {receipt_path}", flush=True)
    code = 0
    try:
        code = execute_commands(receipt, evidence, project, save)
        if identity(base) != before:
            receipt["acceptance_gaps"].append(
                "Source changed during verification; evidence is invalid"
            )
            code = 1
        if not selected["commands"] and plan.scope != "docs-only":
            code = 2
        receipt["outcome"] = "failed" if code else "focused-complete"
    except (KeyboardInterrupt, OSError, ValueError, ET.ParseError) as exc:
        receipt["outcome"] = "interrupted-or-error"
        if receipt["commands"] and receipt["commands"][-1]["outcome"] == "running":
            receipt["commands"][-1]["outcome"] = "interrupted-or-error"
        receipt["acceptance_gaps"].append(str(exc))
        code = 1
    finally:
        receipt["unrun_commands"] = selected["commands"][len(receipt["commands"]) :]
        save()
    print(
        f"{receipt['outcome']}: {receipt_path}; {len(receipt['acceptance_gaps'])} acceptance gaps (not a PR gate)"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
