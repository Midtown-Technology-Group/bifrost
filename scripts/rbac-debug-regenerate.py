#!/usr/bin/env python3
"""TEMPORARY CI-only source regeneration; remove before the final PR.

Child output, including debug login/credential lines, stays in memory and is
never printed or retained. Evidence contains only stage outcomes and hashes.
This diagnostic does not satisfy the repository's acceptance or runtime gates.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refs/heads/codex/upstream-rbac-sync-20261017"
ARTIFACT = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "rbac-regenerated-inputs"
IDENTITY = ARTIFACT / "debug-identity.json"
STAGES = ARTIFACT / "debug-sanitized-stages.jsonl"
GENERATED = ARTIFACT / "generated"
ENV = {**os.environ, "BIFROST_FORCE_PORT": "1"}
ENV.pop("NETBIRD_SETUP_KEY", None)


class DiagnosticFailure(RuntimeError):
    """Failure text must contain no child process output."""


def record(**fields: object) -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    row = json.dumps(fields, sort_keys=True)
    with STAGES.open("a", encoding="utf-8") as stream:
        stream.write(row + "\n")
    print(row, flush=True)


def run(stage: str, argv: list[str], *, env: dict[str, str] | None = None) -> str:
    # Do not tee raw debug output: login and generated credentials are printed
    # by the normal launcher. Suppress all child output rather than guess which
    # credential formats a redactor might miss.
    result = subprocess.run(
        argv, cwd=ROOT, env=env or ENV, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace", check=False,
    )
    failure_markers = [marker for marker in (
        "ModuleNotFoundError", "ImportError", "SyntaxError", "ValidationError",
        "RuntimeError", "AssertionError", "failed to solve", "dependency failed to start",
        "unhealthy", "no space left on device", "api did not become ready",
    ) if marker in result.stdout] if result.returncode else []
    record(stage=stage, exit_code=result.returncode, failure_markers=failure_markers,
           suppressed_output_bytes=len(result.stdout.encode("utf-8")),
           suppressed_output_sha256=hashlib.sha256(result.stdout.encode("utf-8")).hexdigest())
    if result.returncode:
        raise DiagnosticFailure(f"{stage} exited {result.returncode}; child output suppressed")
    return result.stdout


def inventory(project: str, phase: str) -> dict[str, list[str]]:
    label = f"label=com.docker.compose.project={project}"
    commands = {
        "containers": ["docker", "ps", "-a", "--filter", label, "--format", "{{.ID}}"],
        "networks": ["docker", "network", "ls", "--filter", label, "--format", "{{.ID}}"],
        "volumes": ["docker", "volume", "ls", "--filter", label, "--format", "{{.Name}}"],
    }
    return {kind: run(f"{phase}-{kind}", argv).split() for kind, argv in commands.items()}


def write_identity(value: dict[str, object]) -> None:
    IDENTITY.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_generated(relative: str) -> None:
    source = ROOT / relative
    if not source.is_file() or source.is_symlink():
        raise DiagnosticFailure("Generated artifact absent or not a regular file")
    target = GENERATED / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    record(stage="generated-artifact", path=relative,
           sha256=hashlib.sha256(source.read_bytes()).hexdigest())


def cleanup() -> None:
    if not IDENTITY.is_file():
        record(stage="cleanup", result="no-owned-debug-stack-recorded")
        return
    identity = json.loads(IDENTITY.read_text(encoding="utf-8"))
    if not identity.get("task_owned"):
        record(stage="cleanup", result="preexisting-resources-preserved")
        return
    project = identity["project"]
    if not isinstance(project, str) or not re.fullmatch(r"bifrost-debug-[0-9a-f]{8}", project):
        raise DiagnosticFailure("Unsafe cleanup project identity")
    if identity.get("worktree") != str(ROOT) or identity.get("source_sha") != os.environ.get("GITHUB_SHA"):
        raise DiagnosticFailure("Cleanup source/worktree identity changed")
    down_failure: DiagnosticFailure | None = None
    try:
        run("debug-down", ["bash", "debug.sh", "down"])
    except DiagnosticFailure as exc:
        down_failure = exc
    remaining = inventory(project, "final")
    identity["final_resources"] = remaining
    identity["cleanup_verified"] = False
    write_identity(identity)
    if down_failure or any(remaining.values()):
        raise DiagnosticFailure("Debug teardown failed or task containers/networks/volumes remain")
    status = run("debug-status-after-cleanup", ["bash", "debug.sh", "status"])
    if not re.search(r"^Status:\s+DOWN\s*$", status, re.MULTILINE):
        raise DiagnosticFailure("Debug status did not report DOWN after independent empty inventories")
    identity["cleanup_verified"] = True
    write_identity(identity)
    record(stage="cleanup", result="verified-no-project-containers-networks-volumes")


def regenerate() -> None:
    source_sha = run("source-sha", ["git", "rev-parse", "HEAD"]).strip()
    if source_sha != os.environ.get("GITHUB_SHA"):
        raise DiagnosticFailure("Checkout is not the dispatched source SHA")
    project = run("derive-project", ["bash", "-c",
        "source scripts/lib/test_helpers.sh; BIFROST_PROJECT_PREFIX=bifrost-debug compute_project_name ."]).strip()
    if not re.fullmatch(r"bifrost-debug-[0-9a-f]{8}", project):
        raise DiagnosticFailure("Invalid worktree project identity")
    initial = inventory(project, "initial")
    identity = {"purpose": "diagnostic-only-not-candidate-acceptance", "host": socket.gethostname(),
                "worktree": str(ROOT), "project": project, "source_sha": source_sha,
                "initial_resources": initial, "task_owned": False}
    write_identity(identity)
    if any(initial.values()):
        raise DiagnosticFailure("Fresh CI project has preexisting resources; preserved without boot or teardown")
    status = run("debug-status-before-boot", ["bash", "debug.sh", "status"])
    if (not re.search(r"^Status:\s+DOWN\s*$", status, re.MULTILINE)
            or not re.search(r"^Project:\s+" + re.escape(project) + r"\s*$", status, re.MULTILINE)):
        raise DiagnosticFailure("Initial debug status is not DOWN")
    identity["task_owned"] = True
    write_identity(identity)
    try:
        run("debug-up", ["bash", "debug.sh", "up"])
        status = run("debug-status-running", ["bash", "debug.sh", "status"])
        match = re.search(r"^Open:\s+(http://localhost:[0-9]+)\s*$", status, re.MULTILINE)
        if (not match or not re.search(r"^Status:\s+UP\s*$", status, re.MULTILINE)
                or not re.search(r"^Project:\s+" + re.escape(project) + r"\s*$", status, re.MULTILINE)):
            raise DiagnosticFailure("Running worktree did not report its local port URL")
        worktree_url = match.group(1)
        identity["worktree_url"] = worktree_url
        api_container = run("api-container", ["docker", "ps", "-q", "--filter",
            f"label=com.docker.compose.project={project}", "--filter", "label=com.docker.compose.service=api"]).split()
        if len(api_container) != 1:
            raise DiagnosticFailure("Expected exactly one API container for this worktree")
        api_image = run("api-image", ["docker", "inspect", "--format", "{{.Image}}", api_container[0]]).strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", api_image):
            raise DiagnosticFailure("API image identity is not immutable")
        identity["api_image"] = api_image
        write_identity(identity)
        run("generate-client-types", ["npm", "--prefix", "client", "run", "generate:types"],
            env={**ENV, "OPENAPI_URL": worktree_url + "/openapi.json"})
        copy_generated("client/src/lib/v1.d.ts")

        def source_python(stage: str, args: list[str]) -> str:
            return run(stage, ["docker", "run", "--rm", "--entrypoint", "python",
                "--volume", str(ROOT) + ":/repo", "--workdir", "/repo",
                "--env", "PYTHONPATH=/repo/api",
                "--env", "BIFROST_SECRET_KEY=rbac-source-generation-fixture-not-production",
                api_image, *args])

        source_python("generate-access-list", ["api/scripts/generate_access_list.py"])
        copy_generated("docs/generated/access-list.json")
        source_python("generate-operation-catalog", ["api/scripts/operation_catalog/generate.py"])
        copy_generated("docs/generated/operation-surface-inventory.json")
        copy_generated(".claude/skills/bifrost-build/generated/operations.md")
        source_python("generate-skill-truth", ["api/scripts/skill-truth/generate.py"])
        run("sync-skill-mirrors", ["bash", "scripts/sync-codex-skills.sh"])
        for prefix in (".claude/skills/bifrost-build/generated", "plugins/bifrost/skills/bifrost-build/generated",
                       ".agents/skills/bifrost-build/generated"):
            for path in sorted((ROOT / prefix).glob("*.md")):
                copy_generated(path.relative_to(ROOT).as_posix())
        fingerprint_output = source_python("record-dto-fingerprint", ["-c",
            "import json,runpy; m=runpy.run_path('api/tests/unit/test_contract_version.py'); "
            "actual=m['_fingerprint'](m['CONTRACT_FINGERPRINT_MODELS'],m['CLI_ROUTES']); "
            "print('RBAC_FINGERPRINT='+json.dumps({'actual':actual,'expected':m['EXPECTED_CONTRACT_FINGERPRINT'],"
            "'matches':actual==m['EXPECTED_CONTRACT_FINGERPRINT'],'cli_contract_version':m['CLI_CONTRACT_VERSION'],"
            "'server_contract_version':m['SERVER_CONTRACT_VERSION']},sort_keys=True))"])
        marker = next((line for line in fingerprint_output.splitlines() if line.startswith("RBAC_FINGERPRINT=")), None)
        if marker is None:
            raise DiagnosticFailure("Fingerprint generator did not return its structured result")
        fingerprint = json.loads(marker.removeprefix("RBAC_FINGERPRINT="))
        if set(fingerprint) != {"actual", "expected", "matches", "cli_contract_version", "server_contract_version"}:
            raise DiagnosticFailure("Unexpected fingerprint record fields")
        for key in ("actual", "expected"):
            if not isinstance(fingerprint[key], str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint[key]):
                raise DiagnosticFailure("Invalid fingerprint digest")
        if (type(fingerprint["matches"]) is not bool
                or type(fingerprint["cli_contract_version"]) is not int
                or type(fingerprint["server_contract_version"]) is not int):
            raise DiagnosticFailure("Invalid fingerprint record types")
        (ARTIFACT / "dto-fingerprint.json").write_text(json.dumps(fingerprint, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record(stage="dto-fingerprint", matches=fingerprint["matches"])
    finally:
        cleanup()


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != BRANCH:
        print("This temporary helper is restricted to the authorized GitHub Actions branch.", file=sys.stderr)
        return 2
    # Cancellation should run the same finally cleanup while the runner permits.
    def stop(_signum: int, _frame: object) -> None:
        raise DiagnosticFailure("Diagnostic interrupted; attempting owned-stack cleanup")
    signal.signal(signal.SIGTERM, stop)
    try:
        if sys.argv[1:] == ["--cleanup"]:
            cleanup()
        elif not sys.argv[1:]:
            regenerate()
        else:
            raise DiagnosticFailure("Unsupported diagnostic helper arguments")
    except DiagnosticFailure as exc:
        record(stage="diagnostic-failed", reason=str(exc))
        return 1
    except Exception as exc:
        # Exception values can contain subprocess data; retain the type only.
        record(stage="diagnostic-failed", exception_type=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
