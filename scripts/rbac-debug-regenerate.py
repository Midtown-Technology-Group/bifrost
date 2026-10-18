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
import runpy
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


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

    def __init__(self, message: str, *, captured_output: str = "") -> None:
        super().__init__(message)
        self.captured_output = captured_output


def record(**fields: object) -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    row = json.dumps(fields, sort_keys=True)
    with STAGES.open("a", encoding="utf-8") as stream:
        stream.write(row + "\n")
    print(row, flush=True)


def run(stage: str, argv: list[str], *, env: dict[str, str] | None = None,
        stdin_data: str | None = None) -> str:
    # Do not tee raw debug output: login and generated credentials are printed
    # by the normal launcher. Suppress all child output rather than guess which
    # credential formats a redactor might miss.
    result = subprocess.run(
        argv, cwd=ROOT, env=env or ENV, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace", check=False, input=stdin_data,
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
        raise DiagnosticFailure(f"{stage} exited {result.returncode}; child output suppressed",
                                captured_output=result.stdout)
    return result.stdout


def sanitized_failure_details(output: str) -> dict[str, object]:
    """Project log text to bounded locations and fixed categories, never messages."""
    classes = {
        "ModuleNotFoundError", "ImportError", "SyntaxError", "IndentationError", "AttributeError",
        "TypeError", "ValueError", "KeyError", "NameError", "RuntimeError", "AssertionError",
        "PermissionError", "FileNotFoundError", "OSError", "ConnectionError", "ConnectionRefusedError",
        "ConnectionResetError", "TimeoutError", "OperationalError", "ProgrammingError", "IntegrityError",
        "InvalidRequestError", "DBAPIError", "InterfaceError", "PostgresError", "UndefinedTableError",
        "UndefinedColumnError", "DuplicateTableError", "DuplicateColumnError", "InvalidPasswordError",
        "InvalidAuthorizationSpecificationError", "InvalidCatalogNameError", "ConnectionDoesNotExistError",
        "CannotConnectNowError", "TooManyConnectionsError", "DependentObjectsStillExistError",
        "ObjectNotInPrerequisiteStateError", "CircularDependencyError", "CommandError", "ValidationError",
        "PydanticImportError", "PydanticUserError", "HTTPError", "HTTPStatusError", "ConnectError",
        "ConnectTimeout", "ReadTimeout", "RemoteProtocolError", "AuthenticationError",
    }
    exceptions = sorted({name for name in classes if re.search(r"\b" + name + r"\s*(?=:|$)", output, re.MULTILINE)})
    frames = set()
    for filename, line in re.findall(r'^(?:#[0-9]+ [0-9.]+\s+)?\s*File "([^"\r\n]+)", line ([0-9]{1,7})', output, re.MULTILINE):
        if (re.fullmatch(r"/(?:app|repo|usr/local/lib/python3\.14)/[A-Za-z0-9_./-]{1,240}\.(?:py|pyx)", filename)
                and ".." not in Path(filename).parts):
            frames.add((filename, int(line)))
    lower = output.lower()
    categories = {name for name, literal in {
        "connection_refused": "connection refused", "connection_reset": "connection reset",
        "connection_timeout": "connection timed out", "dns_failure": "name or service not known",
        "temporary_dns_failure": "temporary failure in name resolution",
        "database_hostname_failure": "could not translate host name",
        "authentication_failure": "authentication failed", "tls_failure": "certificate verify failed",
        "docker_solve_failure": "failed to solve", "docker_dependency_failure": "dependency failed to start",
        "docker_unhealthy": "unhealthy", "disk_full": "no space left on device",
        "package_version_unavailable": "could not find a version that satisfies",
        "package_distribution_unavailable": "no matching distribution found",
        "docker_build_failure": "failed to build", "registry_rate_limit": "toomanyrequests",
        "registry_access_denied": "pull access denied", "registry_manifest_missing": "manifest unknown",
        "api_readiness_timeout": "api did not become ready",
        "client_config_load_failure": "failed to load config",
        "client_missing_module": "cannot find module",
        "client_missing_package": "cannot find package",
        "client_address_in_use": "eaddrinuse",
        "client_bind_permission_denied": "eacces",
        "client_optional_dependency_failure": "npm has a bug related to optional dependencies",
        "client_vite_ready": "ready in",
    }.items() if literal in lower}
    statuses = sorted({int(value) for value in re.findall(r'HTTP/[0-9.]+[" ]+([1-5][0-9]{2})\b', output)})
    return {"exception_classes": exceptions, "traceback_locations": [
        {"filename": filename, "line": line} for filename, line in sorted(frames)[:100]],
        "categories": sorted(categories), "http_statuses": statuses}


def collect_failed_boot(project: str, boot_output: str, *, phase: str = "boot") -> None:
    if not re.fullmatch(r"bifrost-debug-[0-9a-f]{8}", project):
        raise DiagnosticFailure("Invalid failed-boot project identity")
    if phase not in {"boot", "readiness", "smoke"}:
        raise DiagnosticFailure("Invalid debug failure diagnostic phase")
    report: dict[str, object] = {"source_sha": os.environ["GITHUB_SHA"], "project": project, "phase": phase,
        "boot_summary": sanitized_failure_details(boot_output), "containers": [], "collection_errors": []}
    destination = ARTIFACT / f"debug-{phase}-failure.json"

    def save() -> None:
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    save()
    services = set(re.findall(r"^  ([a-z][a-z0-9_-]{0,63}):$",
        (ROOT / "docker-compose.debug.yml").read_text(encoding="utf-8").split("\nvolumes:", 1)[0], re.MULTILINE))
    container_ids = run("failure-container-list", ["docker", "ps", "-a", "--filter",
        "label=com.docker.compose.project=" + project, "--format", "{{.ID}}" ]).split()
    if len(container_ids) > 32:
        raise DiagnosticFailure("Failed-boot container count exceeds the diagnostic bound")
    inspect_format = ('{"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"image_id":{{json .Image}},'
        '"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},'
        '"oom_killed":{{json .State.OOMKilled}},'
        '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}')
    containers = []
    errors = []
    for container_id in container_ids:
        if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            raise DiagnosticFailure("Invalid failed-boot container identity")
        try:
            state = json.loads(run("failure-container-state", ["docker", "inspect", "--format", inspect_format, container_id]))
            if (set(state) != {"project", "service", "image_id", "status", "exit_code", "oom_killed", "health"}
                    or state["project"] != project or state["service"] not in services
                    or not isinstance(state["image_id"], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", state["image_id"])
                    or state["status"] not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
                    or state["health"] not in {None, "starting", "healthy", "unhealthy"}
                    or type(state["exit_code"]) is not int or type(state["oom_killed"]) is not bool):
                raise DiagnosticFailure("Unexpected failed-boot state fields")
            state.pop("project")
            state["container_id"] = container_id
            try:
                logs = run("failure-container-log-scan", ["docker", "logs", "--tail", "500", container_id])
            except DiagnosticFailure as exc:
                logs = exc.captured_output
                state["log_scan_failed"] = True
            state["log_summary"] = sanitized_failure_details(logs)
            containers.append(state)
        except Exception as exc:
            errors.append({"container_id": container_id, "exception_class": type(exc).__name__})
        report["containers"] = containers
        report["collection_errors"] = errors
        save()
    record(stage=f"failed-{phase}-diagnostics", containers=len(containers), collection_errors=len(errors))


def wait_for_debug_frontend(project: str, worktree_url: str) -> None:
    """Establish the newly booted Vite listener/proxy before running behavior.

    debug.sh currently waits for the API's internal readiness, while the client
    starts only after that API is healthy. Poll only absent/reset listeners
    while the exact client is running; actual HTTP/schema/config errors fail
    immediately and never trigger a behavioral-smoke retry.
    """
    parsed = urlsplit(worktree_url)
    if parsed.scheme != "http" or parsed.hostname != "localhost" or parsed.path or not parsed.port:
        raise DiagnosticFailure("Unexpected frontend readiness URL")
    containers = run("readiness-client-container", ["docker", "ps", "-aq", "--filter",
        f"label=com.docker.compose.project={project}", "--filter", "label=com.docker.compose.service=client"]).split()
    if len(containers) != 1 or not re.fullmatch(r"[a-f0-9]{12,64}", containers[0]):
        raise DiagnosticFailure("Expected exactly one owned client container")
    container_id = containers[0]
    published = run("readiness-client-published-port", ["docker", "port", container_id, "80/tcp"]).splitlines()
    if published != [f"127.0.0.1:{parsed.port}"]:
        raise DiagnosticFailure("Printed frontend URL differs from the owned client's published port")

    class RejectRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise DiagnosticFailure("Frontend readiness redirected away from its expected endpoint")

    http = build_opener(ProxyHandler({}), RejectRedirect())
    started = time.monotonic()
    deadline = started + 90
    observations = []
    destination = ARTIFACT / "debug-frontend-readiness.json"

    def save(passed: bool) -> None:
        destination.write_text(json.dumps({"source_sha": os.environ["GITHUB_SHA"], "project": project,
            "passed": passed, "elapsed_ms": round((time.monotonic() - started) * 1000),
            "observations": observations}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    while time.monotonic() < deadline:
        state = run("readiness-client-state", ["docker", "inspect", "--format", "{{.State.Status}}", container_id]).strip()
        if state not in {"created", "running"}:
            observations.append({"outcome": "client_not_running"})
            save(False)
            raise DiagnosticFailure("Owned client exited or restarted before frontend readiness")
        try:
            request = Request(f"http://127.0.0.1:{parsed.port}/openapi.json", headers={"Accept": "application/json"})
            with http.open(request, timeout=min(3, max(0.1, deadline - time.monotonic()))) as response:
                payload = response.read(4_000_001)
                status = response.status
            if status != 200 or len(payload) > 4_000_000:
                observations.append({"outcome": "unexpected_http_response", "status": status})
                save(False)
                raise DiagnosticFailure("Frontend readiness returned an unexpected HTTP response")
            try:
                schema = json.loads(payload)
            except (ValueError, UnicodeError):
                observations.append({"outcome": "non_json_response", "status": status})
                save(False)
                raise DiagnosticFailure("Frontend proxy did not return JSON OpenAPI") from None
            if not isinstance(schema, dict) or not isinstance(schema.get("openapi"), str) or not isinstance(schema.get("paths"), dict) or "/auth/login" not in schema["paths"]:
                observations.append({"outcome": "unexpected_schema", "status": status})
                save(False)
                raise DiagnosticFailure("Frontend proxy did not return the current API contract")
            observations.append({"outcome": "ready", "status": status, "schema_sha256": hashlib.sha256(payload).hexdigest()})
            save(True)
            record(stage="debug-frontend-ready", observations=len(observations), elapsed_ms=round((time.monotonic() - started) * 1000))
            return
        except HTTPError as exc:
            observations.append({"outcome": "http_failure", "status": exc.code})
            save(False)
            raise DiagnosticFailure("Frontend proxy returned an HTTP error during startup readiness") from None
        except (URLError, OSError) as exc:
            # These observations are explicit startup readiness, never a retry
            # of a failed grant mutation or behavioral assertion.
            observations.append({"outcome": "listener_not_ready", "exception_class": type(exc).__name__})
            save(False)
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise DiagnosticFailure("Owned frontend listener did not become ready within the startup deadline")


def run_debug_smoke(worktree_url: str, status: str, source_sha: str) -> None:
    login = re.search(r"^Login:\s+(\S+) / (\S+)\s*$", status, re.MULTILINE)
    if not login:
        raise DiagnosticFailure("Fresh owned debug status did not provide its login")
    # The smoke guard requires a literal loopback IP. Retain the printed URL in
    # identity and use only its validated localhost prefix/unchanged port here.
    smoke_url = worktree_url.replace("http://localhost:", "http://127.0.0.1:", 1)
    script = ROOT / "scripts/rbac-debug-smoke.py"
    failure = None
    try:
        output = run("debug-api-smoke", ["python3", str(script), "--diagnostic-branch",
            os.environ["GITHUB_REF_NAME"], "--allow-fresh-admin-mfa-setup"],
            stdin_data=json.dumps({"url": smoke_url, "email": login[1], "password": login[2]}))
    except DiagnosticFailure as exc:
        output = exc.captured_output
        failure = exc
    receipt = json.loads(output)
    required = {"passed", "checks", "cleanup", "limitations", "candidate_sha", "script_sha256"}
    if (not isinstance(receipt, dict) or not required <= set(receipt)
            or set(receipt) - required - {"failure"} or type(receipt["passed"]) is not bool
            or receipt["candidate_sha"] != source_sha
            or receipt["script_sha256"] != hashlib.sha256(script.read_bytes()).hexdigest()):
        raise DiagnosticFailure("Smoke receipt identity or field contract is invalid")
    if (not isinstance(receipt["checks"], list) or len(receipt["checks"]) > 500
            or not isinstance(receipt["cleanup"], dict)):
        raise DiagnosticFailure("Smoke receipt collection contract is invalid")
    for check in receipt["checks"]:
        if (not isinstance(check, dict) or not {"check", "passed"} <= set(check)
                or set(check) - {"check", "passed", "status"} or type(check["passed"]) is not bool
                or not isinstance(check["check"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,120}", check["check"])
                or ("status" in check and (type(check["status"]) is not int or not 100 <= check["status"] <= 599))):
            raise DiagnosticFailure("Smoke check contains unexpected fields or values")
    cleanup_names = {"jobs_cancelled", "keys_revoked", "devices_disabled", "users_deleted",
                     "roles_deleted", "organizations_disabled"}
    if (set(receipt["cleanup"]) - cleanup_names
            or any(type(count) is not int or not 0 <= count <= 1000 for count in receipt["cleanup"].values())):
        raise DiagnosticFailure("Smoke cleanup receipt is invalid")
    limitations = {
        "Synthetic debug stack only; no real device agent or script execution",
        "No split-org, foreign allowlist, promotion, namespace or complete migration rehearsal proof",
        "Devices, cancelled jobs and audit records require parent debug-stack teardown for physical removal",
    }
    if not isinstance(receipt["limitations"], list) or set(receipt["limitations"]) != limitations:
        raise DiagnosticFailure("Smoke limitation contract is invalid")
    if "failure" in receipt:
        item = receipt["failure"]
        if (not isinstance(item, dict) or "check" not in item or set(item) - {"check", "status", "type"}
                or not isinstance(item["check"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,120}", item["check"])
                or ("type" in item and (not isinstance(item["type"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", item["type"])))
                or (item.get("status") is not None and (type(item["status"]) is not int or not 100 <= item["status"] <= 599))):
            raise DiagnosticFailure("Smoke failure receipt is invalid")
    if receipt["passed"] != (failure is None) or receipt["passed"] == ("failure" in receipt):
        raise DiagnosticFailure("Smoke receipt outcome is inconsistent")
    (ARTIFACT / "debug-smoke-proof.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record(stage="debug-api-smoke-proof", passed=receipt["passed"], checks=len(receipt["checks"]))
    if failure:
        raise failure


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


def copy_mirror_updates() -> None:
    """Export the supported producer's complete delta, with canonical ownership."""
    mirror_roots = (".agents/skills", "plugins/bifrost/skills")
    guard = runpy.run_path(str(ROOT / "scripts/check_skill_mirrors.py"))
    expected = guard["_expected_mirrors"]()
    if set(expected) != set(mirror_roots):
        raise DiagnosticFailure("Unexpected canonical mirror ownership roots")
    changed = run("mirror-tracked-delta", ["git", "diff", "--name-only", "--no-renames", "-z",
        "HEAD", "--", *mirror_roots])
    untracked = run("mirror-new-files", ["git", "ls-files", "--others", "--exclude-standard", "-z",
        "--", *mirror_roots])
    files: list[dict[str, object]] = []
    deletions: list[dict[str, str]] = []
    for relative in sorted(set((changed + untracked).split("\0")) - {""}):
        mirror = next((prefix for prefix in mirror_roots if relative.startswith(prefix + "/")), None)
        if mirror is None or "\\" in relative or Path(relative).as_posix() != relative or ".." in Path(relative).parts:
            raise DiagnosticFailure("Mirror delta contains an out-of-scope path")
        suffix = relative[len(mirror) + 1:]
        destination = ROOT / relative
        canonical_relative = ".claude/skills/" + suffix
        canonical = ROOT / canonical_relative
        if destination.resolve() != ROOT.resolve() / relative or canonical.resolve() != ROOT.resolve() / canonical_relative:
            raise DiagnosticFailure("Mirror delta contains a linked path")
        owned = expected[mirror].get(suffix)
        if owned is None:
            if destination.exists() or destination.is_symlink():
                raise DiagnosticFailure("Mirror delta has no canonical owner")
            prior_blob = run("mirror-deletion-identity", ["git", "rev-parse", "HEAD:" + relative]).strip()
            if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", prior_blob):
                raise DiagnosticFailure("Deleted mirror file has no tracked identity")
            deletions.append({"path": relative, "prior_git_blob": prior_blob})
            continue
        content, executable = owned
        if (not canonical.is_file() or canonical.is_symlink() or not destination.is_file()
                or destination.is_symlink() or canonical.read_bytes() != content
                or destination.read_bytes() != content
                or bool(destination.stat().st_mode & 0o111) != executable):
            raise DiagnosticFailure("Mirror delta does not match its canonical source")
        digest = hashlib.sha256(content).hexdigest()
        copy_generated(relative)
        files.append({"path": relative, "canonical_source": canonical_relative,
                      "sha256": digest, "executable": executable})
    manifest = {"source_sha": os.environ["GITHUB_SHA"], "files": files, "deletions": deletions}
    (ARTIFACT / "mirror-change-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record(stage="mirror-artifact-closure", files=len(files), deletions=len(deletions))


def regenerate_mirrors_only() -> None:
    source_sha = run("mirror-only-source-sha", ["git", "rev-parse", "HEAD"]).strip()
    if source_sha != os.environ.get("GITHUB_SHA"):
        raise DiagnosticFailure("Mirror checkout is not the dispatched source SHA")
    if run("mirror-only-clean-source", ["git", "status", "--porcelain", "--untracked-files=all"]).strip():
        raise DiagnosticFailure("Mirror-only producer requires a fresh clean checkout")
    inputs = {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() for relative in (
        "scripts/rbac-debug-regenerate.py", "scripts/sync-codex-skills.sh", "scripts/check_skill_mirrors.py")}
    (ARTIFACT / "mirror-input-identity.json").write_text(json.dumps({
        "purpose": "diagnostic-mirror-production-only-not-candidate-acceptance",
        "source_sha": source_sha, "source_ref": BRANCH, "producer_sha256": inputs,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run("sync-skill-mirrors", ["bash", "scripts/sync-codex-skills.sh"])
    copy_mirror_updates()


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
        try:
            run("debug-up", ["bash", "debug.sh", "up"])
        except DiagnosticFailure as boot_failure:
            try:
                collect_failed_boot(project, boot_failure.captured_output)
            except Exception as diagnostic_error:
                record(stage="failed-boot-diagnostic-error", exception_class=type(diagnostic_error).__name__)
            raise
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
        try:
            wait_for_debug_frontend(project, worktree_url)
        except DiagnosticFailure as readiness_failure:
            try:
                collect_failed_boot(project, readiness_failure.captured_output, phase="readiness")
            except Exception as diagnostic_error:
                record(stage="failed-readiness-diagnostic-error", exception_class=type(diagnostic_error).__name__)
            raise
        try:
            run_debug_smoke(worktree_url, status, source_sha)
        except DiagnosticFailure as smoke_failure:
            try:
                collect_failed_boot(project, smoke_failure.captured_output, phase="smoke")
            except Exception as diagnostic_error:
                record(stage="failed-smoke-diagnostic-error", exception_class=type(diagnostic_error).__name__)
            raise
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
        for path in sorted((ROOT / ".claude/skills/bifrost-build/generated").glob("*.md")):
            copy_generated(path.relative_to(ROOT).as_posix())
        copy_mirror_updates()
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
        elif sys.argv[1:] == ["--mirrors-only"]:
            regenerate_mirrors_only()
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
