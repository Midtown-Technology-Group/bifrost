#!/usr/bin/env bash
# Dedicated unchanged-reference experiment; raw diagnostics are never artifacts.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
exec python3 - <<'PY'
import ast
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path

START = time.monotonic()
WORK_END, CLEAN_END, PUB_END = START + 3060, START + 3240, START + 3300
RAW_LIMIT, RAW_TOTAL, SAFE_LIMIT = 16777216, 67108864, 1048576
ROOT = Path.cwd()
TEST = "api/tests/e2e/api/test_http_cancel_reference.py"
TARGET = "tests/e2e/api/test_http_cancel_reference.py"
SOURCE_PATHS = (
    TEST,
    "scripts/ci/http-cancel-reference.sh",
    ".github/workflows/http-cancel-reference.yml",
    "test.sh",
    "docker-compose.test.yml",
    "api/entrypoint.sh",
    "api/Dockerfile.dev",
    "requirements.lock",
    "requirements-pyright.lock",
    "pyproject.toml",
    "api/src/main.py",
    "api/src/routers/executions.py",
    "api/src/core/database.py",
    "api/src/core/auth.py",
    "api/src/core/redis_client.py",
    "api/src/core/principal.py",
    "api/tests/conftest.py",
    "api/tests/e2e/conftest.py",
    "api/tests/e2e/fixtures/setup.py",
    "api/pytest.ini",
    "api/scripts/plan_affected_tests.py",
    "scripts/lib/test_helpers.sh",
    "scripts/lib/pre_pr_stage_evidence.py",
    "api/scripts/check_github_action_pins.py",
    "api/scripts/quality_api.sh",
    "client/Dockerfile",
    "client/package-lock.json",
)
PHASES = {
    "committed_seed_readback",
    "owned_rows_removed",
    "http_200",
    "http_400",
    "http_401",
    "http_403",
    "full_row_and_flag_readback",
    "inner_response_start",
    "inner_terminal_body_barrier",
    "inner_terminal_body_send_return",
    "original_route_handle_return",
    "execution_publisher_entry",
    "history_publisher_entry",
    "request_outer_commit",
    "request_rollback",
    "request_outer_transaction_end",
    "outer_response_start",
    "outer_terminal_body_send_return",
    "outer_app_return",
    "independent_committed_mutation_readback",
    "advisory_lock_held",
    "row_lock_held",
    "advisory_lock_available",
    "row_lock_available",
    "cancel_subscribe_ack",
    "cancel_message_owned",
    "full_row_phase_readback",
}
CASE_NAMES = {
    **{f"H{i:02}": f"test_booted_http_cancel_reference[H{i:02}]" for i in range(1, 10)},
    **{f"P{i:02}": f"test_asgi_http_cancel_reference_phases[P{i:02}]" for i in range(1, 4)},
}
PROPERTY_KEYS = {
    "http_cancel_reference_case",
    "http_cancel_reference_phases",
    "http_cancel_reference_cleanup",
    "http_cancel_reference_application_identity",
}
STAGES = {
    name: {"exit": None, "admission": "not_started"}
    for name in (
        "build",
        "pre_pr",
        "stack",
        "measure",
        "target",
        "cleanup",
        "publication",
    )
}
state = {
    "counter": 0,
    "raw_bytes": 0,
    "safe_files": {},
    "effects_started": False,
    "raw_files": {},
    "runner": None,
    "target_started": False,
    "images": {},
    "image_pending": set(),
    "initial_image_ids": set(),
    "project": None,
    "raw_paths": {},
    "raw_dir": None,
    "safe_dir": None,
    "primary_exit": 0,
    "cleanup_exit": 0,
    "inspection_exit": None,
    "process_settled": True,
    "resources_empty": False,
    "image_cleanup": "not_started",
    "retained": False,
    "planner": None,
    "source": {},
    "candidate": None,
    "members": {},
    "target": {
        "expected_cases": 12,
        "observed_cases": [],
        "controls": None,
        "runner_identity": None,
        "application_identities": {},
    },
    "projection": {"admission": "absent", "sha256": None},
    "identities": {
        "schema": "http-cancel-reference-installed/v1",
        **{key: None for key in ("booted_api", "booted_replica", "runner", "P01", "P02", "P03")},
    },
    "resources": {
        "schema": "http-cancel-reference-owned/v1",
        "project": None,
        "inspection": "failed",
        "containers": None,
        "networks": None,
        "volumes": None,
    },
    "booted": {},
}


class Failure(Exception):
    def __init__(self, label, code=1):
        self.label = label
        self.code = code
        super().__init__(label)


def require(value, label):
    if not value:
        raise Failure(label)


def interrupted(signum, _frame):
    raise SystemExit(128 + signum)


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)


def end_time(cleanup=False):
    return CLEAN_END if cleanup else WORK_END


def remaining(end):
    value = end - time.monotonic()
    require(value > 0, "deadline")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def facts(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        stat.S_IMODE(value.st_mode),
        value.st_nlink,
    )


def fullfacts(value):
    return (*facts(value), value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def finish_error(original, secondary):
    if original is not None:
        raise original
    if secondary is not None:
        raise secondary


def private_file(path, data, limit, end):
    remaining(end)
    require(len(data) <= limit, "file_bound")
    fd = None
    original = secondary = None
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        item = os.fstat(fd)
        require(
            stat.S_ISREG(item.st_mode) and facts(item)[2:] == (os.geteuid(), 0o600, 1),
            "private_identity",
        )
        state["safe_files"][str(path)] = facts(item)
        view = memoryview(data)
        while view:
            remaining(end)
            count = os.write(fd, view)
            require(count > 0, "private_write")
            view = view[count:]
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                secondary = error
    finish_error(original, secondary)
    remaining(end)
    require(
        read_file(path, limit, {os.geteuid()}, end, private=True)[0] == data,
        "private_readback",
    )


def read_file(path, limit, owners, end, private=False, identity=None):
    remaining(end)
    fd = None
    original = secondary = None
    result = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_uid in owners and before.st_nlink == 1,
            "raw_identity",
        )
        require(not private or stat.S_IMODE(before.st_mode) == 0o600, "private_mode")
        require(identity is None or fullfacts(before) == identity, "raw_replaced")
        require(0 <= before.st_size <= limit, "raw_bound")
        blocks = bytearray()
        while len(blocks) <= before.st_size:
            remaining(end)
            block = os.read(fd, min(65536, before.st_size + 1 - len(blocks)))
            if not block:
                break
            blocks.extend(block)
        after = os.fstat(fd)
        require(
            fullfacts(before) == fullfacts(after) and len(blocks) == before.st_size,
            "raw_changed",
        )
        result = (bytes(blocks), fullfacts(after))
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                secondary = error
    finish_error(original, secondary)
    remaining(end)
    return result


def env():
    # Preserve account identity; do not repurpose HOME. Tokens and optional live
    # service credentials are not forwarded to these child processes.
    selected = {
        key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR") if key in os.environ
    }
    selected.update(CI="true", GIT_TERMINAL_PROMPT="0", BIFROST_SKIP_BUILD="1")
    if state["project"] is not None:
        selected["COMPOSE_PROJECT_NAME"] = state["project"]
    return selected


def child(argv, cap, *, end=None, live=False):
    deadline = min(end or state.get("stage_end") or WORK_END, time.monotonic() + cap)
    require(deadline - time.monotonic() > 0.1, "child_budget")
    reserve = min(2.0, cap / 4)
    state["counter"] += 1
    number = state["counter"]
    paths = [state["raw_dir"] / f"{number}.{stream}" for stream in ("stdout", "stderr")]
    fds, pipes = [], []
    process = selector = None
    original = secondary = None
    counts = [0, 0]
    complete = [False, False]
    code = None
    native_wait = False
    spawn = None
    next_probe = None
    attempts = 0
    try:
        for path in paths:
            fd = os.open(
                path,
                os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
            fds.append(fd)
            item = os.fstat(fd)
            require(
                stat.S_ISREG(item.st_mode) and facts(item)[2:] == (os.geteuid(), 0o600, 1),
                "capture_identity",
            )
            state["raw_files"][str(path)] = facts(item)
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            cwd=ROOT,
            env=env(),
        )
        pipes = [pipe for pipe in (process.stdout, process.stderr) if pipe is not None]
        spawn = time.monotonic()
        next_probe = spawn
        for pipe in pipes:
            os.set_blocking(pipe.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, 0)
        selector.register(process.stderr, selectors.EVENT_READ, 1)
        while selector.get_map():
            require(deadline - time.monotonic() > reserve, "child_timeout")
            now = time.monotonic()
            if live and state["runner"] is None and now >= next_probe:
                require(attempts < 64 and now < spawn + 60, "runner_not_observed")
                attempts += 1
                probe_runner(min(deadline - reserve, spawn + 60))
                next_probe = time.monotonic() + 0.25
            for key, _ in selector.select(min(0.25, remaining(deadline - reserve))):
                try:
                    block = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                channel = key.data
                if not block:
                    complete[channel] = True
                    selector.unregister(key.fileobj)
                    continue
                require(
                    sum(counts) + len(block) <= RAW_LIMIT
                    and state["raw_bytes"] + sum(counts) + len(block) <= RAW_TOTAL,
                    "capture_bound",
                )
                view = memoryview(block)
                while view:
                    remaining(deadline - reserve)
                    written = os.write(fds[channel], view)
                    require(written > 0, "capture_write")
                    view = view[written:]
                counts[channel] += len(block)
        code = process.wait(timeout=remaining(deadline - reserve))
        native_wait = True
        if argv == state.get("primary_argv"):
            STAGES[state["active_stage"]]["exit"] = code
        if live:
            require(state["runner"] is not None, "runner_not_observed")
        for index, fd in enumerate(fds):
            require(os.fstat(fd).st_size == counts[index], "capture_size")
    except BaseException as error:
        original = error
    finally:

        def attempt(callback):
            nonlocal secondary
            try:
                callback()
            except BaseException as error:
                if secondary is None:
                    secondary = error

        for pipe in pipes:
            attempt(pipe.close)
        if selector is not None:
            attempt(selector.close)
        for fd in fds:
            attempt(lambda fd=fd: os.close(fd))
        if process is not None:
            running = True
            try:
                running = process.poll() is None
            except BaseException as error:
                if secondary is None:
                    secondary = error
            if running or original is not None:

                def kill():
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)

                attempt(kill)
            attempt(lambda: process.wait(timeout=min(2, remaining(deadline))))
            if process.returncode is None:
                state["process_settled"] = False
                state["retained"] = True
        state["raw_bytes"] += sum(counts)
    finish_error(original, secondary)
    require(native_wait and all(complete), "incomplete_capture")
    output = read_file(paths[0], RAW_LIMIT, {os.geteuid()}, deadline, private=True)[0]
    errors = read_file(paths[1], RAW_LIMIT, {os.geteuid()}, deadline, private=True)[0]
    return code, output, errors


def command(argv, cap, *, end=None, live=False):
    code, output, _errors = child(argv, cap, end=end, live=live)
    if code != 0:
        raise Failure("native_command_failed", code)
    return output


def decode(data):
    require(len(data) <= SAFE_LIMIT, "json_bound")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "json_duplicate")
            result[key] = value
        return result

    def constant(_value):
        raise Failure("json_nonfinite")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError):
        raise Failure("json_invalid") from None


def inspect_one(kind, value, end=None):
    result = decode(command(["docker", kind, "inspect", value], 2, end=end))
    require(
        type(result) is list and len(result) == 1 and type(result[0]) is dict,
        "inspect_shape",
    )
    return result[0]


def ids(data):
    values = data.decode("ascii").splitlines()
    require(
        len(values) <= 128
        and len(values) == len(set(values))
        and all(re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", value) for value in values),
        "inventory_shape",
    )
    return values


def resources(end):
    project = state["project"]
    require(project is not None, "project_missing")
    result = {}
    for kind in ("container", "network", "volume"):
        argv = [
            "docker",
            kind,
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ]
        if kind == "container":
            argv.insert(3, "-a")
        result[kind + "s"] = ids(command(argv, 2, end=end))
    return result


def compose_config():
    value = decode(
        command(
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.test.yml",
                "--profile",
                "e2e",
                "--profile",
                "test",
                "--profile",
                "client-check",
                "config",
                "--format",
                "json",
            ],
            10,
        )
    )
    require(type(value) is dict and value.get("name") == state["project"], "compose_project")
    return value


def build_association(config, service, tag, facts_value):
    selected = config["services"][service]
    build = selected["build"]
    labels = facts_value["Config"].get("Labels") or {}
    expected_context = ROOT / "client" if service == "client-check-runner" else ROOT
    expected_file = "Dockerfile" if service == "client-check-runner" else "api/Dockerfile.dev"
    require(
        selected["image"] == tag
        and Path(build["context"]) == expected_context
        and build["dockerfile"] == expected_file
        and labels.get("com.docker.compose.project") == state["project"]
        and labels.get("com.docker.compose.service") == service
        and tag in facts_value.get("RepoTags", [])
        and re.fullmatch(r"sha256:[0-9a-f]{64}", facts_value["Id"])
        and facts_value["Id"] not in state["initial_image_ids"],
        "build_association",
    )
    if service == "client-check-runner":
        require(build.get("target") == "ci", "client_build_target")
    else:
        require(
            facts_value["Config"].get("Entrypoint") == ["/entrypoint.sh"],
            "api_entrypoint",
        )


def own_image(config, service, tag):
    value = inspect_one("image", tag)
    build_association(config, service, tag, value)
    state["images"][tag] = {
        "id": value["Id"],
        "config": value["Config"],
        "service": service,
    }


def mounted_candidate(value, include_tests=False):
    mappings = {
        "/app/src": ROOT / "api/src",
        "/app/shared": ROOT / "api/shared",
        "/app/bifrost": ROOT / "api/bifrost",
    }
    if include_tests:
        mappings["/app/tests"] = ROOT / "api/tests"
    mounts = {item["Destination"]: item for item in value.get("Mounts", [])}
    require(
        all(
            destination in mounts
            and mounts[destination].get("Type") == "bind"
            and Path(mounts[destination]["Source"]) == source
            for destination, source in mappings.items()
        ),
        "candidate_mounts",
    )
    require(
        value["Image"] == state["images"]["bifrost-test-api-dev:latest"]["id"],
        "container_image",
    )


def runner_source_command():
    return """import hashlib,json,os,stat
from pathlib import Path
p=Path('/app/tests/e2e/api/test_http_cancel_reference.py')
fd=None
original=None
result=None
try:
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 b=os.fstat(fd)
 if not stat.S_ISREG(b.st_mode) or not 0<b.st_size<=1048576: raise RuntimeError('source_bound')
 data=bytearray()
 while len(data)<=b.st_size:
  block=os.read(fd,min(65536,b.st_size+1-len(data)))
  if not block: break
  data.extend(block)
 a=os.fstat(fd)
 if (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)!=(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns) or len(data)!=b.st_size: raise RuntimeError('source_changed')
 result={'sha256':hashlib.sha256(data).hexdigest(),'path_matches':p.resolve()==p}
except BaseException as error: original=error
finally:
 if fd is not None:
  try: os.close(fd)
  except BaseException as error:
   if original is None: original=error
if original is not None: raise original
print(json.dumps(result,separators=(',',':')))
"""


def probe_runner(end):
    result = ids(
        command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={state['project']}",
                "--filter",
                "label=com.docker.compose.service=test-runner",
                "--filter",
                "label=com.docker.compose.oneoff=True",
            ],
            2,
            end=end,
        )
    )
    require(len(result) <= 1, "runner_collision")
    if not result:
        return
    value = inspect_one("container", result[0], end)
    labels = value["Config"].get("Labels") or {}
    expected = [
        "pytest",
        TARGET,
        "-v",
        "--durations=25",
        "--junitxml=/tmp/bifrost/test-results.xml",
    ]
    require(
        value["State"]["Running"] is True
        and value["Config"]["Cmd"] == expected
        and labels.get("com.docker.compose.project") == state["project"]
        and labels.get("com.docker.compose.service") == "test-runner"
        and labels.get("com.docker.compose.oneoff") == "True"
        and re.fullmatch(r"[0-9a-f]{64}", value["Id"]),
        "runner_identity",
    )
    require(
        type(value["State"]["StartedAt"]) is str
        and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z", value["State"]["StartedAt"])
        and not value["State"]["StartedAt"].startswith("0001-"),
        "runner_start",
    )
    mounted_candidate(value, include_tests=True)
    state["runner"] = {
        "id": value["Id"],
        "image": value["Image"],
        "config": value["Config"],
        "started": value["State"]["StartedAt"],
        "hostname": value["Config"]["Hostname"],
        "test_source": None,
    }

    observed_test = decode(
        command(
            ["docker", "exec", value["Id"], "python", "-c", runner_source_command()],
            2,
            end=end,
        )
    )
    closed(observed_test, ("sha256", "path_matches"))
    require(
        observed_test["sha256"] == state["source"][TEST] and observed_test["path_matches"] is True,
        "runner_mount_readback",
    )

    state["runner"]["test_source"] = observed_test


def stage(name, callback):
    ceilings = {
        "build": 600,
        "pre_pr": 900,
        "stack": 240,
        "measure": 120,
        "target": 1500,
    }
    primary = {
        "build": ["docker", "compose", "-f", "docker-compose.test.yml", "build", "api"],
        "pre_pr": ["./test.sh", "pre-pr"],
        "stack": ["./test.sh", "stack", "up"],
        "target": ["./test.sh", TARGET, "-v"],
    }
    state["stage_end"] = min(WORK_END, time.monotonic() + ceilings[name])
    state["active_stage"] = name
    state["primary_argv"] = primary.get(name)
    original = None
    try:
        callback()
        remaining(state["stage_end"])
        if state["primary_argv"] is None:
            STAGES[name]["exit"] = 0
        require(STAGES[name]["exit"] == 0, "stage_native_missing")
        STAGES[name]["admission"] = "passed"
    except BaseException as error:
        original = error
        # Keep the actual literal native exit, including zero followed by a
        # failed source/witness admission. Never replace it with a guessed code.
        STAGES[name]["admission"] = "failed"
    finally:
        state["stage_end"] = None
        state["primary_argv"] = None
    if original is not None:
        raise original


def raw_copy(name, end):
    path = state["raw_paths"][name]["path"]
    require(path.exists() and not path.is_symlink(), "raw_missing")
    data, identity = read_file(path, RAW_LIMIT, {os.geteuid(), 1000}, end)
    require(state["raw_bytes"] + len(data) <= RAW_TOTAL, "raw_total")
    private_file(state["raw_dir"] / ("owned-" + name), data, RAW_LIMIT, end)
    state["raw_bytes"] += len(data)
    state["raw_paths"][name]["identity"] = identity
    return data


def raw_unlink(name, end):
    record = state["raw_paths"][name]
    if record.get("identity") is None:
        require(
            not record["path"].exists() and not record["path"].is_symlink(),
            "raw_unknown_owner",
        )
        return
    _data, identity = read_file(
        record["path"],
        RAW_LIMIT,
        {os.geteuid(), 1000},
        end,
        identity=record["identity"],
    )
    require(fullfacts(os.lstat(record["path"])) == identity, "raw_unlink_identity")
    os.unlink(record["path"])
    remaining(end)
    record["identity"] = None


def closed(value, keys):
    require(type(value) is dict and set(value) == set(keys), "metadata_shape")


def metadata_installed(value):
    closed(value, state["members"])
    for name, row in value.items():
        closed(row, ("sha256", "distribution_matches", "path_matches"))
        require(
            row["sha256"] == state["members"][name]
            and row["distribution_matches"] is True
            and row["path_matches"] is True,
            "installed_member",
        )


def runner_metadata(value):
    closed(
        value,
        (
            "schema",
            "hostname",
            "python",
            "pytest",
            "fastapi",
            "starlette",
            "test_source",
            "installed",
            "controls",
        ),
    )
    require(
        value["schema"] == "http-cancel-reference-runner/v1"
        and state["runner"] is not None
        and value["hostname"] == state["runner"]["hostname"]
        and value["pytest"] == "9.0.3"
        and value["fastapi"] == "0.139.0"
        and value["starlette"] == "1.3.1"
        and type(value["python"]) is str
        and re.fullmatch(r"3\.14\.[0-9]+", value["python"]),
        "runner_metadata",
    )
    closed(value["test_source"], ("sha256", "path_matches"))
    require(
        value["test_source"]["sha256"] == state["source"][TEST] and value["test_source"]["path_matches"] is True,
        "test_source_binding",
    )
    closed(value["controls"], ("completed", "families", "branches"))
    require(
        value["controls"]["completed"] is True
        and type(value["controls"]["families"]) is int
        and value["controls"]["families"] == 15
        and type(value["controls"]["branches"]) is int
        and value["controls"]["branches"] == 19,
        "controls_metadata",
    )
    require(
        value["test_source"] == state["runner"]["test_source"],
        "runner_independent_source",
    )
    metadata_installed(value["installed"])


def application_metadata(value, case):
    closed(value, ("schema", "case", "modules", "shared_route"))
    expected_modules = {
        "src.main": state["source"]["api/src/main.py"],
        "src.routers.executions": state["source"]["api/src/routers/executions.py"],
        **{
            name: state["members"][name.replace(".", "/") + ".py"]
            for name in (
                "fastapi.routing",
                "fastapi.dependencies.utils",
                "fastapi.dependencies.models",
                "starlette.responses",
            )
        },
    }
    require(
        value["schema"] == "http-cancel-reference-application/v1" and value["case"] == case,
        "application_metadata",
    )
    closed(value["modules"], expected_modules)
    for name, row in value["modules"].items():
        closed(row, ("sha256", "path_matches", "loaded"))
        require(
            row["sha256"] == expected_modules[name] and row["path_matches"] is True and row["loaded"] is True,
            "loaded_source_binding",
        )
    closed(
        value["shared_route"],
        ("endpoint_module_matches", "handle_module_matches", "source_sha256"),
    )
    require(
        value["shared_route"]["endpoint_module_matches"] is True
        and value["shared_route"]["handle_module_matches"] is True
        and value["shared_route"]["source_sha256"] == state["source"]["api/src/routers/executions.py"],
        "shared_route_source",
    )


def installed_command():
    # Disk/distribution source evidence, not API process loaded-module proof.
    return """import hashlib,importlib.metadata,json,os,stat
from pathlib import Path
members=MEMBERS
out={"versions":{key:importlib.metadata.version(key) for key in ("fastapi","starlette","pytest")},"members":{},"sources":{}}
def read(path):
 fd=None
 original=None
 result=None
 try:
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
  b=os.fstat(fd)
  if not stat.S_ISREG(b.st_mode) or not 0<b.st_size<=1048576: raise RuntimeError("metadata_bound")
  data=bytearray()
  while len(data)<=b.st_size:
   block=os.read(fd,min(65536,b.st_size+1-len(data)))
   if not block: break
   data.extend(block)
  a=os.fstat(fd)
  if (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)!=(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns) or len(data)!=b.st_size: raise RuntimeError("metadata_changed")
  result=hashlib.sha256(data).hexdigest()
 except BaseException as error: original=error
 finally:
  if fd is not None:
   try: os.close(fd)
   except BaseException as error:
    if original is None: original=error
 if original is not None: raise original
 return result
for member in members:
 package="pytest" if member.startswith("_pytest/") else member.split("/")[0]
 dist=importlib.metadata.distribution(package)
 path=Path(dist.locate_file(member))
 if path.resolve()!=Path(dist.locate_file("")).resolve()/member: raise RuntimeError("metadata_path")
 out["members"][member]=read(path)
for member in ("src/main.py","src/routers/executions.py"):
 out["sources"][member]=read(Path("/app")/member)
print(json.dumps(out,sort_keys=True,separators=(",",":")))
""".replace("members=MEMBERS", "members=" + repr(tuple(state["members"])))


def booted_metadata(service, key):
    cid_list = ids(
        command(
            ["docker", "compose", "-f", "docker-compose.test.yml", "ps", "-q", service],
            2,
        )
    )
    require(len(cid_list) == 1, "booted_cid")
    value = inspect_one("container", cid_list[0])
    labels = value["Config"].get("Labels") or {}
    require(
        value["State"]["Running"] is True
        and labels.get("com.docker.compose.project") == state["project"]
        and labels.get("com.docker.compose.service") == service,
        "booted_identity",
    )
    mounted_candidate(value)
    measured = decode(command(["docker", "exec", value["Id"], "python", "-c", installed_command()], 20))
    closed(measured, ("versions", "members", "sources"))
    require(
        measured["versions"] == {"fastapi": "0.139.0", "starlette": "1.3.1", "pytest": "9.0.3"}
        and measured["members"] == state["members"],
        "booted_installed",
    )
    expected = {
        path: state["source"]["api/" + path]
        for path in (
            "src/main.py",
            "src/routers/executions.py",
        )
    }
    require(measured["sources"] == expected, "booted_sources")
    state["booted"][service] = {
        "id": value["Id"],
        "image": value["Image"],
        "config": value["Config"],
    }
    state["identities"][key] = {
        "admission": "valid",
        "container_id": value["Id"],
        "image_id": value["Image"],
        "versions": measured["versions"],
        "members": measured["members"],
        "source_matches": True,
    }


def verify_booted():
    for service, before in state["booted"].items():
        value = inspect_one("container", before["id"])
        require(
            value["State"]["Running"] is True
            and value["Image"] == before["image"]
            and value["Config"] == before["config"],
            "booted_changed",
        )
        mounted_candidate(value)
        current = ids(
            command(
                [
                    "docker",
                    "compose",
                    "-f",
                    "docker-compose.test.yml",
                    "ps",
                    "-q",
                    service,
                ],
                2,
            )
        )
        require(
            current == [before["id"]] or current == [before["id"][:12]],
            "booted_replaced",
        )
        measured = decode(
            command(
                ["docker", "exec", before["id"], "python", "-c", installed_command()],
                20,
            )
        )
        require(
            measured["members"] == state["members"]
            and measured["sources"]
            == {
                path: state["source"]["api/" + path]
                for path in (
                    "src/main.py",
                    "src/routers/executions.py",
                )
            },
            "booted_source_changed",
        )


def decimal_count(value):
    require(type(value) is str and re.fullmatch(r"0|[1-9][0-9]{0,5}", value), "xml_count")
    return int(value)


def properties(element, allowed):
    require(
        set(element.attrib) == set() and not (element.text or "").strip(),
        "xml_properties",
    )
    result = {}
    for item in element:
        require(
            item.tag == "property"
            and not list(item)
            and set(item.attrib) == {"name", "value"}
            and not (item.text or "").strip()
            and not (item.tail or "").strip(),
            "xml_property",
        )
        name = item.attrib["name"]
        require(
            name in allowed and name not in result and len(item.attrib["value"].encode()) <= 16384,
            "xml_property_shape",
        )
        result[name] = item.attrib["value"]
    return result


def phase_admission(case, labels):
    require(len(labels) <= 64 and all(label in PHASES for label in labels), "phase_enum")
    require(
        "committed_seed_readback" in labels and "owned_rows_removed" in labels,
        "phase_cleanup",
    )
    if case.startswith("H"):
        expected = {"H04": "403", "H08": "400", "H09": "401"}.get(case, "200")
        require(
            "http_" + expected in labels and "full_row_and_flag_readback" in labels,
            "http_phase",
        )
    else:
        require(
            "full_row_phase_readback" in labels and "request_rollback" not in labels,
            "asgi_phase",
        )
        if case == "P01":
            require(
                labels.count("request_outer_commit") == 2
                and labels.index("request_outer_commit") < labels.index("execution_publisher_entry")
                and labels.index("request_outer_commit") < labels.index("history_publisher_entry")
                and "independent_committed_mutation_readback" in labels,
                "mutation_phase",
            )
        else:
            require(
                labels.count("request_outer_commit") == 1
                and labels.index("inner_terminal_body_send_return")
                < labels.index("request_outer_commit")
                < labels.index("original_route_handle_return")
                and all(
                    label in labels
                    for label in (
                        "advisory_lock_held",
                        "row_lock_held",
                        "advisory_lock_available",
                        "row_lock_available",
                    )
                ),
                "replay_phase",
            )
            if case == "P03":
                require(
                    labels.index("cancel_subscribe_ack") < labels.index("cancel_message_owned"),
                    "cancel_delivery_phase",
                )


def properties_once(element, allowed, seen, reason):
    require(not seen, reason)
    return properties(element, allowed), True


def properties_wrapper_controls():
    empty = ET.Element("properties")
    valid = ET.Element("properties")
    ET.SubElement(valid, "property", name="control", value="synthetic")
    for reason in ("xml_suite_duplicate_properties", "xml_case_duplicate_properties"):
        for first, second in ((empty, valid), (empty, empty), (valid, empty)):
            admitted, seen = properties_once(first, {"control"}, False, reason)
            require(
                seen is True and admitted == ({"control": "synthetic"} if first is valid else {}),
                "properties_control_single",
            )
            try:
                properties_once(second, {"control"}, seen, reason)
            except Failure as error:
                require(error.label == reason, "properties_control_reason")
            else:
                raise Failure("properties_control_duplicate")


def project_junit(raw):
    require(len(raw) <= RAW_LIMIT, "xml_bound")
    try:
        value = raw.decode("utf-8")
    except UnicodeError:
        state["projection"]["admission"] = "invalid_utf8"
        raise Failure("xml_utf8") from None
    require(not value.startswith("\ufeff"), "xml_bom")
    declaration = re.match(r"\A\s*<\?xml\s+([^?]*)\?>", value)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration[1], re.I)
        require(encoding is None or encoding[1].lower() in {"utf-8", "utf8"}, "xml_encoding")
    require(not re.search(r"<!\s*(DOCTYPE|ENTITY)", value, re.I), "xml_entity")
    try:
        root = ET.fromstring(value)
    except (ET.ParseError, ValueError, RecursionError):
        raise Failure("xml_invalid") from None
    require(
        root.tag == "testsuites"
        and set(root.attrib) <= {"name", "tests", "failures", "errors", "time"}
        and len(root) == 1
        and root[0].tag == "testsuite",
        "xml_root",
    )
    suite = root[0]
    require(
        set(suite.attrib)
        <= {
            "name",
            "tests",
            "failures",
            "errors",
            "skipped",
            "time",
            "timestamp",
            "hostname",
        }
        and all(name in suite.attrib for name in ("tests", "failures", "errors", "skipped")),
        "xml_suite",
    )
    for item in root.iter():
        require(not (item.tail or "").strip(), "xml_tail")
        if "time" in item.attrib:
            require(
                re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", item.attrib["time"]) is not None
                and len(item.attrib["time"]) <= 32,
                "xml_time",
            )
    safe_root = ET.Element("testsuites")
    safe_suite = ET.SubElement(safe_root, "testsuite", name="http_cancel_reference")
    suite_props = {}
    suite_props_seen = False
    cases = []
    counts = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    for item in suite:
        if item.tag == "properties":
            suite_props, suite_props_seen = properties_once(
                item,
                {"http_cancel_reference_runner_identity"},
                suite_props_seen,
                "xml_suite_duplicate_properties",
            )
            continue
        require(
            item.tag == "testcase" and set(item.attrib) <= {"classname", "name", "time", "file", "line"},
            "xml_case",
        )
        require(
            item.attrib.get("classname") == "tests.e2e.api.test_http_cancel_reference",
            "xml_classname",
        )
        name = item.attrib.get("name")
        matches = [case for case, expected in CASE_NAMES.items() if expected == name]
        require(
            len(matches) == 1 and not any(row["case"] == matches[0] for row in cases),
            "xml_case_identity",
        )
        case = matches[0]
        case_properties = {}
        case_properties_seen = False
        outcome = "passed"
        for node in item:
            if node.tag == "properties":
                case_properties, case_properties_seen = properties_once(
                    node,
                    PROPERTY_KEYS,
                    case_properties_seen,
                    "xml_case_duplicate_properties",
                )
            elif node.tag in {"failure", "error", "skipped"}:
                require(
                    outcome == "passed" and set(node.attrib) <= {"type", "message"} and not list(node),
                    "xml_outcome",
                )
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[node.tag]
            else:
                require(
                    node.tag in {"system-out", "system-err"} and not node.attrib and not list(node),
                    "xml_output",
                )
        require(
            case_properties.get("http_cancel_reference_case", case) == case,
            "xml_case_property",
        )
        cleanup = case_properties.get("http_cancel_reference_cleanup")
        require(
            cleanup is None or cleanup in {"ok", "failed", "retained"},
            "cleanup_property",
        )
        labels = case_properties.get("http_cancel_reference_phases", "").split(",")
        labels = [] if labels == [""] else labels
        require(len(labels) <= 64 and all(label in PHASES for label in labels), "xml_phases")
        if outcome == "passed":
            require(
                cleanup == "ok"
                and set(case_properties)
                >= {
                    "http_cancel_reference_case",
                    "http_cancel_reference_phases",
                    "http_cancel_reference_cleanup",
                },
                "passed_properties",
            )
            phase_admission(case, labels)
        if "http_cancel_reference_application_identity" in case_properties:
            require(case.startswith("P"), "unexpected_application_metadata")
            measured = decode(case_properties["http_cancel_reference_application_identity"].encode())
            application_metadata(measured, case)
            admission = {"admission": "valid", "measurement": measured}
            state["identities"][case] = admission
            state["target"]["application_identities"][case] = admission
        elif outcome == "passed" and case.startswith("P"):
            raise Failure("application_metadata_missing")
        cases.append(
            {
                "case": case,
                "name": name,
                "status": outcome,
                "cleanup": cleanup,
                "phases": labels,
            }
        )
        counts["tests"] += 1
        if outcome != "passed":
            counts[{"failed": "failures", "error": "errors", "skipped": "skipped"}[outcome]] += 1
        safe_case = ET.SubElement(
            safe_suite,
            "testcase",
            classname="tests.e2e.api.test_http_cancel_reference",
            name=name,
        )
        if outcome != "passed":
            element = {"failed": "failure", "error": "error", "skipped": "skipped"}[outcome]
            ET.SubElement(
                safe_case,
                element,
                **({} if outcome == "skipped" else {"type": "http_cancel_reference_failed"}),
            )
    require(
        all(decimal_count(suite.attrib[name]) == count for name, count in counts.items()),
        "xml_counts",
    )
    if suite_props:
        measured = decode(suite_props["http_cancel_reference_runner_identity"].encode())
        runner_metadata(measured)
        state["target"]["runner_identity"] = {
            "admission": "valid",
            "measurement": measured,
        }
        state["target"]["controls"] = measured["controls"]
        state["identities"]["runner"] = state["target"]["runner_identity"]
    for name, count in counts.items():
        safe_suite.set(name, str(count))
    projected = ET.tostring(safe_root, encoding="utf-8", xml_declaration=True)
    state["target"]["observed_cases"] = sorted(cases, key=lambda row: row["case"])
    state["projection"] = {"admission": "valid", "sha256": digest(projected)}
    state["projected"] = projected
    require(
        counts == {"tests": 12, "failures": 0, "errors": 0, "skipped": 0}
        and set(row["case"] for row in cases) == set(CASE_NAMES)
        and bool(suite_props),
        "target_results",
    )


def git(*args, end=None):
    return command(["git", *args], 15, end=end)


def source_guard():
    require(
        not git("status", "--porcelain", "--untracked-files=all").strip(),
        "candidate_dirty",
    )
    head = git("rev-parse", "HEAD").decode("ascii").strip()
    tree = git("rev-parse", "HEAD^{tree}").decode("ascii").strip()
    git("fetch", "--quiet", "origin", "main")
    main = git("rev-parse", "origin/main").decode("ascii").strip()
    git("merge-base", "--is-ancestor", "origin/main", "HEAD")
    require(
        all(re.fullmatch(r"[0-9a-f]{40}", item) for item in (head, tree, main)),
        "revision_shape",
    )
    run_id, run_attempt, job = (
        os.environ.get(name, "") for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")
    )
    require(
        all(re.fullmatch(r"[0-9]{1,32}", item) for item in (run_id, run_attempt))
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", job),
        "github_identity",
    )
    state["candidate"] = {
        "head": head,
        "tree": tree,
        "main": main,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "job": job,
        "project": None,
    }
    for path in SOURCE_PATHS:
        line = git("ls-tree", "HEAD", "--", path).decode("ascii").strip()
        require(
            re.fullmatch(r"100(?:644|755) blob [0-9a-f]{40}\t" + re.escape(path), line),
            "source_mode",
        )
        committed = git("show", "HEAD:" + path)
        require(
            len(committed) <= RAW_LIMIT and not (ROOT / path).is_symlink() and (ROOT / path).read_bytes() == committed,
            "source_bytes",
        )
        state["source"][path] = digest(committed)
    tree_ast = ast.parse((ROOT / TEST).read_text())
    matches = [
        node
        for node in tree_ast.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_METADATA_MEMBERS" for target in node.targets)
    ]
    require(len(matches) == 1, "member_roster")
    state["members"] = ast.literal_eval(matches[0].value)
    require(
        type(state["members"]) is dict
        and len(state["members"]) == 12
        and all(
            type(key) is str
            and re.fullmatch(r"[a-zA-Z0-9_/]+\.py", key)
            and type(value) is str
            and re.fullmatch(r"[0-9a-f]{64}", value)
            for key, value in state["members"].items()
        ),
        "member_roster_shape",
    )
    raw_project = command(["bash", "-c", "source scripts/lib/test_helpers.sh; compute_project_name ."], 5)
    project = raw_project.decode("ascii").strip()
    require(re.fullmatch(r"bifrost-test-[0-9a-f]{8}", project), "project_shape")
    state["project"] = project
    state["candidate"]["project"] = project
    state["resources"]["project"] = project
    require(
        all(not values for values in resources(WORK_END).values()),
        "preexisting_resources",
    )
    initial_images = command(["docker", "image", "ls", "-q", "--no-trunc"], 5).decode("ascii").splitlines()
    require(
        len(initial_images) <= 128 and all(re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in initial_images),
        "initial_images",
    )
    state["initial_image_ids"] = set(initial_images)
    for tag in ("bifrost-test-api-dev:latest", "bifrost-test-client-check:latest"):
        require(
            not command(["docker", "image", "ls", "-q", "--no-trunc", tag], 2).strip(),
            "preexisting_tag",
        )
    log_dir = Path("/tmp") / ("bifrost-" + project)
    for name, filename in (
        ("junit", "test-results.xml"),
        ("runner", "test-runner.log"),
        ("quality", "quality-api.log"),
    ):
        path = log_dir / filename
        require(not path.exists() and not path.is_symlink(), "preexisting_raw")
        state["raw_paths"][name] = {"path": path, "identity": None}
    lock_dir = Path(git("rev-parse", "--path-format=absolute", "--git-path", "bifrost-test-locks").decode().strip())
    state["ledger"] = lock_dir / "pre-pr-stages.json"
    state["plan"] = lock_dir / "pre-pr-affected-plan.json"
    require(not state["ledger"].exists() and not state["plan"].exists(), "preexisting_prepr")
    # Optional live secret files are not part of this dedicated experiment.
    require(not (ROOT / ".env.test").exists(), "optional_environment_present")
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip())
    require(common == ROOT / ".git", "hosted_checkout_required")


def pre_pr_evidence(config):
    plan, _facts = read_file(state["plan"], 65536, {os.geteuid()}, WORK_END)
    value = decode(plan)
    lanes = (
        "api_quality",
        "api_unit",
        "api_e2e",
        "client_quality",
        "client_unit",
        "client_e2e",
        "mcp_conformance",
    )
    require(
        value.get("schema") == "bifrost.affected-tests/v1"
        and value.get("scope") == "comprehensive"
        and value.get("lanes") == dict.fromkeys(lanes, "comprehensive"),
        "prepr_plan",
    )
    context = "scope=comprehensive;full=0;plan=" + digest(plan)
    ledger, _facts = read_file(state["ledger"], 65536, {os.geteuid()}, WORK_END)
    observed = decode(ledger)
    closed(observed, ("stages",))
    require(
        set(observed["stages"]) == {"repository", "client", "quality", "generated"},
        "prepr_stages",
    )
    for _name, row in observed["stages"].items():
        closed(row, ("status", "context", "signature"))
        require(
            row["status"] == "complete"
            and row["context"] == context
            and row["signature"].get("head") == state["candidate"]["head"]
            and row["signature"].get("status") == "",
            "prepr_stage_complete",
        )
    client_row = observed["stages"]["client"]
    snapshot = decode(
        command(
            [
                "python3",
                "scripts/lib/pre_pr_stage_evidence.py",
                "snapshot",
                "--repo",
                str(ROOT),
                "--state",
                str(state["ledger"]),
                "--stage",
                "client",
            ],
            20,
        )
    )
    keys = {
        "head",
        "status",
        "compose_sha256",
        "env_sha256",
        "docker_version",
        "compose_version",
        "python_version",
        "node_version",
        "compose_images",
    }
    require(
        set(client_row["signature"]) == keys and client_row["signature"] == {key: snapshot[key] for key in keys},
        "client_signature",
    )
    tag = "bifrost-test-client-check:latest"
    facts_value = inspect_one("image", tag)
    require(
        facts_value["Id"] in client_row["signature"]["compose_images"],
        "client_image_membership",
    )
    own_image(config, "client-check-runner", tag)
    state["planner"] = {
        "base": state["candidate"]["main"],
        "head": state["candidate"]["head"],
        "sha256": digest(plan),
        "scope": "comprehensive",
        "lanes": value["lanes"],
    }


def target_run():
    require(not state["raw_paths"]["junit"]["path"].exists(), "target_old_junit")
    state["target_started"] = True
    code, _out, _err = child(["./test.sh", TARGET, "-v"], 1500, live=True)
    STAGES["target"]["exit"] = code
    if code != 0:
        raise Failure("target_native_failure", code)


def verify_sources(end):
    for path, expected in state["source"].items():
        require(
            not (ROOT / path).is_symlink() and digest((ROOT / path).read_bytes()) == expected,
            "source_changed",
        )
        remaining(end)


def admission_failure(label):
    if label == "raw_bound":
        return "oversize"
    if label in {"xml_utf8", "xml_bom", "xml_encoding"}:
        return "invalid_utf8"
    if label in {"xml_invalid", "xml_entity"}:
        return "invalid_xml"
    return "invalid_shape"


def finalize_junit(end):
    if not state["target_started"]:
        return
    raw = raw_copy("junit", end)
    try:
        project_junit(raw)
    except BaseException as error:
        if state["projection"]["admission"] != "valid":
            state["projection"]["admission"] = admission_failure(
                error.label if isinstance(error, Failure) else "unknown"
            )
        raise


def remove_runner(end):
    owned = state["runner"]
    if owned is None:
        if state["target_started"]:
            raise Failure("runner_custody_unknown")
        return
    current = ids(
        command(
            ["docker", "container", "ls", "-aq", "--filter", "id=" + owned["id"]],
            2,
            end=end,
        )
    )
    if not current:
        return
    require(len(current) == 1 and owned["id"].startswith(current[0]), "runner_replaced")
    value = inspect_one("container", owned["id"], end)
    labels = value["Config"].get("Labels") or {}
    require(
        value["Image"] == owned["image"]
        and value["Config"] == owned["config"]
        and labels.get("com.docker.compose.project") == state["project"]
        and labels.get("com.docker.compose.service") == "test-runner"
        and labels.get("com.docker.compose.oneoff") == "True",
        "runner_removal_identity",
    )
    command(["docker", "container", "rm", "-f", owned["id"]], 5, end=end)
    require(
        not command(
            ["docker", "container", "ls", "-aq", "--filter", "id=" + owned["id"]],
            2,
            end=end,
        ).strip(),
        "runner_removal",
    )


def image_cleanup(end):
    errors = []
    for tag in sorted(state["image_pending"]):
        try:
            require(tag in state["images"], "partial_build_unknown")
            owned = state["images"][tag]
            facts_value = inspect_one("image", tag, end)
            require(
                facts_value["Id"] == owned["id"] and facts_value["Config"] == owned["config"],
                "image_replaced",
            )
            require(
                not command(
                    [
                        "docker",
                        "container",
                        "ls",
                        "-aq",
                        "--filter",
                        "ancestor=" + owned["id"],
                    ],
                    2,
                    end=end,
                ).strip(),
                "image_referenced",
            )
            command(["docker", "image", "rm", tag], 10, end=end)
            require(
                not command(["docker", "image", "ls", "-q", "--no-trunc", tag], 2, end=end).strip(),
                "image_removal",
            )
            # The own alias was removed. Do not assert removal of unowned base
            # layers or shared BuildKit cache, nor delete other tags.
        except BaseException as error:
            errors.append(error)
    state["image_cleanup"] = (
        "retained_unknown" if errors else "known_tags_removed" if state["image_pending"] else "not_started"
    )
    if errors:
        raise errors[0]


def cleanup():
    first = None

    def attempt(callback):
        nonlocal first
        try:
            callback()
        except BaseException as error:
            if first is None:
                first = error
            state["cleanup_exit"] = 1
            state["retained"] = True

    # Independently capture actual result evidence and dispose the actual runner
    # even if earlier capture/identity failed. Captures happen after host child
    # settlement; a still-running Docker writer fails stable reads red.
    attempt(lambda: finalize_junit(CLEAN_END))
    attempt(lambda: remove_runner(CLEAN_END))
    if state["effects_started"]:
        attempt(lambda: command(["./test.sh", "stack", "down"], 60, end=CLEAN_END))
    if state["project"] is not None:
        try:
            found = resources(CLEAN_END)
            state["resources"].update(inspection="valid", **found)
            state["inspection_exit"] = 0
            state["resources_empty"] = all(not rows for rows in found.values())
            if not state["resources_empty"]:
                raise Failure("owned_resources_retained")
        except BaseException as error:
            state["inspection_exit"] = 1
            if first is None:
                first = error
            state["cleanup_exit"] = 1
            state["retained"] = True
    for name, _record in state["raw_paths"].items():
        if name != "junit":

            def copy_if_present(name=name):
                if state["raw_paths"][name]["path"].exists():
                    raw_copy(name, CLEAN_END)

            attempt(copy_if_present)
        attempt(lambda name=name: raw_unlink(name, CLEAN_END))
    attempt(lambda: image_cleanup(CLEAN_END))
    attempt(lambda: verify_sources(CLEAN_END))
    STAGES["cleanup"].update(exit=state["cleanup_exit"], admission="passed" if first is None else "retained")
    return first


def json_bytes(value):
    def bounded(item, depth=0):
        require(depth <= 16, "safe_depth")
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            require(abs(item) <= 9007199254740991, "safe_integer")
        elif type(item) is str:
            require(len(item.encode()) <= 16384 and "\0" not in item, "safe_string")
        elif type(item) is list:
            require(len(item) <= 128, "safe_list")
            for row in item:
                bounded(row, depth + 1)
        elif type(item) is dict:
            require(len(item) <= 128 and all(type(key) is str for key in item), "safe_keys")
            for key, row in item.items():
                bounded(key, depth + 1)
                bounded(row, depth + 1)
        else:
            raise Failure("safe_type")

    bounded(value)
    data = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    require(len(data) <= SAFE_LIMIT, "safe_bound")
    return data


def public_receipt():
    return {
        "schema": "http-cancel-reference-ci/v1",
        "candidate": state["candidate"],
        "planner": state["planner"],
        "stages": STAGES,
        "target": state["target"],
        "projection": state["projection"],
        "cleanup": {
            "primary_exit": state["primary_exit"],
            "cleanup_exit": state["cleanup_exit"],
            "inspection_exit": state["inspection_exit"],
            "process_settled": state["process_settled"],
            "resources_empty": state["resources_empty"],
            "image_cleanup": state["image_cleanup"],
            "retained": state["retained"],
            "build_cache_disposal": "unverified_runner_lifecycle",
        },
        "limits": {
            "job": 3600,
            "conductor": 3300,
            "cleanup": 180,
            "publication": 60,
            "target": 1500,
            "item": 120,
            "case": 90,
            "work": 75,
            "reserve": 15,
            "raw_each": RAW_LIMIT,
            "raw_total": RAW_TOTAL,
            "artifact": SAFE_LIMIT,
        },
    }


def dispose_private(end):
    directory = state["raw_dir"]
    if directory is None:
        return
    require(
        facts(os.lstat(directory)) == state["raw_dir_identity"],
        "private_directory_changed",
    )
    errors = []
    for path in sorted(directory.iterdir()):
        try:
            identity = state["raw_files"].get(str(path)) or state["safe_files"].get(str(path))
            require(identity is not None, "private_unknown_file")
            require(facts(os.lstat(path)) == identity, "private_file_replaced")
            os.unlink(path)
            remaining(end)
        except BaseException as error:
            errors.append(error)
    try:
        require(
            facts(os.lstat(directory)) == state["raw_dir_identity"],
            "private_directory_changed",
        )
        directory.rmdir()
    except BaseException as error:
        errors.append(error)
    if errors:
        raise errors[0]


def publish():
    remaining(PUB_END)
    require(
        state["safe_dir"] is not None and state["candidate"] is not None,
        "publication_unadmitted",
    )
    require(
        facts(os.lstat(state["safe_dir"])) == state["safe_dir_identity"],
        "safe_directory_changed",
    )
    # Publication status is latched before constructing the primary receipt.
    # Any failure later is separately RED and never writes a replacement green.
    STAGES["publication"].update(exit=0, admission="passed")
    data = {
        "source-hashes.json": json_bytes(
            {
                "schema": "http-cancel-reference-source/v1",
                "head": state["candidate"]["head"],
                "files": state["source"],
            }
        ),
        "installed-identities.json": json_bytes(state["identities"]),
        "owned-resources.json": json_bytes(state["resources"]),
        "projected-tests.xml": state.get("projected", b'<?xml version="1.0" encoding="utf-8"?>\n<testsuites />'),
    }
    # Empty projection is explicitly not actual missing results: admission is
    # absent/invalid and receipt observed_cases stays actual admitted rows only.
    require(
        sum(len(value) for value in data.values()) + len(json_bytes(public_receipt())) <= SAFE_LIMIT,
        "artifact_bound",
    )
    for name, value in data.items():
        private_file(state["safe_dir"] / name, value, SAFE_LIMIT, PUB_END)
    # Write primary receipt last, after all dependent safe files/readbacks.
    private_file(
        state["safe_dir"] / "receipt.json",
        json_bytes(public_receipt()),
        SAFE_LIMIT,
        PUB_END,
    )
    remaining(PUB_END)


def acquire_directories():
    supplied = Path(os.environ.get("RUNNER_TEMP", ""))
    require(re.fullmatch(r"/[a-zA-Z0-9_./-]+", str(supplied)), "runner_temp_shape")
    require(
        supplied.is_absolute() and supplied.exists() and supplied.is_dir() and not supplied.is_symlink(),
        "runner_temp",
    )
    parent = os.lstat(supplied)
    require(parent.st_uid == os.geteuid(), "runner_temp_owner")
    suffix = os.environ.get("GITHUB_RUN_ID", "") + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[0-9]{1,32}-[0-9]{1,32}", suffix), "run_suffix")
    for label, key in (
        ("http-cancel-private-", "raw_dir"),
        ("http-cancel-evidence-", "safe_dir"),
    ):
        directory = supplied / (label + suffix)
        directory.mkdir(mode=0o700)
        state[key] = directory
        value = os.lstat(directory)
        require(
            stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid() and stat.S_IMODE(value.st_mode) == 0o700,
            "owned_directory",
        )
        state[key + "_identity"] = facts(value)
    output = Path(os.environ.get("GITHUB_ENV", ""))
    require(
        output.is_absolute() and output.is_file() and not output.is_symlink(),
        "github_env",
    )
    # Only nonsecret owned safe-directory locator; raw paths are not exported.
    fd = None
    original = secondary = None
    try:
        fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
        item = os.fstat(fd)
        require(
            stat.S_ISREG(item.st_mode) and item.st_uid == os.geteuid() and item.st_nlink == 1,
            "github_env_identity",
        )
        view = memoryview(("HTTP_CANCEL_REFERENCE_EVIDENCE=" + str(state["safe_dir"]) + "\n").encode())
        while view:
            count = os.write(fd, view)
            require(count > 0, "github_env_write")
            view = view[count:]
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                secondary = error
    finish_error(original, secondary)
    remaining(WORK_END)


def main_work():
    properties_wrapper_controls()
    source_guard()
    config = compose_config()
    state["image_pending"].add("bifrost-test-api-dev:latest")

    def build():
        command(["docker", "compose", "-f", "docker-compose.test.yml", "build", "api"], 600)
        own_image(config, "api", "bifrost-test-api-dev:latest")

    stage("build", build)
    state["image_pending"].add("bifrost-test-client-check:latest")

    def pre_pr():
        state["effects_started"] = True
        command(["./test.sh", "pre-pr"], 900)
        pre_pr_evidence(config)

    stage("pre_pr", pre_pr)
    for name in ("junit", "runner", "quality"):
        if state["raw_paths"][name]["path"].exists():
            raw_copy(name, WORK_END)
            raw_unlink(name, WORK_END)
    stage("stack", lambda: command(["./test.sh", "stack", "up"], 240))
    stage(
        "measure",
        lambda: (
            booted_metadata("api", "booted_api"),
            booted_metadata("api-replica", "booted_replica"),
        ),
    )
    require(
        all(not state["raw_paths"][name]["path"].exists() for name in ("junit", "runner")),
        "target_raw_replaced",
    )

    def target():
        target_run()
        verify_booted()
        verify_sources(state["stage_end"])

    stage("target", target)


original = None
try:
    acquire_directories()
    main_work()
except BaseException as error:
    original = error
    native_code = (
        error.code if isinstance(error, (Failure, SystemExit)) else 130 if isinstance(error, KeyboardInterrupt) else 1
    )
    state["primary_exit"] = native_code if type(native_code) is int and 1 <= native_code <= 255 else 1
finally:
    cleanup_error = None
    try:
        cleanup_error = cleanup()
    except BaseException as error:
        cleanup_error = error
        state["cleanup_exit"] = 1
        state["retained"] = True
        STAGES["cleanup"].update(exit=1, admission="retained")
    if original is None and cleanup_error is not None:
        original = cleanup_error
        state["primary_exit"] = (
            cleanup_error.code
            if isinstance(cleanup_error, SystemExit) and type(cleanup_error.code) is int
            else 130
            if isinstance(cleanup_error, KeyboardInterrupt)
            else 1
        )
    try:
        dispose_private(CLEAN_END)
    except BaseException as error:
        state["cleanup_exit"] = 1
        state["retained"] = True
        STAGES["cleanup"].update(exit=1, admission="retained")
        if original is None:
            original = error
            state["primary_exit"] = (
                error.code
                if isinstance(error, SystemExit) and type(error.code) is int
                else 130
                if isinstance(error, KeyboardInterrupt)
                else 1
            )
    try:
        publish()
    except BaseException as error:
        STAGES["publication"].update(exit=1, admission="failed")
        # Invalidate only exclusively acquired safe files; never allow a late
        # publication/readback failure to leave a seemingly green receipt.
        for path, identity in state["safe_files"].items():
            if state["safe_dir"] is not None and Path(path).parent == state["safe_dir"]:
                try:
                    require(facts(os.lstat(path)) == identity, "safe_removal_identity")
                    os.unlink(path)
                except BaseException:
                    state["retained"] = True
        if original is None:
            original = error
            state["primary_exit"] = (
                error.code
                if isinstance(error, SystemExit) and type(error.code) is int
                else 130
                if isinstance(error, KeyboardInterrupt)
                else 1
            )
# No error messages/arguments/tracebacks or private metadata to console.
print("HTTP cancellation reference: " + ("passed" if original is None else "failed"))
raise SystemExit(state["primary_exit"])
PY
