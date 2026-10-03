#!/usr/bin/env bash
# RFMT1: supported hosted CI only. Verification remains a separately held gate.
set -euo pipefail
if [[ "${1:-}" != format || "$#" != 1 ]]; then
    printf '%s\n' 'Result source formatter accepts only format' >&2
    exit 2
fi
python3 - <<'PY'
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

CORE_TREE = "282d4481b05eb612f36688a0b3b554021973d04f"
PINS = {
    "core-rs/Dockerfile": "a42c446bb8c67462ad6afefdc1aa6bdde4a7d241ad4d1122c26af9e5cfe9270b",
    "core-rs/rust-toolchain.toml": "c910997cb152c6dc8ed13b1fdf9fa28a4ed30d6776123e27dd6170e5c66067a0",
    "core-rs/Cargo.toml": "4e22240845140ac0b22e8b17eb51147461048982f989d53616f6c58d421e2232",
    "core-rs/Cargo.lock": "08a36a258d8ebc8b47e322adbae3fd541d66920ccf6dfc5a2cc0ab8a840ed021",
    "core-rs/crates/bifrost-db/Cargo.toml": "f95c09ebb8b35796ab43aa225b85a8d7ed679ac57d90189671b8fde030fb41a2",
    "core-rs/crates/bifrost-db/src/workflow_parity.rs": "0d5c1333a7af79020ae6eca3c77beed530a0be1bcd15e5df5b20620bfac10322",
    "core-rs/crates/bifrost-db/examples/workflow_sql_vectors.rs": "708dbf21072a15a8a70ed21eefcb3e78e2beacae4f016a8e7a2b622be22b6e74",
    "core-rs/crates/bifrost-contracts/src/runtime/codec.rs": "a921ec1c8a1e3ecf30e58266d3ce4bb6b385227e5ba724e3f76dddad77f9f843",
    "core-rs/crates/bifrost-contracts/src/runtime/mod.rs": "d9ead06b93d50d96eb9b71a9dcf6bd96e69b89db15a22bf86f5be66e42848143",
    "core-rs/crates/bifrost-contracts/src/runtime/tests.rs": "642808c15c0085fdabf9c65fbdcf341b6e6256881788e014375608f35cb551a1",
    "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json": "a5501a4fde472ebbae9567803e3b96313672ccfbad41fc8165030cbffd1786e6",
    "core-rs/crates/bifrost-domain/examples/workflow_domain_vectors.rs": "0aa7e2252459061d1ad72514955646e72bd4513c94bdd0697304f5ac7a50ad58",
}
ALLOWED = {
    "core-rs/crates/bifrost-db/src/workflow_parity.rs",
    "core-rs/crates/bifrost-db/examples/workflow_sql_vectors.rs",
}
STREAM_LIMIT = 16 * 1024 * 1024
AGGREGATE_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 4 * 1024 * 1024
PATCH_LIMIT = 4 * 1024 * 1024
START = time.monotonic()
WORK_END = START + 900
CLEANUP_END = START + 1020
END = START + 1080
PUBLICATION_END = END
ROOT = Path.cwd().resolve()
owner = "result-format-" + uuid.uuid4().hex
label = "bifrost.result-format.owner"
image_tag = owner + ":toolchain"
container_name = owner + "-formatter"
private = None
private_identity = None
artifacts = None
capture_count = 0
capture_total = 0
processes = []
child_cleanup_failed = False
image_attempted = False
container_attempted = False
image_id = None
image_config = None
container_id = None
container_config = None
head = None
tree = None
main = None
before = {}
after = {}
changed = []
patch = b""
fmt_exit = None
source_unchanged = None
primary = None
cleanup_errors = []
measured_image = None
measured_container = None
stages = {"source", "build", "format", "measure", "cleanup", "publication"}

DIAGNOSTIC_KINDS = {
    "acquisition",
    "read_bound",
    "deadline",
    "wait",
    "group",
    "fd",
    "native_exit",
    "parse",
    "association",
    "fingerprint",
    "absence",
    "public_output",
    "control",
    "unknown",
}
DIAGNOSTIC_LOCATIONS = {
    "post_measure_milestone",
    "cleanup_start_milestone",
    "container",
    "image",
    "private",
    "cleanup_end_milestone",
    "other_primary",
}
CHECKPOINTS = ("record", "inspect", "association", "fingerprint", "remove", "absence", "complete")
CONTAINER_SECTIONS = ("Id", "Image", "Config", "HostConfig", "Mounts")
IMAGE_SECTIONS = (
    "Id",
    "Config",
    "RepoTags",
    "RepoDigests",
    "Metadata",
    "RootFS",
    "GraphDriver",
    "Size",
    "other_top_level",
)
# Closed reference vocabulary from observed Moby source0d98/roster1bf0.
# This does not attest an installed engine version or exempt any fingerprint.
HOST_CONFIG_FIELDS = (
    "Annotations",
    "AutoRemove",
    "Binds",
    "BlkioDeviceReadBps",
    "BlkioDeviceReadIOps",
    "BlkioDeviceWriteBps",
    "BlkioDeviceWriteIOps",
    "BlkioWeight",
    "BlkioWeightDevice",
    "CapAdd",
    "CapDrop",
    "Cgroup",
    "CgroupParent",
    "CgroupnsMode",
    "ConsoleSize",
    "ContainerIDFile",
    "CpuCount",
    "CpuPercent",
    "CpuPeriod",
    "CpuQuota",
    "CpuRealtimePeriod",
    "CpuRealtimeRuntime",
    "CpuShares",
    "CpusetCpus",
    "CpusetMems",
    "DeviceCgroupRules",
    "DeviceRequests",
    "Devices",
    "Dns",
    "DnsOptions",
    "DnsSearch",
    "ExtraHosts",
    "GroupAdd",
    "IOMaximumBandwidth",
    "IOMaximumIOps",
    "Init",
    "IpcMode",
    "Isolation",
    "KernelMemory",
    "KernelMemoryTCP",
    "Links",
    "LogConfig",
    "MaskedPaths",
    "Memory",
    "MemoryReservation",
    "MemorySwap",
    "MemorySwappiness",
    "Mounts",
    "NanoCpus",
    "NetworkMode",
    "OomKillDisable",
    "OomScoreAdj",
    "PidMode",
    "PidsLimit",
    "PortBindings",
    "Privileged",
    "PublishAllPorts",
    "ReadonlyPaths",
    "ReadonlyRootfs",
    "RestartPolicy",
    "Runtime",
    "SecurityOpt",
    "ShmSize",
    "StorageOpt",
    "Sysctls",
    "Tmpfs",
    "UTSMode",
    "Ulimits",
    "UsernsMode",
    "VolumeDriver",
    "VolumesFrom",
)
NATIVE_SLOTS = (
    "container_inspect",
    "container_remove",
    "container_absence",
    "image_inspect",
    "image_remove",
    "image_absence",
)
cleanup_diagnostics = {
    "first_failure": None,
    "resources": {
        name: {
            "last_completed": None,
            "failed_at": None,
            "association_equal": None,
            "fingerprint_equal": None,
            "changed_sections": None,
            **(
                {"host_config_changed_fields": None, "host_config_other_fields_changed": None}
                if name == "container"
                else {}
            ),
        }
        for name in ("container", "image")
    },
    "native": dict.fromkeys(NATIVE_SLOTS),
    "backing": {
        "eligible": None,
        "container_disposed": None,
        "image_disposed": None,
        "children_settled": None,
        "attempted": None,
    },
    "annotation_failed": False,
}
annotation_error = None
native_errors = {}
image_initial = None
container_initial = None


def annotation_failed(error):
    global annotation_error
    cleanup_diagnostics["annotation_failed"] = True
    if annotation_error is None:
        annotation_error = error


def observed_kind(error, fallback):
    if not isinstance(error, Exception):
        return "control"
    for actual, kind in native_errors.values():
        if actual is error:
            return kind
    return fallback


def first_failure(location, error, fallback):
    try:
        kind = observed_kind(error, fallback)
        if location not in DIAGNOSTIC_LOCATIONS or kind not in DIAGNOSTIC_KINDS:
            raise ValueError("invalid closed diagnostic")
        if cleanup_diagnostics["first_failure"] is None:
            cleanup_diagnostics["first_failure"] = {"location": location, "kind": kind}
    except BaseException as secondary:
        annotation_failed(secondary)


def native_begin(slot):
    if slot is None:
        return
    try:
        if slot not in NATIVE_SLOTS or cleanup_diagnostics["native"][slot] is not None:
            raise ValueError("invalid native diagnostic slot")
        cleanup_diagnostics["native"][slot] = {
            "return_code": None,
            "stdout_eof": None,
            "stderr_eof": None,
            "wait_complete": None,
            "capture_complete": None,
            "group_settled": None,
            "fds_closed": None,
            "failure_kind": None,
        }
    except BaseException as secondary:
        annotation_failed(secondary)


def native_fact(slot, field, value):
    if slot is None:
        return
    try:
        record = cleanup_diagnostics["native"][slot]
        if field not in record:
            raise ValueError("invalid native diagnostic field")
        if field == "return_code":
            valid = value is None or type(value) is int
        elif field == "failure_kind":
            valid = value is None or value in DIAGNOSTIC_KINDS
        else:
            valid = value is None or type(value) is bool
        if not valid:
            raise ValueError("invalid native diagnostic value")
        record[field] = value
    except BaseException as secondary:
        annotation_failed(secondary)


def native_failure(slot, error, fallback):
    if slot is None:
        return
    try:
        kind = "control" if not isinstance(error, Exception) else fallback
        if slot not in NATIVE_SLOTS or kind not in DIAGNOSTIC_KINDS:
            raise ValueError("invalid native diagnostic failure")
        if slot not in native_errors:
            native_errors[slot] = (error, kind)
            native_fact(slot, "failure_kind", kind)
    except BaseException as secondary:
        annotation_failed(secondary)


def resource_fact(resource, field, value):
    try:
        record = cleanup_diagnostics["resources"][resource]
        if (
            field
            not in (
                "last_completed",
                "failed_at",
                "association_equal",
                "fingerprint_equal",
                "changed_sections",
            )
            or field not in record
        ):
            raise ValueError("invalid resource diagnostic field")
        if field in {"last_completed", "failed_at"}:
            valid = value is None or value in CHECKPOINTS
        elif field == "changed_sections":
            names = CONTAINER_SECTIONS if resource == "container" else IMAGE_SECTIONS
            valid = value is None or (type(value) is list and value == [x for x in names if x in value])
        else:
            valid = value is None or type(value) is bool
        if not valid:
            raise ValueError("invalid resource diagnostic value")
        record[field] = value
    except BaseException as secondary:
        annotation_failed(secondary)


def section_changes(resource, initial, current):
    # Same original objects as the unchanged full fingerprint. No values/hashes export.
    try:
        names = CONTAINER_SECTIONS if resource == "container" else IMAGE_SECTIONS
        changes = []
        for name in names:
            if name == "other_top_level":
                left = {k: v for k, v in initial.items() if k not in names[:-1]}
                right = {k: v for k, v in current.items() if k not in names[:-1]}
                equal = json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
                    right, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
            else:
                equal = (name in initial) == (name in current) and json.dumps(
                    initial.get(name), sort_keys=True, separators=(",", ":"), allow_nan=False
                ) == json.dumps(current.get(name), sort_keys=True, separators=(",", ":"), allow_nan=False)
            if not equal:
                changes.append(name)
        resource_fact(resource, "changed_sections", changes)
    except BaseException as secondary:
        annotation_failed(secondary)


def host_config_changes(initial, current):
    # Compute and admit the pair privately before one completed record replacement.
    try:
        left = initial["HostConfig"]
        right = current["HostConfig"]
        if type(left) is not dict or type(right) is not dict:
            raise ValueError("invalid HostConfig diagnostic shape")
        changes = []
        for name in HOST_CONFIG_FIELDS:
            if (name in left) != (name in right) or (
                name in left
                and json.dumps(left[name], sort_keys=True, separators=(",", ":"), allow_nan=False)
                != json.dumps(right[name], sort_keys=True, separators=(",", ":"), allow_nan=False)
            ):
                changes.append(name)
        left_other = {k: v for k, v in left.items() if k not in HOST_CONFIG_FIELDS}
        right_other = {k: v for k, v in right.items() if k not in HOST_CONFIG_FIELDS}
        other_changed = json.dumps(left_other, sort_keys=True, separators=(",", ":"), allow_nan=False) != json.dumps(
            right_other, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if (
            type(changes) is not list
            or len(changes) > 71
            or any(type(name) is not str for name in changes)
            or changes != [name for name in HOST_CONFIG_FIELDS if name in changes]
            or type(other_changed) is not bool
        ):
            raise ValueError("invalid closed HostConfig diagnostic pair")
        record = cleanup_diagnostics["resources"]["container"]
        replacement = {
            **record,
            "host_config_changed_fields": changes,
            "host_config_other_fields_changed": other_changed,
        }
        cleanup_diagnostics["resources"]["container"] = replacement
    except BaseException as secondary:
        annotation_failed(secondary)


def backing_facts(**values):
    try:
        for field, value in values.items():
            if field not in cleanup_diagnostics["backing"] or (value is not None and type(value) is not bool):
                raise ValueError("invalid backing diagnostic")
            cleanup_diagnostics["backing"][field] = value
    except BaseException as secondary:
        annotation_failed(secondary)


class Failure(Exception):
    def __init__(self, stage, code=1):
        self.stage = stage
        self.code = code if type(code) is int and code != 0 and -255 <= code <= 255 else 1
        super().__init__("Result format gate failed")


def require(value, stage):
    if not value:
        raise Failure(stage)


def admission(deadline, stage):
    require(time.monotonic() < deadline, stage)


def milestone(stage, state):
    require(stage in stages and state in {"start", "complete", "failed"}, "source")
    # Only these twelve closed milestones can reach public stdout.
    print("result-format " + stage + " " + state, flush=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_uid)


def safe_env():
    # No inherited resolver/Git/GH/registry/DB credentials enter child processes.
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "DOCKER_CONFIG": str(private / "docker-config"),
    }


def child(argv, cap, *, end=WORK_END, cwd=ROOT, diagnostic_slot=None):
    """One native command; returned pipes/FDs retained before configuration."""
    global capture_count, capture_total, child_cleanup_failed
    deadline = min(end, time.monotonic() + cap)
    normal_end = deadline - min(4, cap / 4)
    native_begin(diagnostic_slot)
    try:
        admission(normal_end, "source")
    except BaseException as error:
        native_failure(diagnostic_slot, error, "deadline")
        raise
    capture_count += 1
    outputs = [bytearray(), bytearray()]
    captures = []
    pipes = []
    selector = None
    process = None
    original = None
    returned = None
    complete = [False, False]
    failure_kind = "acquisition"
    fd_failure = False
    try:
        for suffix in ("stdout", "stderr"):
            path = private / (str(capture_count) + "-" + suffix)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            captures.append(fd)
            require(stat.S_ISREG(os.fstat(fd).st_mode), "source")
        selector = selectors.DefaultSelector()
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=safe_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        processes.append(process)
        # Both returned handles enter custody before any nonblocking/register call.
        pipes.append(process.stdout)
        pipes.append(process.stderr)
        failure_kind = "deadline"
        admission(normal_end, "source")
        failure_kind = "fd"
        for index, pipe in enumerate(pipes):
            require(pipe is not None, "source")
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, index)
        while not all(complete) or process.poll() is None:
            failure_kind = "deadline"
            admission(normal_end, "source")
            failure_kind = "unknown"
            for key, _ in selector.select(min(0.1, max(0, normal_end - time.monotonic()))):
                index = key.data
                data = os.read(key.fileobj.fileno(), 16384)
                if not data:
                    selector.unregister(key.fileobj)
                    complete[index] = True
                    native_fact(diagnostic_slot, "stdout_eof" if index == 0 else "stderr_eof", True)
                    continue
                native_fact(diagnostic_slot, "stdout_eof" if index == 0 else "stderr_eof", False)
                failure_kind = "read_bound"
                require(len(outputs[index]) + len(data) <= STREAM_LIMIT, "source")
                require(capture_total + len(data) <= AGGREGATE_LIMIT, "source")
                capture_total += len(data)
                outputs[index].extend(data)
                remaining = memoryview(data)
                while remaining:
                    failure_kind = "deadline"
                    admission(normal_end, "source")
                    failure_kind = "fd"
                    size = os.write(captures[index], remaining)
                    require(size > 0, "source")
                    remaining = remaining[size:]
        failure_kind = "wait"
        returned = process.wait(timeout=max(0, normal_end - time.monotonic()))
        native_fact(diagnostic_slot, "return_code", returned)
        native_fact(diagnostic_slot, "wait_complete", True)
        failure_kind = "deadline"
        admission(normal_end, "source")
        failure_kind = "read_bound"
        require(all(complete), "source")
        failure_kind = "fd"
        sizes_equal = all(os.fstat(fd).st_size == len(outputs[i]) for i, fd in enumerate(captures))
        native_fact(diagnostic_slot, "capture_complete", sizes_equal)
        require(sizes_equal, "source")
        if returned:
            failure_kind = "native_exit"
            raise Failure("source", returned)
    except BaseException as error:
        original = error
        native_failure(diagnostic_slot, error, failure_kind)
    finally:
        # Independent teardown: a failed kill/close cannot suppress the others.
        if process is not None:
            try:
                if group_exists(process.pid):
                    os.killpg(process.pid, signal.SIGKILL)
            except BaseException as error:
                native_failure(diagnostic_slot, error, "group")
                child_cleanup_failed = True
                if original is None:
                    original = error
            try:
                settlement_kind = "wait"
                settled_return = process.wait(timeout=max(0, deadline - time.monotonic()))
                native_fact(diagnostic_slot, "return_code", settled_return)
                native_fact(diagnostic_slot, "wait_complete", True)
                settlement_kind = "group"
                while group_exists(process.pid):
                    native_fact(diagnostic_slot, "group_settled", False)
                    settlement_kind = "deadline"
                    admission(deadline, "source")
                    settlement_kind = "group"
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
                native_fact(diagnostic_slot, "group_settled", True)
            except BaseException as error:
                native_failure(diagnostic_slot, error, settlement_kind)
                child_cleanup_failed = True
                if original is None:
                    original = error
        for handle in pipes:
            if handle is None:
                continue
            try:
                handle.close()
            except BaseException as error:
                fd_failure = True
                native_failure(diagnostic_slot, error, "fd")
                child_cleanup_failed = True
                if original is None:
                    original = error
        if selector is not None:
            try:
                selector.close()
            except BaseException as error:
                fd_failure = True
                native_failure(diagnostic_slot, error, "fd")
                child_cleanup_failed = True
                if original is None:
                    original = error
        for fd in captures:
            try:
                os.close(fd)
            except BaseException as error:
                fd_failure = True
                native_failure(diagnostic_slot, error, "fd")
                child_cleanup_failed = True
                if original is None:
                    original = error
        close_in_time = time.monotonic() < deadline
        if captures or pipes or selector is not None:
            native_fact(diagnostic_slot, "fds_closed", not fd_failure)
        if not close_in_time and original is None:
            original = Failure("source")
            native_failure(diagnostic_slot, original, "deadline")
    if original is not None:
        raise original
    return bytes(outputs[0]), returned


def group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def git(*args, cap=15, end=WORK_END, cwd=ROOT):
    return child(["git", *args], cap, end=end, cwd=cwd)[0]


def docker(*args, cap=15, end=WORK_END, diagnostic_slot=None):
    return child(["docker", *args], cap, end=end, diagnostic_slot=diagnostic_slot)[0]


def json_one(raw):
    require(len(raw) <= FILE_LIMIT, "source")
    value = json.loads(raw)
    require(type(value) is list and len(value) == 1 and type(value[0]) is dict, "source")
    return value[0]


def inspect_image(reference, *, end=WORK_END, diagnostic_slot=None):
    raw = docker("image", "inspect", reference, end=end, diagnostic_slot=diagnostic_slot)
    try:
        return json_one(raw)
    except BaseException as error:
        native_failure(diagnostic_slot, error, "parse")
        raise


def inspect_container(reference, *, end=WORK_END, diagnostic_slot=None):
    raw = docker("container", "inspect", reference, end=end, diagnostic_slot=diagnostic_slot)
    try:
        return json_one(raw)
    except BaseException as error:
        native_failure(diagnostic_slot, error, "parse")
        raise


def config_digest(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def labels_match(config):
    labels = config.get("Config", {}).get("Labels") or {}
    return (
        labels.get(label) == owner
        and labels.get("bifrost.result-format.head") == head
        and labels.get("bifrost.result-format.core-tree") == CORE_TREE
    )


def container_matches(config):
    mounts = config.get("Mounts", [])
    return (
        labels_match(config)
        and config.get("Image") == image_id
        and config.get("Config", {}).get("Cmd") == ["cargo", "fmt", "--all"]
        and config.get("HostConfig", {}).get("NetworkMode") == "none"
        and len(mounts) == 1
        and mounts[0].get("Type") == "bind"
        and mounts[0].get("Source") == str(private / "copy" / "core-rs")
        and mounts[0].get("Destination") == "/workspace/core-rs"
        and mounts[0].get("RW") is True
    )


def read_source(path):
    fd = None
    original = None
    result = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= FILE_LIMIT, "source")
        data = bytearray()
        while True:
            admission(WORK_END, "source")
            block = os.read(fd, min(16384, FILE_LIMIT + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            require(len(data) <= FILE_LIMIT, "source")
        final = os.fstat(fd)
        require(
            identity(path.lstat()) == identity(info) == identity(final)
            and info.st_size == final.st_size == len(data)
            and info.st_mtime_ns == final.st_mtime_ns
            and info.st_mode == final.st_mode,
            "source",
        )
        result = (bytes(data), stat.S_IMODE(info.st_mode))
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    return result


def inventory(base):
    result = {}
    for path in sorted((base / "core-rs").rglob("*")):
        admission(WORK_END, "source")
        if path.is_dir() and not path.is_symlink():
            continue
        data, mode = read_source(path)
        result[path.relative_to(base).as_posix()] = {"sha256": digest(data), "mode": mode}
    require(len(result) <= 128, "source")
    return result


def source_gate():
    global head, tree, main, before
    require(os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true", "source")
    require(os.environ.get("GITHUB_EVENT_NAME") == "push", "source")
    require(os.environ.get("GITHUB_REF") == "refs/heads/test/workflow-result-parity", "source")
    require(not git("status", "--porcelain=v1", "--untracked-files=all"), "source")
    head = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", head) and re.fullmatch(r"[0-9a-f]{40}", tree), "source")
    require(head == os.environ.get("GITHUB_SHA"), "source")
    require(
        git("log", "-1", "--format=%s").decode().rstrip("\n") == "ci: format reviewed workflow Result source", "source"
    )
    require(git("rev-parse", "HEAD:core-rs").decode().strip() == CORE_TREE, "source")
    # Anonymous fresh main, with no checkout helper/token credential inheritance.
    git("fetch", "--no-tags", "https://github.com/Midtown-Technology-Group/bifrost.git", "refs/heads/main", cap=60)
    main = git("rev-parse", "FETCH_HEAD").decode().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", main), "source")
    git("merge-base", "--is-ancestor", main, head)
    names = git("ls-tree", "-rz", "HEAD", "core-rs").split(b"\0")
    for record in names:
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, _object = header.decode().split()
        require(kind == "blob" and mode in {"100644", "100755"}, "source")
        relative = name.decode("utf-8")
        require(relative.startswith("core-rs/") and ".." not in Path(relative).parts, "source")
        data = git("show", head + ":" + relative)
        require(len(data) <= FILE_LIMIT, "source")
        actual, actual_mode = read_source(ROOT / relative)
        require(actual == data and actual_mode == int(mode, 8) & 0o777, "source")
        destination = private / "copy" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(actual_mode)
    before = inventory(private / "copy")
    for path, expected in PINS.items():
        require(before.get(path, {}).get("sha256") == expected, "source")
    git("init", "--quiet", cwd=private / "copy")
    git("add", "--", "core-rs", cwd=private / "copy")


def format_source():
    global image_attempted, container_attempted, image_id, image_config
    global container_id, container_config, fmt_exit, measured_image, measured_container
    global image_initial, container_initial
    require(not docker("image", "ls", "-q", "--no-trunc", "--filter", "reference=" + image_tag), "build")
    require(not docker("ps", "-aq", "--no-trunc", "--filter", "name=^/" + container_name + "$"), "build")
    image_attempted = True
    docker(
        "build",
        "--target",
        "toolchain",
        "--tag",
        image_tag,
        "--label",
        label + "=" + owner,
        "--label",
        "bifrost.result-format.head=" + head,
        "--label",
        "bifrost.result-format.core-tree=" + CORE_TREE,
        "--file",
        str(private / "copy" / "core-rs" / "Dockerfile"),
        str(private / "copy" / "core-rs"),
        cap=600,
    )
    config = inspect_image(image_tag)
    require(labels_match(config) and re.fullmatch(r"sha256:[0-9a-f]{64}", config.get("Id", "")), "build")
    image_id = config["Id"]
    image_config = config_digest(config)
    image_initial = config
    measured_image = {"id": image_id, "config_sha256": image_config, "labels_match": True}
    milestone("build", "complete")
    milestone("format", "start")
    container_attempted = True
    created = (
        docker(
            "create",
            "--name",
            container_name,
            "--label",
            label + "=" + owner,
            "--label",
            "bifrost.result-format.head=" + head,
            "--label",
            "bifrost.result-format.core-tree=" + CORE_TREE,
            "--network",
            "none",
            "--mount",
            "type=bind,src=" + str(private / "copy" / "core-rs") + ",dst=/workspace/core-rs",
            image_id,
            "cargo",
            "fmt",
            "--all",
        )
        .decode()
        .strip()
    )
    require(re.fullmatch(r"[0-9a-f]{64}", created), "format")
    config = inspect_container(created)
    require(config.get("Id") == created and container_matches(config), "format")
    container_id = created
    container_config = config_digest({k: config[k] for k in ("Id", "Image", "Config", "HostConfig", "Mounts")})
    container_initial = config
    measured_container = {"id": created, "image_id": image_id, "network_none": True, "mount_matches": True}
    _, fmt_exit = child(["docker", "start", "--attach", created], 60)
    config = inspect_container(created)
    require(container_matches(config) and config.get("State", {}).get("Running") is False, "format")
    require(config["State"].get("ExitCode") == 0 and fmt_exit == 0, "format")
    milestone("format", "complete")


def measure():
    global after, changed, patch, source_unchanged
    after = inventory(private / "copy")
    require(set(before) == set(after), "measure")
    changed = sorted(path for path in before if before[path] != after[path])
    require(set(changed) <= ALLOWED, "measure")
    require(all(before[path]["mode"] == after[path]["mode"] for path in before), "measure")
    require(not git("ls-files", "--others", "--exclude-standard", cwd=private / "copy"), "measure")
    patch = git("diff", "--no-ext-diff", "--no-textconv", "--binary", "--", "core-rs", cwd=private / "copy")
    require(len(patch) <= PATCH_LIMIT, "measure")
    diff_paths = git("diff", "--name-only", "-z", "--", "core-rs", cwd=private / "copy").split(b"\0")
    require(sorted(p.decode() for p in diff_paths if p) == changed, "measure")
    require(git("rev-parse", "HEAD").decode().strip() == head, "measure")
    require(not git("status", "--porcelain=v1", "--untracked-files=all"), "measure")
    require(inventory(ROOT) == before, "measure")
    source_unchanged = True


def cleanup():
    result = {"container": None, "image": None, "copy": False, "captures": False}
    deadline = min(CLEANUP_END, time.monotonic() + 120)

    def independent(action, location):
        kind = "deadline"
        try:
            admission(deadline, "cleanup")
            kind = "unknown"
            action()
            kind = "deadline"
            admission(deadline, "cleanup")
        except BaseException as error:
            first_failure(location, error, kind)
            cleanup_errors.append(error)

    def remove_container():
        checkpoint = "record"
        kind = "acquisition"
        try:
            if not container_attempted:
                result["container"] = True
                resource_fact("container", "last_completed", "complete")
                return
            require(container_id is not None and container_config is not None, "cleanup")
            resource_fact("container", "last_completed", "record")
            checkpoint, kind = "inspect", "unknown"
            config = inspect_container(container_id, end=deadline, diagnostic_slot="container_inspect")
            resource_fact("container", "last_completed", "inspect")
            checkpoint = "association"
            association = container_matches(config)
            resource_fact("container", "association_equal", association)
            kind = "association"
            require(association, "cleanup")
            resource_fact("container", "last_completed", "association")
            checkpoint, kind = "fingerprint", "unknown"
            equal = (
                config_digest({k: config[k] for k in ("Id", "Image", "Config", "HostConfig", "Mounts")})
                == container_config
            )
            resource_fact("container", "fingerprint_equal", equal)
            section_changes("container", container_initial, config)
            host_config_changes(container_initial, config)
            kind = "fingerprint"
            require(equal, "cleanup")
            resource_fact("container", "last_completed", "fingerprint")
            checkpoint, kind = "remove", "unknown"
            docker("rm", "--force", container_id, end=deadline, diagnostic_slot="container_remove")
            resource_fact("container", "last_completed", "remove")
            checkpoint = "absence"
            remaining = docker(
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                "id=" + container_id,
                end=deadline,
                diagnostic_slot="container_absence",
            )
            kind = "absence"
            require(not remaining, "cleanup")
            resource_fact("container", "last_completed", "absence")
            result["container"] = True
            resource_fact("container", "last_completed", "complete")
        except BaseException as error:
            resource_fact("container", "failed_at", checkpoint)
            first_failure("container", error, kind)
            raise

    def remove_image():
        checkpoint = "record"
        kind = "acquisition"
        try:
            if not image_attempted:
                result["image"] = True
                resource_fact("image", "last_completed", "complete")
                return
            require(image_id is not None and image_config is not None, "cleanup")
            resource_fact("image", "last_completed", "record")
            checkpoint, kind = "inspect", "unknown"
            config = inspect_image(image_id, end=deadline, diagnostic_slot="image_inspect")
            resource_fact("image", "last_completed", "inspect")
            checkpoint = "association"
            association = labels_match(config)
            resource_fact("image", "association_equal", association)
            equal = None
            if association:
                resource_fact("image", "last_completed", "association")
                checkpoint = "fingerprint"
                equal = config_digest(config) == image_config
                resource_fact("image", "fingerprint_equal", equal)
                section_changes("image", image_initial, config)
            kind = "fingerprint" if association else "association"
            require(association and equal, "cleanup")
            resource_fact("image", "last_completed", "fingerprint")
            # Exact immutable ID; a replaced alias never authorizes deleting its new ID.
            checkpoint, kind = "remove", "unknown"
            docker("image", "rm", image_id, end=deadline, diagnostic_slot="image_remove")
            resource_fact("image", "last_completed", "remove")
            checkpoint = "absence"
            remaining = docker(
                "image",
                "ls",
                "-q",
                "--no-trunc",
                "--filter",
                "label=" + label + "=" + owner,
                end=deadline,
                diagnostic_slot="image_absence",
            )
            kind = "absence"
            require(not remaining, "cleanup")
            resource_fact("image", "last_completed", "absence")
            result["image"] = True
            resource_fact("image", "last_completed", "complete")
        except BaseException as error:
            resource_fact("image", "failed_at", checkpoint)
            first_failure("image", error, kind)
            raise

    independent(remove_container, "container")
    independent(remove_image, "image")
    # Do not delete backing paths while an owned child/container may still use them.
    children_settled = not child_cleanup_failed and all(
        process.poll() is not None and not group_exists(process.pid) for process in processes
    )
    settled = children_settled and result["container"] is True and result["image"] is True
    backing_facts(
        eligible=settled,
        container_disposed=result["container"],
        image_disposed=result["image"],
        children_settled=children_settled,
        attempted=False,
    )
    if settled:

        def remove_private():
            backing_facts(attempted=True)
            try:
                require(private is not None and identity(private.lstat()) == private_identity, "cleanup")
                require(stat.S_IMODE(private.lstat().st_mode) == 0o700, "cleanup")
                shutil.rmtree(private)
                require(not os.path.lexists(private), "cleanup")
                result["copy"] = result["captures"] = True
            except BaseException as error:
                first_failure("private", error, "unknown")
                raise

        independent(remove_private, "private")
    else:
        error = Failure("cleanup")
        cleanup_errors.append(error)
        first_failure("private", error, "unknown")

    return result


def publish_file(name, data, bound):
    require(artifacts is not None and len(data) <= bound, "publication")
    admission(PUBLICATION_END, "publication")
    fd = None
    original = None
    path = artifacts / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        remaining = memoryview(data)
        while remaining:
            admission(PUBLICATION_END, "publication")
            count = os.write(fd, remaining)
            require(count > 0, "publication")
            remaining = remaining[count:]
        os.fsync(fd)
        require(os.fstat(fd).st_size == len(data), "publication")
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original
    # The returned exclusive file has exactly the bounded bytes just written;
    # final descriptor size and close admission are checked before publication.
    admission(PUBLICATION_END, "publication")


def exit_fact(error):
    if isinstance(error, Failure):
        return error.code
    if isinstance(error, KeyboardInterrupt):
        return 130
    if isinstance(error, SystemExit) and type(error.code) is int:
        return error.code if error.code != 0 and -255 <= error.code <= 255 else 1
    return 1 if error is not None else 0


def as_json(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def main_run():
    global private, private_identity, artifacts, primary, PUBLICATION_END
    disposal = {"container": None, "image": None, "copy": False, "captures": False}
    try:
        parent = Path(os.environ["RUNNER_TEMP"]).resolve()
        require(parent.is_dir(), "source")
        private = Path(tempfile.mkdtemp(prefix=owner + "-", dir=parent))
        private_identity = identity(private.lstat())
        private.chmod(0o700)
        for name in ("docker-config", "copy"):
            (private / name).mkdir(mode=0o700)
        artifacts = parent / (owner + "-artifacts")
        artifacts.mkdir(mode=0o700)
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
            output.write("RESULT_FORMAT_ARTIFACTS=" + str(artifacts) + "\n")
        milestone("source", "start")
        source_gate()
        milestone("source", "complete")
        milestone("build", "start")
        format_source()
        milestone("measure", "start")
        measure()
        try:
            milestone("measure", "complete")
        except BaseException as error:
            first_failure("post_measure_milestone", error, "public_output")
            raise
    except BaseException as error:
        primary = error
        first_failure("other_primary", error, "unknown")
    finally:
        try:
            milestone("cleanup", "start")
        except BaseException as error:
            first_failure("cleanup_start_milestone", error, "public_output")
            cleanup_errors.append(error)
        try:
            disposal = cleanup()
        except BaseException as error:
            first_failure("private", error, "unknown")
            cleanup_errors.append(error)
        try:
            milestone("cleanup", "failed" if cleanup_errors else "complete")
        except BaseException as error:
            first_failure("cleanup_end_milestone", error, "public_output")
            cleanup_errors.append(error)
        if primary is None and cleanup_errors:
            primary = cleanup_errors[0]
        if primary is None and annotation_error is not None:
            primary = annotation_error
    PUBLICATION_END = min(END, time.monotonic() + 60)
    try:
        milestone("publication", "start")
        metadata = {
            "schema": "bifrost.test.workflow-result-format-source/v1",
            "mode": "format",
            "candidate": {"head": head, "tree": tree},
            "main": main,
            "core_tree": CORE_TREE,
            "pins": PINS,
            "before": before,
            "after": after,
            "changed_paths": changed,
            "patch_sha256": digest(patch),
            "image": measured_image,
            "container": measured_container,
            "format_exit": fmt_exit,
            "limits": {
                "whole": 1080,
                "work": 900,
                "cleanup": 120,
                "publication": 60,
                "build": 600,
                "native_format": 60,
                "stream_bytes": STREAM_LIMIT,
                "aggregate_bytes": AGGREGATE_LIMIT,
            },
        }
        # Native format/source/disposal facts only. No receipt self-attests final
        # publication or job success; the actual job/step exit is separately required.
        primary_exit = exit_fact(primary)
        cleanup_exit = 1 if cleanup_errors else 0
        report = {
            "schema": "bifrost.test.workflow-result-format-disposal/v3",
            "owner": owner,
            **disposal,
            "original_source_unchanged": source_unchanged,
            "primary_exit": primary_exit,
            "cleanup_exit": cleanup_exit,
            "complete": all(value is True for value in disposal.values()),
            "cleanup_diagnostics": cleanup_diagnostics,
        }
        publish_file("format.patch", patch, PATCH_LIMIT)
        publish_file("source-metadata.json", as_json(metadata), 1024 * 1024)
        publish_file("disposal.json", as_json(report), 16384)
        admission(PUBLICATION_END, "publication")
        milestone("publication", "complete")
        admission(PUBLICATION_END, "publication")
    except BaseException as error:
        if primary is None:
            primary = error
    if primary is not None:
        raise primary
    if annotation_error is not None:
        raise annotation_error


def shutdown(signum, _frame):
    # Catchable job termination still enters first-error-preserving disposal.
    raise SystemExit(128 + signum)


signal.signal(signal.SIGTERM, shutdown)
try:
    main_run()
except BaseException as failure:
    # Preserve the first object through all feasible cleanup; classify only at the
    # process boundary and never export an exception message/chain or child logs.
    code = exit_fact(failure)
    # The first error determines exit even if diagnostic streams are unavailable.
    sys.exit(code if 0 < code <= 255 else 128 - code if -127 <= code < 0 else 1)
PY
