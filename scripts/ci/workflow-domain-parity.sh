#!/usr/bin/env bash
# Test-only claim/domain lane. SOURCE package; supported CI execution only.
set -euo pipefail
python3 -I -S -B - <<'PY'
import time

T0 = time.monotonic()

import ast  # noqa: E402 - T0 is anchored before imports.
import configparser  # noqa: E402 - T0 is anchored before imports.
import hashlib  # noqa: E402 - T0 is anchored before imports.
import ipaddress  # noqa: E402 - T0 is anchored before imports.
import json  # noqa: E402 - T0 is anchored before imports.
import os  # noqa: E402 - T0 is anchored before imports.
import re  # noqa: E402 - T0 is anchored before imports.
import selectors  # noqa: E402 - T0 is anchored before imports.
import signal  # noqa: E402 - T0 is anchored before imports.
import socket  # noqa: E402 - T0 is anchored before imports.
import stat  # noqa: E402 - T0 is anchored before imports.
import struct  # noqa: E402 - T0 is anchored before imports.
import subprocess  # noqa: E402 - T0 is anchored before imports.
import tempfile  # noqa: E402 - T0 is anchored before imports.
import xml.etree.ElementTree as ET  # noqa: E402 - T0 is anchored before imports.
from pathlib import Path  # noqa: E402 - T0 is anchored before imports.

# T0 precedes every other import/source/bootstrap/native acquisition. The earlier job deadline wins.
WORK_END = T0 + 2520
CLEANUP_END = T0 + 2640
PUBLICATION_END = T0 + 2700
STREAM_LIMIT = 16 * 1024 * 1024
CAPTURE_LIMIT = 64 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}")
IID = re.compile(r"sha256:[0-9a-f]{64}")
WIRE = "bifrost.test.claim-frontend-control/v1"
FILE_CUSTODY = []  # All local returned FDs, including unknown close outcomes; never retried.
PUBLIC_OUTPUTS = frozenset(
    (
        "source.txt",
        "receipt.json",
        "custody-before.txt",
        "custody-after.txt",
        "custody-ownership-after.txt",
        "api-image-before.txt",
        "api-image-after.txt",
        "frontend.json",
        "gate-exit-status.txt",
        "exit-status.txt",
        "project-resources.txt",
    )
)
FAILURE_SCHEMA = "bifrost.test.claim-parent-failure/v1"
FAILURE_PREFIX = b"bifrost-claim-parent-failure/v1 "
FAILURE_PHASES = frozenset(
    ("setup", "controls", "source", "prepr", "build", "stack", "target", "frontend", "cleanup", "publication")
)
FAILURE_CLASSES = frozenset(("control", "guard", "os", "system_exit", "interrupt", "other"))
PREPR_STAGES = frozenset(
    ("repository", "client", "stack", "quality", "generated", "unit", "e2e", "mcp", "client-unit", "browser", "image")
)
PREPR_SCHEMA = "bifrost.test.claim-prepr-stage/v1"
PREPR_HELPER_HASH = "60da3f4c3e95cb220738d077c9dec59451bb66acde732b71067e8d44b9f5de11"
BUILD_GUARD_SCHEMA = "bifrost.test.claim-build-guard/v1"
BUILD_GUARD_PREDICATES = frozenset(
    (
        "builder-identity",
        "builder-label",
        "builder-command",
        "builder-source-image",
        "builder-current-custody",
        "builder-security",
        "builder-mounts",
        "builder-state",
        "builder-terminal",
    )
)

SOURCE_PATHS = (
    ".github/workflows/workflow-domain-parity.yml",
    "api/Dockerfile.dev",
    "api/alembic/versions/20260831_execution_attempts.py",
    "api/alembic/versions/20260919_postgres_delivery.py",
    "api/entrypoint.sh",
    "api/pytest.ini",
    "api/src/config.py",
    "api/src/core/database.py",
    "api/src/jobs/consumers/workflow_execution.py",
    "api/src/models/enums.py",
    "api/src/models/orm/executions.py",
    "api/src/models/orm/work_deliveries.py",
    "api/src/repositories/executions.py",
    "api/src/services/execution/attempts.py",
    "api/src/services/execution/process_pool.py",
    "api/src/services/work_delivery_store.py",
    "api/tests/conftest.py",
    "api/tests/parity/fixtures/workflow-claim-v1.json",
    "api/tests/parity/fixtures/workflow-domain-v1.json",
    "api/tests/parity/test_workflow_domain.py",
    "api/tests/parity/workflow_domain_harness.py",
    "core-rs/Cargo.lock",
    "core-rs/Dockerfile",
    "core-rs/crates/bifrost-domain/Cargo.toml",
    "core-rs/crates/bifrost-domain/examples/workflow_domain_vectors.rs",
    "core-rs/crates/bifrost-domain/src/lib.rs",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs",
    "core-rs/rust-toolchain.toml",
    "docker-compose.test.yml",
    "requirements.lock",
    "scripts/ci/workflow-domain-parity.sh",
    "scripts/lib/test_helpers.sh",
    "test.sh",
)

ORIGINAL_COMMANDS = (
    ("source.root", ("git", "rev-parse", "--show-toplevel"), "work"),
    (
        "source.project",
        ("bash", "-eu", "-o", "pipefail", "-c", "source scripts/lib/test_helpers.sh; compute_project_name ."),
        "work",
    ),
    ("source.clean", ("git", "status", "--porcelain", "--untracked-files=all"), "work"),
    ("initial.containers", ("docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=<PROJECT>"), "work"),
    (
        "initial.volumes",
        ("docker", "volume", "ls", "-q", "--filter", "label=com.docker.compose.project=<PROJECT>"),
        "work",
    ),
    (
        "initial.networks",
        ("docker", "network", "ls", "-q", "--filter", "label=com.docker.compose.project=<PROJECT>"),
        "work",
    ),
    ("evidence.mkdir", ("mkdir", "-p", "<EVIDENCE>"), "work"),
    ("evidence.mode", ("chmod", "1777", "<EVIDENCE>"), "work"),
    ("observations.mkdir", ("mkdir", "<EVIDENCE>/observations"), "work"),
    ("observations.mode", ("chmod", "1777", "<EVIDENCE>/observations"), "work"),
    ("source.head", ("git", "rev-parse", "HEAD"), "work"),
    ("source.head_tree", ("git", "rev-parse", "HEAD", "HEAD^{tree}"), "work"),
    ("prepr", ("./test.sh", "pre-pr"), "work"),
    ("prepr.head", ("git", "rev-parse", "HEAD"), "work"),
    ("prepr.clean", ("git", "status", "--porcelain", "--untracked-files=all"), "work"),
    (
        "checks.build",
        ("docker", "build", "--target", "checks", "-t", "<CHECKS_IMAGE>", "-f", "core-rs/Dockerfile", "core-rs"),
        "work",
    ),
    ("checks.image", ("docker", "image", "inspect", "<CHECKS_IMAGE>", "--format", "{{.Id}}"), "work"),
    (
        "builder.create",
        (
            "docker",
            "create",
            "--name",
            "<BUILDER_NAME>",
            "--label",
            "com.docker.compose.project=<PROJECT>",
            "<CHECKS_IMAGE>",
            "sh",
            "-eu",
            "-c",
            (
                "\n"
                "        cargo test --locked --offline -p bifrost-domain --example workflow_domain_vectors\n"
                "        cargo build --locked --offline -p bifrost-domain --example workflow_domain_vectors\n"
                "        install -m 755 target/debug/examples/workflow_domain_vectors /tmp/workflow-domain-driver\n"
                "    "
            ),
        ),
        "work",
    ),
    ("builder.start", ("docker", "start", "-a", "<BUILDER_NAME>"), "work"),
    ("builder.terminal", ("docker", "inspect", "--type", "container", "<ACTUAL_RETAINED_BUILDER_CID>"), "work"),
    ("driver.copy", ("docker", "cp", "<BUILDER_NAME>:/tmp/workflow-domain-driver", "<EVIDENCE>/driver"), "work"),
    ("driver.mode", ("chmod", "755", "<EVIDENCE>/driver"), "work"),
    ("builder.remove", ("docker", "rm", "<BUILDER_NAME>"), "work"),
    ("receipt.head", ("git", "rev-parse", "HEAD"), "work"),
    ("receipt.tree", ("git", "rev-parse", "HEAD^{tree}"), "work"),
    ("custody.before", ("sha256sum", "<EVIDENCE>/driver", "<EVIDENCE>/receipt.json"), "work"),
    ("stack.up", ("./test.sh", "stack", "up"), "work"),
    ("api.before", ("docker", "image", "inspect", "bifrost-test-api-dev:latest", "--format", "{{.Id}}"), "work"),
    ("junit.invalidate", ("rm", "-f", "<LOG_DIR>/test-results.xml"), "work"),
    ("target", ("./test.sh", "tests/parity/test_workflow_domain.py", "-v"), "work"),
    ("source.final_head", ("git", "rev-parse", "HEAD"), "work"),
    ("source.final_clean", ("git", "status", "--porcelain", "--untracked-files=all"), "work"),
    ("custody.after", ("sha256sum", "<EVIDENCE>/driver", "<EVIDENCE>/receipt.json"), "cleanup"),
    (
        "custody.ownership",
        ("stat", "-c", "%n uid=%u gid=%g mode=%a", "<EVIDENCE>", "<EVIDENCE>/driver", "<EVIDENCE>/receipt.json"),
        "cleanup",
    ),
    ("api.after", ("docker", "image", "inspect", "bifrost-test-api-dev:latest", "--format", "{{.Id}}"), "cleanup"),
    ("junit.copy", ("cp", "<LOG_DIR>/test-results.xml", "<EVIDENCE>/test-results.xml"), "cleanup"),
    ("builder.cleanup_inspect", ("docker", "container", "inspect", "<BUILDER_NAME>"), "cleanup"),
    ("builder.cleanup_remove", ("docker", "rm", "-f", "<BUILDER_NAME>"), "cleanup"),
    ("stack.down", ("./test.sh", "stack", "down"), "cleanup"),
    ("final.containers", ("docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=<PROJECT>"), "cleanup"),
    (
        "final.volumes",
        ("docker", "volume", "ls", "-q", "--filter", "label=com.docker.compose.project=<PROJECT>"),
        "cleanup",
    ),
    (
        "final.networks",
        ("docker", "network", "ls", "-q", "--filter", "label=com.docker.compose.project=<PROJECT>"),
        "cleanup",
    ),
    (
        "cleanup.stack_census",
        (
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=<PROJECT>",
            "--format",
            "{{json .}}",
        ),
        "cleanup",
    ),
    (
        "cleanup.stack_inspect",
        ("docker", "inspect", "--type", "container", "<ALL_ACTUAL_PROJECT_CIDS_FROM_CLEANUP_CENSUS>"),
        "cleanup",
    ),
    (
        "cleanup.stack_images",
        ("docker", "image", "inspect", "<UNIQUE_ACTUAL_IMAGE_IIDS_FROM_CLEANUP_INSPECT>"),
        "cleanup",
    ),
)

FRONTEND_LABELS = (
    "model",
    "discover",
    "pair",
    "images",
    "version",
    "entrypoint",
    *("before." + str(index) for index in range(1, 9)),
    *("after." + str(index) for index in range(1, 9)),
    "runner_final",
    "runner_absent",
    "runner_tree_before",
    "runner_tree_after",
)
NATIVE_LABELS = frozenset(label for label, _argv, _stage in ORIGINAL_COMMANDS) | frozenset(
    "frontend." + label for label in FRONTEND_LABELS
)


class Failure(Exception):
    """Closed failure label; no private native payload in an exception."""


class Control(BaseException):
    pass


def require(value, label):
    if not value:
        raise Failure(label)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def first_error(first, error):
    return error if first is None else first


def pairs(items):
    value = {}
    for key, item in items:
        require(key not in value, "duplicate-key")
        value[key] = item
    return value


def decode(raw, bound):
    require(type(raw) is bytes and len(raw) <= bound, "json-bound")
    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(Failure("json-constant")),
        )
    except (ValueError, UnicodeError) as error:
        raise Failure("json-syntax") from error


def closed(value, keys):
    require(type(value) is dict and set(value) == set(keys), "closed-keys")
    return value


def progress_write(write, fd, raw, end, clock):
    position = 0
    while position < len(raw):
        require(clock() < end, "write-deadline")
        count = write(fd, raw[position:])
        require(integer(count, 1, len(raw) - position), "write-progress")
        position += count


def close_handles(handles, close, first):
    """Never retry a numeric FD after an unknown close outcome."""
    unknown = False
    errors = []
    for handle in handles:
        if handle is None:
            continue
        try:
            close(handle)
        except BaseException as error:
            first = first_error(first, error)
            errors.append(error)
            unknown = True
    return first, unknown, errors


def close_native_handles(native, close_fd):
    """Consume retained handles before closure; unknown numeric closes are never retried."""
    first = native.close_error
    errors = []
    pipes, native.pipes = native.pipes, []
    for pipe in pipes:
        if pipe is None:
            continue
        try:
            pipe.close()
        except BaseException as error:
            first = first_error(first, error)
            errors.append(error)
    files, native.files = native.files, []
    first, unknown, fd_errors = close_handles(files, close_fd, first)
    errors.extend(fd_errors)
    selector, native.selector = native.selector, None
    if selector is not None:
        try:
            selector.close()
        except BaseException as error:
            first = first_error(first, error)
            errors.append(error)
    native.closed = True
    native.close_error = first
    native.unknown |= unknown or first is not None
    return first, errors


def native_terminal(native, timeout, group_probe, first):
    """Actual wait and group readback are independent of selector and capture completeness."""
    errors = []
    first = first_error(first, native.terminal_error)
    if not native.reap_attempted:
        native.reap_attempted = True
        try:
            native.code = native.process.wait(timeout=timeout)
        except BaseException as error:
            first = first_error(first, error)
            native.terminal_error = first_error(native.terminal_error, error)
            errors.append(error)
        try:
            group_probe(native.group, 0)
        except ProcessLookupError:
            native.group_absent = True
        except BaseException as error:
            first = first_error(first, error)
            native.terminal_error = first_error(native.terminal_error, error)
            errors.append(error)
    native.unknown |= first is not None
    return first, errors


def signal_native(native, number, kill_group, end, clock):
    if native.group is None or native.group_absent or number in native.signal_attempts:
        return None
    phase_admit(end, clock, "native-signal-deadline")
    native.signal_attempts.add(number)  # Retain the attempt before a fallible signal; never repeat ambiguity.
    try:
        kill_group(native.group, number)
    except ProcessLookupError:
        pass
    except BaseException as error:
        return error
    return None


def failed_native_cleanup(native, end, close_fd, group_probe, clock):
    first, errors = close_native_handles(native, close_fd)
    timeout = 0
    try:
        timeout = max(0, end - clock())
    except BaseException as error:
        first = first_error(first, error)
        errors.append(error)
    first, terminal_errors = native_terminal(native, timeout, group_probe, first)
    errors.extend(terminal_errors)
    try:
        phase_admit(end, clock, "native-terminal-deadline")
    except BaseException as error:
        first = first_error(first, error)
        errors.append(error)
    try:
        native_result(native, native.group_absent, first)
    except BaseException as error:
        if not any(error is previous for previous in errors):
            errors.append(error)
    return errors


def phase_admit(end, clock, kind):
    require(clock() < end, kind)


def frontend_matched(parent):
    return parent.front_complete and parent.front_before is not None and parent.front_before == parent.front_after


def capture_chunk(parent, native, index, raw, write, clock):
    require(type(raw) is bytes and index in (0, 1), "capture-chunk")
    require(len(native.output[index]) + len(raw) <= native.bound, "stream-bound")
    require(parent.capture_bytes + len(raw) <= CAPTURE_LIMIT, "capture-bound")
    parent.capture_bytes += len(raw)
    native.output[index].extend(raw)
    if len(native.files) == 2 and all(type(fd) is int for fd in native.files):
        progress_write(write, native.files[index], raw, native.end, clock)
    else:
        native.unknown = True


def native_result(native, group_absent, error):
    native.unknown |= error is not None
    native.settled = group_absent and all(native.eof) and error is None
    if error is not None:
        raise error
    require(native.settled, "native-group-or-fd")


def peer_identity(credentials, raw, root):
    require(type(credentials) is tuple and len(credentials) == 3, "peer-shape")
    require(credentials[1:] == (1000, 1000), "peer-runtime-identity")
    return process_tree(raw, root, credentials[0])


def owned_binding(current, owned):
    require(current is owned, "signal-handler-foreign")


def received_frame(raw, phase, seq):
    require(type(raw) is bytes and 0 < len(raw) <= 1024, "wire-bound")
    require(raw.endswith(b"\n") and raw.count(b"\n") == 1, "wire-line")
    keys = ("schema", "phase", "seq")
    if seq == 1:
        keys += ("container_pid", "host", "port", "source_role")
    value = closed(decode(raw, 1024), keys)
    require(value["schema"] == WIRE and value["phase"] == phase, "wire-kind")
    require(type(value["seq"]) is int and value["seq"] == seq, "wire-sequence")
    if seq == 1:
        require(integer(value["container_pid"], 1, 2**63 - 1), "wire-pid")
        require(
            type(value["host"]) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", value["host"]) is not None
            and integer(value["port"], 1, 65535)
            and value["source_role"] == "original_pooled_constructor",
            "wire-endpoint",
        )
    require(
        raw == (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"),
        "wire-canonical",
    )
    return value


def process_tree(raw, root, peer):
    require(0 < len(raw) <= 16384, "tree-bound")
    lines = raw.decode("ascii").splitlines()
    require(lines and lines[0].split() == ["PID", "PPID"] and 1 <= len(lines) - 1 <= 128, "tree-header")
    parents = {}
    for line in lines[1:]:
        fields = line.split()
        require(len(fields) == 2 and all(re.fullmatch(r"[0-9]+", part) for part in fields), "tree-row")
        pid, parent = map(int, fields)
        require(integer(pid, 1, 2**63 - 1) and integer(parent, 1, 2**63 - 1) and pid not in parents, "tree-id")
        parents[pid] = parent
    require(root in parents and peer in parents, "tree-membership")
    seen = set()
    pid = peer
    while pid != root:
        require(pid in parents and pid not in seen, "tree-ancestry")
        seen.add(pid)
        pid = parents[pid]
    return parents


def cid(value):
    require(type(value) is str and HEX64.fullmatch(value) is not None, "container-id")
    return value


def image_id(value):
    require(type(value) is str and IID.fullmatch(value) is not None, "image-id")
    return value


def object_set(raw, identities, field="Id", bound=1048576):
    values = decode(raw, bound)
    require(type(values) is list and len(values) == len(identities) <= 1024, "inspect-count")
    result = {}
    for value in values:
        require(type(value) is dict and type(value.get(field)) is str, "inspect-object")
        identity = value[field]
        require(identity in identities and identity not in result, "inspect-identity")
        result[identity] = value
    require(set(result) == set(identities), "inspect-set")
    return result


def census(raw):
    require(len(raw) <= 1048576 and (raw == b"" or raw.endswith(b"\n")), "census-bound")
    result = {}
    for line in raw.splitlines():
        require(0 < len(line) <= 65536, "census-row")
        value = decode(line, 65536)
        require(
            type(value) is dict
            and set(value)
            == {
                "Command",
                "CreatedAt",
                "ID",
                "Image",
                "Labels",
                "LocalVolumes",
                "Mounts",
                "Names",
                "Networks",
                "Ports",
                "RunningFor",
                "Size",
                "State",
                "Status",
            }
            and all(type(item) is str for item in value.values()),
            "census-object",
        )
        identity = cid(value.get("ID"))
        require(identity not in result or result[identity] == value, "census-duplicate")
        result[identity] = value
    require(len(result) <= 1024, "census-count")
    return result


def live_container(value):
    state = value.get("State")
    require(
        type(state) is dict
        and state.get("Running") is True
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
        and integer(state.get("Pid"), 1, 2**63 - 1)
        and type(state.get("StartedAt")) is str
        and state["StartedAt"] != "",
        "container-live",
    )


def stable_container(before, after):
    for field in ("Id", "Image", "Config", "HostConfig", "Mounts", "NetworkSettings", "RestartCount"):
        require(field in before and field in after and before[field] == after[field], "container-drift")
    for field in ("Pid", "StartedAt", "Running", "Paused", "Restarting", "Dead"):
        require(before["State"][field] == after["State"][field], "process-drift")


def environment_map(values):
    require(type(values) is list and all(type(value) is str for value in values), "env-type")
    result = {}
    for value in values:
        key, separator, item = value.partition("=")
        require(separator == "=" and key and key not in result, "env-key")
        result[key] = item
    return result


def binding_paths(model, actual, repo_root):
    expected = model.get("volumes", [])
    require(type(expected) is list and type(actual) is list and len(actual) == len(expected), "mount-count")
    destinations = {}
    for mount in actual:
        require(type(mount) is dict and type(mount.get("Destination")) is str, "mount-shape")
        destination = mount["Destination"]
        require(destination not in destinations, "mount-duplicate")
        destinations[destination] = mount
    for source in expected:
        require(type(source) is dict and source.get("target") in destinations, "mount-model")
        mount = destinations[source["target"]]
        require(mount.get("Type") == source.get("type"), "mount-type")
        require(type(mount.get("RW")) is bool and mount["RW"] is not source.get("read_only", False), "mount-readonly")
        if source["type"] == "bind":
            require(type(source.get("source")) is str, "mount-source")
            path = Path(source["source"])
            expected_path = str(path if path.is_absolute() else repo_root / path)
            require(mount.get("Source") == expected_path, "mount-bind")
        elif source["type"] == "volume":
            require(type(mount.get("Name")) is str and mount["Name"], "mount-volume")
            if not source.get("source"):
                require(HEX64.fullmatch(mount["Name"]) is not None, "mount-anonymous-name")
            if source.get("source"):
                require(
                    mount["Name"] == source["source"] or mount["Name"] == source.get("resolved_name"), "mount-named"
                )
        else:
            raise Failure("mount-unsupported")


def model_container(value, model, image, project, repo_root, expected_cmd=None):
    """Actual image defaults plus full source-defined fields, never tag-only ownership."""
    cid(value.get("Id"))
    require(value.get("Image") == image.get("Id"), "container-image")
    config, defaults = value.get("Config"), image.get("Config")
    host = value.get("HostConfig")
    require(type(config) is dict and type(defaults) is dict and type(host) is dict, "container-config")
    require(
        set(model)
        <= {
            "service_name",
            "image",
            "build",
            "environment",
            "volumes",
            "depends_on",
            "healthcheck",
            "user",
            "command",
            "entrypoint",
            "security_opt",
            "cap_add",
            "cap_drop",
            "working_dir",
            "profiles",
            "tmpfs",
            "networks",
            "hostname",
            "domainname",
            "read_only",
            "restart",
            "labels",
            "expose",
            "ports",
            "name",
        },
        "container-model-unsupported",
    )
    require(not model.get("ports") and not model.get("restart"), "container-model-external")
    labels = config.get("Labels")
    require(type(labels) is dict and labels.get("com.docker.compose.project") == project, "container-project")
    source_labels = model.get("labels", {})
    require(
        type(source_labels) is dict and all(labels.get(key) == item for key, item in source_labels.items()),
        "container-source-labels",
    )
    service = labels.get("com.docker.compose.service")
    require(type(service) is str and service == model.get("service_name"), "container-service")
    oneoff = labels.get("com.docker.compose.oneoff")
    require(oneoff in ("True", "False"), "container-oneoff")
    require(config.get("Image") == model.get("image"), "container-source-image")
    command = expected_cmd if expected_cmd is not None else model.get("command", defaults.get("Cmd"))
    require(type(command) is list and config.get("Cmd") == command, "container-command")
    require(config.get("Entrypoint") == model.get("entrypoint", defaults.get("Entrypoint")), "container-entrypoint")
    require(config.get("User") == model.get("user", defaults.get("User", "")), "container-user")
    require(config.get("WorkingDir") == model.get("working_dir", defaults.get("WorkingDir", "")), "container-cwd")
    source_env = model.get("environment", {})
    require(type(source_env) is dict, "model-env")
    expected_env = environment_map(defaults.get("Env") or [])
    for key, item in source_env.items():
        require(type(key) is str and type(item) is str, "model-env-type")
        expected_env[key] = item
    require(environment_map(config.get("Env")) == expected_env, "container-env")
    hostname = config.get("Hostname")
    require(type(hostname) is str and hostname == model.get("hostname", value["Id"][:12]), "container-hostname")
    require(config.get("Domainname", "") == model.get("domainname", ""), "container-domain")
    expected_health = model.get("healthcheck", defaults.get("Healthcheck"))
    require(config.get("Healthcheck") == expected_health, "container-healthcheck")
    require(host.get("Privileged") is False and host.get("UsernsMode") in ("", "host"), "container-privilege")
    require(host.get("PidMode") == "" and host.get("IpcMode") in ("private", ""), "container-namespace")
    require(host.get("NetworkMode") == project + "_default", "container-network-mode")
    require(host.get("UTSMode") == "" and not host.get("CgroupParent"), "container-host-namespace")
    for field in ("Dns", "DnsOptions", "DnsSearch", "Links", "VolumesFrom", "GroupAdd"):
        require(not host.get(field), "container-host-override")
    require(host.get("PublishAllPorts") is False and not host.get("ContainerIDFile"), "container-host-publication")
    require(host.get("ReadonlyRootfs") is model.get("read_only", False), "container-rootfs")
    security = model.get("security_opt", [])
    require((host.get("SecurityOpt") or []) == security, "container-security")
    require((host.get("CapAdd") or []) == model.get("cap_add", []), "container-cap-add")
    require((host.get("CapDrop") or []) == model.get("cap_drop", []), "container-cap-drop")
    require(
        not host.get("Devices") and not host.get("DeviceRequests") and not host.get("ExtraHosts"), "container-devices"
    )
    require(not host.get("PortBindings"), "container-port-bindings")
    ports = value.get("NetworkSettings", {}).get("Ports")
    require(type(ports) is dict and all(item is None for item in ports.values()), "container-ports")
    require(type(host.get("Binds")) in (list, type(None)), "container-binds")
    binding_paths(model, value.get("Mounts"), repo_root)
    expected_tmpfs = model.get("tmpfs", [])
    require(type(expected_tmpfs) is list, "tmpfs-model")
    actual_tmpfs = host.get("Tmpfs") or {}
    require(type(actual_tmpfs) is dict, "tmpfs-type")
    require(set(actual_tmpfs) == {part.split(":", 1)[0] for part in expected_tmpfs}, "tmpfs-set")
    for part in expected_tmpfs:
        path, separator, options = part.partition(":")
        require(actual_tmpfs[path] == (options if separator else ""), "tmpfs-options")
    networks = value.get("NetworkSettings", {}).get("Networks")
    require(type(networks) is dict and set(networks) == {project + "_default"}, "container-network")
    endpoint = networks[project + "_default"]
    require(type(endpoint) is dict and HEX64.fullmatch(endpoint.get("NetworkID", "")), "container-network-id")
    require(str(ipaddress.IPv4Address(endpoint.get("IPAddress"))) == endpoint["IPAddress"], "container-ip")
    aliases = endpoint.get("Aliases")
    if oneoff == "True":
        require(
            aliases is None or (type(aliases) is list and all(type(item) is str for item in aliases)),
            "container-oneoff-alias",
        )
    else:
        require(
            type(aliases) is list and service in aliases and all(type(item) is str for item in aliases),
            "container-alias",
        )
    return endpoint


class Native:
    def __init__(self, label, end, bound):
        self.label, self.end, self.bound = label, end, bound
        self.process = None
        self.pipes = []
        self.selector = None
        self.files = [None, None]
        self.output = [bytearray(), bytearray()]
        self.eof = [False, False]
        self.closed = False
        self.acquired = False
        self.unknown = False
        self.code = None
        self.group = None
        self.settled = False
        self.reap_attempted = False
        self.group_absent = False
        self.signal_attempts = set()
        self.close_error = None
        self.terminal_error = None


class Parent:
    def __init__(self):
        self.calls = set()
        self.natives = []
        self.phase = "setup"
        self.failure_snapshot = None
        self.failure_native = None
        self.failure_error = None
        self.prepr_stage = None
        self.build_guard = None
        self.diagnostic_failed = False
        self.capture_bytes = 0
        self.private = None
        self.private_identity = None
        self.private_files = []
        self.selector_failures = False
        self.cleanup_errors = []
        self.values = {}
        self.front_before = None
        self.front_after = None
        self.front_complete = False
        self.front_end = None
        self.listener = None
        self.peer = None
        self.socket_identity = None
        self.socket_path = None
        self.model = None
        self.runner = None
        self.pb = None
        self.peer_credentials = None
        self.wire_bytes = 0
        self.buffer = bytearray()
        self.builder_id = None
        self.builder_removed = False
        self.builder_snapshot = None
        self.parity_started = False
        self.stack_attempted = False
        self.initial_empty = False
        self.custody_before = None
        self.api_before = None
        self.snapshot = None
        self.absence = {"containers": "UNKNOWN", "volumes": "UNKNOWN", "networks": "UNKNOWN"}
        self.front_model = None
        self.target = None

    def acquire_private(self):
        self.private = Path(tempfile.mkdtemp(prefix="bifrost-claim-private-", dir="/tmp"))
        self.private_identity = self.private.stat()
        require(stat.S_IMODE(self.private_identity.st_mode) == 0o700, "private-mode")

    def launch(self, label, argv, end, bound=STREAM_LIMIT):
        require(label in NATIVE_LABELS and label not in self.calls and len(self.calls) < 71, "native-roster")
        require(time.monotonic() < end and 0 < bound <= STREAM_LIMIT, "native-deadline")
        self.calls.add(label)
        native = Native(label, end, bound)
        if label == "target":
            self.target = native
        self.natives.append(native)  # Custody before every later fallible operation.
        try:
            native.process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=os.environ.copy(),
            )
            native.pipes = [native.process.stdout, native.process.stderr]
            native.group = native.process.pid
            native.selector = selectors.DefaultSelector()
            for index, pipe in enumerate(native.pipes):
                require(pipe is not None, "native-pipe")
                os.set_blocking(pipe.fileno(), False)
                native.selector.register(pipe, selectors.EVENT_READ, index)
            for index in (0, 1):
                path = self.private / (str(len(self.natives)) + "-" + str(index))
                self.private_files.append((path, None))
                owned_index = len(self.private_files) - 1
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                native.files[index] = fd
                self.private_files[owned_index] = (path, os.fstat(fd))
        except BaseException as error:
            freeze_failure(self, error, native)
            native.unknown = True
            raise
        native.acquired = True
        return native

    def pump(self, native):
        try:
            require(native.process is not None and native.acquired, "native-acquisition")
            require(time.monotonic() < native.end, "native-expired")
            for key, _events in native.selector.select(0):
                index = key.data
                try:
                    raw = os.read(key.fileobj.fileno(), 16384)
                except BlockingIOError:
                    continue
                if not raw:
                    native.eof[index] = True
                    native.selector.unregister(key.fileobj)
                    continue
                capture_chunk(self, native, index, raw, os.write, time.monotonic)
            native.code = native.process.poll()
            if native.code is not None and all(native.eof):
                self.finish_native(native)
            return native.settled
        except BaseException as error:
            freeze_failure(self, error, native)
            raise

    def finish_native(self, native):
        if native.closed:
            return
        error, _close_errors = close_native_handles(native, os.close)
        error, _terminal_errors = native_terminal(native, 0, os.killpg, error)
        native_result(native, native.group_absent, error)
        require(time.monotonic() < native.end, "native-terminal-deadline")

    def poll_all(self):
        for native in self.natives:
            if not native.closed and native.process is not None:
                self.pump(native)

    def wait(self, native):
        try:
            while not native.settled:
                self.poll_all()
                if not native.settled:
                    time.sleep(min(0.01, max(0, native.end - time.monotonic())))
            require(native.code == 0 and not native.unknown, "native-exit-or-capture")
            return bytes(native.output[0])
        except BaseException as error:
            freeze_failure(self, error, native)
            raise

    def run(self, label, argv, end, bound=STREAM_LIMIT):
        return self.wait(self.launch(label, argv, end, bound))

    def original(self, label, phase_end, **substitutions):
        selected = [entry for entry in ORIGINAL_COMMANDS if entry[0] == label]
        require(len(selected) == 1, "original-command")
        _, argv, stage = selected[0]
        require(
            (stage == "work" and phase_end == WORK_END) or (stage == "cleanup" and phase_end == CLEANUP_END),
            "original-stage",
        )
        args = []
        for arg in argv:
            value = arg
            for key, item in self.values.items():
                if type(item) is str:
                    value = value.replace("<" + key + ">", item)
            for key, item in substitutions.items():
                if type(item) is list:
                    if arg == "<" + key + ">":
                        args.extend(item)
                        value = None
                        break
                    continue
                value = value.replace("<" + key + ">", item)
            if value is not None:
                require("<" not in value or label == "source.project", "original-substitution")
                args.append(value)
        long = {"prepr", "checks.build", "builder.start", "stack.up", "target", "stack.down"}
        end = phase_end if label in long else min(time.monotonic() + 2, phase_end)
        bound = 1048576 if label.startswith("cleanup.stack_") or label == "builder.terminal" else STREAM_LIMIT
        return self.run(label, args, end, bound)

    def front(self, label, argv, bound=1048576, shared=False):
        end = min(time.monotonic() + 2, WORK_END)
        if shared:
            if self.front_end is None:
                self.front_end = min(time.monotonic() + 40, WORK_END)
            end = min(end, self.front_end)
        return self.run("frontend." + label, argv, end, bound)

    def check_socket(self):
        if self.socket_path is None:
            return
        value = os.lstat(self.socket_path)
        require(
            self.socket_identity is not None
            and (value.st_dev, value.st_ino) == (self.socket_identity.st_dev, self.socket_identity.st_ino)
            and stat.S_ISSOCK(value.st_mode),
            "socket-custody",
        )

    def read_wire(self, phase, seq, end):
        while b"\n" not in self.buffer:
            require(time.monotonic() < end and len(self.buffer) < 1024, "wire-deadline")
            self.poll_all()
            require(self.target is None or not self.target.settled, "wire-runner-ended")
            try:
                raw = self.peer.recv(1025 - len(self.buffer))
            except BlockingIOError:
                time.sleep(0.005)
                continue
            require(raw, "wire-eof")
            self.buffer.extend(raw)
            self.wire_bytes += len(raw)
            require(self.wire_bytes <= 4096 and len(self.buffer) <= 1024, "wire-total")
        require(self.buffer.count(b"\n") == 1 and self.buffer.endswith(b"\n"), "wire-extra")
        value = received_frame(bytes(self.buffer), phase, seq)
        self.buffer.clear()
        return value

    def send_wire(self, phase, seq):
        self.check_socket()
        raw = json.dumps({"schema": WIRE, "phase": phase, "seq": seq}, separators=(",", ":")).encode() + b"\n"
        self.wire_bytes += len(raw)
        require(self.wire_bytes <= 4096, "wire-total")
        progress_write(lambda _fd, data: self.peer.send(data), None, raw, WORK_END, time.monotonic)

    def listen(self, evidence):
        self.socket_path = evidence / "frontend.sock"
        require(len(os.fsencode(self.socket_path)) < 108 and not os.path.lexists(self.socket_path), "socket-path")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))
        self.socket_identity = os.lstat(self.socket_path)
        os.chmod(self.socket_path, 0o666, follow_symlinks=False)
        self.check_socket()
        self.listener.listen(1)
        self.listener.setblocking(False)

    def accept(self):
        end = WORK_END
        while self.peer is None:
            require(time.monotonic() < end, "accept-deadline")
            self.poll_all()
            require(self.target is not None and not self.target.settled, "accept-runner-ended")
            try:
                self.peer, _address = self.listener.accept()
            except BlockingIOError:
                time.sleep(0.005)
                continue
            self.peer_credentials = struct.unpack("3i", self.peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            self.peer.setblocking(False)
            self.check_socket()
        require(self.peer_credentials[1:] == (1000, 1000), "peer-runtime-identity")

    def close_ipc(self):
        first = None
        for handle in (self.peer, self.listener):
            if handle is not None:
                try:
                    handle.close()
                except BaseException as error:
                    first = first_error(first, error)
        if self.socket_identity is not None:
            try:
                self.check_socket()
                os.unlink(self.socket_path)
            except BaseException as error:
                first = first_error(first, error)
        if first is not None:
            raise first

    def settle_failure(self):
        # Signals target only the retained start_new_session groups. Docker ambiguity remains RED.
        for native in self.natives:
            if native.settled or native.process is None:
                continue
            native.end = CLEANUP_END
            try:
                error = signal_native(native, signal.SIGTERM, os.killpg, CLEANUP_END, time.monotonic)
                if error is not None:
                    self.cleanup_errors.append(error)
            except BaseException as error:
                self.cleanup_errors.append(error)
        term_end = min(time.monotonic() + 1, CLEANUP_END)
        for native in self.natives:
            if native.settled or native.process is None:
                continue
            try:
                while time.monotonic() < term_end and native.acquired and not native.closed:
                    self.pump(native)
                    if not native.settled:
                        time.sleep(0.005)
            except BaseException as error:
                self.cleanup_errors.append(error)
            if not native.settled:
                try:
                    error = signal_native(native, signal.SIGKILL, os.killpg, CLEANUP_END, time.monotonic)
                    if error is not None:
                        self.cleanup_errors.append(error)
                    while time.monotonic() < CLEANUP_END and native.acquired and not native.closed:
                        self.pump(native)
                        if not native.settled:
                            time.sleep(0.005)
                except BaseException as error:
                    self.cleanup_errors.append(error)
            if not native.settled:
                try:
                    self.cleanup_errors.extend(
                        failed_native_cleanup(native, CLEANUP_END, os.close, os.killpg, time.monotonic)
                    )
                except BaseException as error:
                    self.cleanup_errors.append(error)
                self.cleanup_errors.append(Failure("native-unsettled"))

    def close_failed_natives(self):
        for native in self.natives:
            if native.settled or native.process is None:
                continue
            # A control may have interrupted settlement before either termination attempt.
            for number in (signal.SIGTERM, signal.SIGKILL):
                try:
                    error = signal_native(native, number, os.killpg, CLEANUP_END, time.monotonic)
                    if error is not None:
                        self.cleanup_errors.append(error)
                except BaseException as error:
                    self.cleanup_errors.append(error)
            try:
                self.cleanup_errors.extend(
                    failed_native_cleanup(native, CLEANUP_END, os.close, os.killpg, time.monotonic)
                )
            except BaseException as error:
                self.cleanup_errors.append(error)

    def dispose_private(self):
        require(all(native.settled for native in self.natives), "private-live-capture")
        require(not any(native.unknown for native in self.natives), "private-fd-unknown")
        for path, identity in self.private_files:
            require(identity is not None, "private-acquisition-unknown")
            current = os.lstat(path)
            require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), "private-file-custody")
            require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1, "private-file-type")
            os.unlink(path)
        current = os.lstat(self.private)
        require(
            (current.st_dev, current.st_ino) == (self.private_identity.st_dev, self.private_identity.st_ino),
            "private-dir-custody",
        )
        os.rmdir(self.private)


FRONTEND_PROCESS = '\nset -eu\nrecord_file() {\n  tag=$1; path=$2; bound=$3\n  length=$(dd if="$path" bs="$((bound+1))" count=1 2>/dev/null | wc -c)\n  case "$length" in \'\'|*[!0-9]*) exit 1;; esac\n  [ "$length" -le "$bound" ] || exit 1\n  printf \'bifrost-proc/v1 %s %s\\n\' "$tag" "$length"\n  dd if="$path" bs="$((bound+1))" count=1 2>/dev/null\n  printf \'\\n\'\n}\nrecord_value() {\n  tag=$1; value=$2\n  [ "${#value}" -le 4096 ] || exit 1\n  printf \'bifrost-proc/v1 %s %s\\n%s\\n\' "$tag" "${#value}" "$value"\n}\nrecord_file stat /proc/1/stat 4096\nrecord_file cmdline /proc/1/cmdline 4096\nexe=$(readlink /proc/1/exe)\ncwd=$(readlink /proc/1/cwd)\nnetns=$(readlink /proc/1/ns/net)\n[ "$exe" = /usr/bin/pgbouncer ] || exit 1\nrecord_value exe "$exe"\nrecord_value cwd "$cwd"\nrecord_value netns "$netns"\nsize=$(stat -Lc \'%s\' /proc/1/exe)\ncase "$size" in \'\'|*[!0-9]*) exit 1;; esac\n[ "$size" -gt 0 ] && [ "$size" -le 67108864 ] || exit 1\nrecord_value exe_bytes "$size"\nhash=$(sha256sum /proc/1/exe)\nhash=${hash%% *}\nrecord_value exe_sha256 "$hash"\nset -- /proc/1/fd/*\n[ "$#" -le 2048 ] || exit 1\nsockets=\'\'\ncount=0\nfor path do\n  target=$(readlink "$path")\n  case "$target" in\n    socket:\\[*\\])\n      count=$((count+1)); [ "$count" -le 128 ] || exit 1\n      sockets="${sockets}${target}\n";;\n  esac\ndone\nrecord_value sockets "$sockets"\nprintf \'bifrost-proc-end/v1\\n\'\n'  # noqa: E501 - exact reviewed static literal.

FRONTEND_TCP = '\nset -eu\nfor table in tcp tcp6; do\n  file=/proc/1/net/$table\n  length=$(dd if="$file" bs=65537 count=1 2>/dev/null | wc -c)\n  case "$length" in \'\'|*[!0-9]*) exit 1;; esac\n  [ "$length" -le 65536 ] || exit 1\n  printf \'bifrost-tcp/v1 %s %s\\n\' "$table" "$length"\n  dd if="$file" bs=65537 count=1 2>/dev/null\n  printf \'\\n\'\ndone\nprintf \'bifrost-tcp-end/v1\\n\'\n'  # noqa: E501 - exact reviewed static literal.

FRONTEND_CONFIG = '\nset -eu\nopened=0\ntrap \'first=$?; if [ "$opened" -eq 1 ]; then if exec 3<&-; then closed=0; else closed=$?; fi; if [ "$first" -eq 0 ] && [ "$closed" -ne 0 ]; then first=$closed; fi; fi; exit "$first"\' EXIT\npath=$1\n[ "$path" = /etc/pgbouncer/pgbouncer.ini ] || exit 1\nfor ancestor in /etc /etc/pgbouncer; do\n  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1\ndone\n[ -f "$path" ] && [ ! -L "$path" ] || exit 1\nformat=$(printf \'%%d\\t%%i\\t%%f\\t%%h\\t%%s\\t%%y\\t%%z\')\nexec 3< "$path"\nopened=1\nbefore=$(stat -Lc "$format" /proc/self/fd/3)\nactual=$(stat -Lc "$format" "$path")\n[ "$before" = "$actual" ] || exit 1\nfor ancestor in /etc /etc/pgbouncer; do\n  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1\ndone\nsize=$(stat -Lc \'%s\' /proc/self/fd/3)\nlinks=$(stat -Lc \'%h\' /proc/self/fd/3)\ncase "$size" in \'\'|*[!0-9]*) exit 1;; esac\n[ "$size" -gt 0 ] && [ "$size" -le 65536 ] && [ "$links" = 1 ] || exit 1\nprintf \'bifrost-config-read/v1\\nbefore %s\\n%s\\npayload %s\\n\' "${#before}" "$before" "$size"\ndd bs=65537 count=1 <&3 2>/dev/null\nprintf \'\\n\'\nafter=$(stat -Lc "$format" /proc/self/fd/3)\nactual=$(stat -Lc "$format" "$path")\n[ "$before" = "$after" ] && [ "$before" = "$actual" ] && [ ! -L "$path" ] || exit 1\nfor ancestor in /etc /etc/pgbouncer; do\n  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1\ndone\nexec 3<&-\nopened=0\nprintf \'after %s\\n%s\\nend\\n\' "${#after}" "$after"\n'  # noqa: E501 - exact reviewed static literal.

FRONTEND_ENTRYPOINT = '\nset -eu\n[ -f /entrypoint.sh ] && [ ! -L /entrypoint.sh ] || exit 1\nsize=$(stat -c \'%s\' /entrypoint.sh)\ncase "$size" in \'\'|*[!0-9]*) exit 1;; esac\n[ "$size" -gt 0 ] && [ "$size" -le 16384 ] || exit 1\nhash=$(sha256sum /entrypoint.sh)\nhash=${hash%% *}\nprintf \'%s %s\\n\' "$size" "$hash"\n'  # noqa: E501 - exact reviewed static literal.

FRONTEND_DNS = "\nimport json\nimport socket\nimport sys\nhost = sys.argv[1]\nif not host.isascii() or not 1 <= len(host) <= 253:\n    raise SystemExit(1)\naddresses = sorted({row[4][0] for row in socket.getaddrinfo(host,None,socket.AF_INET,socket.SOCK_STREAM)})\nif not 1 <= len(addresses) <= 8:\n    raise SystemExit(1)\nsys.stdout.write(json.dumps(addresses,ensure_ascii=True,separators=(',',':'))+'\\n')\n"  # noqa: E501 - exact reviewed static literal.


def private_frames(raw, prefix, tags, end_marker, limit):
    require(len(raw) <= limit and raw.endswith(end_marker), "private-frame-bound")
    index = 0
    result = {}
    for tag in tags:
        stop = raw.find(b"\n", index)
        require(stop >= index, "private-frame-header")
        match = re.fullmatch(re.escape(prefix + b" " + tag.encode()) + rb" ([0-9]+)", raw[index:stop])
        require(match is not None, "private-frame-tag")
        length = int(match[1])
        begin = stop + 1
        finish = begin + length
        require(length <= limit and raw[finish : finish + 1] == b"\n", "private-frame-length")
        result[tag] = raw[begin:finish]
        index = finish + 1
    require(raw[index:] == end_marker, "private-frame-extra")
    return result


def parse_process(raw, docker_pid):
    rows = private_frames(
        raw,
        b"bifrost-proc/v1",
        ("stat", "cmdline", "exe", "cwd", "netns", "exe_bytes", "exe_sha256", "sockets"),
        b"bifrost-proc-end/v1\n",
        256 * 1024,
    )
    require(len(rows["stat"]) <= 4096 and len(rows["cmdline"]) <= 4096, "proc-bounds")
    fields = re.fullmatch(rb"1 \(([^()]*)\) ([A-Za-z]) (.*)\n", rows["stat"])
    require(fields is not None, "proc-stat")
    tail = fields[3].decode("ascii").split()
    require(len(tail) >= 19 and re.fullmatch(r"[0-9]+", tail[18]) is not None, "proc-start")
    start = int(tail[18])
    argv = rows["cmdline"].split(b"\0")
    require(
        argv == [b"/usr/bin/pgbouncer", b"/etc/pgbouncer/pgbouncer.ini", b""] and rows["exe"] == b"/usr/bin/pgbouncer",
        "proc-exec-profile",
    )
    require(rows["cwd"].startswith(b"/") and len(rows["cwd"]) <= 256, "proc-cwd")
    namespace = re.fullmatch(rb"net:\[([0-9]+)\]", rows["netns"])
    require(namespace is not None, "proc-netns")
    inode = int(namespace[1])
    require(re.fullmatch(rb"[0-9]+", rows["exe_bytes"]) is not None, "proc-exe-size")
    size = int(rows["exe_bytes"])
    digest = rows["exe_sha256"].decode("ascii")
    require(
        integer(start, 1, 2**63 - 1)
        and integer(inode, 1, 2**63 - 1)
        and integer(size, 1, 64 * 1024 * 1024)
        and HEX64.fullmatch(digest) is not None,
        "proc-types",
    )
    sockets = []
    for line in rows["sockets"].splitlines():
        match = re.fullmatch(rb"socket:\[([0-9]+)\]", line)
        require(match is not None, "proc-socket")
        sockets.append(int(match[1]))
    require(len(sockets) <= 128 and len(sockets) == len(set(sockets)), "proc-socket-count")
    return {
        "docker_state_pid": docker_pid,
        "container_pid": 1,
        "start_ticks": start,
        "netns_inode": inode,
        "exe_sha256": digest,
        "exe_bytes": size,
    }, sockets


def strict_frontend_config(raw, endpoint, port, username):
    require(0 < len(raw) <= 65536 and b"\0" not in raw, "frontend-config-bound")
    text = raw.decode("utf-8", "strict")
    require(
        "%" not in text
        and not any(ord(char) < 32 and char not in "\t\n\r" for char in text)
        and "\r" not in text.replace("\r\n", ""),
        "frontend-config-controls",
    )
    sections = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        require(not line[0].isspace(), "frontend-config-continuation")
        if line.startswith("["):
            require(line in {"[databases]", "[pgbouncer]"} and line not in sections, "frontend-config-section")
            sections.append(line)
        else:
            key, separator, _value = line.partition("=")
            require(
                separator == "="
                and key.strip(" ").isascii()
                and re.fullmatch(r"[A-Za-z0-9_*]+", key.strip(" ")) is not None
                and key.strip(" ").lower() not in {"include", "%include"},
                "frontend-config-assignment",
            )
    require(set(sections) == {"[databases]", "[pgbouncer]"}, "frontend-config-sections")
    parser = configparser.ConfigParser(
        interpolation=None, strict=True, empty_lines_in_values=False, inline_comment_prefixes=None
    )
    parser.read_string(text)
    require(not parser.defaults(), "frontend-config-defaults")
    settings = parser["pgbouncer"]
    require(
        settings.get("client_tls_sslmode", "disable") == "disable"
        and settings.get("auth_type") == "plain"
        and settings.get("listen_addr") in {"0.0.0.0", endpoint}
        and settings.get("listen_port") == str(port),
        "frontend-config-effective",
    )
    for name in ("admin_users", "stats_users"):
        value = settings.get(name)
        if value is None:
            continue
        names = [item.strip(" ") for item in value.split(",")]
        require(
            names
            and len(names) == len(set(names))
            and all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item) is not None for item in names)
            and username not in names,
            "frontend-console-exclusion",
        )
    return parser


def parse_config_readback(raw):
    require(raw.startswith(b"bifrost-config-read/v1\n") and len(raw) <= 131072, "config-readback-bound")
    cursor = len(b"bifrost-config-read/v1\n")
    records = {}
    for tag in ("before", "payload", "after"):
        stop = raw.find(b"\n", cursor)
        require(stop >= cursor, "config-readback-header")
        match = re.fullmatch(tag.encode() + rb" ([0-9]+)", raw[cursor:stop])
        require(match is not None, "config-readback-tag")
        size = int(match[1])
        require(0 < size <= (65536 if tag == "payload" else 1024), "config-readback-size")
        cursor = stop + 1
        records[tag] = raw[cursor : cursor + size]
        cursor += size
        require(raw[cursor : cursor + 1] == b"\n", "config-readback-delimiter")
        cursor += 1
    require(raw[cursor:] == b"end\n" and records["before"] == records["after"], "config-readback-stability")
    fields = records["before"].decode("ascii").split("\t")
    require(
        len(fields) == 7
        and all(re.fullmatch(r"[0-9]+", fields[index]) is not None for index in (0, 1, 3, 4))
        and re.fullmatch(r"[0-9a-f]+", fields[2]) is not None,
        "config-stat-fields",
    )
    mode = int(fields[2], 16)
    require(
        stat.S_ISREG(mode)
        and int(fields[3]) == 1
        and int(fields[4]) == len(records["payload"])
        and all(
            re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9} [+-][0-9]{4}", fields[index])
            is not None
            for index in (5, 6)
        ),
        "config-stat-profile",
    )
    return records["payload"], tuple(fields)


def parse_listener(raw, sockets, endpoint, port):
    records = private_frames(raw, b"bifrost-tcp/v1", ("tcp", "tcp6"), b"bifrost-tcp-end/v1\n", 131072)
    matches = []
    for family, body in records.items():
        require(len(body) <= 65536, "tcp-table-bound")
        lines = body.decode("ascii").splitlines()
        require(lines and len(lines) <= 129, "tcp-table-count")
        header = lines[0].split()
        require(header[:4] == ["sl", "local_address", "rem_address", "st"] and "inode" in header, "tcp-header")
        for line in lines[1:]:
            fields = line.split()
            require(
                len(fields) >= 10
                and re.fullmatch(r"[0-9]+:", fields[0]) is not None
                and re.fullmatch(r"[0-9A-F]{2}", fields[3]) is not None
                and re.fullmatch(r"[0-9]+", fields[9]) is not None,
                "tcp-row",
            )
            width = 8 if family == "tcp" else 32
            local = re.fullmatch(r"([0-9A-F]{" + str(width) + r"}):([0-9A-F]{4})", fields[1])
            remote = re.fullmatch(r"[0-9A-F]{" + str(width) + r"}:[0-9A-F]{4}", fields[2])
            require(local is not None and remote is not None, "tcp-address")
            if fields[3] != "0A" or int(local[2], 16) != port:
                continue
            require(family == "tcp", "tcp-relevant-ipv6")
            address = ".".join(str(value) for value in bytes.fromhex(local[1])[::-1])
            require(address in {"0.0.0.0", endpoint}, "tcp-listen-address")
            inode = int(fields[9])
            require(integer(inode, 1, 2**63 - 1) and inode in sockets, "tcp-listen-owner")
            matches.append(
                {
                    "socket_inode": inode,
                    "owner_container_pid": 1,
                    "family": "ipv4",
                    "address_kind": "wildcard" if address == "0.0.0.0" else "owned_endpoint",
                    "port": port,
                }
            )
    require(len(matches) == 1, "tcp-listener-multiplicity")
    return matches[0]


def duration(value):
    require(type(value) is str, "duration-type")
    match = re.fullmatch(r"([0-9]+)(ns|us|ms|s|m|h)", value)
    require(match is not None, "duration-profile")
    multiplier = {"ns": 1, "us": 1000, "ms": 1000000, "s": 1000000000, "m": 60000000000, "h": 3600000000000}[match[2]]
    result = int(match[1]) * multiplier
    require(integer(result, 0, 2**63 - 1), "duration-range")
    return result


def healthcheck(value):
    if value is None:
        return None
    require(
        type(value) is dict
        and set(value) <= {"test", "interval", "timeout", "retries", "start_period", "start_interval", "disable"},
        "health-model",
    )
    if value.get("disable") is True:
        require(set(value) == {"disable"}, "health-disable")
        return {"Test": ["NONE"]}
    result = {}
    fields = {
        "test": "Test",
        "interval": "Interval",
        "timeout": "Timeout",
        "retries": "Retries",
        "start_period": "StartPeriod",
        "start_interval": "StartInterval",
    }
    for key, item in value.items():
        require(key in fields, "health-key")
        result[fields[key]] = (
            duration(item) if key in {"interval", "timeout", "start_period", "start_interval"} else item
        )
    return result


def source_model(parent, raw):
    value = decode(raw, 1048576)
    require(type(value) is dict and type(value.get("services")) is dict, "compose-model")
    require(value.get("name") == parent.values["PROJECT"], "compose-project")
    for name, model in value["services"].items():
        require(type(name) is str and type(model) is dict, "compose-service")
        model["service_name"] = name
        if "healthcheck" in model:
            model["healthcheck"] = healthcheck(model["healthcheck"])
        for mount in model.get("volumes", []):
            if mount.get("type") == "volume" and mount.get("source"):
                declared = value.get("volumes", {}).get(mount["source"])
                require(type(declared) is dict and type(declared.get("name")) is str, "compose-volume-name")
                mount["resolved_name"] = declared["name"]
    parent.model = value
    return value


def select_pair(parent, raw):
    rows = census(raw)
    selections = {}
    for identity, row in rows.items():
        labels = row.get("Labels")
        require(type(labels) is str, "discovery-labels")
        parsed = {}
        for item in labels.split(","):
            key, separator, value = item.partition("=")
            require(separator == "=" and key not in parsed, "discovery-label")
            parsed[key] = value
        if parsed.get("com.docker.compose.service") in {"pgbouncer", "test-runner"}:
            service = parsed["com.docker.compose.service"]
            require(service not in selections, "discovery-multiplicity")
            require(parsed.get("com.docker.compose.project") == parent.values["PROJECT"], "discovery-project")
            selections[service] = identity
    require(set(selections) == {"pgbouncer", "test-runner"}, "discovery-pair")
    return selections


def pair_admission(parent, selected, raw, images):
    values = object_set(raw, list(selected.values()))
    image_values = object_set(images, sorted({image_id(item["Image"]) for item in values.values()}))
    parent.pb = values[selected["pgbouncer"]]
    parent.runner = values[selected["test-runner"]]
    for service, value in (("pgbouncer", parent.pb), ("test-runner", parent.runner)):
        live_container(value)
        model = parent.model["services"].get(service)
        require(type(model) is dict, "pair-model")
        command = None
        if service == "test-runner":
            command = [
                "pytest",
                "tests/parity/test_workflow_domain.py",
                "-v",
                "--durations=25",
                "--junitxml=/tmp/bifrost/test-results.xml",
            ]
        endpoint = model_container(
            value, model, image_values[value["Image"]], parent.values["PROJECT"], Path(parent.values["ROOT"]), command
        )
        if service == "pgbouncer":
            parent.values["NETWORK_ID"] = endpoint["NetworkID"]
            parent.values["ENDPOINT"] = endpoint["IPAddress"]
        else:
            require(value["Image"] == parent.api_before, "runner-image-custody")
    runner_endpoint = parent.runner["NetworkSettings"]["Networks"][parent.values["PROJECT"] + "_default"]
    require(runner_endpoint["NetworkID"] == parent.values["NETWORK_ID"], "pair-network")
    provider = environment_map(parent.runner["Config"]["Env"])
    require(provider.get("BIFROST_ENVIRONMENT") == "testing", "runner-testing")
    require(
        provider.get("BIFROST_DATABASE_URL") == "postgresql+asyncpg://bifrost:bifrost_test@pgbouncer:5432/bifrost_test",
        "runner-provider",
    )
    require(
        not any(
            provider.get(key)
            for key in (
                "GITHUB_TEST_PAT",
                "ANTHROPIC_API_TEST_KEY",
                "OPENAPI_API_TEST_KEY",
                "GENERIC_AI_TEST_KEY",
                "EMBEDDINGS_AI_TEST_KEY",
            )
        ),
        "runner-external-credentials",
    )


def network_admission(parent, raw):
    network_id = parent.values["NETWORK_ID"]
    value = object_set(raw, [network_id])[network_id]
    require(
        value.get("Name") == parent.values["PROJECT"] + "_default"
        and value.get("Driver") == "bridge"
        and value.get("Scope") == "local"
        and value.get("Internal") is False
        and value.get("Labels", {}).get("com.docker.compose.project") == parent.values["PROJECT"]
        and value.get("Labels", {}).get("com.docker.compose.network") == "default",
        "network-source",
    )
    for container in (parent.pb, parent.runner):
        endpoint = value.get("Containers", {}).get(container["Id"])
        require(type(endpoint) is dict, "network-member")
        expected = container["NetworkSettings"]["Networks"][parent.values["PROJECT"] + "_default"]["IPAddress"]
        require(str(ipaddress.IPv4Interface(endpoint.get("IPv4Address")).ip) == expected, "network-address")
    return value


def frontend_profile(parent):
    pb = parent.pb["Id"]
    version = parent.front("version", ["docker", "exec", pb, "/proc/1/exe", "--version"], 4096, True)
    require(version == b"PgBouncer 1.26.0\n", "frontend-version")
    source = parent.front("entrypoint", ["docker", "exec", pb, "/bin/sh", "-c", FRONTEND_ENTRYPOINT], 16384, True)
    require(source == b"8449 9d9d23849f0180d7fb25263dca3870955c39e0fcf0211529b10238f280143333\n", "frontend-source")


def bracket(parent, when, ready):
    pb, runner = parent.pb["Id"], parent.runner["Id"]
    prefix = when + "."
    before = object_set(
        parent.front(prefix + "1", ["docker", "inspect", "--type", "container", pb], 262144, True), [pb]
    )[pb]
    live_container(before)
    stable_container(parent.pb, before)
    pid = before["State"]["Pid"]
    top = parent.front(prefix + "2", ["docker", "top", pb, "-eo", "pid,ppid"], 16384, True)
    process_tree(top, pid, pid)
    process, sockets = parse_process(
        parent.front(prefix + "3", ["docker", "exec", pb, "/bin/sh", "-c", FRONTEND_PROCESS], 262144, True), pid
    )
    listener = parse_listener(
        parent.front(prefix + "4", ["docker", "exec", pb, "/bin/sh", "-c", FRONTEND_TCP], 131072, True),
        sockets,
        parent.values["ENDPOINT"],
        ready["port"],
    )
    configuration, identity = parse_config_readback(
        parent.front(
            prefix + "5",
            ["docker", "exec", pb, "/bin/sh", "-c", FRONTEND_CONFIG, "observer", "/etc/pgbouncer/pgbouncer.ini"],
            131072,
            True,
        )
    )
    strict_frontend_config(configuration, parent.values["ENDPOINT"], ready["port"], "bifrost")
    network_admission(
        parent, parent.front(prefix + "6", ["docker", "network", "inspect", parent.values["NETWORK_ID"]], 262144, True)
    )
    addresses = decode(
        parent.front(prefix + "7", ["docker", "exec", runner, "python", "-c", FRONTEND_DNS, ready["host"]], 4096, True),
        4096,
    )
    require(addresses == [parent.values["ENDPOINT"]], "frontend-dns")
    after = object_set(
        parent.front(prefix + "8", ["docker", "inspect", "--type", "container", pb], 262144, True), [pb]
    )[pb]
    live_container(after)
    stable_container(before, after)
    return {
        "observed": {
            "cid": pb,
            "image_id": before["Image"],
            "version": "1.26.0",
            "network_id": parent.values["NETWORK_ID"],
            "evidence": "Docker_API_same_CID",
            "process": process,
            "listener": listener,
        },
        "configuration": configuration,
        "configuration_identity": identity,
        "started_at": before["State"]["StartedAt"],
    }


def target_gate(parent):
    selected = select_pair(
        parent,
        parent.front(
            "discover",
            [
                "docker",
                "ps",
                "-a",
                "--no-trunc",
                "--filter",
                "label=com.docker.compose.project=" + parent.values["PROJECT"],
                "--format",
                "{{json .}}",
            ],
        ),
    )
    raw = parent.front(
        "pair", ["docker", "inspect", "--type", "container", selected["pgbouncer"], selected["test-runner"]]
    )
    objects = object_set(raw, list(selected.values()))
    identities = sorted({image_id(value["Image"]) for value in objects.values()})
    images = parent.front("images", ["docker", "image", "inspect", *identities])
    pair_admission(parent, selected, raw, images)
    ready = parent.read_wire("before", 1, WORK_END)
    require(ready["host"] == "pgbouncer" and ready["port"] == 5432, "constructor-endpoint")
    peer_identity(
        parent.peer_credentials,
        parent.front("runner_tree_before", ["docker", "top", parent.runner["Id"], "-eo", "pid,ppid"], 16384, True),
        parent.runner["State"]["Pid"],
    )
    frontend_profile(parent)
    parent.front_before = bracket(parent, "before", ready)
    parent.send_wire("before", 2)
    parent.read_wire("after", 3, WORK_END)
    parent.front_after = bracket(parent, "after", ready)
    require(parent.front_before == parent.front_after, "frontend-drift")
    finish_frontend(parent)


def finish_frontend(parent):
    runner = parent.runner["Id"]
    current = object_set(
        parent.front("runner_final", ["docker", "inspect", "--type", "container", runner], 262144, True), [runner]
    )[runner]
    live_container(current)
    stable_container(parent.runner, current)
    peer_identity(
        parent.peer_credentials,
        parent.front("runner_tree_after", ["docker", "top", runner, "-eo", "pid,ppid"], 16384, True),
        current["State"]["Pid"],
    )
    parent.send_wire("after", 4)
    parent.front_complete = True


def builder_guard(parent, value, terminal):
    require(
        value.get("Id") == parent.builder_id and value.get("Image") == parent.values.get("CHECKS_IID"),
        "builder-identity",
    )
    config = value.get("Config")
    require(
        type(config) is dict and config.get("Labels", {}).get("com.docker.compose.project") == parent.values["PROJECT"],
        "builder-label",
    )
    command = next(argv for label, argv, _stage in ORIGINAL_COMMANDS if label == "builder.create")
    require(config.get("Cmd") == list(command[-4:]), "builder-command")
    require(config.get("Image") == parent.values["CHECKS_IMAGE"], "builder-source-image")
    if parent.builder_snapshot is not None:
        for field in ("Id", "Image", "Config", "HostConfig", "Mounts"):
            require(value.get(field) == parent.builder_snapshot.get(field), "builder-current-custody")
    host = value.get("HostConfig")
    require(
        type(host) is dict and host.get("Privileged") is False and host.get("NetworkMode") == "default",
        "builder-security",
    )
    require(not value.get("Mounts") and not host.get("Binds") and not host.get("Devices"), "builder-mounts")
    state = value.get("State")
    require(
        type(state) is dict
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False,
        "builder-state",
    )
    if terminal:
        require(
            state.get("Running") is False and type(state.get("ExitCode")) is int and state["ExitCode"] == 0,
            "builder-terminal",
        )


def stack_cleanup_guard(parent):
    require(all(native.settled for native in parent.natives), "cleanup-live-child")
    raw = parent.original("cleanup.stack_census", CLEANUP_END)
    found = census(raw)
    if not found:
        return
    require(parent.model is not None and parent.api_before is not None, "cleanup-model-unknown")
    identities = sorted(found)
    objects = object_set(
        parent.original("cleanup.stack_inspect", CLEANUP_END, ALL_ACTUAL_PROJECT_CIDS_FROM_CLEANUP_CENSUS=identities),
        identities,
    )
    images = sorted({image_id(value.get("Image")) for value in objects.values()})
    profiles = object_set(
        parent.original("cleanup.stack_images", CLEANUP_END, UNIQUE_ACTUAL_IMAGE_IIDS_FROM_CLEANUP_INSPECT=images),
        images,
    )
    for identity, value in objects.items():
        if identity == parent.builder_id:
            builder_guard(parent, value, False)
            continue
        labels = value.get("Config", {}).get("Labels")
        require(type(labels) is dict, "cleanup-label")
        service = labels.get("com.docker.compose.service")
        model = parent.model["services"].get(service)
        require(type(model) is dict, "cleanup-service")
        oneoff = labels.get("com.docker.compose.oneoff")
        command = None
        if oneoff == "True":
            if service == "test-runner":
                # Interrupted opaque pre-PR test invocations have no selected command witness.
                require(parent.runner is not None and identity == parent.runner["Id"], "cleanup-oneoff-unknown")
                command = parent.runner["Config"]["Cmd"]
            else:
                raise Failure("cleanup-oneoff-profile-unknown")
        model_container(
            value, model, profiles[value["Image"]], parent.values["PROJECT"], Path(parent.values["ROOT"]), command
        )
        if model.get("image") == "bifrost-test-api-dev:latest":
            require(value["Image"] == parent.api_before, "cleanup-api-image")
        if service == "pgbouncer":
            require(
                parent.pb is not None and identity == parent.pb["Id"] and value["Image"] == parent.pb["Image"],
                "cleanup-pgbouncer-identity",
            )
            stable_container(parent.pb, value)
        state = value.get("State")
        require(
            type(state) is dict
            and state.get("Dead") is False
            and state.get("Paused") is False
            and state.get("Restarting") is False,
            "cleanup-state",
        )
        if service == "init":
            require(
                state.get("Running") is False and type(state.get("ExitCode")) is int and state["ExitCode"] == 0,
                "cleanup-init",
            )
        else:
            live_container(value)
    parent.snapshot = objects


def cleanup_attempt(parent, operation):
    try:
        operation()
    except BaseException as error:
        freeze_failure(parent, error)
        parent.cleanup_errors.append(error)


def retained_file(path, bound, end):
    fd = None
    record = {"fd": None, "path": path, "identity": None, "closed": True, "unknown": False}
    FILE_CUSTODY.append(record)
    first = None
    result = None
    try:
        phase_admit(end, time.monotonic, "file-deadline")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        record["fd"] = fd
        record["closed"] = False
        before = os.fstat(fd)
        record["identity"] = (before.st_dev, before.st_ino)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= bound, "file-type")
        chunks = []
        count = 0
        while True:
            phase_admit(end, time.monotonic, "file-deadline")
            data = os.read(fd, min(65536, bound + 1 - count))
            if not data:
                break
            chunks.append(data)
            count += len(data)
            require(count <= bound, "file-bound")
        phase_admit(end, time.monotonic, "file-deadline")
        after = os.fstat(fd)
        actual = os.stat(path, follow_symlinks=False)
        require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "file-drift",
        )
        require((actual.st_dev, actual.st_ino) == (before.st_dev, before.st_ino), "file-association")
        result = b"".join(chunks)
    except BaseException as error:
        first = error
    if fd is not None:
        try:
            os.close(fd)
            record["closed"] = True
        except BaseException as error:
            record["unknown"] = True
            first = first_error(first, error)
    if first is not None:
        raise first
    phase_admit(end, time.monotonic, "file-close-deadline")
    return result


def public_file_guard(file_os, fd, path, identity, end, clock):
    phase_admit(end, clock, "publication-completion-deadline")
    value = file_os.fstat(fd)
    actual = file_os.stat(path, follow_symlinks=False)
    require(
        stat.S_ISREG(value.st_mode)
        and value.st_nlink == 1
        and (value.st_dev, value.st_ino) == identity
        and (actual.st_dev, actual.st_ino) == identity
        and stat.S_ISREG(actual.st_mode)
        and actual.st_nlink == 1
        and stat.S_IMODE(value.st_mode) == 0o644
        and stat.S_IMODE(actual.st_mode) == 0o644,
        "publication-association",
    )


def publish_body(path, raw, bound, end, file_os, clock, records):
    # Actual publish hardwires os/time; only inert controls supply these narrow bindings.
    require(path.name in PUBLIC_OUTPUTS and len(raw) <= bound, "publication-bound")
    phase_admit(end, clock, "publication-bound")
    fd = None
    record = {"fd": None, "path": path, "identity": None, "closed": True, "unknown": False}
    records.append(record)
    first = None
    try:
        fd = file_os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        record["fd"] = fd
        record["closed"] = False
        value = file_os.fstat(fd)
        record["identity"] = (value.st_dev, value.st_ino)
        actual = file_os.stat(path, follow_symlinks=False)
        require(
            stat.S_ISREG(value.st_mode)
            and value.st_nlink == 1
            and stat.S_ISREG(actual.st_mode)
            and actual.st_nlink == 1
            and (actual.st_dev, actual.st_ino) == record["identity"],
            "publication-acquisition",
        )
        phase_admit(end, clock, "publication-completion-deadline")
        file_os.fchmod(fd, 0o644)  # Exact acquired FD, before bytes; ambient umask is unchanged.
        public_file_guard(file_os, fd, path, record["identity"], end, clock)
        progress_write(file_os.write, fd, raw, end, clock)
        phase_admit(end, clock, "publication-completion-deadline")
        file_os.fsync(fd)
        public_file_guard(file_os, fd, path, record["identity"], end, clock)
    except BaseException as error:
        first = error
    if fd is not None:
        try:
            file_os.close(fd)
            record["closed"] = True
        except BaseException as error:
            record["unknown"] = True
            first = first_error(first, error)
    if first is not None:
        raise first
    phase_admit(end, clock, "publication-close-deadline")


def publish(path, raw, bound, end):
    publish_body(path, raw, bound, end, os, time.monotonic, FILE_CUSTODY)


def coarse_failure(error):
    if isinstance(error, Control):
        return "control"
    if isinstance(error, Failure):
        return "guard"
    if isinstance(error, OSError):
        return "os"
    if isinstance(error, SystemExit):
        return "system_exit"
    if isinstance(error, KeyboardInterrupt):
        return "interrupt"
    return "other"


def freeze_failure(parent, error, native=None):
    if parent.failure_snapshot is not None or parent.diagnostic_failed:
        return
    try:
        operation = code = settled = None
        if native is not None and any(owned is native for owned in parent.natives):
            require(type(native.label) is str and native.label in NATIVE_LABELS, "diagnostic-operation")
            operation = native.label
            # Read retained observations only; never poll merely to fill a diagnostic.
            if type(native.code) is int and -(2**31) <= native.code < 2**31:
                code = native.code
            if native.process is not None and native.acquired is True and type(native.settled) is bool:
                settled = native.settled
        value = {
            "schema": FAILURE_SCHEMA,
            "phase": parent.phase,
            "operation": operation,
            "exit_code": code,
            "native_settled": settled,
            "exception_class": coarse_failure(error),
        }
        diagnostic_bytes(value)  # Validate before the single completed snapshot assignment.
        parent.failure_snapshot, parent.failure_native, parent.failure_error = (
            value,
            native if native is not None and any(owned is native for owned in parent.natives) else None,
            error,
        )
        try:
            if (
                native is None
                and value["phase"] == "build"
                and operation is None
                and value["exception_class"] == "guard"
            ):
                predicate = "unknown"
                if (
                    type(error) is Failure
                    and type(error.args) is tuple
                    and len(error.args) == 1
                    and type(error.args[0]) is str
                    and error.args[0] in BUILD_GUARD_PREDICATES
                ):
                    predicate = error.args[0]
                parent.build_guard = {"schema": BUILD_GUARD_SCHEMA, "predicate": predicate}
        except BaseException:
            pass  # Optional preparation never invalidates the genuine frozen primary.
    except BaseException:
        # Diagnostic faults are secondary; do not replace the genuine boundary error.
        parent.diagnostic_failed = True


def diagnostic_bytes(value):
    require(
        type(value) is dict
        and set(value) == {"schema", "phase", "operation", "exit_code", "native_settled", "exception_class"},
        "diagnostic-shape",
    )
    require(
        type(value["schema"]) is str
        and value["schema"] == FAILURE_SCHEMA
        and type(value["phase"]) is str
        and value["phase"] in FAILURE_PHASES
        and type(value["exception_class"]) is str
        and value["exception_class"] in FAILURE_CLASSES,
        "diagnostic-enum",
    )
    operation, code, settled = value["operation"], value["exit_code"], value["native_settled"]
    require(operation is None or (type(operation) is str and operation in NATIVE_LABELS), "diagnostic-operation")
    require(code is None or (type(code) is int and -(2**31) <= code < 2**31), "diagnostic-code")
    require(settled is None or type(settled) is bool, "diagnostic-settled")
    require(operation is not None or (code is None and settled is None), "diagnostic-association")
    raw = FAILURE_PREFIX + json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
    require(len(raw) <= 512 and raw.isascii(), "diagnostic-bound")
    return raw


def prepr_stage_bytes(value):
    closed(value, {"schema", "stage", "status"})
    require(type(value["schema"]) is str and value["schema"] == PREPR_SCHEMA, "prepr-stage-schema")
    stage, status = value["stage"], value["status"]
    require(
        type(stage) is str
        and type(status) is str
        and ((stage in PREPR_STAGES and status in {"running", "failed"}) or (stage, status) == ("unknown", "unknown")),
        "prepr-stage-enum",
    )
    raw = b"bifrost-claim-prepr-stage/v1 " + json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
    require(len(raw) <= 192 and raw.isascii(), "prepr-stage-bound")
    return raw


def build_guard_bytes(value):
    closed(value, {"schema", "predicate"})
    require(type(value["schema"]) is str and value["schema"] == BUILD_GUARD_SCHEMA, "build-guard-schema")
    predicate = value["predicate"]
    require(
        type(predicate) is str and (predicate in BUILD_GUARD_PREDICATES or predicate == "unknown"),
        "build-guard-predicate",
    )
    raw = b"bifrost-claim-build-guard/v1 " + json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
    require(len(raw) <= 128 and raw.isascii(), "build-guard-bound")
    return raw


def combined_diagnostic_bytes(value, stage, build=None):
    raw = diagnostic_bytes(value)  # An optional observation can never suppress a valid primary line.
    if stage is not None:
        try:
            combined = raw + prepr_stage_bytes(stage)
            require(len(combined) <= 512 and combined.isascii(), "diagnostic-combined-bound")
            return combined
        except BaseException:
            pass  # Primary already exists; even an interrupted optional rendering keeps its delivery available.
    if build is not None:
        try:
            require(
                stage is None
                and value["phase"] == "build"
                and value["operation"] is None
                and value["exception_class"] == "guard",
                "build-guard-association",
            )
            combined = raw + build_guard_bytes(build)
            require(len(combined) <= 512 and combined.isascii(), "diagnostic-combined-bound")
            return combined
        except BaseException:
            pass  # A refused optional predicate cannot suppress the existing primary.
    return raw


def prepr_eligible(parent, native, error):
    value = parent.failure_snapshot
    return (
        native is not None
        and any(owned is native for owned in parent.natives)
        and parent.failure_native is native
        and parent.failure_error is error
        and isinstance(error, Failure)
        and native.label == "prepr"
        and native.process is not None
        and native.acquired is True
        and native.unknown is False
        and native.settled is True
        and type(native.code) is int
        and -(2**31) <= native.code < 2**31
        and native.code != 0
        and type(value) is dict
        and value.get("phase") == "prepr"
        and value.get("operation") == "prepr"
        and type(value.get("exit_code")) is int
        and value["exit_code"] == native.code
        and value.get("native_settled") is True
        and value.get("exception_class") == "guard"
    )


def prepr_projection(raw, candidate):
    value = decode(raw, 65536)
    closed(value, {"stages"})
    stages = value["stages"]
    require(type(stages) is dict and len(stages) <= 11 and set(stages) <= PREPR_STAGES, "prepr-ledger-stages")
    context = None
    incomplete = []
    base = {
        "head",
        "status",
        "compose_sha256",
        "env_sha256",
        "docker_version",
        "compose_version",
        "python_version",
        "node_version",
    }
    for stage, record in stages.items():
        closed(record, {"status", "context", "signature"})
        status = record["status"]
        require(type(status) is str and status in {"running", "complete", "failed"}, "prepr-record-status")
        observed_context = record["context"]
        require(
            type(observed_context) is str
            and re.fullmatch(r"scope=(affected|comprehensive);full=0;plan=[0-9a-f]{64}", observed_context),
            "prepr-context",
        )
        if context is None:
            context = observed_context
        require(context == observed_context, "prepr-context-drift")
        keys = set(base)
        if stage != "repository" and not (
            status == "running" and stage in {"client", "stack", "quality", "browser", "image"}
        ):
            keys.add("compose_images")
        if stage == "browser":
            keys.add("browser_config_sha256")
        signature = record["signature"]
        closed(signature, keys)
        require(
            type(signature["head"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", signature["head"])
            and signature["head"] == candidate,
            "prepr-head",
        )
        state = signature["status"]
        require(
            type(state) is str and "\x00" not in state and len(state.encode("utf-8", "strict")) <= 32768, "prepr-status"
        )
        require(
            type(signature["compose_sha256"]) is str and HEX64.fullmatch(signature["compose_sha256"]), "prepr-compose"
        )
        for key in ("env_sha256", "browser_config_sha256"):
            if key in signature:
                digest = signature[key]
                require(type(digest) is str and (digest == "missing" or HEX64.fullmatch(digest)), "prepr-digest")
        for key in ("docker_version", "compose_version", "node_version"):
            version = signature[key]
            require(type(version) is str and re.fullmatch(r"[ -~]{0,256}", version), "prepr-version")
        version = signature["python_version"]
        require(type(version) is str and re.fullmatch(r"[!-~]{1,64}", version), "prepr-python")
        if "compose_images" in signature:
            images = signature["compose_images"]
            require(
                type(images) is list
                and len(images) <= 128
                and all(type(image) is str and IID.fullmatch(image) for image in images),
                "prepr-images",
            )
            require(images == sorted(set(images)), "prepr-images-order")
        if status != "complete":
            incomplete.append((stage, status))
    stage, status = incomplete[0] if len(incomplete) == 1 else ("unknown", "unknown")
    return {"schema": PREPR_SCHEMA, "stage": stage, "status": status}


class PreprLedger:
    """One fixed optional host ledger; actual caller hardwires os/clock/custody, controls are inert."""

    def __init__(self, root, candidate, file_os, clock, records):
        self.root, self.candidate = root, candidate
        self.os, self.clock, self.records = file_os, clock, records
        self.handles = {}
        self.ready = False
        self.observation_error = None  # Private actual secondary identity, including eligibility/read interrupts.
        self.uid, self.gid = file_os.geteuid(), file_os.getegid()

    def check_end(self):
        phase_admit(WORK_END, self.clock, "prepr-reader-end")

    def location(self, name):
        # This fixed roster has no runtime path/module/file selector.
        return {
            "root": (None, self.root),
            "git": ("root", ".git"),
            "locks": ("git", "bifrost-test-locks"),
            "scripts": ("root", "scripts"),
            "lib": ("scripts", "lib"),
            "helper": ("lib", "pre_pr_stage_evidence.py"),
            "ledger": ("locks", "pre-pr-stages.json"),
        }[name]

    def metadata(self, name, value):
        mode = stat.S_IMODE(value.st_mode)
        require(value.st_uid == self.uid, "prepr-owner")
        if name in {"helper", "ledger"}:
            require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, "prepr-file-type")
            require(mode == (0o644 if name == "helper" else 0o600), "prepr-file-mode")
            if name == "ledger":
                require(value.st_gid == self.gid and 0 < value.st_size <= 65536, "prepr-ledger-size-gid")
            else:
                require(value.st_size == 6024, "prepr-helper-size")
            return (
                value.st_dev,
                value.st_ino,
                value.st_uid,
                value.st_gid,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )
        require(
            stat.S_ISDIR(value.st_mode) and mode & 0o7000 == 0 and mode & 0o700 == 0o700 and mode & 0o022 == 0,
            "prepr-directory-mode",
        )
        return value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode

    def open(self, name):
        self.check_end()
        parent, component = self.location(name)
        parent_fd = None if parent is None else self.handles[parent]["fd"]
        record = {"fd": None, "path": component, "identity": None, "closed": True, "unknown": False}
        self.records.append(record)
        self.handles[name] = record  # Registration precedes every returned-FD validation.
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        if name not in {"helper", "ledger"}:
            flags |= os.O_DIRECTORY
        record["fd"] = self.os.open(component, flags, dir_fd=parent_fd)
        record["closed"] = False
        require(self.os.get_inheritable(record["fd"]) is False, "prepr-fd-inheritance")
        record["identity"] = self.metadata(name, self.os.fstat(record["fd"]))
        self.check(name)

    def check(self, name):
        self.check_end()
        record = self.handles[name]
        parent, component = self.location(name)
        parent_fd = None if parent is None else self.handles[parent]["fd"]
        actual = self.os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        require(
            self.metadata(name, self.os.fstat(record["fd"])) == record["identity"]
            and self.metadata(name, actual) == record["identity"],
            "prepr-path-association",
        )

    def check_all(self):
        for name, record in self.handles.items():
            if record["fd"] is not None and not record["closed"]:
                self.check(name)

    def read(self, name, bound):
        self.check(name)
        fd = self.handles[name]["fd"]
        self.check_end()
        require(self.os.lseek(fd, 0, os.SEEK_SET) == 0, "prepr-seek")
        chunks, count = [], 0
        while True:
            self.check_end()
            raw = self.os.read(fd, min(16384, bound + 1 - count))
            require(type(raw) is bytes, "prepr-read-type")
            if not raw:
                break
            chunks.append(raw)
            count += len(raw)
            require(count <= bound, "prepr-read-bound")
        self.check(name)
        require(count == self.os.fstat(fd).st_size, "prepr-read-eof")
        return b"".join(chunks)

    def helper(self):
        require(hashlib.sha256(self.read("helper", 8192)).hexdigest() == PREPR_HELPER_HASH, "prepr-helper-hash")

    def prepare(self):
        for name in ("root", "git", "scripts", "lib", "helper"):
            self.open(name)
        self.helper()
        try:
            self.open("locks")
        except FileNotFoundError:
            require(self.handles["locks"]["fd"] is None, "prepr-locks-acquired")
        else:
            try:
                self.os.stat("pre-pr-stages.json", dir_fd=self.handles["locks"]["fd"], follow_symlinks=False)
            except FileNotFoundError:
                pass  # Real relative absence; no before-ledger inode exists.
            else:
                raise Failure("prepr-preexisting")
        self.check_all()
        self.ready = True

    def collect(self):
        require(self.ready, "prepr-not-ready")
        self.check_all()
        self.helper()  # Same retained helper FD and frozen bytes after writer settlement.
        if self.handles["locks"]["fd"] is None:
            self.open("locks")
        self.open("ledger")
        raw = self.read("ledger", 65536)
        self.check_all()
        return prepr_projection(raw, self.candidate)

    def close(self):
        errors = []
        for name in ("ledger", "helper", "locks", "lib", "scripts", "git", "root"):
            record = self.handles.get(name)
            if record is None or record["fd"] is None or record.get("attempted") is True:
                continue
            record["attempted"] = True
            try:
                self.os.close(record["fd"])  # Independent once-close even after WorkEnd.
                record["closed"] = True
            except BaseException as error:
                record["unknown"] = True
                errors.append(error)
        return errors


def prepr_original(parent):
    reader = PreprLedger(parent.values["ROOT"], parent.values["CANDIDATE"], os, time.monotonic, FILE_CUSTODY)
    prepr_execute(parent, reader)


def prepr_execute(parent, reader):
    # Fixed actual composition, shared only with inert source controls; no runtime selector.
    try:
        reader.prepare()
    except (Failure, OSError, ValueError):
        reader.ready = False  # Diagnostic-only profile refusal cannot replace literal prePR.
        errors = reader.close()
        parent.cleanup_errors.extend(errors)
        if errors:
            raise errors[0] from None
    except BaseException:
        parent.cleanup_errors.extend(reader.close())
        raise
    try:
        parent.original("prepr", WORK_END)
    except BaseException as error:
        stage = None
        try:
            eligible = prepr_eligible(parent, parent.failure_native, error) and reader.clock() < WORK_END
            if eligible:
                stage = {"schema": PREPR_SCHEMA, "stage": "unknown", "status": "unknown"}
                if reader.ready:
                    stage = reader.collect()
        except BaseException as observation_error:
            # Optional eligibility/read faults cannot bypass closes or replace original failed prePR.
            reader.observation_error = observation_error
            if stage is not None:
                stage = {"schema": PREPR_SCHEMA, "stage": "unknown", "status": "unknown"}
        errors = reader.close()
        parent.cleanup_errors.extend(errors)
        if errors and stage is not None:
            stage = {"schema": PREPR_SCHEMA, "stage": "unknown", "status": "unknown"}
        parent.prepr_stage = stage  # Nonunknown assignment only after known closure.
        raise
    else:
        errors = reader.close()
        parent.cleanup_errors.extend(errors)
        if errors:
            raise errors[0]


def emit_diagnostic(value, end, file_os, clock, stage=None, build=None):
    # Inherited stderr is borrowed, never closed. One nonblocking write, no retry or new end.
    first = None
    blocking = None
    try:
        raw = combined_diagnostic_bytes(value, stage, build)
        phase_admit(end, clock, "diagnostic-deadline")
        blocking = file_os.get_blocking(2)
        require(type(blocking) is bool, "diagnostic-blocking")
        phase_admit(end, clock, "diagnostic-deadline")
        file_os.set_blocking(2, False)
        phase_admit(end, clock, "diagnostic-deadline")
        count = file_os.write(2, raw)
        require(type(count) is int and count == len(raw), "diagnostic-write")
        phase_admit(end, clock, "diagnostic-completion-deadline")
    except BaseException as error:
        first = error
    if type(blocking) is bool:
        try:
            file_os.set_blocking(2, blocking)  # Independent restoration even after write failure or expiry.
        except BaseException as error:
            first = first_error(first, error)
    if first is not None:
        raise first


def junit_actual(parent):
    raw = retained_file(Path(parent.values["EVIDENCE"]) / "test-results.xml", 1048576, CLEANUP_END)
    require(b"<!DOCTYPE" not in raw and b"<!ENTITY" not in raw, "junit-declarations")
    root = ET.fromstring(raw)
    require(root.tag in {"testsuites", "testsuite"}, "junit-root")
    cases = root.findall(".//testcase")
    require(0 < len(cases) <= 1024, "junit-count")
    source = ast.parse(retained_file(Path("api/tests/parity/test_workflow_domain.py"), 1048576, CLEANUP_END))
    expected = set()
    domain = decode(
        retained_file(Path("api/tests/parity/fixtures/workflow-domain-v1.json"), 1048576, CLEANUP_END), 1048576
    )
    claims = decode(
        retained_file(Path("api/tests/parity/fixtures/workflow-claim-v1.json"), 1048576, CLEANUP_END), 1048576
    )
    require(type(domain) is dict and type(domain.get("cases")) is list, "junit-domain-fixture")
    require(type(claims) is dict and type(claims.get("scenarios")) is list, "junit-claim-fixture")
    for declaration in source.body:
        if not isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef)) or not declaration.name.startswith(
            "test_"
        ):
            continue
        name = declaration.name
        if name == "test_real_workflow_domain_differential":
            identifiers = [case["case_id"] for case in domain["cases"]]
        elif name == "test_original_claim_characterization":
            identifiers = [case["case_id"] for case in claims["scenarios"]]
        elif name == "test_real_rust_plan_comparator_detects_drift":
            identifiers = ["status", "nullable_input", "projection", "clock", "unselected_row"]
        else:
            require(
                not any(
                    isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "parametrize"
                    for dec in declaration.decorator_list
                ),
                "junit-unknown-parametrization",
            )
            identifiers = None
        names = [name] if identifiers is None else [name + "[" + identity + "]" for identity in identifiers]
        require(not (expected & set(names)), "junit-source-duplicate")
        expected.update(names)
    observed = set()
    for case in cases:
        name = case.get("name")
        require(name in expected and name not in observed, "junit-selected-identity")
        require(case.get("classname") == "tests.parity.test_workflow_domain", "junit-source-module")
        require(not any(case.find(tag) is not None for tag in ("failure", "error", "skipped")), "junit-failed")
        observed.add(name)
    require(observed == expected, "junit-omitted")
    require(time.monotonic() < CLEANUP_END, "junit-deadline")


def receipt(parent, evidence):
    source = {path: hashlib.sha256(retained_file(Path(path), 1048576, WORK_END)).hexdigest() for path in SOURCE_PATHS}
    value = {
        "schema": "bifrost.test.workflow-domain-receipt/v2",
        "candidate_sha": parent.original("receipt.head", WORK_END).decode("ascii").strip(),
        "candidate_tree": parent.original("receipt.tree", WORK_END).decode("ascii").strip(),
        "driver_sha256": hashlib.sha256(retained_file(evidence / "driver", 67108864, WORK_END)).hexdigest(),
        "fixture_sha256": source["api/tests/parity/fixtures/workflow-domain-v1.json"],
        "source_sha256": source,
        "claim_frontend_policy": "required/v1",
    }
    require(value["candidate_sha"] == parent.values["CANDIDATE"], "receipt-candidate")
    require(HEX64.fullmatch(value["driver_sha256"]), "receipt-driver")
    publish(evidence / "receipt.json", (json.dumps(value, indent=2) + "\n").encode(), 16384, WORK_END)


def main_work(parent):
    parent.phase = "source"
    root = parent.original("source.root", WORK_END).decode("utf-8").strip()
    require(Path(root).is_absolute() and root == str(Path.cwd()), "source-root")
    parent.values["ROOT"] = root
    project = parent.original("source.project", WORK_END).decode("ascii").strip()
    require(re.fullmatch(r"bifrost-test-[0-9a-f]{8}", project), "project-profile")
    parent.values.update(
        PROJECT=project,
        LOG_DIR="/tmp/bifrost-" + project,
        BUILDER_NAME=project + "-workflow-domain-builder",
        CHECKS_IMAGE=project + "-workflow-domain-checks",
    )
    evidence = Path(parent.values["LOG_DIR"]) / "workflow-domain-parity"
    parent.values["EVIDENCE"] = str(evidence)
    os.environ.update(
        COMPOSE_FILE="docker-compose.test.yml", COMPOSE_PROJECT_NAME=project, LOG_DIR=parent.values["LOG_DIR"]
    )
    require(parent.original("source.clean", WORK_END) == b"", "source-dirty")
    initial = [
        parent.original(label, WORK_END) for label in ("initial.containers", "initial.volumes", "initial.networks")
    ]
    require(all(raw == b"" for raw in initial) and not os.path.lexists(evidence), "initial-resources")
    parent.initial_empty = True
    for label in ("evidence.mkdir", "evidence.mode", "observations.mkdir", "observations.mode"):
        parent.original(label, WORK_END)
    parent.values["CANDIDATE"] = parent.original("source.head", WORK_END).decode("ascii").strip()
    require(re.fullmatch(r"[0-9a-f]{40}", parent.values["CANDIDATE"]), "candidate-id")
    publish(evidence / "source.txt", parent.original("source.head_tree", WORK_END), 256, WORK_END)
    parent.phase = "prepr"
    prepr_original(parent)
    require(parent.original("prepr.head", WORK_END).decode("ascii").strip() == parent.values["CANDIDATE"], "prepr-head")
    require(parent.original("prepr.clean", WORK_END) == b"", "prepr-dirty")
    parent.phase = "build"
    parent.original("checks.build", WORK_END)
    parent.values["CHECKS_IID"] = image_id(parent.original("checks.image", WORK_END).decode("ascii").strip())
    parent.builder_id = cid(parent.original("builder.create", WORK_END).decode("ascii").strip())
    parent.values["ACTUAL_RETAINED_BUILDER_CID"] = parent.builder_id
    parent.original("builder.start", WORK_END)
    parent.builder_snapshot = object_set(parent.original("builder.terminal", WORK_END), [parent.builder_id])[
        parent.builder_id
    ]
    builder_guard(parent, parent.builder_snapshot, True)
    parent.original("driver.copy", WORK_END)
    parent.original("driver.mode", WORK_END)
    parent.original("builder.remove", WORK_END)
    parent.builder_removed = True
    receipt(parent, evidence)
    parent.custody_before = parent.original("custody.before", WORK_END)
    publish(evidence / "custody-before.txt", parent.custody_before, 4096, WORK_END)
    parent.phase = "stack"
    parent.stack_attempted = True
    parent.original("stack.up", WORK_END)
    parent.api_before = image_id(parent.original("api.before", WORK_END).decode("ascii").strip())
    publish(evidence / "api-image-before.txt", (parent.api_before + "\n").encode(), 128, WORK_END)
    parent.phase = "frontend"
    source_model(
        parent,
        parent.front(
            "model",
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.test.yml",
                "--profile",
                "e2e",
                "--profile",
                "test",
                "config",
                "--format",
                "json",
            ],
        ),
    )
    parent.original("junit.invalidate", WORK_END)
    parent.listen(evidence)
    parent.parity_started = True
    argv = next(args for label, args, _stage in ORIGINAL_COMMANDS if label == "target")
    parent.phase = "target"
    target = parent.launch("target", list(argv), WORK_END)
    parent.phase = "frontend"
    parent.accept()
    target_gate(parent)
    parent.phase = "target"
    parent.wait(target)
    parent.phase = "frontend"
    require(parent.peer.recv(1) == b"", "wire-final-eof")
    rows = census(
        parent.front(
            "runner_absent",
            [
                "docker",
                "ps",
                "-a",
                "--no-trunc",
                "--filter",
                "label=com.docker.compose.project=" + project,
                "--format",
                "{{json .}}",
            ],
        )
    )
    require(parent.runner["Id"] not in rows, "runner-retained")
    parent.phase = "source"
    require(
        parent.original("source.final_head", WORK_END).decode("ascii").strip() == parent.values["CANDIDATE"],
        "final-head",
    )
    require(parent.original("source.final_clean", WORK_END) == b"", "final-source-dirty")


def main_cleanup(parent):
    parent.phase = "cleanup"
    cleanup_attempt(parent, parent.close_failed_natives)
    cleanup_attempt(parent, parent.close_ipc)
    if parent.parity_started:

        def custody():
            after = parent.original("custody.after", CLEANUP_END)
            publish(Path(parent.values["EVIDENCE"]) / "custody-after.txt", after, 4096, CLEANUP_END)
            require(after == parent.custody_before, "custody-drift")

        cleanup_attempt(parent, custody)
        cleanup_attempt(
            parent,
            lambda: publish(
                Path(parent.values["EVIDENCE"]) / "custody-ownership-after.txt",
                parent.original("custody.ownership", CLEANUP_END),
                4096,
                CLEANUP_END,
            ),
        )

        def api():
            actual = parent.original("api.after", CLEANUP_END).decode("ascii").strip()
            require(actual == parent.api_before, "api-image-drift")
            publish(Path(parent.values["EVIDENCE"]) / "api-image-after.txt", (actual + "\n").encode(), 128, CLEANUP_END)

        cleanup_attempt(parent, api)

        def junit():
            parent.original("junit.copy", CLEANUP_END)
            junit_actual(parent)

        cleanup_attempt(parent, junit)
    if parent.builder_id is not None and not parent.builder_removed:

        def remove_builder():
            values = object_set(parent.original("builder.cleanup_inspect", CLEANUP_END), [parent.builder_id])
            builder_guard(parent, values[parent.builder_id], False)
            parent.original("builder.cleanup_remove", CLEANUP_END)
            parent.builder_removed = True

        cleanup_attempt(parent, remove_builder)
    if parent.stack_attempted and parent.initial_empty:

        def down():
            stack_cleanup_guard(parent)
            parent.original("stack.down", CLEANUP_END)
            parent.snapshot = None

        cleanup_attempt(parent, down)
    if parent.initial_empty:
        for label in ("final.containers", "final.volumes", "final.networks"):

            def absent(label=label):
                actual = parent.original(label, CLEANUP_END)
                require(actual == b"", "resource-remaining")
                parent.absence[label.split(".", 1)[1]] = ""

            cleanup_attempt(parent, absent)
    if parent.private is not None:
        cleanup_attempt(parent, parent.dispose_private)
    cleanup_attempt(
        parent,
        lambda: require(
            all(record["closed"] is True and record["unknown"] is False for record in FILE_CUSTODY), "local-fd-unknown"
        ),
    )


def prepr_stage_controls():
    candidate = "1" * 40
    context = "scope=comprehensive;full=0;plan=" + "2" * 64
    base_keys = (
        "head",
        "status",
        "compose_sha256",
        "env_sha256",
        "docker_version",
        "compose_version",
        "python_version",
        "node_version",
    )
    images_keys = (*base_keys, "compose_images")
    browser_keys = (*base_keys, "browser_config_sha256")
    browser_images_keys = (*images_keys, "browser_config_sha256")
    # Independent fixed expected map: each row supplies running/finished keys, yielding all33 variants.
    variants = tuple(
        (stage, status, keys)
        for stage, running, finished in (
            ("repository", base_keys, base_keys),
            ("client", base_keys, images_keys),
            ("stack", base_keys, images_keys),
            ("quality", base_keys, images_keys),
            ("generated", images_keys, images_keys),
            ("unit", images_keys, images_keys),
            ("e2e", images_keys, images_keys),
            ("mcp", images_keys, images_keys),
            ("client-unit", images_keys, images_keys),
            ("browser", browser_keys, browser_images_keys),
            ("image", base_keys, images_keys),
        )
        for status, keys in (("running", running), ("failed", finished), ("complete", finished))
    )
    values = {
        "head": candidate,
        "status": "unavailable",
        "compose_sha256": "3" * 64,
        "env_sha256": "missing",
        "docker_version": "unavailable",
        "compose_version": "",
        "python_version": "3.13.5",
        "node_version": "v24.0.0",
        "compose_images": [],
        "browser_config_sha256": "missing",
    }

    def record(status, keys):
        return {"status": status, "context": context, "signature": {key: values[key] for key in keys}}

    def encoded(records):
        return json.dumps({"stages": records}, ensure_ascii=True).encode()

    for stage, status, keys in variants:
        actual = record(status, keys)
        result = prepr_projection(encoded({stage: actual}), candidate)
        wanted = ("unknown", "unknown") if status == "complete" else (stage, status)
        require((result["stage"], result["status"]) == wanted, "control-prepr-variant")
        if status == "complete":
            other = "unit" if stage == "repository" else "repository"
            other_keys = next(keys for name, disposition, keys in variants if name == other and disposition == "failed")
            result = prepr_projection(encoded({stage: actual, other: record("failed", other_keys)}), candidate)
            require((result["stage"], result["status"]) == (other, "failed"), "control-prepr-complete-carry")
        wrong = record(status, keys)
        if "compose_images" in wrong["signature"]:
            del wrong["signature"]["compose_images"]
        else:
            wrong["signature"]["compose_images"] = []
        try:
            prepr_projection(encoded({stage: wrong}), candidate)
        except Failure as error:
            require(str(error) == "closed-keys", "control-prepr-variant-negative")
        else:
            raise Failure("control-prepr-variant-negative")
    repository_keys = next(keys for stage, status, keys in variants if stage == "repository" and status == "failed")
    baseline = record("failed", repository_keys)
    positive = record("failed", repository_keys)
    positive["signature"]["status"] = " M private-χ.py\n"
    require(
        prepr_projection(encoded({"repository": positive}), candidate)["stage"] == "repository", "control-prepr-utf8"
    )
    for records in (
        {},
        {"repository": record("complete", repository_keys)},
        {"repository": baseline, "client": record("failed", (*repository_keys, "compose_images"))},
    ):
        require(prepr_projection(encoded(records), candidate)["stage"] == "unknown", "control-prepr-incomplete-count")
    mutations = (
        ("head", "4" * 40, "prepr-head"),
        ("status", None, "prepr-status"),
        ("status", "x" * 32769, "prepr-status"),
        ("status", "\x00", "prepr-status"),
        ("compose_sha256", "A" * 64, "prepr-compose"),
        ("env_sha256", False, "prepr-digest"),
        ("env_sha256", "unavailable", "prepr-digest"),
        ("docker_version", "x" * 257, "prepr-version"),
        ("node_version", "v1\n", "prepr-version"),
        ("python_version", "3.13 5", "prepr-python"),
        ("python_version", "", "prepr-python"),
    )
    rejected = []
    for key, value, label in mutations:
        changed = record("failed", repository_keys)
        changed["signature"][key] = value
        rejected.append((encoded({"repository": changed}), label))
    for field, value, label in (
        ("status", True, "prepr-record-status"),
        ("context", context.replace("full=0", "full=1"), "prepr-context"),
        ("context", context + "\n", "prepr-context"),
        ("status", "unknown", "prepr-record-status"),
    ):
        changed = record("failed", repository_keys)
        changed[field] = value
        rejected.append((encoded({"repository": changed}), label))
    changed = record("failed", repository_keys)
    changed["signature"]["extra"] = None
    rejected.append((encoded({"repository": changed}), "closed-keys"))
    changed = record("failed", repository_keys)
    del changed["signature"]["head"]
    rejected.append((encoded({"repository": changed}), "closed-keys"))
    changed = record("complete", repository_keys)
    changed["context"] = context.replace("comprehensive", "affected")
    rejected.append((encoded({"repository": baseline, "client": changed}), "prepr-context-drift"))
    rejected.extend(
        (
            (encoded({"unknown": baseline}), "prepr-ledger-stages"),
            (b'{"stages":{},"stages":{}}', "duplicate-key"),
            (b'{"stages":{"repository":NaN}}', "json-constant"),
            (b"\xef\xbb\xbf" + encoded({"repository": baseline}), "json-syntax"),
            (b" " * 65537, "json-bound"),
            (b'{"stages":{},"extra":null}', "closed-keys"),
            (b'{"stages":[]}', "prepr-ledger-stages"),
        )
    )
    unit_keys = next(keys for stage, status, keys in variants if stage == "unit" and status == "failed")
    for images, label in (
        (["sha256:" + "0" * 64], None),
        (["sha256:" + f"{index:064x}" for index in range(128)], None),
        (["sha256:" + "0" * 64] * 2, "prepr-images-order"),
        (["sha256:" + "1" * 64, "sha256:" + "0" * 64], "prepr-images-order"),
        (["sha256:" + f"{index:064x}" for index in range(129)], "prepr-images"),
        ([None], "prepr-images"),
        (["tag:latest"], "prepr-images"),
        (None, "prepr-images"),
    ):
        changed = record("failed", unit_keys)
        changed["signature"]["compose_images"] = images
        raw = encoded({"unit": changed})
        if label is None:
            require(prepr_projection(raw, candidate)["stage"] == "unit", "control-prepr-images-positive")
        else:
            rejected.append((raw, label))
    for raw, label in rejected:
        try:
            prepr_projection(raw, candidate)
        except Failure as error:
            if str(error) != label:
                raise
        else:
            raise Failure("control-prepr-grammar-negative")
    primary = {
        "schema": FAILURE_SCHEMA,
        "phase": "prepr",
        "operation": "prepr",
        "exit_code": 1,
        "native_settled": True,
        "exception_class": "guard",
    }
    stage = {"schema": PREPR_SCHEMA, "stage": "repository", "status": "failed"}
    require(
        combined_diagnostic_bytes(primary, stage) == diagnostic_bytes(primary) + prepr_stage_bytes(stage),
        "control-prepr-combined",
    )
    for invalid in ({}, {**stage, "stage": True}, {**stage, "status": "unknown"}, {**stage, "schema": None}):
        require(combined_diagnostic_bytes(primary, invalid) == diagnostic_bytes(primary), "control-prepr-primary-only")
    parent = Parent()
    parent.phase = "prepr"
    native = Native("prepr", WORK_END, 1)
    native.process, native.acquired, native.settled, native.code = object(), True, True, 1
    parent.natives.append(native)
    error = Failure("synthetic")
    freeze_failure(parent, error, native)
    require(prepr_eligible(parent, native, error), "control-prepr-eligible")
    for field, value in (
        ("unknown", True),
        ("acquired", False),
        ("settled", False),
        ("code", 0),
        ("code", True),
        ("label", "target"),
    ):
        old = getattr(native, field)
        setattr(native, field, value)
        require(not prepr_eligible(parent, native, error), "control-prepr-ineligible")
        setattr(native, field, old)
    require(
        not prepr_eligible(parent, native, Control())
        and not prepr_eligible(parent, Native("prepr", WORK_END, 1), error),
        "control-prepr-first-object",
    )


def prepr_reader_controls():
    # Exact reviewed source is inert bytes only: no import/exec or hash-binding override.
    helper_bytes = (
        b'#!/usr/bin/env python3\n"""Small, dependency-free evidence ledger for resumable p'
        b're-PR stages."""\n\nfrom __future__ import annotations\n\nimport argparse\nimport has'
        b"hlib\nimport json\nimport os\nimport subprocess\nimport sys\nimport tempfile\nfrom pat"
        b"hlib import Path\n\n\ndef run(command: list[str], cwd: Path) -> str:\n    try:\n     "
        b"   return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess"
        b".DEVNULL).strip()\n    except (OSError, subprocess.CalledProcessError):\n        r"
        b'eturn "unavailable"\n\n\ndef digest(path: Path) -> str:\n    if not path.is_file():\n'
        b'        return "missing"\n    h = hashlib.sha256()\n    with path.open("rb") as st'
        b'ream:\n        for chunk in iter(lambda: stream.read(1024 * 1024), b""):\n        '
        b"    h.update(chunk)\n    return h.hexdigest()\n\n\ndef snapshot(repo: Path, compose_"
        b"file: str, env_file: str, stage: str | None = None) -> dict[str, object]:\n    st"
        b'atus = run(["git", "status", "--porcelain", "--untracked-files=all"], repo)\n    '
        b'head = run(["git", "rev-parse", "HEAD"], repo)\n    command = ["docker", "compose'
        b'", "-f", compose_file]\n    profile = {"client": "client-check", "client-unit": "'
        b'client-check", "browser": "client", "mcp": "test"}.get(stage)\n    if profile:\n  '
        b'      command += ["--profile", profile]\n    compose = run([*command, "config"], '
        b'repo)\n    names = run([*command, "config", "--images"], repo)\n    if stage == "i'
        b'mage" and names != "unavailable":\n        names += f"\\nbifrost-local-api-candida'
        b'te:{head[:12]}"\n    # Resolve configured tags, not just running containers: anot'
        b"her checkout can\n    # rebuild a shared test-image tag between two local gate in"
        b'vocations.\n    images = run(["docker", "image", "inspect", "--format", "{{.Id}}"'
        b', *names.splitlines()], repo) if names and names != "unavailable" else "unavaila'
        b'ble"\n    return {\n        "head": head,\n        "status": status,\n        "compo'
        b'se_sha256": hashlib.sha256(compose.encode()).hexdigest(),\n        "compose_avail'
        b'able": compose != "unavailable",\n        "compose_images": sorted(set(images.spl'
        b'itlines())) if images != "unavailable" else [],\n        "env_sha256": digest(rep'
        b'o / env_file),\n        "docker_version": run(["docker", "version", "--format", "'
        b'{{.Server.Version}}"], repo),\n        "compose_version": run(["docker", "compose'
        b'", "version", "--short"], repo),\n        "python_version": sys.version.split()[0'
        b'],\n        "node_version": run(["node", "--version"], repo),\n        "browser_co'
        b'nfig_sha256": digest(repo / "client" / "playwright.config.ts"),\n    }\n\n\ndef sign'
        b'ature(value: dict[str, object], stage: str) -> dict[str, object]:\n    keys = {"h'
        b'ead", "status", "compose_sha256", "env_sha256", "docker_version", "compose_versi'
        b'on", "python_version", "node_version"}\n    if stage != "repository":\n        key'
        b's.add("compose_images")\n    if stage == "browser":\n        keys.update({"compose'
        b'_images", "browser_config_sha256"})\n    return {key: value[key] for key in sorte'
        b"d(keys)}\n\n\ndef invariant_signature(value: dict[str, object], stage: str) -> dict"
        b'[str, object]:\n    result = signature(value, stage)\n    if stage in {"client", "'
        b'stack", "quality", "browser", "image"}:\n        result.pop("compose_images", Non'
        b"e)\n    return result\n\n\ndef read_state(path: Path) -> dict[str, object]:\n    try:"
        b'\n        return json.loads(path.read_text(encoding="utf-8"))\n    except (OSError'
        b", json.JSONDecodeError):\n        return {}\n\n\ndef atomic_write(path: Path, value:"
        b" dict[str, object]) -> None:\n    path.parent.mkdir(parents=True, exist_ok=True)\n"
        b'    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)\n  '
        b'  try:\n        with os.fdopen(fd, "w", encoding="utf-8") as stream:\n            '
        b'json.dump(value, stream, sort_keys=True, indent=2)\n            stream.write("\\n"'
        b")\n        os.replace(temporary, path)\n    finally:\n        if os.path.exists(tem"
        b"porary):\n            os.unlink(temporary)\n\n\ndef main() -> int:\n    parser = argp"
        b'arse.ArgumentParser()\n    parser.add_argument("action", choices=["snapshot", "st'
        b'art", "success", "failed", "reuse", "fresh"])\n    parser.add_argument("--repo", '
        b'required=True, type=Path)\n    parser.add_argument("--state", required=True, type'
        b'=Path)\n    parser.add_argument("--stage")\n    parser.add_argument("--context", d'
        b'efault="")\n    parser.add_argument("--compose-file", default="docker-compose.tes'
        b't.yml")\n    parser.add_argument("--env-file", default=".env.test")\n    args = pa'
        b"rser.parse_args()\n    current = snapshot(args.repo, args.compose_file, args.env_"
        b'file, args.stage)\n\n    if args.action == "snapshot":\n        print(json.dumps(cu'
        b'rrent, sort_keys=True))\n        return 0\n    if args.action == "fresh":\n        '
        b"args.state.unlink(missing_ok=True)\n        return 0\n    if not args.stage:\n     "
        b'   parser.error("--stage is required for stage actions")\n    state = read_state('
        b'args.state)\n    stages = state.setdefault("stages", {})\n    if args.action == "s'
        b'uccess" and current["status"]:\n        return 1\n    previous = stages.get(args.s'
        b'tage, {})\n    if args.action == "success" and previous.get("signature") != invar'
        b'iant_signature(current, args.stage):\n        return 1\n    if args.action == "reu'
        b'se":\n        record = stages.get(args.stage, {})\n        available = current["co'
        b'mpose_available"] and current["docker_version"] != "unavailable" and current["co'
        b'mpose_version"] != "unavailable" and current["node_version"] != "unavailable"\n  '
        b'      if args.stage != "repository":\n            available = available and bool('
        b'current["compose_images"])\n        reusable = available and record.get("status")'
        b' == "complete" and record.get("context") == args.context and record.get("signatu'
        b're") == signature(current, args.stage) and not current["status"]\n        return '
        b"0 if reusable else 1\n    stored_signature = invariant_signature(current, args.st"
        b'age) if args.action == "start" else signature(current, args.stage)\n    stages[ar'
        b'gs.stage] = {"status": {"start": "running", "success": "complete", "failed": "fa'
        b'iled"}[args.action], "context": args.context, "signature": stored_signature}\n   '
        b' atomic_write(args.state, state)\n    return 0\n\n\nif __name__ == "__main__":\n    r'
        b"aise SystemExit(main())\n"
    )
    require(
        len(helper_bytes) == 6024 and hashlib.sha256(helper_bytes).hexdigest() == PREPR_HELPER_HASH,
        "control-prepr-fixture",
    )
    candidate = "1" * 40
    context = "scope=affected;full=0;plan=" + "2" * 64
    ledger_bytes = json.dumps(
        {
            "stages": {
                "repository": {
                    "status": "failed",
                    "context": context,
                    "signature": {
                        "head": candidate,
                        "status": "",
                        "compose_sha256": "3" * 64,
                        "env_sha256": "missing",
                        "docker_version": "unavailable",
                        "compose_version": "",
                        "python_version": "3.13.5",
                        "node_version": "",
                    },
                }
            }
        }
    ).encode()

    class Fact:
        def __init__(self, inode, mode, size=0):
            self.st_dev, self.st_ino = 1, inode
            self.st_uid = self.st_gid = 1001
            self.st_mode, self.st_size = mode, size
            self.st_nlink = 1
            self.st_mtime_ns = self.st_ctime_ns = 0

    class Files:
        def __init__(self, locks=True, preexisting=False):
            self.entries = {}
            for index, path in enumerate(
                ("/repo", "/repo/.git", "/repo/scripts", "/repo/scripts/lib", "/repo/.git/bifrost-test-locks")
            ):
                self.entries[path] = Fact(index + 1, stat.S_IFDIR | 0o755)
            self.data = {"/repo/scripts/lib/pre_pr_stage_evidence.py": helper_bytes}
            self.entries["/repo/scripts/lib/pre_pr_stage_evidence.py"] = Fact(7, stat.S_IFREG | 0o644, 6024)
            if not locks:
                del self.entries["/repo/.git/bifrost-test-locks"]
            if preexisting:
                self.install()
            self.fds, self.offsets, self.opened, self.closed = {}, {}, [], []
            self.flags = []
            self.open_failure = self.stat_failure = None
            self.close_failures = {}
            self.read_failure = None
            self.partial = self.overflow = self.drift_read = False
            self.stat_counts = {}
            self.replace_before = None

        def geteuid(self):
            return 1001

        def getegid(self):
            return 1001

        def path(self, name, dir_fd):
            return name if dir_fd is None else self.fds[dir_fd][0] + "/" + name

        def install(self):
            self.entries.setdefault("/repo/.git/bifrost-test-locks", Fact(6, stat.S_IFDIR | 0o755))
            path = "/repo/.git/bifrost-test-locks/pre-pr-stages.json"
            self.entries[path] = Fact(8, stat.S_IFREG | 0o600, len(ledger_bytes))
            self.data[path] = ledger_bytes

        def open(self, name, flags, dir_fd=None):
            path = self.path(name, dir_fd)
            if path == self.open_failure:
                raise OSError("synthetic-open")
            if path not in self.entries:
                raise FileNotFoundError
            fd = len(self.opened) + 10
            self.fds[fd] = path, self.entries[path]
            self.offsets[fd] = 0
            self.opened.append(fd)
            self.flags.append(flags)
            return fd

        def get_inheritable(self, fd):
            require(fd in self.fds, "control-prepr-acquired")
            return False

        def fstat(self, fd):
            if self.fds[fd][0] == self.stat_failure:
                raise OSError("synthetic-fstat-after-fd")
            return self.fds[fd][1]

        def stat(self, name, dir_fd=None, follow_symlinks=False):
            require(follow_symlinks is False, "control-prepr-no-follow")
            path = self.path(name, dir_fd)
            self.stat_counts[path] = self.stat_counts.get(path, 0) + 1
            if path == self.replace_before and self.stat_counts[path] == 2:
                self.replace(path)
            if path not in self.entries:
                raise FileNotFoundError
            return self.entries[path]

        def replace(self, path):
            old = self.entries[path]
            new = Fact(old.st_ino + 100, old.st_mode, old.st_size)
            self.entries[path] = new

        def lseek(self, fd, offset, whence):
            require(offset == 0 and whence == os.SEEK_SET, "control-prepr-seek")
            self.offsets[fd] = 0
            return 0

        def read(self, fd, count):
            path, fact = self.fds[fd]
            if path.endswith("pre-pr-stages.json"):
                if self.read_failure is not None:
                    raise self.read_failure
                if self.partial:
                    return b""
                if self.overflow:
                    return b"x" * count
                if self.drift_read:
                    fact.st_mtime_ns += 1
            raw = self.data[path][self.offsets[fd] : self.offsets[fd] + count]
            self.offsets[fd] += len(raw)
            return raw

        def close(self, fd):
            self.closed.append(fd)
            path = self.fds[fd][0]
            if path in self.close_failures:
                raise self.close_failures[path]

    def composed(files, mutation=None, expected=None, clock=None):
        isolated = Parent()
        isolated.phase = "prepr"
        native = Native("prepr", WORK_END, 1)
        native.process, native.acquired, native.settled, native.code = object(), True, True, 1
        isolated.natives.append(native)
        original = Failure("synthetic-prepr") if expected is None else expected
        calls = []
        records = []
        reader = PreprLedger("/repo", candidate, files, (lambda: 0) if clock is None else clock, records)

        def original_command(label, end):
            calls.append((label, end))
            files.install()
            if mutation is not None:
                mutation(files, native)
            freeze_failure(isolated, original, native)
            raise original

        isolated.original = original_command
        try:
            prepr_execute(isolated, reader)  # Actual prepare→original command→observe→independent close body.
        except BaseException as error:
            if error is not original:
                raise
        else:
            raise Failure("control-prepr-first-error")
        require(calls == [("prepr", WORK_END)], "control-prepr-literal-boundary")
        require(
            sorted(files.closed) == sorted(files.opened) and len(files.closed) == len(set(files.closed)),
            "control-prepr-all-once-close",
        )
        require(not files.closed or files.fds[files.closed[-1]][0] == "/repo", "control-prepr-root-last")
        require(
            all(flags & os.O_CLOEXEC and flags & os.O_NOFOLLOW for flags in files.flags), "control-prepr-noninherited"
        )
        before = list(files.closed)
        require(reader.close() == [] and files.closed == before, "control-prepr-no-close-retry")
        if (
            reader.observation_error is not None
            and not isinstance(reader.observation_error, Failure)
            and reader.observation_error is not files.read_failure
        ):
            raise reader.observation_error.with_traceback(reader.observation_error.__traceback__)
        for error in isolated.cleanup_errors:
            if not any(error is expected for expected in files.close_failures.values()):
                raise error.with_traceback(error.__traceback__)
        return isolated, records

    for locks in (True, False):
        parent, records = composed(Files(locks=locks))
        require(
            parent.prepr_stage == {"schema": PREPR_SCHEMA, "stage": "repository", "status": "failed"},
            "control-prepr-reader-positive",
        )
        require(all(record["closed"] and not record["unknown"] for record in records), "control-prepr-closure-positive")
    parent, _records = composed(Files(preexisting=True))
    require(parent.prepr_stage["stage"] == "unknown", "control-prepr-old-state")
    for path in ("/repo", "/repo/.git", "/repo/.git/bifrost-test-locks"):
        files = Files()
        files.replace_before = path
        parent, _records = composed(files)
        require(parent.prepr_stage["stage"] == "unknown", "control-prepr-before-replacement")
        parent, _records = composed(Files(), lambda files, _native, path=path: files.replace(path))
        require(parent.prepr_stage["stage"] == "unknown", "control-prepr-after-replacement")
    for mode in (stat.S_IFREG | 0o644, stat.S_IFLNK | 0o777, stat.S_IFDIR | 0o777, stat.S_IFDIR | 0o2755):
        files = Files()
        files.entries["/repo/.git"].st_mode = mode
        parent, _records = composed(files)
        require(parent.prepr_stage["stage"] == "unknown", "control-prepr-layout")
    for path in ("/repo/scripts/lib/pre_pr_stage_evidence.py", "/repo/.git/bifrost-test-locks"):
        for fault in ("open_failure", "stat_failure"):
            files = Files()
            setattr(files, fault, path)
            parent, _records = composed(files)
            require(parent.prepr_stage["stage"] == "unknown", "control-prepr-partial-acquisition")
    for field, value in (
        ("st_mode", stat.S_IFLNK | 0o600),
        ("st_mode", stat.S_IFREG | 0o644),
        ("st_nlink", 2),
        ("st_uid", 0),
        ("st_gid", 0),
        ("st_size", 65537),
    ):

        def changed(files, _native, field=field, value=value):
            setattr(files.entries["/repo/.git/bifrost-test-locks/pre-pr-stages.json"], field, value)

        parent, _records = composed(Files(), changed)
        require(parent.prepr_stage["stage"] == "unknown", "control-prepr-file-metadata")
    for field in ("partial", "overflow", "drift_read"):
        parent, _records = composed(Files(), lambda files, _native, field=field: setattr(files, field, True))
        require(parent.prepr_stage["stage"] == "unknown", "control-prepr-read-refusal")
    for original in (Control(), SystemExit(), KeyboardInterrupt()):
        parent, _records = composed(Files(), expected=original)
        require(parent.prepr_stage is None, "control-prepr-control-ineligible")
    for field, value in (("code", 0), ("unknown", True), ("settled", False), ("acquired", False)):
        parent, _records = composed(
            Files(), lambda _files, native, field=field, value=value: setattr(native, field, value)
        )
        require(parent.prepr_stage is None, "control-prepr-native-ineligible")
    moments = [0]

    def expired(_files, _native):
        moments[0] = WORK_END + 1

    parent, _records = composed(Files(), expired, clock=lambda: moments[0])
    require(parent.prepr_stage is None, "control-prepr-expired")
    for secondary in (Control(), SystemExit(), KeyboardInterrupt()):

        def interrupted(files, _native, secondary=secondary):
            files.read_failure = secondary
            files.close_failures["/repo/scripts/lib/pre_pr_stage_evidence.py"] = secondary
            files.close_failures["/repo/.git"] = secondary

        parent, records = composed(Files(), interrupted)
        require(
            parent.prepr_stage["stage"] == "unknown" and parent.cleanup_errors == [secondary, secondary],
            "control-prepr-secondary-retention",
        )
        require(sum(record["unknown"] for record in records) == 2, "control-prepr-close-unknown")

    def helper_drift(files, _native):
        path = "/repo/scripts/lib/pre_pr_stage_evidence.py"
        files.data[path] = b"x" + helper_bytes[1:]

    parent, _records = composed(Files(), helper_drift)
    require(parent.prepr_stage["stage"] == "unknown", "control-prepr-helper-hash-drift")
    parent, _records = composed(
        Files(), lambda files, _native: files.replace("/repo/scripts/lib/pre_pr_stage_evidence.py")
    )
    require(parent.prepr_stage["stage"] == "unknown", "control-prepr-helper-path-drift")


def publication_diagnostic_controls():
    # Inert data-only bindings exercise the same public body, never real files or inherited stderr.
    class PublicOS:
        def __init__(self, fault=None, primary=None, close_error=None):
            self.fault, self.primary, self.close_error = fault, primary, close_error
            self.mode = 0o600  # Restrictive synthetic acquisition umask; only fchmod may change it.
            self.events = []
            self.data = bytearray()
            self.stats = 0

        def fail(self, stage):
            if self.fault == stage and self.primary is not None:
                raise self.primary

        def open(self, _path, flags, mode):
            require(flags == os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, "control-public-flags")
            require(mode == 0o644, "control-public-open-mode")
            self.events.append("open")
            self.fail("open")
            return 11

        def value(self, inode=2):
            return type(
                "SyntheticPublicStat",
                (),
                {
                    "st_dev": 1,
                    "st_ino": inode,
                    "st_mode": (stat.S_IFDIR if self.fault == "type" else stat.S_IFREG) | self.mode,
                    "st_nlink": 2 if self.fault == "links" else 1,
                },
            )()

        def fstat(self, fd):
            require(fd == 11, "control-public-fd")
            self.events.append("fstat")
            self.fail("fstat")
            self.stats += 1
            return self.value(3 if self.fault == "fd_identity" and self.stats > 1 else 2)

        def stat(self, _path, *, follow_symlinks):
            require(follow_symlinks is False, "control-public-no-follow")
            self.events.append("stat")
            return self.value(3 if self.fault == "path_identity" else 2)

        def fchmod(self, fd, mode):
            require(fd == 11 and mode == 0o644 and not self.data, "control-public-fchmod-fd-before-bytes")
            self.events.append("fchmod")
            self.fail("fchmod")
            if self.fault != "mode":
                self.mode = mode

        def write(self, fd, raw):
            require(fd == 11 and self.mode == 0o644, "control-public-write-mode")
            self.events.append("write")
            self.fail("write")
            self.data.extend(raw)
            return len(raw)

        def fsync(self, fd):
            require(fd == 11, "control-public-sync")
            self.events.append("fsync")
            self.fail("fsync")

        def close(self, fd):
            require(fd == 11, "control-public-close")
            self.events.append("close")
            if self.close_error is not None:
                raise self.close_error

    path = Path("/inert-public/source.txt")
    actual = PublicOS()
    records = []
    publish_body(path, b"safe", 4, 10, actual, lambda: 0, records)
    require(
        actual.data == b"safe"
        and actual.mode == 0o644
        and actual.events.count("fchmod") == 1
        and actual.events.count("close") == 1
        and records[0]["closed"]
        and not records[0]["unknown"],
        "control-public-positive",
    )
    for fault in ("type", "links", "path_identity", "fd_identity", "mode"):
        actual, records = PublicOS(fault), []
        try:
            publish_body(path, b"safe", 4, 10, actual, lambda: 0, records)
        except Failure:
            pass
        else:
            raise Failure("control-public-guard")
        require(not actual.data and actual.events.count("close") == 1, "control-public-refused-before-write")
    for fault in ("open", "fstat", "fchmod", "write", "fsync"):
        for primary in (Control(), SystemExit(), KeyboardInterrupt()):
            secondary = Control()
            actual, records = PublicOS(fault, primary, secondary), []
            try:
                publish_body(path, b"safe", 4, 10, actual, lambda: 0, records)
            except BaseException as error:
                if error is not primary:
                    raise
            else:
                raise Failure("control-public-primary")
            require(
                actual.events.count("close") == int(fault != "open") and records[0]["unknown"] is (fault != "open"),
                "control-public-independent-close",
            )
    primary = Control()
    actual, records = PublicOS(close_error=primary), []
    try:
        publish_body(path, b"safe", 4, 10, actual, lambda: 0, records)
    except BaseException as error:
        if error is not primary:
            raise
    else:
        raise Failure("control-public-close-fault")
    require(actual.events.count("close") == 1 and records[0]["unknown"], "control-public-no-close-retry")
    for rejected in (Path("/inert-public/private-raw.txt"), path):
        actual, records = PublicOS(), []
        try:
            publish_body(
                rejected, b"safe", 4, 10, actual, lambda rejected=rejected: 20 if rejected == path else 0, records
            )
        except Failure:
            pass
        else:
            raise Failure("control-public-admission")
        require(not actual.events, "control-public-no-acquisition")

    for error, kind in (
        (Control(), "control"),
        (Failure("private payload must not appear"), "guard"),
        (OSError("private payload must not appear"), "os"),
        (SystemExit("private payload must not appear"), "system_exit"),
        (KeyboardInterrupt(), "interrupt"),
        (RuntimeError("private payload must not appear"), "other"),
    ):
        isolated = Parent()
        isolated.phase = "prepr"
        freeze_failure(isolated, error)
        require(isolated.failure_snapshot["exception_class"] == kind, "control-diagnostic-class")
        raw = diagnostic_bytes(isolated.failure_snapshot)
        require(len(raw) <= 512 and raw.isascii() and b"private payload" not in raw, "control-diagnostic-privacy")

    class FailingSelector:
        def __init__(self, error):
            self.error = error

        def select(self, _timeout):
            raise self.error

    for primary in (Failure("synthetic-capture"), Control(), SystemExit(), KeyboardInterrupt()):
        isolated = Parent()
        isolated.phase = "frontend"
        failed = Native("prepr", WORK_END, 1)
        failed.acquired = True
        failed.process = object()
        failed.selector = FailingSelector(primary)
        failed.code = 7  # Synthetic previously observed status, never a new poll.
        awaited = Native("target", WORK_END, 1)
        isolated.natives = [failed, awaited]
        try:
            isolated.wait(awaited)  # Actual poll_all -> actual failing child pump -> wait unwind.
        except BaseException as error:
            if error is not primary:
                raise
        else:
            raise Failure("control-diagnostic-actual-child")
        value = isolated.failure_snapshot
        require(
            value["operation"] == "prepr" and value["exit_code"] == 7 and value["native_settled"] is False,
            "control-diagnostic-child-association",
        )
        failed.code, failed.settled = 0, True
        isolated.phase = "cleanup"
        freeze_failure(isolated, Control(), awaited)
        require(isolated.failure_snapshot is value and value["exit_code"] == 7, "control-diagnostic-first-snapshot")
    for partial in (False, True):
        isolated = Parent()
        selected = Native("prepr", WORK_END, 1)
        selected.process = object() if partial else None
        isolated.natives = [selected]
        freeze_failure(isolated, OSError(), selected)
        require(
            isolated.failure_snapshot["exit_code"] is None and isolated.failure_snapshot["native_settled"] is None,
            "control-diagnostic-unmeasured",
        )
    for foreign in (None, Native("target", WORK_END, 1)):
        isolated = Parent()
        isolated.natives = [failed]  # A completed previous native is not the new local guard's cause.
        freeze_failure(isolated, Failure("synthetic-local"), foreign)
        require(
            all(isolated.failure_snapshot[key] is None for key in ("operation", "exit_code", "native_settled")),
            "control-diagnostic-local-null",
        )
    selected = Native("prepr", WORK_END, 1)
    selected.process, selected.acquired, selected.code, selected.unknown = object(), True, 0, True
    isolated = Parent()
    isolated.natives = [selected]
    freeze_failure(isolated, Failure("synthetic-custody"), selected)
    require(
        isolated.failure_snapshot["exit_code"] == 0 and isolated.failure_snapshot["native_settled"] is False,
        "control-diagnostic-zero-not-settled",
    )
    baseline = {**isolated.failure_snapshot, "operation": None, "exit_code": None, "native_settled": None}
    for drift in (
        {"operation": "foreign"},
        {"exit_code": True},
        {"operation": "prepr", "exit_code": 2**31},
        {"operation": "prepr", "exit_code": -(2**31) - 1},
        {"native_settled": 1},
        {"phase": "unknown"},
        {"exception_class": "private-class-name"},
        {"schema": "unknown"},
        {"extra": "private"},
        {"operation": None, "exit_code": 1},
    ):
        try:
            diagnostic_bytes({**baseline, **drift})
        except Failure:
            pass
        else:
            raise Failure("control-diagnostic-grammar")
    for label in NATIVE_LABELS:
        for code in (-(2**31), 2**31 - 1):
            raw = diagnostic_bytes({**baseline, "operation": label, "exit_code": code, "native_settled": False})
            require(len(raw) <= 512 and raw.isascii(), "control-diagnostic-bound-positive")

    class DiagnosticOS:
        def __init__(self, fault=None, primary=None, restore_error=None):
            self.fault, self.primary, self.restore_error = fault, primary, restore_error
            self.events = []
            self.writes = []

        def get_blocking(self, fd):
            require(fd == 2, "control-diagnostic-inherited-fd")
            self.events.append("get")
            if self.fault == "get":
                raise self.primary
            return True

        def set_blocking(self, fd, blocking):
            require(fd == 2 and type(blocking) is bool, "control-diagnostic-blocking-fd")
            self.events.append(("set", blocking))
            if blocking and self.restore_error is not None:
                raise self.restore_error
            if not blocking and self.fault == "set":
                raise self.primary

        def write(self, fd, raw):
            require(fd == 2, "control-diagnostic-write-fd")
            self.events.append("write")
            self.writes.append(raw)
            if self.fault == "write":
                raise self.primary
            return len(raw) - 1 if self.fault == "partial" else len(raw)

    for predicate in (
        "builder-identity",
        "builder-label",
        "builder-command",
        "builder-source-image",
        "builder-current-custody",
        "builder-security",
        "builder-mounts",
        "builder-state",
        "builder-terminal",
    ):
        isolated = Parent()
        isolated.phase = "build"
        primary = Failure(predicate)
        freeze_failure(isolated, primary)
        record = isolated.build_guard
        require(record == {"schema": BUILD_GUARD_SCHEMA, "predicate": predicate}, "control-build-exact-predicate")
        before = isolated.failure_snapshot
        raw = diagnostic_bytes(before) + build_guard_bytes(record)
        actual = DiagnosticOS()
        emit_diagnostic(before, 10, actual, lambda: 0, build=record)
        require(
            actual.writes == [raw]
            and len(raw) <= 512
            and actual.events == ["get", ("set", False), "write", ("set", True)],
            "control-build-one-combined-write",
        )
        isolated.phase = "cleanup"
        freeze_failure(isolated, Failure("builder-security"))
        require(
            isolated.failure_snapshot is before
            and isolated.failure_error is primary
            and isolated.build_guard is record,
            "control-build-first-predicate",
        )

    class PrivatePayload:
        def __str__(self):
            raise Control()

    class ForeignFailure(Failure):
        pass

    for primary in (
        Failure("private payload"),
        Failure(),
        Failure("builder-identity", "private payload"),
        Failure(True),
        Failure(PrivatePayload()),
        ForeignFailure("builder-identity"),
    ):
        isolated = Parent()
        isolated.phase = "build"
        freeze_failure(isolated, primary)
        require(
            isolated.failure_error is primary
            and isolated.build_guard == {"schema": BUILD_GUARD_SCHEMA, "predicate": "unknown"}
            and b"private payload" not in build_guard_bytes(isolated.build_guard),
            "control-build-unknown-private",
        )
    for primary in (Control(), SystemExit(), KeyboardInterrupt()):
        isolated = Parent()
        isolated.phase = "build"
        freeze_failure(isolated, primary)
        require(isolated.failure_error is primary and isolated.build_guard is None, "control-build-error-class")
    for phase in FAILURE_PHASES - {"build"}:
        isolated = Parent()
        isolated.phase = phase
        freeze_failure(isolated, Failure("builder-identity"))
        require(isolated.build_guard is None, "control-build-phase")
    for owned in (False, True):
        isolated = Parent()
        isolated.phase = "build"
        selected = Native("checks.build", WORK_END, 1)
        isolated.natives = [selected] if owned else []
        freeze_failure(isolated, Failure("builder-identity"), selected)
        require(isolated.build_guard is None, "control-build-no-native")
    build = {"schema": BUILD_GUARD_SCHEMA, "predicate": "builder-current-custody"}
    for mutation, label in (
        ({"schema": "unknown"}, "build-guard-schema"),
        ({"predicate": "private payload"}, "build-guard-predicate"),
        ({"predicate": True}, "build-guard-predicate"),
        ({"extra": "private payload"}, "closed-keys"),
    ):
        try:
            build_guard_bytes({**build, **mutation})
        except Failure as error:
            if error.args != (label,):
                raise
        else:
            raise Failure("control-build-record-grammar")
    build_value = {**baseline, "phase": "build", "exception_class": "guard"}
    for optional in (build, {"schema": "unknown"}, {**build, "predicate": PrivatePayload()}):
        actual = DiagnosticOS()
        emit_diagnostic(build_value, 10, actual, lambda: 0, build=optional)
        expected_raw = diagnostic_bytes(build_value) + (build_guard_bytes(build) if optional is build else b"")
        require(
            actual.writes == [expected_raw] and actual.events == ["get", ("set", False), "write", ("set", True)],
            "control-build-optional-primary-delivery",
        )
    for value in (
        baseline,
        {**build_value, "operation": "checks.build", "exit_code": 0, "native_settled": True},
        {**build_value, "exception_class": "other"},
    ):
        actual = DiagnosticOS()
        emit_diagnostic(value, 10, actual, lambda: 0, build=build)
        require(actual.writes == [diagnostic_bytes(value)], "control-build-render-association")

    actual = DiagnosticOS()
    emit_diagnostic(baseline, 10, actual, lambda: 0)
    require(
        len(actual.writes) == 1 and actual.events == ["get", ("set", False), "write", ("set", True)],
        "control-diagnostic-one-write",
    )
    stage = {"schema": PREPR_SCHEMA, "stage": "repository", "status": "failed"}
    for optional in (stage, {"schema": "unknown"}, None):
        actual = DiagnosticOS()
        emit_diagnostic(baseline, 10, actual, lambda: 0, optional)
        expected_raw = diagnostic_bytes(baseline) + (prepr_stage_bytes(stage) if optional is stage else b"")
        require(
            actual.writes == [expected_raw] and actual.events == ["get", ("set", False), "write", ("set", True)],
            "control-prepr-one-combined-or-primary-write",
        )
    actual = DiagnosticOS("partial")
    try:
        emit_diagnostic(baseline, 10, actual, lambda: 0, stage)
    except Failure as error:
        if str(error) != "diagnostic-write":
            raise
    else:
        raise Failure("control-prepr-combined-partial")
    require(len(actual.writes) == 1 and actual.events[-1] == ("set", True), "control-prepr-combined-no-retry")
    for fault in ("get", "set", "write"):
        for primary in (BlockingIOError(), Control(), SystemExit(), KeyboardInterrupt()):
            actual = DiagnosticOS(fault, primary, Control())
            try:
                emit_diagnostic(baseline, 10, actual, lambda: 0)
            except BaseException as error:
                if error is not primary:
                    raise
            else:
                raise Failure("control-diagnostic-first-error")
            require(len(actual.writes) == int(fault == "write"), "control-diagnostic-no-retry")
            require(
                ("set", True) in actual.events if fault != "get" else len(actual.events) == 1,
                "control-diagnostic-restore",
            )
    actual = DiagnosticOS("partial")
    try:
        emit_diagnostic(baseline, 10, actual, lambda: 0)
    except Failure:
        pass
    else:
        raise Failure("control-diagnostic-partial-write")
    require(len(actual.writes) == 1 and actual.events[-1] == ("set", True), "control-diagnostic-partial-restore")
    actual = DiagnosticOS()
    try:
        emit_diagnostic(baseline, 10, actual, lambda: 20)
    except Failure:
        pass
    else:
        raise Failure("control-diagnostic-expiry")
    require(not actual.events, "control-diagnostic-expired-no-write")
    for times, expected_writes in (((0, 0, 20), 0), ((0, 0, 0, 20), 1)):
        actual = DiagnosticOS()
        moments = iter(times)
        try:
            emit_diagnostic(baseline, 10, actual, lambda moments=moments: next(moments))
        except Failure:
            pass
        else:
            raise Failure("control-diagnostic-inflight-expiry")
        require(
            len(actual.writes) == expected_writes and actual.events[-1] == ("set", True),
            "control-diagnostic-expired-independent-restore",
        )
    primary = Control()
    actual = DiagnosticOS(restore_error=primary)
    try:
        emit_diagnostic(baseline, 10, actual, lambda: 0)
    except BaseException as error:
        if error is not primary:
            raise
    else:
        raise Failure("control-diagnostic-restoration-fault")
    require(len(actual.writes) == 1, "control-diagnostic-restoration-no-replay")


def source_controls():
    """Inert same-helper drift controls; no subprocess/OS mutation in these cases."""
    prepr_stage_controls()
    prepr_reader_controls()
    publication_diagnostic_controls()
    expected = Failure("synthetic")
    actual = Control()
    require(first_error(expected, actual) is expected and first_error(None, actual) is actual, "control-first")
    handles = []

    def closer(fd):
        handles.append(fd)
        if fd == 2:
            raise actual

    first, unknown, failures = close_handles([1, 2, 3], closer, expected)
    for caught in failures:
        if caught is not actual:
            raise caught.with_traceback(caught.__traceback__)
    if first is not expected:
        raise first.with_traceback(first.__traceback__)
    require(first is expected and unknown is True and handles == [1, 2, 3], "control-independent-close")

    # These synthetic retained objects never touch a real selector, process or FD.
    class RetainedObject:
        def __init__(self, label, calls, error=None):
            self.label, self.calls, self.error = label, calls, error
            self.registered = [0]  # One registered pipe; the second EOF remains genuinely unobserved.

        def close(self):
            self.calls.append(self.label)
            if self.error is not None:
                raise self.error

    class RetainedProcess:
        def __init__(self, calls, error=None):
            self.calls, self.error = calls, error

        def wait(self, timeout):
            self.calls.append(("wait", timeout))
            if self.error is not None:
                raise self.error
            return 0

    for selector_present, capture_present in ((False, False), (True, False), (True, True)):
        calls = []
        partial = Native("synthetic-partial", 10, 3)
        partial.unknown = True
        partial.process = RetainedProcess(calls)
        partial.group = 7
        partial.pipes = [RetainedObject("stdout", calls), RetainedObject("stderr", calls)]
        if selector_present:
            partial.selector = RetainedObject("selector", calls)
        if capture_present:
            partial.files[0] = 11
        try:
            Parent().pump(partial)
        except Failure as error:
            require(str(error) == "native-acquisition", "control-partial-acquisition-kind")
        else:
            raise Failure("control-partial-pump")

        def absent_group(group, value, calls=calls):
            calls.append(("group", group, value))
            raise ProcessLookupError

        errors = failed_native_cleanup(
            partial, 10, lambda fd, calls=calls: calls.append(("fd", fd)), absent_group, lambda: 0
        )
        for error in errors:
            if not isinstance(error, Failure) or str(error) != "native-group-or-fd":
                raise error.with_traceback(error.__traceback__)
        expected_calls = ["stdout", "stderr"]
        if capture_present:
            expected_calls.append(("fd", 11))
        if selector_present:
            expected_calls.append("selector")
        expected_calls += [("wait", 10), ("group", 7, 0)]
        require(calls == expected_calls and len(errors) == 1, "control-partial-close")
        require(
            partial.closed and partial.unknown and not partial.settled and partial.eof == [False, False],
            "control-partial-facts",
        )
        # Consumed retained handles and attempted wait cannot be retried after an unknown outcome.
        close_native_handles(partial, lambda fd, calls=calls: calls.append(("retry", fd)))
        native_terminal(partial, 0, absent_group, None)
        require(calls == expected_calls, "control-partial-no-retry")

    calls = []
    pipe_error, fd_error, selector_error, wait_error = Control(), SystemExit(), KeyboardInterrupt(), Control()
    partial = Native("synthetic-close-errors", 10, 3)
    partial.process = RetainedProcess(calls, wait_error)
    partial.group = 7
    partial.pipes = [RetainedObject("stdout", calls, pipe_error), RetainedObject("stderr", calls)]
    partial.files = [11, 12]
    partial.selector = RetainedObject("selector", calls, selector_error)

    def close_capture(fd):
        calls.append(("fd", fd))
        if fd == 11:
            raise fd_error

    def absent_error_group(group, value):
        calls.append(("group", group, value))
        raise ProcessLookupError

    errors = failed_native_cleanup(partial, 10, close_capture, absent_error_group, lambda: 0)
    for error in errors:
        if not any(error is selected for selected in (pipe_error, fd_error, selector_error, wait_error)):
            raise error.with_traceback(error.__traceback__)
    require(
        len(errors) == 4
        and errors[0] is pipe_error
        and errors[1] is fd_error
        and errors[2] is selector_error
        and errors[3] is wait_error
        and calls == ["stdout", "stderr", ("fd", 11), ("fd", 12), "selector", ("wait", 10), ("group", 7, 0)],
        "control-partial-independent-errors",
    )
    require(partial.unknown and not partial.settled, "control-partial-unknown")
    repeated_calls = list(calls)
    repeated_errors = failed_native_cleanup(partial, 10, close_capture, absent_error_group, lambda: 0)
    for error in repeated_errors:
        if error is not pipe_error:
            raise error.with_traceback(error.__traceback__)
    require(
        calls == repeated_calls and not partial.settled and partial.unknown and repeated_errors == [pipe_error],
        "control-partial-retained-errors",
    )
    # Scoped inert underlying bindings exercise the unchanged launch retention path itself.
    for fault in ("selector_create", "second_register", "second_open", "returned_fd_fstat"):
        launch_fault = Failure("synthetic-acquisition-" + fault)
        events = []
        returned = {}
        isolated = Parent()
        isolated.private = Path("/inert-claim-private")
        acquisition_argv = next(args for label, args, _stage in ORIGINAL_COMMANDS if label == "source.root")

        class LaunchPipe:
            def __init__(self, number, events=events):
                self.number, self.events = number, events

            def fileno(self):
                return self.number

            def close(self):
                self.events.append(("pipe_close", self.number))

        class LaunchProcess:
            def __init__(self, events=events, pipe_class=LaunchPipe):
                self.pid = 7001
                self.events = events
                self.stdout, self.stderr = pipe_class(11), pipe_class(12)

            def wait(self, timeout):
                self.events.append(("wait", timeout))
                return 0

        class LaunchSelector:
            def __init__(self, events=events, fault=fault, launch_fault=launch_fault):
                self.registered = []
                self.events, self.fault, self.error = events, fault, launch_fault

            def register(self, pipe, _kind, index):
                self.events.append(("register", index))
                if self.fault == "second_register" and index == 1:
                    raise self.error
                self.registered.append(pipe)

            def close(self):
                self.events.append(("selector_close",))

        def inert_popen(
            argv,
            acquisition_argv=acquisition_argv,
            returned=returned,
            events=events,
            process_class=LaunchProcess,
            **options,
        ):
            require(
                tuple(argv) == acquisition_argv
                and options["start_new_session"] is True
                and options["stdout"] == subprocess.PIPE
                and options["stderr"] == subprocess.PIPE,
                "control-acquisition-argv",
            )
            process = process_class()
            returned["process"] = process
            events.append(("popen",))
            return process

        def inert_selector(
            events=events, fault=fault, launch_fault=launch_fault, returned=returned, selector_class=LaunchSelector
        ):
            events.append(("selector_create",))
            if fault == "selector_create":
                raise launch_fault
            result = selector_class()
            returned["selector"] = result
            return result

        def inert_blocking(fd, blocking, events=events):
            require(fd in (11, 12) and blocking is False, "control-acquisition-blocking")
            events.append(("blocking", fd))

        def inert_open(
            path,
            flags,
            mode,
            isolated=isolated,
            returned=returned,
            events=events,
            fault=fault,
            launch_fault=launch_fault,
        ):
            require(path.parent == isolated.private and mode == 0o600, "control-acquisition-open")
            require(flags == os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, "control-acquisition-flags")
            calls = returned.setdefault("opened", [])
            events.append(("open", len(calls)))
            if fault == "second_open" and calls:
                raise launch_fault
            fd = 41 + len(calls)
            calls.append(fd)
            return fd

        def inert_fstat(fd, events=events, fault=fault, launch_fault=launch_fault, returned=returned):
            require(fd == 41, "control-acquisition-fstat-fd")
            events.append(("fstat", fd))
            if fault == "returned_fd_fstat":
                raise launch_fault
            identity = object()
            returned["identity"] = identity
            return identity

        def inert_close(fd, events=events):
            events.append(("fd_close", fd))

        def inert_kill(group, number, events=events):
            require(group == 7001, "control-acquisition-group")
            events.append(("group", number))
            if number == 0:
                raise ProcessLookupError

        bindings = (
            (subprocess, "Popen", inert_popen),
            (selectors, "DefaultSelector", inert_selector),
            (os, "set_blocking", inert_blocking),
            (os, "open", inert_open),
            (os, "fstat", inert_fstat),
            (os, "close", inert_close),
            (os, "killpg", inert_kill),
        )
        retained_bindings = []
        first, first_traceback = None, None
        try:
            for module, name, replacement in bindings:
                retained_bindings.append((module, name, getattr(module, name), replacement))
                setattr(module, name, replacement)
            try:
                isolated.launch("source.root", list(acquisition_argv), WORK_END)
            except BaseException as error:
                if error is not launch_fault:
                    raise
                launch_traceback = error.__traceback__
            else:
                raise Failure("control-acquisition-fault")
            require(len(isolated.natives) == 1 and isolated.calls == {"source.root"}, "control-acquisition-retained")
            retained = isolated.natives[0]
            require(
                isolated.failure_snapshot["operation"] == "source.root"
                and isolated.failure_snapshot["exit_code"] is None
                and isolated.failure_snapshot["native_settled"] is None,
                "control-acquisition-diagnostic",
            )
            process = returned["process"]
            require(
                retained.process is process
                and retained.pipes == [process.stdout, process.stderr]
                and retained.group == process.pid
                and retained.selector is returned.get("selector")
                and not retained.acquired
                and retained.unknown,
                "control-acquisition-object-retention",
            )
            opened = returned.get("opened", [])
            require(retained.files == ([41, None] if opened else [None, None]), "control-acquisition-fd-retention")
            if fault == "second_register":
                require(retained.selector.registered == [process.stdout], "control-acquisition-registration")
            if fault == "returned_fd_fstat":
                require(
                    len(isolated.private_files) == 1 and isolated.private_files[0][1] is None,
                    "control-acquisition-unknown-stat",
                )
            elif fault == "second_open":
                require(
                    len(isolated.private_files) == 2
                    and isolated.private_files[0][1] is returned["identity"]
                    and isolated.private_files[1][1] is None,
                    "control-acquisition-stat-retention",
                )
            settlement_start = time.monotonic()
            isolated.settle_failure()
            isolated.close_failed_natives()
            settlement_end = time.monotonic()
            for error in isolated.cleanup_errors:
                if not isinstance(error, Failure) or str(error) not in ("native-group-or-fd", "native-unsettled"):
                    raise error.with_traceback(error.__traceback__)
            expected_closes = [("pipe_close", 11), ("pipe_close", 12)]
            if opened:
                expected_closes.append(("fd_close", 41))
            if fault != "selector_create":
                expected_closes.append(("selector_close",))
            actual_closes = [event for event in events if event[0] in ("pipe_close", "fd_close", "selector_close")]
            require(actual_closes == expected_closes, "control-acquisition-independent-closure")
            waits = [event for event in events if event[0] == "wait"]
            require(
                len(waits) == 1
                and max(0, CLEANUP_END - settlement_end) <= waits[0][1] <= max(0, CLEANUP_END - settlement_start),
                "control-acquisition-reap",
            )
            require(
                [event for event in events if event[0] == "group"]
                == [("group", signal.SIGTERM), ("group", signal.SIGKILL), ("group", 0)],
                "control-acquisition-group-attempts",
            )
            require(
                retained.closed
                and retained.reap_attempted
                and retained.group_absent
                and retained.unknown
                and not retained.settled
                and retained.eof == [False, False]
                and launch_fault.__traceback__ is launch_traceback,
                "control-acquisition-first-and-facts",
            )
            prior_events = list(events)
            isolated.close_failed_natives()
            require(events == prior_events, "control-acquisition-no-repeat")
        except BaseException as error:
            first, first_traceback = error, error.__traceback__
        finally:
            for module, name, incumbent, replacement in retained_bindings:
                try:
                    owned_binding(getattr(module, name), replacement)
                    setattr(module, name, incumbent)
                except BaseException as error:
                    if first is None:
                        first, first_traceback = error, error.__traceback__
        if first is not None:
            raise first.with_traceback(first_traceback)
    for stopped_after in (0, 1, 2):
        selected = Native("synthetic-signal-attempts", 10, 3)
        selected.group = 7
        calls = []
        secondary = Control()

        def kill_owned(group, number, calls=calls, secondary=secondary):
            calls.append((group, number))
            if number == signal.SIGTERM:
                raise secondary

        numbers = (signal.SIGTERM, signal.SIGKILL)
        for number in numbers[:stopped_after]:
            error = signal_native(selected, number, kill_owned, 10, lambda: 0)
            if error is not None and error is not secondary:
                raise error.with_traceback(error.__traceback__)
        for number in numbers:
            error = signal_native(selected, number, kill_owned, 10, lambda: 0)
            if error is not None and error is not secondary:
                raise error.with_traceback(error.__traceback__)
        for number in numbers:
            require(
                signal_native(selected, number, kill_owned, 10, lambda: 20) is None, "control-signal-repeat-expired"
            )
        require(
            calls == [(7, signal.SIGTERM), (7, signal.SIGKILL)] and selected.signal_attempts == set(numbers),
            "control-signal-independent",
        )
    selected = Native("synthetic-proven-group-absent", 10, 3)
    selected.group = 7
    selected.group_absent = True
    selected.unknown = True  # An unrelated close failure must not invalidate the actual absence observation.
    calls = []
    for number in (signal.SIGTERM, signal.SIGKILL):
        require(
            signal_native(selected, number, lambda group, value: calls.append((group, value)), 10, lambda: 20) is None,
            "control-signal-absent-error",
        )
    require(not calls and not selected.signal_attempts, "control-signal-proven-absent")
    selected = Native("synthetic-expired-signal", 10, 3)
    selected.group = 7
    calls = []
    selected.pipes = [RetainedObject("stdout", calls), RetainedObject("stderr", calls)]
    selected.files = [11, None]
    selected.process = RetainedProcess(calls)
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            signal_native(selected, number, lambda group, value: calls.append((group, value)), 10, lambda: 20)
        except Failure as error:
            require(str(error) == "native-signal-deadline", "control-expired-signal-kind")
        else:
            raise Failure("control-expired-signal")
    require(not calls and not selected.signal_attempts, "control-expired-signal-dispatch")

    def expired_group(group, value):
        calls.append(("group", group, value))
        raise ProcessLookupError

    errors = failed_native_cleanup(selected, 10, lambda fd: calls.append(("fd", fd)), expired_group, lambda: 20)
    for error in errors:
        if not isinstance(error, Failure) or str(error) != "native-terminal-deadline":
            raise error.with_traceback(error.__traceback__)
    require(
        calls == ["stdout", "stderr", ("fd", 11), ("wait", 0), ("group", 7, 0)]
        and len(errors) == 1
        and selected.closed
        and selected.unknown
        and not selected.settled,
        "control-expired-independent-close",
    )
    original = Failure("synthetic-original")
    try:
        raise original
    except BaseException as error:
        if error is not original:
            raise
        original_traceback = error.__traceback__
    for secondary in (Control(), SystemExit(), KeyboardInterrupt()):
        cleanup_parent = Parent()

        def settlement_error(secondary=secondary):
            raise secondary

        cleanup_attempt(cleanup_parent, settlement_error)
        for error in cleanup_parent.cleanup_errors:
            if error is not secondary:
                raise error.with_traceback(error.__traceback__)
        require(
            first_error(original, cleanup_parent.cleanup_errors[0]) is original
            and original.__traceback__ is original_traceback,
            "control-settlement-original",
        )
        later = []
        cleanup_attempt(cleanup_parent, lambda later=later: later.append("cleanup"))
        cleanup_attempt(cleanup_parent, lambda later=later: later.append("restore"))
        require(later == ["cleanup", "restore"], "control-settlement-independent")
    for end in (1, 2):
        phase_admit(end, lambda: 0, "file-deadline")
        try:
            phase_admit(end, lambda: 3, "file-deadline")
        except Failure as error:
            require(str(error) == "file-deadline", "control-phase-kind")
        else:
            raise Failure("control-phase-expiry")
    for bad in (0, -1, True, 5):
        try:
            progress_write(lambda _fd, _raw, bad=bad: bad, None, b"abc", 10, lambda: 0)
        except Failure as error:
            require(str(error) == "write-progress", "control-progress-kind")
        else:
            raise Failure("control-progress")
    try:
        progress_write(lambda _fd, _raw: 1, None, b"x", 0, lambda: 0)
    except Failure as error:
        require(str(error) == "write-deadline", "control-deadline-kind")
    else:
        raise Failure("control-deadline")
    good = {
        "schema": WIRE,
        "phase": "before",
        "seq": 1,
        "container_pid": 7,
        "host": "pgbouncer",
        "port": 5432,
        "source_role": "original_pooled_constructor",
    }
    raw = (json.dumps(good, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    require(received_frame(raw, "before", 1) == good, "control-frame-positive")
    for bad in (
        (json.dumps(good) + "\n").encode(),
        raw[:-1] + b"\r\n",
        raw.replace(b'"pgbouncer"', rb'"pg\u0062ouncer"'),
    ):
        try:
            received_frame(bad, "before", 1)
        except Failure as error:
            require(str(error) == "wire-canonical", "control-frame-canonical-kind")
        else:
            raise Failure("control-frame-canonical")
    for mutation in ({**good, "seq": True}, {**good, "seq": 2}, {**good, "host": "bad@host"}, {**good, "extra": 1}):
        try:
            received_frame((json.dumps(mutation, separators=(",", ":")) + "\n").encode(), "before", 1)
        except Failure:
            pass
        else:
            raise Failure("control-frame-drift")
    for bad in (raw[:-1], raw + b"{}\n", b"x" * 1025):
        try:
            received_frame(bad, "before", 1)
        except Failure:
            pass
        else:
            raise Failure("control-frame-bound")
    tree = b"PID PPID\n10 1\n11 10\n12 11\n"
    require(process_tree(tree, 10, 12)[12] == 11, "control-tree-positive")
    for bad_peer, bad in ((13, tree), (12, b"PID PPID\n10 1\n11 12\n12 11\n")):
        try:
            process_tree(bad, 10, bad_peer)
        except Failure:
            pass
        else:
            raise Failure("control-peer")
    for credentials in ((12, 0, 1000), (12, 1000, 0), (99, 1000, 1000)):
        try:
            peer_identity(credentials, tree, 10)
        except Failure:
            pass
        else:
            raise Failure("control-peer-identity")
    owner = object()
    owned_binding(owner, owner)
    try:
        owned_binding(object(), owner)
    except Failure as error:
        require(str(error) == "signal-handler-foreign", "control-foreign-kind")
    else:
        raise Failure("control-foreign")
    native = Native("synthetic", 10, 3)
    native.files = [1, 2]
    parent = Parent()
    writes = []
    capture_chunk(parent, native, 0, b"abc", lambda fd, data: writes.append((fd, data)) or len(data), lambda: 0)
    require(
        bytes(native.output[0]) == b"abc" and writes == [(1, b"abc")] and parent.capture_bytes == 3,
        "control-capture-positive",
    )
    try:
        capture_chunk(parent, native, 0, b"x", lambda _fd, data: len(data), lambda: 0)
    except Failure as error:
        require(str(error) == "stream-bound", "control-capture-kind")
    else:
        raise Failure("control-capture-overflow")
    parent.capture_bytes = CAPTURE_LIMIT
    try:
        capture_chunk(parent, native, 1, b"x", lambda _fd, data: len(data), lambda: 0)
    except Failure as error:
        require(str(error) == "capture-bound", "control-aggregate-kind")
    else:
        raise Failure("control-aggregate")
    partial = Native("synthetic-partial", 10, 3)
    capture_chunk(Parent(), partial, 0, b"a", lambda _fd, data: len(data), lambda: 0)
    require(partial.unknown is True, "control-partial-acquisition")
    complete = Native("synthetic-complete", 10, 3)
    complete.eof = [True, True]
    native_result(complete, True, None)
    require(complete.settled is True, "control-native-positive")
    for group, ends in ((False, [True, True]), (True, [True, False])):
        bad = Native("synthetic-unsettled", 10, 3)
        bad.eof = ends
        try:
            native_result(bad, group, None)
        except Failure as error:
            require(str(error) == "native-group-or-fd", "control-native-kind")
        else:
            raise Failure("control-native-settlement")
    for unexpected in (Control(), SystemExit(), KeyboardInterrupt()):
        try:
            native_result(complete, True, unexpected)
        except BaseException as error:
            if error is not unexpected:
                raise
        else:
            raise Failure("control-native-first-error")

    class FinalGateParent:
        def __init__(self, drift=None, grant_error=None):
            self.front_before = {"measured": "same"}
            self.front_after = dict(self.front_before)
            self.front_complete = False
            self.peer_credentials = (12, 1000, 1000)
            self.runner = {
                "Id": "a" * 64,
                "Image": "sha256:" + "b" * 64,
                "Config": {},
                "HostConfig": {},
                "Mounts": [],
                "NetworkSettings": {},
                "RestartCount": 0,
                "State": {
                    "Running": True,
                    "Paused": False,
                    "Restarting": False,
                    "Dead": False,
                    "Pid": 10,
                    "StartedAt": "synthetic-start",
                },
            }
            self.drift, self.grant_error = drift, grant_error
            self.grants = []

        def front(self, label, _argv, _bound, _shared):
            if label == "runner_final":
                current = {**self.runner, "State": dict(self.runner["State"])}
                if self.drift == "runner":
                    current["State"]["Running"] = False
                return json.dumps([current]).encode()
            require(label == "runner_tree_after", "control-final-label")
            return tree if self.drift != "peer" else b"PID PPID\n10 1\n11 10\n"

        def send_wire(self, phase, seq):
            self.grants.append((phase, seq))
            if self.grant_error is not None:
                raise self.grant_error

    final = FinalGateParent()
    finish_frontend(final)
    require(frontend_matched(final) and final.grants == [("after", 4)], "control-final-positive")
    for drift, kind in (("runner", "container-live"), ("peer", "tree-membership")):
        final = FinalGateParent(drift)
        try:
            finish_frontend(final)
        except Failure as error:
            require(str(error) == kind, "control-final-kind")
        else:
            raise Failure("control-final-admission")
        require(not frontend_matched(final) and not final.grants, "control-final-incomplete")
    for unexpected in (Control(), SystemExit(), KeyboardInterrupt()):
        final = FinalGateParent(grant_error=unexpected)
        try:
            finish_frontend(final)
        except BaseException as error:
            if error is not unexpected:
                raise
        else:
            raise Failure("control-final-grant")
        require(not frontend_matched(final), "control-final-grant-incomplete")
    configuration = (
        b"[databases]\n"
        b"bifrost_test=host=postgres\n"
        b"[pgbouncer]\n"
        b"auth_type=plain\n"
        b"listen_addr=0.0.0.0\n"
        b"listen_port=5432\n"
        b"client_tls_sslmode=disable\n"
    )
    strict_frontend_config(configuration, "10.0.0.2", 5432, "bifrost")
    for drift in (
        configuration.replace(b"disable", b"require"),
        configuration + b"admin_users=bifrost\n",
        configuration + b"include=other\n",
    ):
        try:
            strict_frontend_config(drift, "10.0.0.2", 5432, "bifrost")
        except Failure:
            pass
        else:
            raise Failure("control-config")
    one = "a" * 64
    require(object_set(json.dumps([{"Id": one}]).encode(), [one])[one]["Id"] == one, "control-inspect-positive")
    for objects in ([{"Id": "b" * 64}], [{"Id": one}, {"Id": one}]):
        try:
            object_set(json.dumps(objects).encode(), [one])
        except Failure:
            pass
        else:
            raise Failure("control-cleanup-census-join")
    model = {"service_name": "synthetic", "image": "synthetic:reviewed"}
    image = {
        "Id": "sha256:" + "b" * 64,
        "Config": {"Cmd": ["synthetic"], "Entrypoint": None, "Env": [], "User": "", "WorkingDir": ""},
    }
    config = {
        "Labels": {
            "com.docker.compose.project": "synthetic-project",
            "com.docker.compose.service": "synthetic",
            "com.docker.compose.oneoff": "False",
        },
        "Image": "synthetic:reviewed",
        "Cmd": ["synthetic"],
        "Entrypoint": None,
        "User": "",
        "WorkingDir": "",
        "Env": [],
        "Hostname": one[:12],
        "Domainname": "",
        "Healthcheck": None,
    }
    host = {
        "Privileged": False,
        "UsernsMode": "",
        "PidMode": "",
        "IpcMode": "private",
        "NetworkMode": "synthetic-project_default",
        "UTSMode": "",
        "CgroupParent": "",
        "PublishAllPorts": False,
        "ReadonlyRootfs": False,
    }
    container = {
        "Id": one,
        "Image": image["Id"],
        "Config": config,
        "HostConfig": host,
        "Mounts": [],
        "NetworkSettings": {
            "Ports": {},
            "Networks": {
                "synthetic-project_default": {"NetworkID": "c" * 64, "IPAddress": "10.0.0.2", "Aliases": ["synthetic"]}
            },
        },
    }
    model_container(container, model, image, "synthetic-project", Path("/synthetic"))
    for field, drift in (
        ("Privileged", True),
        ("NetworkMode", "host"),
        ("ReadonlyRootfs", True),
        ("CapAdd", ["SYS_ADMIN"]),
        ("GroupAdd", ["0"]),
    ):
        changed = {**container, "HostConfig": {**host, field: drift}}
        try:
            model_container(changed, model, image, "synthetic-project", Path("/synthetic"))
        except Failure:
            pass
        else:
            raise Failure("control-cleanup-security-guard")
    for changed in (
        {**container, "Image": "sha256:" + "d" * 64},
        {**container, "Config": {**config, "Cmd": ["foreign"]}},
        {**container, "Config": {**config, "Env": ["UNQUALIFIED=value"]}},
    ):
        try:
            model_container(changed, model, image, "synthetic-project", Path("/synthetic"))
        except Failure:
            pass
        else:
            raise Failure("control-image-default-source-join")

    def framed(prefix, records, ending):
        return (
            b"".join(
                prefix + b" " + tag.encode() + b" " + str(len(payload)).encode() + b"\n" + payload + b"\n"
                for tag, payload in records
            )
            + ending
        )

    process_records = [
        ("stat", b"1 (pgbouncer) S " + b"0 " * 18 + b"7\n"),
        ("cmdline", b"/usr/bin/pgbouncer\0/etc/pgbouncer/pgbouncer.ini\0"),
        ("exe", b"/usr/bin/pgbouncer"),
        ("cwd", b"/"),
        ("netns", b"net:[9]"),
        ("exe_bytes", b"7"),
        ("exe_sha256", b"a" * 64),
        ("sockets", b"socket:[9]\n"),
    ]
    process_raw = framed(b"bifrost-proc/v1", process_records, b"bifrost-proc-end/v1\n")
    process, sockets = parse_process(process_raw, 10)
    require(process["start_ticks"] == 7 and sockets == [9], "control-process-positive")
    for bad in (process_raw[:-1], process_raw + b"extra", process_raw.replace(b"net:[9]", b"bad:[9]")):
        try:
            parse_process(bad, 10)
        except Failure:
            pass
        else:
            raise Failure("control-process-parser")
    header = b"sl local_address rem_address st tx_queue rx_queue tr tm_when retrnsmt inode\n"
    tcp = framed(
        b"bifrost-tcp/v1",
        [("tcp", header + b"0: 00000000:1538 00000000:0000 0A 0 0 0 0 0 9\n"), ("tcp6", header)],
        b"bifrost-tcp-end/v1\n",
    )
    require(parse_listener(tcp, [9], "10.0.0.2", 5432)["socket_inode"] == 9, "control-listener-positive")
    for bad, owns in ((tcp, []), (tcp[:-1], [9]), (tcp + b"extra", [9])):
        try:
            parse_listener(bad, owns, "10.0.0.2", 5432)
        except Failure:
            pass
        else:
            raise Failure("control-listener-owner")
    metadata = (
        b"1\t2\t81a4\t1\t"
        + str(len(configuration)).encode()
        + b"\t2026-10-04 00:00:00.000000000 +0000\t2026-10-04 00:00:00.000000000 +0000"
    )
    config_raw = b"bifrost-config-read/v1\n"
    for tag, payload in (("before", metadata), ("payload", configuration), ("after", metadata)):
        config_raw += tag.encode() + b" " + str(len(payload)).encode() + b"\n" + payload + b"\n"
    config_raw += b"end\n"
    require(parse_config_readback(config_raw)[0] == configuration, "control-retained-config-positive")
    for bad in (
        config_raw[:-1],
        config_raw + b"extra",
        config_raw.replace(b"after " + str(len(metadata)).encode(), b"after 0"),
    ):
        try:
            parse_config_readback(bad)
        except Failure:
            pass
        else:
            raise Failure("control-retained-config-parser")
    for malformed in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        try:
            decode(malformed, 100)
        except Failure:
            pass
        else:
            raise Failure("control-json")


def entry():
    parent = Parent()  # Retained before any acquisition-capable initialization.
    handlers = {}

    def controlled(_number, _frame):
        raise Control()

    first = None
    traceback = None
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            handlers[number] = signal.getsignal(number)
            signal.signal(number, controlled)
        parent.acquire_private()
        parent.phase = "controls"
        source_controls()
        main_work(parent)
    except BaseException as error:
        first, traceback = error, error.__traceback__
        freeze_failure(parent, error)
        parent.phase = "cleanup"
        cleanup_attempt(parent, parent.settle_failure)
    try:
        main_cleanup(parent)
    except BaseException as error:
        freeze_failure(parent, error)
        parent.cleanup_errors.append(error)
    for number, handler in handlers.items():
        try:
            owned_binding(signal.getsignal(number), controlled)
            signal.signal(number, handler)
        except BaseException as error:
            freeze_failure(parent, error)
            parent.cleanup_errors.append(error)
    if first is None and parent.cleanup_errors:
        first = parent.cleanup_errors[0]
        traceback = first.__traceback__
    parent.phase = "publication"
    evidence = parent.values.get("EVIDENCE")
    if evidence is not None:
        try:
            if Path(evidence).is_dir():
                projection = {
                    "schema": "bifrost.test.workflow-claim-frontend/v1",
                    "before": None if parent.front_before is None else parent.front_before["observed"],
                    "after": None if parent.front_after is None else parent.front_after["observed"],
                    "matched": frontend_matched(parent),
                }
                publish(
                    Path(evidence) / "frontend.json",
                    (json.dumps(projection, separators=(",", ":")) + "\n").encode(),
                    4096,
                    PUBLICATION_END,
                )
                status = b"0\n" if first is None else b"1\n"
                publish(Path(evidence) / "gate-exit-status.txt", status, 16, PUBLICATION_END)
                publish(Path(evidence) / "exit-status.txt", status, 16, PUBLICATION_END)
                summary = (
                    "project="
                    + parent.values["PROJECT"]
                    + "\ncleanup_status="
                    + str(int(bool(parent.cleanup_errors)))
                    + "\ninspection_status="
                    + str(int(bool(parent.cleanup_errors)))
                    + "\ncontainers="
                    + parent.absence["containers"]
                    + "\nvolumes="
                    + parent.absence["volumes"]
                    + "\nnetworks="
                    + parent.absence["networks"]
                    + "\n"
                ).encode()
                publish(Path(evidence) / "project-resources.txt", summary, 512, PUBLICATION_END)
        except BaseException as error:
            freeze_failure(parent, error)
            first = first_error(first, error)
            if traceback is None:
                traceback = first.__traceback__
    if first is not None:
        if parent.failure_snapshot is not None and not parent.diagnostic_failed:
            try:
                emit_diagnostic(
                    parent.failure_snapshot, PUBLICATION_END, os, time.monotonic, parent.prepr_stage, parent.build_guard
                )
            except BaseException:
                parent.diagnostic_failed = True  # No delivery claim or replacement of the retained first error.
        # No private exception/native payload is printed or reclassified as success.
        # Preserve the actual first error privately; only a closed diagnostic and static exit here.
        if isinstance(first, Control):
            raise SystemExit(130)
        raise SystemExit(1)


if __name__ == "__main__":
    entry()
PY
