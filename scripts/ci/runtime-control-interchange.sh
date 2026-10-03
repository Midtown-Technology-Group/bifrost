#!/usr/bin/env bash
# Hosted source gate only. Physical-host execution is prohibited.
set -euo pipefail
exec python3 - "${1:-verify}" <<'PY'
import hashlib
from collections import Counter
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import tomllib

MODE = sys.argv[1]
if MODE not in {"guard", "pre-pr", "prepare", "format", "verify", "cleanup"}:
    sys.exit(2)
ROOT = Path.cwd()
EVIDENCE = Path(os.environ["RUNTIME_CONTROL_EVIDENCE"])
STATE = EVIDENCE / "state.json"
PREFIX = "bifrost-control-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
LABEL = "bifrost.runtime-control.owner"
LOCK = "0345fd4d3b8bf4ab61169e60963d7cf0a454b46d7b55581b111cc40aaa827097"
CAPS = {"pre-pr": 360, "prepare": 240, "toolchain": 120, "fetch": 90,
        "checks": 400, "O": 90, "W": 90, "feature-build": 90, "products": 90, "cleanup": 60}
HELPER = "scripts/ci/workflow-sql-source.sh"
HELPERS = {
    HELPER: "87da2b469e242251df9d57c4483a8e81fa784db5b3e04b216a7fa743f5d8f1eb",
    "api/scripts/ci/prepare-test-images.sh": "a8a2fc0561ea470152998f2ccd87f1c9e07b816fc51505a899289de8b7092600",
}
state = json.loads(STATE.read_text())
stage_start = None
stage_name = None
counter = len(state.get("containers", []))

def save():
    temporary = STATE.with_suffix(".new")
    temporary.write_text(json.dumps(state, sort_keys=True))
    temporary.replace(STATE)

def require(condition):
    if not condition:
        raise RuntimeError("closed custody gate failed")

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

RAW_LIMIT = 16 * 1024 * 1024
RAW_TOTAL_LIMIT = 64 * 1024 * 1024

class GateFailure(Exception):
    def __init__(self, code):
        self.code = code if code >= 0 else 128 - code

class GateInterrupted(BaseException):
    def __init__(self, code):
        self.code = code

def interrupted(signum, _frame):
    raise GateInterrupted(128 + signum)

signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGTERM, interrupted)
raw_directory = Path(os.environ["RUNNER_TEMP"]) / (PREFIX + "-private-diagnostics")
if not state.get("raw_directory"):
    raw_directory.mkdir(mode=0o700)
    info = raw_directory.lstat()
    state["raw_directory"] = {"path": str(raw_directory), "dev": info.st_dev,
                              "ino": info.st_ino, "uid": info.st_uid}
    save()
else:
    record = state["raw_directory"]
    info = raw_directory.lstat()
    require((info.st_dev, info.st_ino, info.st_uid) == (record["dev"], record["ino"], record["uid"]) and
            stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid())


def bounded_child(argv, name, timeout, env=None):
    require(timeout > 2)
    state["raw_counter"] = state.get("raw_counter", 0) + 1
    save()
    logfile = raw_directory / (name + "-" + str(state["raw_counter"]) + ".log")
    require(not logfile.exists())
    deadline = time.monotonic() + timeout
    process = selected = output = pipe = None
    byte_count, failure, original_error = 0, None, None
    code, reaped = 126, False
    cleanup_labels, cleanup_error = [], None

    def note(label, error):
        nonlocal cleanup_error
        cleanup_labels.append(label)
        if cleanup_error is None:
            cleanup_error = error

    # Every returned resource is immediately owned within this protected lifetime.
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
        pipe = process.stdout
        require(pipe is not None)
        selected = selectors.DefaultSelector()
        selected.register(pipe, selectors.EVENT_READ)
        output = logfile.open("xb")
        while selected.get_map():
            normal_left = deadline - time.monotonic() - 2
            if normal_left <= 0:
                failure = 124
                break
            for key, _ in selected.select(min(normal_left, 0.25)):
                chunk = os.read(key.fd, 65_536)
                if not chunk:
                    selected.unregister(key.fileobj)
                    continue
                if byte_count + len(chunk) > RAW_LIMIT or state.get("raw_bytes", 0) + byte_count + len(chunk) > RAW_TOTAL_LIMIT:
                    failure = 125
                    break
                output.write(chunk)
                byte_count += len(chunk)
            if failure is not None:
                break
        if failure is None:
            try:
                code = process.wait(timeout=max(0.01, deadline - time.monotonic() - 2))
                reaped = True
            except subprocess.TimeoutExpired:
                failure = 124
    except BaseException as error:
        original_error = error
        failure = error.code if isinstance(error, GateInterrupted) else 126
    finally:
        # A close/control failure cannot prevent any later independent attempt.
        for label, resource in (("log_close", output), ("selector_close", selected), ("pipe_close", pipe)):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    note(label, error)
        if process is not None:
            needs_kill = failure is not None or original_error is not None
            try:
                needs_kill = needs_kill or process.poll() is None
                reaped = process.returncode is not None
            except BaseException as error:
                needs_kill = True
                note("poll", error)
            if needs_kill:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    note("group_kill", error)
            try:
                process.wait(timeout=max(0.01, min(2, deadline - time.monotonic())))
                reaped = True
            except BaseException as error:
                note("reap", error)
        if failure is not None:
            code = failure
        if cleanup_labels and code == 0:
            code = 126
        # Receipt failure remains secondary to the pending original operation/control.
        try:
            state["raw_bytes"] = state.get("raw_bytes", 0) + byte_count
            state.setdefault("child_receipts", []).append({"name": name, "bytes": byte_count,
                "exit": code, "bound_failed": failure == 125, "timed_out": failure == 124,
                "process_started": process is not None, "process_reaped": reaped,
                "cleanup_failed": bool(cleanup_labels), "cleanup_classes": cleanup_labels[:]})
            save()
        except BaseException as error:
            note("receipt", error)
            if state.get("child_receipts"):
                state["child_receipts"][-1].update(exit=code or 126, cleanup_failed=True, cleanup_classes=cleanup_labels[:])
    if original_error is not None:
        raise original_error
    if cleanup_error is not None:
        if isinstance(cleanup_error, GateInterrupted) and failure is None and code == 126:
            raise cleanup_error
        raise GateFailure(code or 126)
    return code, logfile


def capture(argv):
    timeout = min(10, remaining()) if stage_start is not None else min(10, 170 - (time.monotonic() - state["start"]))
    name = "metadata-" + str(len(state.get("child_receipts", [])))
    code, logfile = bounded_child(argv, name, timeout, env=sanitized_environment())
    require(code == 0)
    return logfile.read_text().strip()

def begin(name):
    global stage_start, stage_name
    now = time.monotonic()
    if stage_start is not None:
        state["stages"][-1].update(end=now, duration=now-stage_start)
    stage_name, stage_start = name, now
    state.setdefault("stages", []).append({"name": name, "start": stage_start, "cap": CAPS[name]})
    save()

def remaining():
    require(stage_start is not None)
    reserve = 110 if stage_name != "cleanup" else 50
    job_left = 1800 - (time.monotonic() - state["start"]) - reserve
    spent = sum(record.get("duration", 0) for record in state.get("stages", [])[:-1] if record["name"] == stage_name)
    stage_left = CAPS[stage_name] - spent - (time.monotonic() - stage_start)
    seconds = min(job_left, stage_left)
    require(seconds > 0)
    return seconds

def command(argv, name, env=None, expected=0):
    code, logfile = bounded_child(argv, name + "-" + str(len(state.get("child_receipts", []))), remaining(), env)
    state.setdefault("commands", []).append({"name": name, "argv": argv, "exit": code,
                                            "stage": stage_name, "end": time.monotonic()})
    text = logfile.read_text(errors="replace")
    # Only finite numeric summaries are artifact-safe; never copy arbitrary child text.
    summary_allowed = name in {"workspace-default", "contracts-default", "contracts-feature-tests",
                               "domain-feature-tests", "python-full139", "python-e0-32"} or bool(
        re.fullmatch(r"(?:O|W)-(?:off-marker|on-marker|e0-red)|C-(?:marker|e0)-(?:True|False)", name))
    summaries = ([{"count": int(number), "class": kind} for number, kind in
                  re.findall(r"\b([0-9]{1,6}) (passed|failed|ignored|skipped)\b", text)]
                 if summary_allowed else [])
    state.setdefault("proofs", []).append({"command": name, "exit": code, "summaries": summaries[:128]})
    save()
    if code == 0:
        require(not re.search(r"\b[1-9][0-9]* (?:skipped|ignored)\b|no tests ran", text))
    if expected is not None and code != expected:
        raise GateFailure(code or 1)
    return code, logfile

def sanitized_environment():
    env = os.environ.copy()
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GHCR_TOKEN", "GHCR_USERNAME", "DOCKER_CONFIG",
                "BIFROST_ACTION_PIN_TOKEN_FILE", "SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY"):
        env.pop(key, None)
    return env

def new_directory(name):
    path = (Path(os.environ["RUNNER_TEMP"]) / (PREFIX + "-docker-config")) if name == "docker-config" else EVIDENCE / name
    path.mkdir(mode=0o700)
    info = path.lstat()
    state.setdefault("directories", []).append({"path": str(path), "dev": info.st_dev,
                                               "ino": info.st_ino, "uid": info.st_uid, "mode": 0o700})
    save()
    return path

def check_directory(record):
    path = Path(record["path"])
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino, info.st_uid) ==
            (record["dev"], record["ino"], record["uid"]) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == record["mode"])
    return path

def inspect(kind, identity):
    return json.loads(capture(["docker", kind, "inspect", identity]))[0]

def volume(name):
    require(not capture(["docker", "volume", "ls", "-q", "--filter", "name=^" + name + "$"]))
    record = {"name": name, "mountpoint": None, "created": None}
    state.setdefault("volumes", []).append(record)
    save()
    command(["docker", "volume", "create", "--label", LABEL + "=" + PREFIX, name], name)
    facts = inspect("volume", name)
    require(facts["Name"] == name and facts["Labels"][LABEL] == PREFIX)
    record.update(mountpoint=facts["Mountpoint"], created=facts["CreatedAt"])
    save()
    return name

def container(image, argv, name, mounts=(), variables=(), network="none", expected=0, python_peer=False):
    global counter
    counter += 1
    cname = PREFIX + "-" + str(counter)
    require(not capture(["docker", "ps", "-aq", "--filter", "name=^/" + cname + "$"]))
    create = ["docker", "create", "--name", cname, "--label", LABEL + "=" + PREFIX,
              "--network", network]
    for source, destination, readonly in mounts:
        create += ["--mount", "type=" + ("bind" if source.startswith("/") else "volume") +
                   ",source=" + source + ",target=" + destination + (",readonly" if readonly else "")]
    for key, value in variables:
        require(key not in {"GITHUB_TOKEN", "GH_TOKEN", "GHCR_TOKEN", "GHCR_USERNAME", "DOCKER_CONFIG"})
        create += ["-e", key + "=" + value]
    if python_peer:
        create += ["--user", "1000:1000", "--entrypoint", "python"]
    create += [image] + argv
    record = {"id": None, "name": cname, "image": image, "network": network,
              "mounts": list(mounts), "variables": list(variables), "python_peer": python_peer}
    state.setdefault("containers", []).append(record)
    save()
    command(create, name + "-create", env=sanitized_environment())
    cid = capture(["docker", "inspect", cname, "--format", "{{.Id}}"])
    record["id"] = cid
    save()
    facts = inspect("container", cid)
    require(facts["Name"] == "/" + cname and facts["Image"] == image and
            facts["Config"]["Labels"][LABEL] == PREFIX and facts["HostConfig"]["NetworkMode"] == network)
    require(len(facts["Mounts"]) == len(mounts))
    for source, destination, readonly in mounts:
        actual_mount = next(mount for mount in facts["Mounts"] if mount["Destination"] == destination)
        require(actual_mount["RW"] == (not readonly))
        require(actual_mount["Source"] == source if source.startswith("/") else actual_mount["Name"] == source)
    env_keys = {value.split("=", 1)[0] for value in facts["Config"]["Env"]}
    require(not env_keys.intersection({"GITHUB_TOKEN", "GH_TOKEN", "GHCR_TOKEN", "GHCR_USERNAME", "DOCKER_CONFIG", "BIFROST_ACTION_PIN_TOKEN_FILE"}))
    if python_peer:
        require(facts["Config"]["User"] == "1000:1000" and facts["Config"]["Entrypoint"] == ["python"])
    code, logfile = command(["docker", "start", "-a", cid], name, env=sanitized_environment(), expected=None)
    facts = inspect("container", cid)
    if facts["State"]["Running"]:
        raise GateFailure(code or 1)
    actual = facts["State"]["ExitCode"]
    if code != actual:
        raise GateFailure(code or 1)
    record["exit"] = actual
    save()
    if expected is not None and actual != expected:
        raise GateFailure(actual or 1)
    return actual, logfile

def cleanup_pre_pr():
    if not state.get("pre_pr_started") or state.get("pre_pr_disposed"):
        return
    require(state.get("pre_pr_initially_absent"))
    project = state["pre_pr_project"]
    require(project == capture(["bash", "-c", "source scripts/lib/test_helpers.sh; compute_project_name ."]))
    # Only the previously admitted project's ordinary stack is selected.
    env = sanitized_environment()
    env["COMPOSE_PROJECT_NAME"] = project
    env["COMPOSE_FILE"] = "docker-compose.test.yml"
    owned = {"containers": [], "volumes": [], "networks": []}
    for cid in capture(["docker", "ps", "-aq", "--no-trunc", "--filter", "label=com.docker.compose.project=" + project]).splitlines():
        facts = inspect("container", cid)
        require(facts["Config"]["Labels"]["com.docker.compose.project"] == project)
        owned["containers"].append({"id": facts["Id"], "name": facts["Name"], "image": facts["Image"],
                                    "service": facts["Config"]["Labels"].get("com.docker.compose.service")})
    for volume_name in capture(["docker", "volume", "ls", "-q", "--filter", "label=com.docker.compose.project=" + project]).splitlines():
        facts = inspect("volume", volume_name)
        require(facts["Labels"]["com.docker.compose.project"] == project)
        owned["volumes"].append({"name": facts["Name"], "created": facts["CreatedAt"]})
    for nid in capture(["docker", "network", "ls", "-q", "--no-trunc", "--filter", "label=com.docker.compose.project=" + project]).splitlines():
        facts = inspect("network", nid)
        require(facts["Labels"]["com.docker.compose.project"] == project)
        owned["networks"].append({"id": facts["Id"], "name": facts["Name"]})
    state["pre_pr_owned_before_down"] = owned
    save()
    down_failed = False
    try:
        command(["./test.sh", "stack", "down"], "pre-pr-owned-down", env=env)
    except Exception:
        down_failed = True
    inventory = {}
    for kind, argv in (("containers", ["docker", "ps", "-aq"]),
                       ("volumes", ["docker", "volume", "ls", "-q"]),
                       ("networks", ["docker", "network", "ls", "-q"])):
        try:
            inventory[kind] = capture(argv + ["--filter", "label=com.docker.compose.project=" + project])
        except Exception:
            inventory[kind] = "inspection_failed"
    state["pre_pr_actual_inventory"] = inventory
    save()
    require(not down_failed and not any(inventory.values()))
    state["pre_pr_disposed"] = True
    save()


def cleanup_resources():
    failures = []
    try:
        cleanup_pre_pr()
    except Exception:
        failures.append("pre-pr-project")
    for record in state.get("containers", []):
        if record.get("removed"):
            continue
        try:
            existing = capture(["docker", "ps", "-aq", "--no-trunc", "--filter", "name=^/" + record["name"] + "$"])
            if not existing:
                require(record["id"] is None)
                record["removed"] = True
                save()
                continue
            require(record["id"] is None or existing == record["id"])
            record["id"] = existing
            facts = inspect("container", record["id"])
            require(facts["Name"] == "/" + record["name"] and facts["Image"] == record["image"] and
                    facts["Config"]["Labels"][LABEL] == PREFIX)
            command(["docker", "rm", "-f", record["id"]], record["name"] + "-remove")
            record["removed"] = True
            save()
        except Exception:
            failures.append("container")
    for record in state.get("volumes", []):
        if record.get("removed"):
            continue
        try:
            existing = capture(["docker", "volume", "ls", "-q", "--filter", "name=^" + record["name"] + "$"])
            if not existing:
                require(record["created"] is None)
                record["removed"] = True
                save()
                continue
            facts = inspect("volume", record["name"])
            require(facts["Labels"][LABEL] == PREFIX and (record["mountpoint"] is None or facts["Mountpoint"] == record["mountpoint"]) and
                    (record["created"] is None or facts["CreatedAt"] == record["created"]))
            command(["docker", "volume", "rm", record["name"]], record["name"] + "-remove")
            record["removed"] = True
            save()
        except Exception:
            failures.append("volume")
    for record in state.get("images", []):
        if record.get("removed"):
            continue
        try:
            existing = capture(["docker", "image", "ls", "-q", "--no-trunc", record["tag"]])
            if not existing:
                require(record["id"] is None and record.get("complete"))
                record["removed"] = True
                save()
                continue
            facts = inspect("image", existing)
            require(record["id"] is None or existing == record["id"])
            require(record["tag"] in facts.get("RepoTags", []))
            command(["docker", "image", "rm", record["tag"]], "image-remove-" + hashlib.sha256(record["tag"].encode()).hexdigest()[:16])
            record["removed"] = True
            save()
        except Exception:
            failures.append("image")
    for record in state.get("directories", []):
        if record.get("removed"):
            continue
        try:
            directory = check_directory(record)
            if directory.name == PREFIX + "-docker-config":
                env = sanitized_environment()
                env["DOCKER_CONFIG"] = str(directory)
                command(["docker", "logout", "ghcr.io"], "fallback-ghcr-logout", env=env)
                config = directory / "config.json"
                if config.exists():
                    data = json.loads(config.read_text())
                    require(not data.get("credsStore") and not data.get("credHelpers") and not any(data.get("auths", {}).values()))
            shutil.rmtree(directory)
            record["removed"] = True
            save()
        except Exception:
            failures.append("directory")
    containers = capture(["docker", "ps", "-aq", "--filter", "label=" + LABEL + "=" + PREFIX])
    volumes = capture(["docker", "volume", "ls", "-q", "--filter", "label=" + LABEL + "=" + PREFIX])
    networks = capture(["docker", "network", "ls", "-q", "--filter", "label=" + LABEL + "=" + PREFIX])
    (EVIDENCE / "owned-inventory.json").write_text(json.dumps({"containers": containers,
        "volumes": volumes, "networks": networks, "cleanup_failures": failures, "images": [{"tag": record["tag"], "removed": record.get("removed", False)} for record in state.get("images", [])]}))
    require(not failures and not containers and not volumes and not networks)
    state["disposed"] = True
    save()

def guard():
    require(not capture(["git", "status", "--porcelain", "--untracked-files=all"]))
    require(capture(["git", "rev-parse", "HEAD"]) == os.environ["GITHUB_SHA"])
    for path, expected in HELPERS.items():
        require(digest(ROOT / path) == expected)
    # This public metadata fetch has no persisted checkout credential or helper.
    capture(["git", "-c", "credential.helper=", "-c", "http.extraheader=", "fetch", "origin", "main"])
    capture(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"])
    state["source"] = capture(["git", "rev-parse", "HEAD", "HEAD^{tree}"]).splitlines()
    project = capture(["bash", "-c", "source scripts/lib/test_helpers.sh; compute_project_name ."])
    for argv in (["docker", "ps", "-aq"], ["docker", "volume", "ls", "-q"], ["docker", "network", "ls", "-q"]):
        require(not capture(argv + ["--filter", "label=com.docker.compose.project=" + project]))
    require(not capture(["docker", "ps", "-aq", "--filter", "label=" + LABEL + "=" + PREFIX]))
    require(not capture(["docker", "volume", "ls", "-q", "--filter", "label=" + LABEL + "=" + PREFIX]))
    state["pre_pr_project"] = project
    state["pre_pr_initially_absent"] = True
    save()
    state["guard_elapsed"] = time.monotonic() - state["start"]
    require(state["guard_elapsed"] < 170)
    paths = ["core-rs/Dockerfile", "core-rs/Cargo.lock", HELPER,
             "scripts/ci/runtime-control-interchange.sh", ".github/workflows/runtime-control.yml",
             "core-rs/crates/bifrost-contracts/src/runtime/codec.rs",
             "core-rs/crates/bifrost-contracts/src/runtime/mod.rs",
             "core-rs/crates/bifrost-contracts/src/runtime/tests.rs",
             "core-rs/crates/bifrost-domain/examples/workflow_domain_vectors.rs",
             "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json",
             "api/src/runtime_protocol/control.py", "api/tests/runtime_protocol/test_control.py",
             "api/tests/runtime_protocol/interchange.py", "api/scripts/ci/prepare-test-images.sh",
             "api/scripts/check_github_action_pins.py", "scripts/ci/tests/test_workflow_sql_credential.py"]
    state["source_hashes"] = {path: digest(ROOT / path) for path in paths}
    save()

def pre_pr():
    begin("pre-pr")
    original = 0
    try:
        require(os.environ.get("BIFROST_ACTION_PIN_TOKEN_FILE") and
                os.environ.get("SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY"))
        state["pre_pr_started"] = True
        save()
        command(["./test.sh", "pre-pr"], "literal-pre-pr")
        project = capture(["bash", "-c", "source scripts/lib/test_helpers.sh; compute_project_name ."])
        for kind, argv in (("containers", ["docker", "ps", "-aq"]), ("volumes", ["docker", "volume", "ls", "-q"]), ("networks", ["docker", "network", "ls", "-q"])):
            require(not capture(argv + ["--filter", "label=com.docker.compose.project=" + project]))
        state["pre_pr_inventory"] = "EMPTY"
        save()
        require(not capture(["git", "status", "--porcelain", "--untracked-files=all"]))
    except Exception as error:
        original = getattr(error, "code", 1)
    finally:
        try:
            command(["bash", HELPER, "credential-cleanup"], "action-credential-disposal")
            os.environ.pop("BIFROST_ACTION_PIN_TOKEN_FILE", None)
            os.environ.pop("SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY", None)
            state["action_disposed"] = True
            save()
        except Exception as error:
            original = original or getattr(error, "code", 1)
    if original:
        begin("cleanup")
    try:
        cleanup_pre_pr()
    except Exception:
        original = original or 1
    if original:
        raise GateFailure(original)

def prepare():
    require(state.get("action_disposed"))
    begin("prepare")
    directory = new_directory("docker-config")
    env = os.environ.copy()
    env["DOCKER_CONFIG"] = str(directory)
    original = 0
    remote = os.environ["REGISTRY"] + "/" + os.environ["CI_API_TEST_IMAGE"] + ":" + os.environ["CI_TEST_IMAGE_TAG"]
    prior = {tag: capture(["docker", "image", "ls", "-q", "--no-trunc", tag]) for tag in ("bifrost-test-api-dev:latest", remote)}
    try:
        command(["bash", "api/scripts/ci/prepare-test-images.sh", "api"], "python-image-prepare", env=env)
        state["python_image"] = capture(["docker", "image", "inspect", "bifrost-test-api-dev:latest", "--format", "{{.Id}}"])
        state["python_image_facts"] = {"id": state["python_image"], "prior_references": prior}
        save()
    except Exception as error:
        original = getattr(error, "code", 1)
    finally:
        for tag, previous in prior.items():
            try:
                current = capture(["docker", "image", "ls", "-q", "--no-trunc", tag])
                if current and current != previous:
                    require(not previous)
                    state.setdefault("images", []).append({"id": current, "tag": tag, "complete": True})
                    save()
            except Exception as error:
                original = original or getattr(error, "code", 1)
        try:
            command(["docker", "logout", "ghcr.io"], "ghcr-explicit-logout", env=env)
            config = directory / "config.json"
            if config.exists():
                data = json.loads(config.read_text())
                require(not data.get("credsStore") and not data.get("credHelpers") and
                        not any(value for value in data.get("auths", {}).values()))
            record = next(record for record in state["directories"] if record["path"] == str(directory))
            shutil.rmtree(check_directory(record))
            state["directories"].remove(record)
            state["ghcr_disposed"] = True
            save()
        except Exception as error:
            original = original or getattr(error, "code", 1)
    if original:
        raise GateFailure(original)

def image_build():
    begin("toolchain")
    tag = PREFIX + ":toolchain"
    require(not capture(["docker", "image", "ls", "-q", tag]))
    record = {"id": None, "tag": tag, "complete": False}
    state.setdefault("images", []).append(record)
    save()
    command(["docker", "build", "--target", "toolchain", "-t", tag, "-f", "core-rs/Dockerfile", "core-rs"], "toolchain-build", env=sanitized_environment())
    image = capture(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])
    record["id"] = image
    record["complete"] = True
    save()
    return image

def run_cargo(image, source, target, argv, name, feature=False, package="bifrost-contracts", fixture=None, expected=0):
    mounts = [(str(source), "/workspace/core-rs", True),
              (state["cargo_home"], "/usr/local/cargo", False), (target, "/targets", False)]
    variables = [("CARGO_TARGET_DIR", "/targets")]
    if state.get("exchange"):
        mounts.append((state["exchange"], "/exchange", True))
    if fixture is not None:
        mounts.append((str(fixture), "/vectors/control-vectors.json", True))
        variables.append(("BIFROST_RUNTIME_VECTORS", "/vectors/control-vectors.json"))
    return container(image, argv, name, mounts, variables, expected=expected)

def verify():
    require(state.get("action_disposed") and state.get("ghcr_disposed"))
    image = image_build()
    state["toolchain_image"] = image
    save()
    sources = new_directory("sources")
    variants = {"O": "77af2db4ba6364005660d87b7a980b4ba77d9058",
                "W": "f2eb46bf400300664d6ea689728d51f878a89227", "C": os.environ["GITHUB_SHA"]}
    for name, revision in variants.items():
        archive = sources / (name + ".tar")
        with archive.open("wb") as output:
            subprocess.run(["git", "archive", revision, "core-rs"], stdout=output, check=True)
        destination = sources / name
        destination.mkdir()
        with tarfile.open(archive) as data:
            data.extractall(destination, filter="data")
        source = destination / "core-rs"
        require(digest(source / "Cargo.lock") == LOCK)
        require(len(tomllib.loads((source / "Cargo.lock").read_text())["package"]) == 232)
        state.setdefault("archives", {})[name] = {"revision": revision, "tree": capture(["git", "rev-parse", revision + "^{tree}"]), "sha256": digest(archive)}
    fixture_path = ROOT / "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json"
    require(digest(fixture_path) == "a5501a4fde472ebbae9567803e3b96313672ccfbad41fc8165030cbffd1786e6")
    data = json.loads(fixture_path.read_text())
    require([len(data[key]) for key in ("wire", "binary", "sessions")] == [139, 7, 35])
    subsets = new_directory("subsets")
    for name, selection in (("marker", [row for row in data["wire"] if row["name"] == "rawvalue-hidden-start"]),
                            ("e0", [row for row in data["wire"] if row["name"].startswith("e0-")])):
        require(len(selection) == (1 if name == "marker" else 32))
        require(len({row["name"] for row in selection}) == len(selection))
        if name == "e0":
            require(Counter(row["expected"] for row in selection) == {"UnsupportedProtocol": 14, "InvalidJson": 8, "InvalidFrame": 9, "UnsupportedFrame": 1})
        subset = {key: data[key] for key in ("profile", "max_frame_bytes", "max_depth")}
        subset.update(wire=selection, binary=[], sessions=[])
        path = subsets / (name + ".json")
        path.write_text(json.dumps(subset, separators=(",", ":")))
        state.setdefault("subsets", {})[name] = {"hash": digest(path), "count": len(selection)}
        save()
    marker = data["wire"][99]
    require(marker["name"] == "rawvalue-hidden-start" and
            hashlib.sha256(marker["json"].encode()).hexdigest() == "1755f5276157690deabcbab1719bf66ee50727b4525bd3c28f1e539f36c64c9e")
    begin("fetch")
    state["cargo_home"] = volume(PREFIX + "-cargo-home")
    container(image, ["sh", "-c", "test ! -e /usr/local/cargo/credentials && test ! -e /usr/local/cargo/credentials.toml && test ! -e /usr/local/cargo/config && test ! -e /usr/local/cargo/config.toml"], "cargo-home-admission", [(state["cargo_home"], "/usr/local/cargo", False)])
    container(image, ["cargo", "fetch", "--locked"], "locked-fetch",
              [(str(sources / "C/core-rs"), "/workspace/core-rs", True),
               (state["cargo_home"], "/usr/local/cargo", False)], network="bridge")
    targets = {}
    for variant in variants:
        for on in (False, True):
            targets[(variant, on)] = volume(PREFIX + "-target-" + variant.lower() + ("-on" if on else "-off"))
    def cargo(variant, on, argv, name, fixture=None, expected=0):
        return run_cargo(image, sources / variant / "core-rs", targets[(variant, on)], argv, name, fixture=fixture, expected=expected)
    def test(variant, on, fixture, name, expected=0):
        argv = ["cargo", "test", "--offline", "--locked", "-p", "bifrost-contracts", "--lib"]
        if on:
            argv += ["--features", "serde_json/raw_value"]
        argv += ["runtime::tests::shared_wire_vectors", "--", "--exact", "--nocapture"]
        code, log = cargo(variant, on, argv, name, fixture, expected)
        require("running 1 test" in log.read_text())
        return code, log
    begin("checks")
    for argv, name in ((["cargo", "fmt", "--check"], "fmt-check"),
                       (["cargo", "clippy", "--offline", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings"], "clippy"),
                       (["cargo", "test", "--offline", "--locked", "--all"], "workspace-default"),
                       (["cargo", "test", "--offline", "--locked", "-p", "bifrost-contracts", "--lib"], "contracts-default"),
                       (["cargo", "build", "--offline", "--locked", "-p", "bifrost-contracts", "--example", "runtime_control_vectors"], "exchange-default-build")):
        cargo("C", False, argv, name, fixture_path)
    for variant in ("O", "W"):
        begin(variant)
        test(variant, False, subsets / "marker.json", variant + "-off-marker")
        code, log = test(variant, True, subsets / "marker.json", variant + "-on-marker", None if variant == "O" else 0)
        if variant == "O":
            require(code != 0 and "accepted invalid vector: rawvalue-hidden-start" in log.read_text())
            state.setdefault("drift_receipts", []).append({"variant": "O", "probe": "rawvalue-hidden-start", "actual": "accepted", "normative": "InvalidFrame", "exit": code})
            save()
        code, log = test(variant, False, subsets / "e0.json", variant + "-e0-red", None)
        text = log.read_text()
        require(code != 0 and "e0-unsupported-sequence-zero" in text and "InvalidFrame" in text and "UnsupportedProtocol" in text)
        state.setdefault("drift_receipts", []).append({"variant": variant, "probe": "e0-unsupported-sequence-zero", "actual": "InvalidFrame", "normative": "UnsupportedProtocol", "exit": code})
        save()
    begin("feature-build")
    for package, selector in (("bifrost-contracts", ["--lib"]),
                              ("bifrost-domain", ["--example", "workflow_domain_vectors"])):
        cargo("C", True, ["cargo", "test", "--offline", "--locked", "-p", package,
                           "--features", "serde_json/raw_value"] + selector + ["--no-run"], package + "-feature-build")
    cargo("C", True, ["cargo", "build", "--offline", "--locked", "-p", "bifrost-contracts", "--features", "serde_json/raw_value", "--example", "runtime_control_vectors"], "exchange-feature-build")
    begin("products")
    _, rustc_log = container(image, ["rustc", "--version"], "rustc-version")
    require(re.fullmatch(r"rustc 1\.98\.1 \([0-9a-f]+ [0-9-]+\)", rustc_log.read_text().strip()))
    state["rustc_version"] = "1.98.1"
    _, cargo_log = container(image, ["cargo", "--version"], "cargo-version")
    cargo_version = re.fullmatch(r"cargo ([0-9]+\.[0-9]+\.[0-9]+) \([0-9a-f]+ [0-9-]+\)", cargo_log.read_text().strip())
    require(cargo_version is not None)
    state["cargo_version"] = cargo_version[1]
    _, python_version_log = container(state["python_image"], ["--version"], "python-version", python_peer=True)
    python_version = re.fullmatch(r"Python ([0-9]+\.[0-9]+\.[0-9]+)", python_version_log.read_text().strip())
    require(python_version is not None)
    state["python_version"] = python_version[1]
    _, python_hash_log = container(state["python_image"], ["-c", "import hashlib,sys; print(hashlib.sha256(open(sys.executable, 'rb').read()).hexdigest())"], "python-binary-hash", python_peer=True)
    python_hash = python_hash_log.read_text().strip()
    require(re.fullmatch(r"[0-9a-f]{64}", python_hash))
    state["python_binary_sha256"] = python_hash
    save()
    for variant in variants:
        for on in (False, True):
            for package in (["bifrost-contracts", "bifrost-domain"] if variant == "C" else ["bifrost-contracts"]):
                argv = ["cargo", "tree", "--offline", "--locked", "-p", package]
                if on:
                    argv += ["--features", "serde_json/raw_value"]
                _, log = cargo(variant, on, argv + ["-e", "features", "-i", "serde_json"], variant + "-" + package + ("-on" if on else "-off") + "-features")
                text = log.read_text()
                require("serde_json v1.0.151" in text)
                require(('feature "raw_value"' in text) == on and 'feature "float_roundtrip"' not in text and 'feature "arbitrary_precision"' not in text)
                state.setdefault("feature_receipts", []).append({"variant": variant, "package": package, "raw_value": on, "serde_json": "1.0.151", "unexpected_numeric_features": False})
                save()
    for on in (False, True):
        test("C", on, subsets / "marker.json", "C-marker-" + str(on))
        test("C", on, subsets / "e0.json", "C-e0-" + str(on))
    cargo("C", True, ["cargo", "test", "--offline", "--locked", "-p", "bifrost-contracts", "--features", "serde_json/raw_value", "--lib"], "contracts-feature-tests", fixture_path)
    cargo("C", True, ["cargo", "test", "--offline", "--locked", "-p", "bifrost-domain", "--features", "serde_json/raw_value", "--example", "workflow_domain_vectors"], "domain-feature-tests")
    exchange = new_directory("exchange")
    # UID1000 reads only synthetic source/fixtures and bounded peer outputs.
    os.chmod(exchange, 0o755)
    next(record for record in state["directories"] if record["path"] == str(exchange))["mode"] = 0o755
    state["exchange"] = str(exchange)
    save()
    def python(argv, name, fixture=fixture_path):
        return container(state["python_image"], argv, name,
            [(str(ROOT / "api/src"), "/app/src", True),
             (str(ROOT / "api/tests/runtime_protocol"), "/app/tests/runtime_protocol", True),
             (str(ROOT / "api/pytest.ini"), "/app/pytest.ini", True),
             (str(fixture), "/contracts/control-vectors.json", True),
             (str(exchange), "/exchange", True)],
            [("BIFROST_RUNTIME_VECTORS", "/contracts/control-vectors.json")], python_peer=True)
    python(["-m", "pytest", "--confcutdir=tests/runtime_protocol", "tests/runtime_protocol", "-q", "--no-cov"], "python-full139")
    python(["-m", "pytest", "--confcutdir=tests/runtime_protocol", "tests/runtime_protocol/test_control.py", "-q", "--no-cov", "-k", "wire"], "python-e0-32", subsets / "e0.json")
    for on in (False, True):
        argv = ["cargo", "run", "--offline", "--locked", "-p", "bifrost-contracts"]
        if on:
            argv += ["--features", "serde_json/raw_value"]
        argv += ["--example", "runtime_control_vectors", "--"]
        _, log = cargo("C", on, argv + ["emit"], "rust-emit-" + str(on), fixture_path)
        lines = [line for line in log.read_text().splitlines() if line.startswith('{"profile"')]
        require(len(lines) == 1 and len(lines[0].encode()) <= 1024 * 1024)
        (exchange / "rust.json").write_text(lines[0] + "\n")
        (exchange / "rust.json").chmod(0o644)
        python(["-m", "tests.runtime_protocol.interchange", "validate", "/exchange/rust.json"], "python-validate-" + str(on))
        _, log = python(["-m", "tests.runtime_protocol.interchange", "emit"], "python-emit-" + str(on))
        require(log.stat().st_size <= 1024 * 1024)
        (exchange / "python.json").write_bytes(log.read_bytes())
        (exchange / "python.json").chmod(0o644)
        cargo("C", on, argv + ["validate", "/exchange/python.json"], "rust-validate-" + str(on), fixture_path)
    for variant in variants:
        for on in (False, True):
            _, hashes_log = cargo(variant, on, ["sh", "-c", "find /targets/debug -type f -perm /111 -exec sha256sum {} +"], variant + "-binary-hashes-" + str(on))
            safe = []
            for line in hashes_log.read_text().splitlines():
                matched = re.fullmatch(r"([0-9a-f]{64})  /targets/debug/(deps/bifrost_contracts-[0-9a-f]+|examples/(?:runtime_control_vectors|workflow_domain_vectors)(?:-[0-9a-f]+)?)", line)
                if matched:
                    safe.append({"sha256": matched[1], "binary": matched[2]})
            require(any(row["binary"].startswith("deps/bifrost_contracts-") for row in safe))
            state.setdefault("native_binary_receipts", []).append({"variant": variant, "raw_value": on, "image": image, "binaries": safe})
            save()
    require(not capture(["git", "status", "--porcelain", "--untracked-files=all"]))

def format_only():
    image = image_build()
    directory = new_directory("format-copy")
    command(["git", "clone", "--no-hardlinks", "--no-checkout", str(ROOT), str(directory / "repo")], "format-clone")
    copy = directory / "repo"
    command(["git", "-C", str(copy), "checkout", "--detach", os.environ["GITHUB_SHA"]], "format-checkout")
    original_status = capture(["git", "status", "--porcelain", "--untracked-files=all"])
    container(image, ["cargo", "fmt", "--all"], "format",
              [(str(copy / "core-rs"), "/workspace/core-rs", False)])
    changed = set(capture(["git", "-C", str(copy), "diff", "--name-only"]).splitlines())
    allowed = {"core-rs/crates/bifrost-contracts/src/runtime/codec.rs",
               "core-rs/crates/bifrost-contracts/src/runtime/mod.rs",
               "core-rs/crates/bifrost-contracts/src/runtime/tests.rs",
               "core-rs/crates/bifrost-domain/examples/workflow_domain_vectors.rs"}
    require(changed <= allowed)
    code, patch_log = bounded_child(["git", "-C", str(copy), "diff", "--binary"], "format-exact-patch", remaining(), sanitized_environment())
    require(code == 0)
    (EVIDENCE / "format.patch").write_bytes(patch_log.read_bytes())
    require(capture(["git", "status", "--porcelain", "--untracked-files=all"]) == original_status)

status = 0
try:
    if MODE == "guard":
        guard()
    elif MODE == "pre-pr":
        pre_pr()
    elif MODE == "prepare":
        prepare()
    elif MODE == "format":
        format_only()
    elif MODE == "verify":
        verify()
    elif MODE == "cleanup" and (not state.get("disposed") or (state.get("pre_pr_started") and not state.get("pre_pr_disposed"))):
        begin("cleanup")
        cleanup_resources()
except GateInterrupted as interruption:
    status = interruption.code
except GateFailure as failure:
    status = failure.code
except Exception:
    status = 1
    print("runtime control custody gate failed", file=sys.stderr)
finally:
    if MODE in {"format", "verify"} or (MODE == "pre-pr" and status != 0):
        original = status
        try:
            begin("cleanup")
            cleanup_resources()
        except Exception:
            status = original or 1
    if stage_start is not None:
        now = time.monotonic()
        state["stages"][-1].update(end=now, duration=now-stage_start)
    state.setdefault("mode_exits", []).append({"mode": MODE, "exit": status, "time": time.monotonic()})
    save()
sys.exit(status)
PY
