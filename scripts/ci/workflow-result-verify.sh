#!/usr/bin/env bash
# Reviewed Result parity custody. Execute only in the separately released CI lane.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
exec python3 -I -B - "$@" <<'PY'
"""One bounded owner for genuine source/build/SQL observations; safe exports only."""

from __future__ import annotations

import ast
import configparser
import fnmatch
import hashlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path.cwd().resolve(strict=True)
START = time.monotonic()
NORMAL_END = START + 2460
WHOLE_END = START + 2700
STAGE_CAPS = {
    "prepr": 480,
    "checks": 480,
    "binary": 120,
    "stack": 180,
    "manifest": 60,
    "target": 900,
    "native": 120,
    "cleanup": 120,
}
STREAM_LIMIT = 16 * 1024 * 1024
AGGREGATE_LIMIT = 64 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
ARTIFACT_CAPS = {
    "producer.json": 1048576,
    "features.json": 16384,
    "junit.json": 1048576,
    "disposal.json": 16384,
    "receipt.json": 1048576,
}
OWNER = str(uuid.uuid4())
LABELS = {}


class Failure(Exception):
    """Only closed static labels may cross the private diagnostic boundary."""

    def __init__(self, label, code=1):
        super().__init__(label)
        self.label = label
        self.code = code


def require(condition, label):
    if not condition:
        raise Failure(label)


def closed(value, keys, label="shape"):
    require(type(value) is dict and set(value) == set(keys), label)
    return True


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def deadline(end):
    require(time.monotonic() < min(end, WHOLE_END), "deadline")


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate-json")
        value[key] = item
    return value


def decode(raw, limit=STREAM_LIMIT):
    require(type(raw) is bytes and len(raw) <= limit, "read-bound")
    return json.loads(
        raw.decode("utf-8", "strict"),
        object_pairs_hook=unique_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(Failure("nonfinite-json")),
    )


def encode(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def stable_file(path, limit, *, expected_uid=None, expected_mode=None, allow_empty=False):
    """No-follow full-byte software or bounded nonsecret record observation."""
    before = os.stat(path, follow_symlinks=False)
    require(
        stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and (0 if allow_empty else 1) <= before.st_size <= limit,
        "file-profile",
    )
    if expected_uid is not None:
        require(before.st_uid == expected_uid, "file-owner")
    if expected_mode is not None:
        require(stat.S_IMODE(before.st_mode) == expected_mode, "file-mode")
    fd = None
    first = None
    data = bytearray()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        opened = os.fstat(fd)
        require(file_identity(opened) == file_identity(before), "file-open-identity")
        while block := os.read(fd, min(65536, limit + 1 - len(data))):
            data.extend(block)
            require(len(data) <= limit, "file-bound")
        require(len(data) == before.st_size and file_identity(os.fstat(fd)) == file_identity(before), "file-eof")
    except BaseException as error:
        first = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                if first is None:
                    first = error
    if first is not None:
        raise first
    require(file_identity(os.stat(path, follow_symlinks=False)) == file_identity(before), "file-path-identity")
    return bytes(data), before


def file_identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


class Controller:
    def __init__(self):
        # main retains this object before invoking __init__; paths are retained before mkdir.
        self.private = None
        self.private_acquired = False
        self.capture_directory = None
        self.capture_acquired = False
        self.docker_directory = None
        self.docker_acquired = False
        self.evidence = None
        self.evidence_acquired = False
        self.selector = None
        self.sessions = []
        self.commands = []
        self.used = set()
        self.capture_total = 0
        self.current_stage = "source"
        self.end = NORMAL_END
        self.stages = {
            name: {"started": False, "finished": False, "native_exit": None, "admitted": False}
            for name in ("source", "prepr", "checks", "native", "binary", "stack", "manifest", "target")
        }
        self.cleanup_failed = False
        self.cleanup_started = False
        self.endpoint_admitted = False
        self.after_admitted = False
        self.target_complete = False
        self.prepr_started = False
        self.target_started = False
        self.prepr_complete = False
        self.project_final = None
        self.initial_census = None
        self.initial_image_ids = set()
        self.observer_final = None
        self.junit = None
        self.fixture_feature = None
        self.resources = {}
        self.images = {}
        self.qualified_images = {}
        self.volume_resources = {}
        self.runner_anonymous_volumes = []
        self.stack_images = {}
        self.project_anchors = {}
        self.teardown_snapshot = None
        self.owned_files = []
        self.frontend_seconds = 0.0
        self.frontend_count = 0
        self.candidate = None
        self.main = None
        self.first = None
        self.frontend_failed = False
        self.package_domains = {}
        self.invocations = {}
        self.graphs = {}
        self.binary = None
        self.source_map = {}
        self.native_url = None
        self.before_schema = None
        self.after_schema = None
        self.frontend_before = None
        self.frontend_after = None
        self.private = Path(os.environ["RUNNER_TEMP"]) / ("result-private-" + OWNER)
        require(not os.path.lexists(self.private), "private-preexists")
        self.private.mkdir(mode=0o700)
        self.private_acquired = True
        self.private_identity = file_identity(os.stat(self.private, follow_symlinks=False))
        self.capture_directory = self.private / "captures"
        self.capture_directory.mkdir(mode=0o700)
        self.capture_acquired = True
        self.docker_directory = self.private / "docker-config"
        self.docker_directory.mkdir(mode=0o700)
        self.docker_acquired = True
        self.docker_directory_identity = file_identity(os.stat(self.docker_directory, follow_symlinks=False))
        self.evidence = ROOT / "result-verify-evidence"
        require(not os.path.lexists(self.evidence), "evidence-preexists")
        self.evidence.mkdir(mode=0o700)
        self.evidence_acquired = True
        self.selector = selectors.DefaultSelector()

    def environment(self, extra=None):
        result = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "DOCKER_CONFIG": str(self.docker_directory),
        }
        result.update(extra or {})
        require(
            not any(key in result for key in ("GITHUB_TOKEN", "GH_TOKEN", "BIFROST_ACTION_PIN_TOKEN_FILE")),
            "credential-env",
        )
        return result

    def begin(self, name):
        require(name in STAGE_CAPS, "stage")
        if name == "cleanup":
            require(not self.cleanup_started, "cleanup-replay")
            self.cleanup_started = True
        else:
            require(not self.stages[name]["started"], "stage-replay")
            self.stages[name]["started"] = True
        self.current_stage = name
        self.end = min(time.monotonic() + STAGE_CAPS[name], WHOLE_END if name == "cleanup" else NORMAL_END)
        deadline(self.end)

    def finish(self):
        if self.current_stage in self.stages:
            self.stages[self.current_stage].update(finished=True, native_exit=0, admitted=True)

    def pump(self, end):
        """All live children share one selector, including a target held during readbacks."""
        now = time.monotonic()
        for session in self.sessions:
            if not session["retired"]:
                require(now < session["end"], "deadline")
        for key, _mask in self.selector.select(min(0.05, max(0, end - now))):
            session, index = key.data
            handle = key.fileobj
            if index == 2:
                pending = session["stdin"]
                written = os.write(handle.fileno(), pending[session["offset"] :])
                require(written > 0, "stdin-write")
                session["offset"] += written
                if session["offset"] == len(pending):
                    self.selector.unregister(handle)
                    session["handle_registered"][index] = False
                    handle.close()
                    session["handle_closed"][index] = True
                    session["stdin_closed"] = True
                continue
            block = os.read(handle.fileno(), 16384)
            if not block:
                self.selector.unregister(handle)
                session["handle_registered"][index] = False
                session["eof"][index] = True
                continue
            require(len(session["outputs"][index]) + len(block) <= STREAM_LIMIT, "stream-bound")
            require(self.capture_total + len(block) <= AGGREGATE_LIMIT, "aggregate-bound")
            self.capture_total += len(block)
            session["outputs"][index].extend(block)
            view = memoryview(block)
            while view:
                written = os.write(session["capture_fds"][index], view)
                require(written > 0, "capture-write")
                view = view[written:]

    def run(self, label, argv, *, stdin=b"", cap=None, extra=None, tick=None, allow_exit=False):
        require(label in LABELS and label not in self.used and len(self.used) < 128, "command-roster")
        self.used.add(label)  # Count acquisition attempts BEFORE any Popen.
        record = {"label": label, "stage": self.current_stage, "exit": None, "complete": False}
        self.commands.append(record)
        end = min(self.end, WHOLE_END, time.monotonic() + cap if cap is not None else self.end)
        deadline(end)
        session = {
            "process": None,
            "command_index": len(self.commands),
            "handles": [],
            "handle_registered": [],
            "handle_closed": [],
            "capture_fds": [],
            "capture_closed": [],
            "capture_close_attempted": [],
            "capture_identities": [],
            "outputs": [bytearray(), bytearray()],
            "eof": [False, False],
            "stdin": stdin,
            "offset": 0,
            "stdin_closed": not stdin,
            "end": end,
            "settled": False,
            "retired": False,
        }
        self.sessions.append(session)
        first = None
        try:
            for suffix in ("out", "err"):
                path = self.capture_directory / f"{len(self.commands)}-{suffix}"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                session["capture_fds"].append(fd)
                session["capture_closed"].append(False)
                session["capture_close_attempted"].append(False)
                acquired = os.fstat(fd)
                session["capture_identities"].append((acquired.st_dev, acquired.st_ino))
                require(stat.S_ISREG(acquired.st_mode), "capture-profile")
            process = subprocess.Popen(
                argv,
                cwd=ROOT,
                env=self.environment(extra),
                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            session["process"] = process
            session["handles"].extend([process.stdout, process.stderr])
            if stdin:
                session["handles"].append(process.stdin)
            session["handle_registered"] = [False] * len(session["handles"])
            session["handle_closed"] = [False] * len(session["handles"])
            for index, handle in enumerate(session["handles"]):
                require(handle is not None, "pipe-acquisition")
                os.set_blocking(handle.fileno(), False)
                self.selector.register(
                    handle, selectors.EVENT_WRITE if index == 2 else selectors.EVENT_READ, (session, index)
                )
                session["handle_registered"][index] = True
            while not all(session["eof"]) or process.poll() is None:
                deadline(end)
                self.pump(end)
                if tick is not None and process.poll() is None:
                    tick(process)
            record["exit"] = process.wait(timeout=max(0, end - time.monotonic()))
            require(integer(record["exit"], -255, 255), "native-exit")
            require(session["stdin_closed"] and all(session["eof"]), "native-eof")
            require(
                all(
                    os.fstat(fd).st_size == len(session["outputs"][index])
                    for index, fd in enumerate(session["capture_fds"])
                ),
                "capture-size",
            )
            if not allow_exit:
                require(record["exit"] == 0, "native-failure")
        except BaseException as error:
            first = error
        finally:
            process = session["process"]
            if process is not None:
                try:
                    if group_exists(process.pid):
                        os.killpg(process.pid, signal.SIGKILL)
                except BaseException as error:
                    self.cleanup_failed = True
                    if first is None:
                        first = error
                try:
                    record["exit"] = process.wait(timeout=max(0, end - time.monotonic()))
                    while group_exists(process.pid):
                        deadline(end)
                        time.sleep(0.01)
                except BaseException as error:
                    self.cleanup_failed = True
                    if first is None:
                        first = error
            fd_failed = False
            for index, handle in enumerate(session["handles"]):
                if session["handle_registered"][index]:
                    try:
                        self.selector.unregister(handle)
                        session["handle_registered"][index] = False
                    except BaseException as error:
                        fd_failed = True
                        self.cleanup_failed = True
                        if first is None:
                            first = error
                if not session["handle_closed"][index]:
                    try:
                        handle.close()
                        session["handle_closed"][index] = True
                    except BaseException as error:
                        fd_failed = True
                        self.cleanup_failed = True
                        if first is None:
                            first = error
            for index, fd in enumerate(session["capture_fds"]):
                if not session["capture_closed"][index]:
                    session["capture_close_attempted"][index] = True
                    try:
                        os.close(fd)
                        session["capture_closed"][index] = True
                    except BaseException as error:
                        fd_failed = True
                        self.cleanup_failed = True
                        if first is None:
                            first = error
            session["retired"] = True
            group_absent = process is None
            if process is not None:
                try:
                    group_absent = not group_exists(process.pid)
                except BaseException as error:
                    fd_failed = True
                    self.cleanup_failed = True
                    if first is None:
                        first = error
            session["settled"] = not fd_failed and group_absent and (process is None or process.returncode is not None)
            record["complete"] = (
                process is not None
                and session["settled"]
                and all(session["eof"])
                and session["stdin_closed"]
                and first is None
            )
        if first is not None:
            raise first
        return bytes(session["outputs"][0]), bytes(session["outputs"][1]), record["exit"]

    def front_run(self, label, argv):
        require(self.frontend_count < 20 and self.frontend_seconds < 40, "frontend-budget")
        self.frontend_count += 1
        begin = time.monotonic()
        first = None
        result = None
        try:
            result = self.run(label, argv, cap=min(2, 40 - self.frontend_seconds))
        except BaseException as error:
            first = error
        finally:
            self.frontend_seconds += time.monotonic() - begin
            if self.frontend_seconds > 40:
                self.frontend_failed = True
                if first is None:
                    first = Failure("frontend-budget")
        if first is not None:
            raise first
        return result

    def write_artifact(self, name, value):
        data = encode(value) + b"\n"
        require(name in ARTIFACT_CAPS and len(data) <= ARTIFACT_CAPS[name], "artifact-bound")
        deadline(WHOLE_END)
        path = self.evidence / name
        require(not os.path.lexists(path), "artifact-replay")
        fd = None
        first = None
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                require(written > 0, "artifact-write")
                offset += written
            os.fsync(fd)
        except BaseException as error:
            first = error
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    if first is None:
                        first = error
        if first is not None:
            raise first


LABELS = {
    "git_fetch_main": 1,
    "git_candidate_identity": 2,
    "git_initial_clean": 3,
    "git_main_ancestry": 4,
    "git_source_tree": 5,
    "git_source_blobs": 6,
    "git_post_prepr_clean": 7,
    "git_post_prepr_identity": 8,
    "git_final_clean": 9,
    "git_final_identity": 10,
    "docker_version": 11,
    "docker_context": 12,
    "compose_version": 13,
    "compose_base_config": 14,
    "cargo_home_create": 15,
    "targets_create": 16,
    "cargo_volumes_inspect": 17,
    "cargo_volumes_remove": 18,
    "images_initial": 19,
    "images_post_prepr": 20,
    "images_prepr_inspect": 21,
    "toolchain_build": 22,
    "toolchain_inspect": 23,
    "api_before_build": 24,
    "api_build": 25,
    "api_after_build": 26,
    "owned_images_remove": 27,
    "images_final": 28,
    "census_initial": 29,
    "census_prepr_teardown": 30,
    "inspect_prepr_owned": 31,
    "census_prepr_absent": 32,
    "census_stack_ready": 33,
    "inspect_stack_owned": 34,
    "census_target_teardown": 35,
    "census_final": 36,
    "create_fetch": 37,
    "start_fetch": 38,
    "create_fmt": 39,
    "start_fmt": 40,
    "create_clippy": 41,
    "start_clippy": 42,
    "create_default_tests": 43,
    "start_default_tests": 44,
    "create_metadata_default": 45,
    "start_metadata_default": 46,
    "create_metadata_selected": 47,
    "start_metadata_selected": 48,
    "create_metadata_all_features": 49,
    "start_metadata_all_features": 50,
    "create_release": 51,
    "start_release": 52,
    "create_uid_probe": 53,
    "start_uid_probe": 54,
    "create_group_helper": 55,
    "start_group_helper": 56,
    "create_schema_before": 57,
    "start_schema_before": 58,
    "create_contracts_default_lib": 59,
    "start_contracts_default_lib": 60,
    "create_contracts_raw_lib": 61,
    "start_contracts_raw_lib": 62,
    "create_db_selected_lib": 63,
    "start_db_selected_lib": 64,
    "create_db_selected_example": 65,
    "start_db_selected_example": 66,
    "create_domain_raw_example": 67,
    "start_domain_raw_example": 68,
    "create_workspace_all_lib": 69,
    "start_workspace_all_lib": 70,
    "create_workspace_all_examples": 71,
    "start_workspace_all_examples": 72,
    "create_schema_after": 73,
    "start_schema_after": 74,
    "inspect_pre_setup": 75,
    "inspect_pre_release": 76,
    "inspect_pre_uid": 77,
    "inspect_pre_helper": 78,
    "inspect_pre_schema_before": 79,
    "inspect_pre_native": 80,
    "inspect_post_fetch": 81,
    "inspect_post_checks": 82,
    "inspect_post_release": 83,
    "inspect_post_uid": 84,
    "inspect_post_helper": 85,
    "inspect_post_schema_before": 86,
    "inspect_post_native": 87,
    "remove_native_uid": 88,
    "remove_native_helper": 89,
    "remove_native_remaining": 90,
    "absence_uid": 91,
    "absence_helper": 92,
    "frontend_before_1": 93,
    "frontend_before_2": 94,
    "frontend_before_3": 95,
    "frontend_before_4": 96,
    "frontend_before_5": 97,
    "frontend_before_6": 98,
    "frontend_before_7": 99,
    "frontend_before_8": 100,
    "frontend_after_1": 101,
    "frontend_after_2": 102,
    "frontend_after_3": 103,
    "frontend_after_4": 104,
    "frontend_after_5": 105,
    "frontend_after_6": 106,
    "frontend_after_7": 107,
    "frontend_after_8": 108,
    "frontend_version": 109,
    "frontend_entrypoint_digest": 110,
    "frontend_postcleanup_1": 111,
    "frontend_postcleanup_2": 112,
    "runner_discovery": 113,
    "runner_config": 114,
    "runner_source_readback": 115,
    "release_binary_copy": 116,
    "literal_prepr": 117,
    "prepr_stack_down": 118,
    "target_stack_up": 119,
    "target_pytest": 120,
    "target_stack_down": 121,
    "failure_active_native_kill": 122,
    "failure_native_state": 123,
    "failure_project_container_remove": 124,
    "failure_project_volume_remove": 125,
    "failure_project_network_remove": 126,
    "failure_residual_inspect": 127,
    "failure_residual_census": 128,
}
FINAL_GIT = [
    "sh",
    "-c",
    'first=0\nprintf \'bifrost-git-final/v1\\nstatus-begin\\n\'\nframe=$?\nif [ "$frame" -ne 0 ]; then first=$frame; fi\ngit status --porcelain=v1 --untracked-files=all\nstatus=$?\nif [ "$first" -eq 0 ] && [ "$status" -ne 0 ]; then first=$status; fi\nprintf \'\\nstatus-exit %s\\nidentity-begin\\n\' "$status"\nframe=$?\nif [ "$first" -eq 0 ] && [ "$frame" -ne 0 ]; then first=$frame; fi\ngit rev-parse HEAD \'HEAD^{tree}\'\nidentity=$?\nif [ "$first" -eq 0 ] && [ "$identity" -ne 0 ]; then first=$identity; fi\nprintf \'\\nidentity-exit %s\\nend\\n\' "$identity"\nframe=$?\nif [ "$first" -eq 0 ] && [ "$frame" -ne 0 ]; then first=$frame; fi\nexit "$first"\n',
]
API_COPY = [
    "pyproject.toml",
    "requirements.lock",
    "requirements-pyright.lock",
    "api/_bifrost_workspace_effects.py",
    "api/src/services/app_compiler/package.json",
    "api/src/services/app_compiler/package-lock.json*",
    "api/src/services/app_compiler/compile.js",
    "api/src/services/app_compiler/tailwind.js",
    "api/src/services/app_bundler/package.json",
    "api/src/services/app_bundler/package-lock.json*",
    "api/src/services/sdk_package/package.json",
    "api/src/services/sdk_package/package-lock.json",
    "api/src/services/sdk_package/build_sdk.js",
    "client/src/lib/app-sdk/provider.tsx",
    "client/src/lib/app-sdk/tables.ts",
    "client/src/lib/app-sdk/transport.ts",
    "client/src/lib/app-sdk/use-table.ts",
    "client/src/lib/app-sdk/use-infinite-table.ts",
    "client/src/lib/app-sdk/ws-client.ts",
    "client/src/lib/app-sdk/execution-stream.ts",
    "client/src/lib/app-sdk/use-workflow.ts",
    "client/src/lib/app-sdk/use-workflow-hooks.ts",
    "client/src/lib/app-sdk/files.ts",
    "client/src/lib/app-sdk/use-files.ts",
    "client/src/lib/app-sdk/bifrost-header.tsx",
    "client/src/lib/app-sdk/webmcp.ts",
    "client/src/lib/app-sdk/index.v2.ts",
    "client/src/lib/app-sdk/sdk-contract.json",
    "api/bifrost/",
    "api/_bifrost_workspace_effects.py",
    "api/shared/cli_artifact.py",
    "api/scripts/build_cli_artifact.py",
    "api/entrypoint.sh",
    "assets/",
]
API_COPY_PINS = {
    "api/_bifrost_workspace_effects.py": "c5ee9048f68315255ec8bdc20062df25dcf36916f3f4537c70565f158007f6ca",
    "api/bifrost/__init__.py": "ee6376146eb1028721a0b63de66e154cf6e9d460641e6d2c10e62ec64f7c3212",
    "api/bifrost/__main__.py": "bd772e99553ba5ab753c384dab75e3f69e5faa22592a55c4327d1a8ecc690e64",
    "api/bifrost/_context.py": "17e28d29ed7afb457afd497cd2f2b748ba4c79ea43b35800abf263f50899f719",
    "api/bifrost/_execution_context.py": "23704610792383fa44305989f2829cabf5f7c4c9c2067c6bd26710a078f819a3",
    "api/bifrost/_local_resources.py": "6e030ad690c3a1545da9a9f53f67c41b2fc465151cfb7e2eff16111df740381a",
    "api/bifrost/_logging.py": "36558e90e9c3f01dab96a6becf629d01e9f5f3af502cd9b9e31f7ac90c97f08d",
    "api/bifrost/_service_runtime.py": "295071b1073de0e0c8cb220d9a767dbcb9a0ac22e834ac5d970b5d6a5ae1da21",
    "api/bifrost/_solution_workspace.py": "986d2084e00eed59ae5991b44aafb23f502dccca1bdb363f6c740d89b602d0c5",
    "api/bifrost/_sync.py": "47e1fe99c142591b88031c314cc373e7274031fa96f1cd9f32570b6ddd21763c",
    "api/bifrost/_version_notice.py": "d3c453afc7ec9c4cb184ea7475d948e57ef46c47f63aeea66cfb40d622ffcc8c",
    "api/bifrost/_workspace_lock.py": "f014151253419b7638d00d91cde4b69045db74561f2329b53e38cc013a6ec704",
    "api/bifrost/_write_buffer.py": "aed2b584a649d2c4a29bd0fef3ae25d10bc29588302da32db2ea5b96b7d89fc9",
    "api/bifrost/admin_models.py": "6c6f7648525c321a1c5019ef7206eb0165d68da2f2e4a6119b5b65631492fcd7",
    "api/bifrost/agents.py": "f2ad8645a558cf0b7770e2d547e1179cc523d8a3695b19d3882b953329fb490c",
    "api/bifrost/ai.py": "2f4f223e2b8b0ba3531fae9a3b4bc56fd6566e6fc7cc64be82635b5fd96f4c97",
    "api/bifrost/api.py": "e22646a36fdbdb88beadd3a787d3af9ef5f9a041abf564f4f1d058cf4acd8e20",
    "api/bifrost/app_binding.py": "4b22c4696fd146a6cd766ecea07b09df46098e2e0e9d825d167cff2d4d1e7e27",
    "api/bifrost/app_migration.py": "648097667bc710ca6952ffffc7132d4d363d92eca624f786de3251ac12985a12",
    "api/bifrost/artifacts.py": "b1d2cc2a823486f47af8cc61178dc6f817cf5cb8513bdbcd01c85332da4e825d",
    "api/bifrost/cli.py": "103b34a9591914adbb6a32885b78da5661397b882ee523a25b020fac6b30ccd2",
    "api/bifrost/client.py": "60607baac3da4981331589d44fd98964d29cd434f67ea92f03d298ba527b176e",
    "api/bifrost/commands/__init__.py": "a01dfa39aef87b0dee41ff6eaba34c66a003381eee6b686c3b60912a489ce2a2",
    "api/bifrost/commands/agents.py": "3401d6d03b87fbf0b73a82f907e909f85ee93be143fdaca30de490c41e1dbb6b",
    "api/bifrost/commands/app.py": "d933fe72ccc39783677e80962d5f756bafeb214662d6be466bcad8ff405913a8",
    "api/bifrost/commands/apps.py": "48b15736278bcc60303cbd7aa06445096d37b6e5da1b6249bd1f75c395b5068a",
    "api/bifrost/commands/base.py": "b1200096d60f2e9020585cb2f751c7bfd721b4033aaf1c4e3efd297009bba2d6",
    "api/bifrost/commands/claims.py": "5edf024201f4c659c9bc6209f630a3c7d201b0ff43011e93e74f22cc528663c4",
    "api/bifrost/commands/configs.py": "1e33329fa949c9f111c2556cb8af685eee88ae1d1a6b9d755f44792a8cc2a0bd",
    "api/bifrost/commands/events.py": "63606687dad18aed8146738e7a8d8176ba7d1990f31c13702ee5c0432ce3bd68",
    "api/bifrost/commands/files.py": "9f025209dabec96c73a89fc86bc01c3b9214b8aa3268c87a67e9ea4d80b62c02",
    "api/bifrost/commands/forms.py": "09d49242372d68686eaa8870b70ad7d91ed29aa046f3d26326ac4c265df53e27",
    "api/bifrost/commands/integrations.py": "96fd4908916362f3753670fd128b069f41d8411bb630f10aca49fd5500a9db69",
    "api/bifrost/commands/orgs.py": "83e939659bc62c2ad24cc68a4e8b67e7a1bf0af5236010d995af596de004cbc0",
    "api/bifrost/commands/policy_rules.py": "146aff1b7271ddd90c081a8e012108beeb580780ae704bd3efa6e3fa476662dd",
    "api/bifrost/commands/promote.py": "d0d087174795c9535cd2469c887abfbe1044146e045fcf1674f324348ca3bcee",
    "api/bifrost/commands/requirements.py": "4ec9d224e33cc2f4c848ace2ca64e1ebcdb73115108c7d117c28970f64604b60",
    "api/bifrost/commands/roles.py": "20c472989f727afc46490b4349885f35da942e989d5b9b6d9758a2f5683dc942",
    "api/bifrost/commands/services.py": "5e243b1e5f81d919a149ae3555550a23ec44d9747802dccc9d2053cd33790ba9",
    "api/bifrost/commands/solution.py": "4989560a2a0417fb312b6860f0ab0a3c792efbfddcd1211cda922b1115978587",
    "api/bifrost/commands/tables.py": "5a742de8712fd65e1ec32f8d5b851c670089b8a7fc931d81750469d103a4d615",
    "api/bifrost/commands/workflows.py": "b3048e5e0bf7c611599ae6a20f28e3e6e630f09b3f710af7d8b56f4a6aea81ef",
    "api/bifrost/config.py": "22cd7ed21988827f0ba6e6b876d1043fd38f445750a8db773a20952423a305e1",
    "api/bifrost/contract_version.py": "3ff05520ae0d5aa4a38c1dc9ee381670f0505fe14f7052c4f494ec2eb1d7903e",
    "api/bifrost/contracts/__init__.py": "538fb53fd87b16c6de038ba6352787c5f102cd11cdaa2e57a05bd728bf3feafe",
    "api/bifrost/contracts/agents.py": "e7a7b7a477a12951308a32934e8790fac7366c3c8fc08caf905fbf27be5600cb",
    "api/bifrost/contracts/applications.py": "1de5f7ca3193fc1e1cf5ded86bde3a9feef93116cc90be7936bbe819483ab9a6",
    "api/bifrost/contracts/claims.py": "e43254da07fe1c41aa489e56d3cfcaa72af4a3c5b6649f50b50c92da2631f424",
    "api/bifrost/contracts/config.py": "fc1f05b8e81c8b7a388016acb4988c305472662a7a8df1f2caa4346f4f590523",
    "api/bifrost/contracts/enums.py": "15bd92086b021c4246f91e2b2f28225f520b1af2895f702ae3d2fa55c76af33f",
    "api/bifrost/contracts/events.py": "a5b4249ca04f80013f197fef02cb868e351ce1c48b131cb889249b4b6283d11f",
    "api/bifrost/contracts/files.py": "b0b838c9f43210ed4bbca0ee3cdf6f8769a3d63f753c46eef68c2496ddc4e39a",
    "api/bifrost/contracts/forms.py": "779356e2aa136cee81f7a6d33762f14158eec84f37dd0abd56c254145cf08cea",
    "api/bifrost/contracts/integrations.py": "200ef779fbe7773d2cdd22fd98cd4372b4c5884c9220f6f8acb08296a39627bf",
    "api/bifrost/contracts/organizations.py": "f146f37002bcc8c500880247afa53e0f5d0217f853cb8b902a828909a6209aa6",
    "api/bifrost/contracts/policy_rules.py": "f10b5844c20bf2e0b6dcce720414f0f818fc7a6ab0191ae180c065c95a1f2fbe",
    "api/bifrost/contracts/services.py": "4ee8cc79c35050a1fc0856a6ab97c81fcaaee34ea38c9761c0df8d26cafe5d6c",
    "api/bifrost/contracts/solutions.py": "c75dd4b6f71fc0f04a822d934a6ebacc8137d1528cac009d88aa41afcd984aa7",
    "api/bifrost/contracts/tables.py": "6610228adbaab17c55abc160894cda97b40f4a94593b31a862660461f62e91f7",
    "api/bifrost/contracts/users.py": "f9cdf81e616acac0bf37440f3520642fc1e5fa28bf8beb091d810ad49a40fe82",
    "api/bifrost/contracts/workflows.py": "97a3832a229178d30df8ef33132f8817f33aa8c1b09df480c2f1e3dd299e3b18",
    "api/bifrost/credentials.py": "5ba97f51577aba9a67810870600212f89a2e8dcf823635122e1924fa3eeb2543",
    "api/bifrost/decorators.py": "d630b241cc700e0f7cc1933e6693c488b5dbc3c647dcd00d90e8bf2470302d3f",
    "api/bifrost/dto_flags.py": "a8fa66fea3baf364ec3d961fec66736d05e9b51bf7973d10aae1774f41757a43",
    "api/bifrost/events.py": "a11fec674bb6211326978663844cbe55f8059d98aefbe13169f67d669f82781c",
    "api/bifrost/executions.py": "d5d4c285411d2d67414920f577c1f8f1ba8bbfecb64181de7fc00aa07688bcd7",
    "api/bifrost/field_classes.py": "a86afed8adb9e2f846de0d730b7251c71045e220fe89f21de716583908d177ce",
    "api/bifrost/files.py": "cea78dc25ec41f716a297a5747dc296e0e5aefffd39a3f7f8795c8885cc1eb77",
    "api/bifrost/forms.py": "60bc12c26952aaa47303f54367138acb80c226c3357bf7ea2615461f26af4dec",
    "api/bifrost/git_commands.py": "12cde8f9b0dc3e94ee26200750956a7bcca8e26160891ce1b29bf4b4a8d41836",
    "api/bifrost/ignore_patterns.py": "4f2e75ec36d2ee5e05b3bc997ac248d53fb847839186fc88319831baf1420f12",
    "api/bifrost/integrations.py": "15e6677985d6caad85f97966ed7c1bbbc4abd0b5a99e06d8c4d1021df82689ef",
    "api/bifrost/knowledge.py": "d483fe847148248d44ea14e984b9a1add0bf884fe7bf856877b9e64772b3067b",
    "api/bifrost/lucide_icon_names.json": "f7673fbdabb456dcdd29faabb50744352be29b2892e0801c9fb404e7ceea4ef4",
    "api/bifrost/manifest.py": "a98e205dc80a705f5d48d056afd2b748e079388209adfd1d449219333b83c23d",
    "api/bifrost/manifest_codec.py": "70241bf0e3655903080fa4b1f8338aac6811422e873bea514e9b05ebc1f02b1d",
    "api/bifrost/migrate_imports.py": "9d7ec7f846badd1d012e0ca7648c7ab94b4d8419401c604868cd790725f6c35a",
    "api/bifrost/migrate_v2.py": "634e1d6b146923f25f0cb1a58bee4c8fa87777abbeca58c87564dc07d8789a8d",
    "api/bifrost/models.py": "7a6fd36bba694dc482af62ded6dd3ff9148a9fd671c44cf42b93c3ce2a0b1ad9",
    "api/bifrost/oauth_admin.py": "3a425458e22dff882c7a160270da86a735bb463d66284a36d4d9c6680e0194ec",
    "api/bifrost/org_target.py": "904f74b0815cbf0923db5c9eed843bb7251e0c6b81630bfb82c671cf6e3dddcb",
    "api/bifrost/organizations.py": "296c53f391e3434f0ccc16e2f3b3b2615b6f689110db5d45a518e291922c3193",
    "api/bifrost/platform_jobs.py": "25095b43d1854b89c42e5aff0260e3866f76c2b895a229e5dca5a7d01d2c9d42",
    "api/bifrost/platform_names.py": "43a422b9390416c05f76d064291dcbb65a3ff9364a9ef411179919d18854554d",
    "api/bifrost/promotion.py": "dc309bc5f7839217c215b48664d9eab025e3003bcb4e8024e32c75a9808445b0",
    "api/bifrost/pyproject.toml": "1cfa9c4bd8094b59ba0adfc3bd3f39c5ff30222d0cd16e941d27d23a29c9fd48",
    "api/bifrost/refs.py": "c765a6342c5cf90239abd9ecdd7762a4f3a41c2a7ce2a9c90ee1f95c9411a5c9",
    "api/bifrost/resources.py": "e32699a2af7608c3a0201a7148dfdbbeeb308b902d5e02115c6d8e175830ebd5",
    "api/bifrost/roles.py": "cddc027c937a738002ebec4db41996f98f0376554a908b424ac4f15ffae2e1ca",
    "api/bifrost/root_file_bindings.py": "ab9766b6afa8514606bac347cefeb2c641b1a8462101f5642409f247480f931c",
    "api/bifrost/skill.py": "42a417942ef946def399f9615d9e30fe19d2aa1ff8bfe140afc067f7ff14e447",
    "api/bifrost/solution_binding.py": "212b5bcb539fea547d3c67d157c6965c1af2afb480bbb32c3c0168c750aa4c33",
    "api/bifrost/solution_delivery_review.py": "dd1ebe240e8fc0be4bb5bee2181c5bf2de37207fdf2c3bf09bcd9b1bcef6054c",
    "api/bifrost/solution_descriptor.py": "47f8218b042f39d10d0e4b3e74aaf3fd166c3fb183aa01592d8537f4788dcc41",
    "api/bifrost/solution_dev/__init__.py": "e44afa219dd90ac55ca81835a2f75d191ed637998aba98fedc49ea2710eb29d3",
    "api/bifrost/solution_dev/app_select.py": "66521cfffb0fe74eb0efb864b3f14a42f3ba11c48d874fd6ded094fbc5032620",
    "api/bifrost/solution_dev/function_host.py": "657c7f8956357e9dd49d52975ecdf1e053cdecb57880099d04f11c623654d7f6",
    "api/bifrost/solution_dev/proxy.py": "355cb72a2b6da8dad0706ec426b614165b7e1a8710fd29d6ee217b9e035fe2ad",
    "api/bifrost/solution_dev/reload.py": "8d74f06e5621ef4b4d1a519ac6b2a0734f2140b481bcaf55339fbdf2a81113e6",
    "api/bifrost/solution_dev/scaffold_check.py": "997e5531982b39dcfa499f8f3d6d91420dfd1ac313e5bb857e72c37bf11fde34",
    "api/bifrost/solution_jobs.py": "618c42ffa27995088872c71be33d8609de64a5bea68fc5e9978d95c43c8e0b29",
    "api/bifrost/solution_source_closure.py": "06ec3f318398573c5b076e20a707c295599e0e0faa84513b28ac3439465a9931",
    "api/bifrost/solution_vendoring.py": "b7e658e77fb6f3d55ccda832c14671bfb28c2ae77215db65d0475d6e5f16d5ac",
    "api/bifrost/tables.py": "e45ba1f2d2b57c5a4aa8ed0a7f8cb4e89475bb966dd0d7cdfd7c0d27d3dfad65",
    "api/bifrost/tui/__init__.py": "8f1941fb71428ae5f9eb0832b33016efede9366c46bcadf86c4d07347568d38d",
    "api/bifrost/tui/connection_select.py": "2d90141d878bae811da6530fd88f7f3c1a4b60e97a2e11ac8d51ace735462d0b",
    "api/bifrost/tui/file_select.py": "a116977742a9d2e678d4fe2fa0e992562fe59d9f167b2dbd99b0df9aea388345",
    "api/bifrost/tui/progress.py": "e888dd5e48455bdb2979a4fb373dfd41869053e1a701fc9afad1b99db3605c56",
    "api/bifrost/tui/sync_app.py": "2c20ff9a009bbeec83ac7aae6f4d50648f4808feb3a4c35affcf3706d653c7b0",
    "api/bifrost/tui/theme.py": "2297a03a0dd1e6a4bf88cb1cfb2c39ac0c7a6b26637edf503ee6de6abe563099",
    "api/bifrost/tui/watch.py": "bd9d2a9147bff2165b5407663f24efcf5ce9e34dea35b510fe2f76022bdb1abf",
    "api/bifrost/users.py": "8525af066468b42e9bb1dbb23fede45ddba1ab734745d61fc0ad898e8ae3a36a",
    "api/bifrost/webhooks.py": "e6caad7bb77be1b4e7b9248f1894ec2f7092bc147bec60e6cbcc44e1603901e8",
    "api/bifrost/workflow_parameters.py": "99a967b3f79529ff0188de4c8e5675affa669080b4cb156b360e9db77cf80c4c",
    "api/bifrost/workflows.py": "c469c0f423e9e82738aab0c945ed0763db499fa23df0b0f8abeb26278b548f47",
    "api/bifrost/workspace_effects.py": "0f2a28275e3dd01dae30916ff2287ee9a58eb4dfe3e939a116a0f37b531428a3",
    "api/bifrost/workspace_impact.py": "1c9e73ce65ca5408106531a3630954eb34badb5a5e99bf87de87448faf14fcdd",
    "api/bifrost/workspace_release.py": "6e7d20a4ad7c5fd5b3a51b0932f7c70a69477c217c3f6496902051c61957ed92",
    "api/bifrost/workspace_release_authorization.py": "8611e26dbb1bfa97be95961ec7a24d012a23f3dcddc9ce3628a7fcce68f33e0d",
    "api/entrypoint.sh": "d638f919a0b11e43cdac1c0c82678258f08811a659557bf78e90955b490167f7",
    "api/scripts/build_cli_artifact.py": "84bf644d7ec5bc92a9a66271a54ed4edbbb39f13b7985b067292e9bbe11c529f",
    "api/shared/cli_artifact.py": "1523984bfe50767e4f0632981f69fa0d1a334c94dcc849e4bd63db0b1f258b52",
    "api/src/services/app_bundler/package-lock.json": "5dc2c5de8558a8185091974d5f649bf041a2e62934e44a1cd2bce3cbe22e281f",
    "api/src/services/app_bundler/package.json": "dece64f427fbfc60b0e42ef915ff04f4399b369b79f0c16b68aaf4059c93dedf",
    "api/src/services/app_compiler/compile.js": "a9e89da0e50df42452a50fdcd14f9dacd39a8590dcd8d29d620adc4853565a73",
    "api/src/services/app_compiler/package-lock.json": "6821b0a6c4f3aec27af46198335e79bdfcef04e8b004871fc42ffd8b80719362",
    "api/src/services/app_compiler/package.json": "9c01a2e3760445a81d5fa2766b6b1e2ae0356cdb42796b1edaf54781e9bddb32",
    "api/src/services/app_compiler/tailwind.js": "b681bb95bc152f2a79165fe1635f744eaf6cb713fd38b57abba6ea0f9837d56e",
    "api/src/services/sdk_package/build_sdk.js": "4ff82f4b0c826a6f2a0360c8488b80c6aaa4d8053261bfdc1a5df8873265588e",
    "api/src/services/sdk_package/package-lock.json": "5d42ae547cb9a44db8bfbb4ad534a1572518ca82eaef5af97b8d268b81413165",
    "api/src/services/sdk_package/package.json": "6eb68a72407dab11512fb38c08ef11bf90cdab0d629b5858b7a5a250693f3eff",
    "assets/icon.png": "508e34526abb5a88bb2d3dcb2d787f9bf52abc436f080a5d0c9875c22e2c3819",
    "assets/logo.png": "6d2ca9264f55d24d80c62f828394ea717cb4f1ff0e830eb7656d5eb636accd0e",
    "client/src/lib/app-sdk/bifrost-header.tsx": "d0ffb8f461706262aecfedc109186d3ae4564da66b17a7143843abfac05d4fbf",
    "client/src/lib/app-sdk/execution-stream.ts": "1255c7b14d9ef942f828c4c1db65c1352b1d02212979cf23db6043e514cdb22c",
    "client/src/lib/app-sdk/files.ts": "9b49af80100658bb4ffe213ae9739bdfccfd51a85b78438ad95694a1cda27430",
    "client/src/lib/app-sdk/index.v2.ts": "11c5a7b25fbe16f1b344dfd8d308bbda21f67327b195b8eb7a87d9c30f08a50d",
    "client/src/lib/app-sdk/provider.tsx": "5a8e37765579b9c5c9334d4e5e5b51d3707b3e80433c9b0eb99e6b3e5768cb9a",
    "client/src/lib/app-sdk/sdk-contract.json": "b7aaca4bfcc6f7c9998ff8a0ce6a5b7b08b0f3f79056da99cda10754099d328f",
    "client/src/lib/app-sdk/tables.ts": "74b4f7d3ca6c1ae5cbe75f44841ef73e329c66cb527e9f0c4b780006baac5d78",
    "client/src/lib/app-sdk/transport.ts": "f412f47d319860a80f83585f37e11fb991bfca298f5054641d4684c5ae690715",
    "client/src/lib/app-sdk/use-files.ts": "e65e1cf562266e201871de823951974959a3455de58c066e73d9b3f72a610637",
    "client/src/lib/app-sdk/use-infinite-table.ts": "7c6dfaa413d1a56635304261fb351b2d8cbcb94e2694be2b3a429055be05e5ca",
    "client/src/lib/app-sdk/use-table.ts": "c936e36e984ff42ff82fb44ec0c1e1f2d1e424bf5d6b7e3ffd7864a97e277d15",
    "client/src/lib/app-sdk/use-workflow-hooks.ts": "b84298509d1607ce8a971b353956804089f79ca7c36618e1550bc02db882c3d1",
    "client/src/lib/app-sdk/use-workflow.ts": "adc4f5cac3b40782aa04ce4c354d40483d11f07152459b1643a4fc325ae3318b",
    "client/src/lib/app-sdk/webmcp.ts": "3bb0dffc22d5c7938a0148f5d599f38d04a9831763afd4a4d2de62591661014e",
    "client/src/lib/app-sdk/ws-client.ts": "5c2ef29e1f110a7f3bf70791ad3542c766a341d66d229f6fa4dbce9442b13d76",
    "pyproject.toml": "7fac5222b03a31882095b894f4dc1b5de4ba6cd51a272f9cb0db354770feb05a",
    "requirements-pyright.lock": "464362c56fc226e0034621fb15b2237cf287de9c3d7c787417873abebc35b39b",
    "requirements.lock": "be76160e9eb3b3ba9ab0d9af9e77f311db3578cfaf02b7042d9697bd7c2feea1",
}
TEST_CATALOG = [
    {"id": 0, "source_group": "contracts_lib", "test": "runtime::tests::shared_wire_vectors"},
    {"id": 1, "source_group": "contracts_lib", "test": "runtime::tests::shared_binary_vectors_with_partial_io"},
    {"id": 2, "source_group": "contracts_lib", "test": "runtime::tests::shared_parent_session_vectors"},
    {"id": 3, "source_group": "contracts_lib", "test": "runtime::tests::over_cap_payload_and_concatenated_frames"},
    {
        "id": 4,
        "source_group": "contracts_lib",
        "test": "runtime::tests::ordinary_json_retains_literal_private_looking_keys",
    },
    {
        "id": 5,
        "source_group": "contracts_lib",
        "test": "runtime::tests::ordinary_json_keeps_incumbent_structural_limits",
    },
    {
        "id": 6,
        "source_group": "contracts_lib",
        "test": "runtime::tests::protocol_precedence_preserves_size_and_depth_boundaries",
    },
    {
        "id": 7,
        "source_group": "domain_lib",
        "test": "workflow::tests::running_covers_all_current_attempt_states_and_exact_columns",
    },
    {
        "id": 8,
        "source_group": "domain_lib",
        "test": "workflow::tests::running_preserves_first_start_and_non_none_process_including_empty",
    },
    {
        "id": 9,
        "source_group": "domain_lib",
        "test": "workflow::tests::running_rejects_absence_foreign_missing_wrong_and_completed_fences",
    },
    {
        "id": 10,
        "source_group": "domain_lib",
        "test": "workflow::tests::missing_result_fence_precedes_every_logical_or_attempt_guard",
    },
    {
        "id": 11,
        "source_group": "domain_lib",
        "test": "workflow::tests::tracked_result_checks_every_logical_state_before_attempt_and_outcome",
    },
    {
        "id": 12,
        "source_group": "domain_lib",
        "test": "workflow::tests::all_ten_normalized_success_statuses_and_optional_zero_duration_are_preserved",
    },
    {
        "id": 13,
        "source_group": "domain_lib",
        "test": "workflow::tests::result_has_no_new_attempt_status_or_phase_start_guard",
    },
    {
        "id": 14,
        "source_group": "domain_lib",
        "test": "workflow::tests::result_rejects_exact_stale_fences_before_coordinator_policy",
    },
    {
        "id": 15,
        "source_group": "domain_lib",
        "test": "workflow::tests::four_failure_mappings_assign_nullable_attempt_inputs_without_payload_clearing",
    },
    {
        "id": 16,
        "source_group": "domain_lib",
        "test": "workflow::tests::coordinator_defers_running_but_cancelling_overrides_every_outcome",
    },
    {
        "id": 17,
        "source_group": "domain_lib",
        "test": "workflow::tests::queued_cancel_has_exact_optional_active_attempt_plan",
    },
    {
        "id": 18,
        "source_group": "domain_lib",
        "test": "workflow::tests::running_cancel_ignores_foreign_and_completed_attempts",
    },
    {
        "id": 19,
        "source_group": "domain_lib",
        "test": "workflow::tests::cancel_checks_logical_identity_and_rejects_other_states_before_attempt",
    },
    {
        "id": 20,
        "source_group": "domain_lib",
        "test": "workflow::tests::opaque_token_supports_clone_equality_without_secret_projection",
    },
    {"id": 21, "source_group": "db_numeric", "test": "workflow_numeric::tests::exact_half_cent_inputs"},
    {"id": 22, "source_group": "db_numeric", "test": "workflow_numeric::tests::integers_preserve_full_width"},
    {"id": 23, "source_group": "db_numeric", "test": "workflow_numeric::tests::zeros_and_float_integer_trailing_zeros"},
    {"id": 24, "source_group": "db_numeric", "test": "workflow_numeric::tests::finite_extremes_are_exact_and_bounded"},
    {"id": 25, "source_group": "db_numeric", "test": "workflow_numeric::tests::nonfinite_inputs_are_rejected"},
    {"id": 26, "source_group": "db_lib", "test": "workflow_parity::tests::timestamp_precision_and_range"},
    {"id": 27, "source_group": "db_lib", "test": "workflow_parity::tests::schema_vocabulary_fails_closed"},
    {
        "id": 28,
        "source_group": "db_lib",
        "test": "workflow_parity::tests::infrastructure_errors_are_static_and_not_domain_rejections",
    },
    {"id": 29, "source_group": "db_lib", "test": "workflow_parity::result_error_tests::result_error_allowlist_exact"},
    {"id": 30, "source_group": "db_lib", "test": "workflow_parity::result_error_tests::result_error_allowlist_closed"},
    {
        "id": 31,
        "source_group": "db_lib",
        "test": "workflow_parity::result_error_tests::result_error_nondatabase_has_no_code",
    },
    {"id": 32, "source_group": "db_lib", "test": "workflow_parity::result_error_tests::result_error_decode_mapping"},
    {"id": 33, "source_group": "db_lib", "test": "workflow_parity::result_error_tests::result_error_clock_mapping"},
    {
        "id": 34,
        "source_group": "db_lib",
        "test": "workflow_parity::result_error_tests::result_error_cardinality_mapping",
    },
    {
        "id": 35,
        "source_group": "db_lib",
        "test": "workflow_parity::result_error_tests::checked_prepared_json_rejects_class_and_private_profile_drift",
    },
    {
        "id": 36,
        "source_group": "db_lib",
        "test": "workflow_parity::result_error_tests::html_predicate_matches_python_whitespace_without_changing_storage",
    },
    {"id": 37, "source_group": "db_live", "test": "live_tests::migrated_schema_readiness_and_drift"},
    {"id": 38, "source_group": "db_example", "test": "tests::result_error_first_primary_wins"},
    {"id": 39, "source_group": "db_example", "test": "tests::result_settlement_commit_unknown"},
    {"id": 40, "source_group": "db_example", "test": "tests::result_settlement_rollback_unknown"},
    {"id": 41, "source_group": "db_example", "test": "tests::result_settlement_close_no_database_code"},
    {"id": 42, "source_group": "db_example", "test": "tests::original_lexemes_preserve_integer_width_and_float_sign"},
    {"id": 43, "source_group": "db_example", "test": "tests::ordinary_object_keys_and_duplicate_depth_rejections"},
    {
        "id": 44,
        "source_group": "db_example",
        "test": "tests::source_jsondata_negative_zero_is_integer_and_floats_are_held",
    },
    {
        "id": 45,
        "source_group": "domain_example",
        "test": "tests::actual_running_call_outputs_all_columns_and_non_none_empty_process",
    },
    {
        "id": 46,
        "source_group": "domain_example",
        "test": "tests::result_response_maps_all_actual_fields_and_null_failure_values",
    },
    {
        "id": 47,
        "source_group": "domain_example",
        "test": "tests::all_ten_normalized_success_strings_pass_through_actual_kernel",
    },
    {
        "id": 48,
        "source_group": "domain_example",
        "test": "tests::four_failure_inputs_and_coordinator_cancellation_are_real_calls",
    },
    {
        "id": 49,
        "source_group": "domain_example",
        "test": "tests::cancel_response_maps_all_actual_columns_and_nullable_attempt",
    },
    {
        "id": 50,
        "source_group": "domain_example",
        "test": "tests::actual_rejection_and_required_null_history_precedence_are_closed",
    },
    {
        "id": 51,
        "source_group": "domain_example",
        "test": "tests::unknown_fields_are_rejected_at_every_request_object_level",
    },
    {
        "id": 52,
        "source_group": "domain_example",
        "test": "tests::marker_wrappers_fail_preflight_and_original_typed_decode",
    },
    {
        "id": 53,
        "source_group": "domain_example",
        "test": "tests::mandatory_nullable_fields_cannot_be_inferred_from_omission",
    },
    {
        "id": 54,
        "source_group": "domain_example",
        "test": "tests::enum_uuid_schema_label_and_scalar_errors_fail_without_echo",
    },
    {
        "id": 55,
        "source_group": "domain_example",
        "test": "tests::malformed_trailing_duplicate_and_overlimit_inputs_never_emit_response",
    },
    {
        "id": 56,
        "source_group": "domain_example",
        "test": "tests::largest_safe_label_and_static_plan_remain_below_output_ceiling",
    },
    {"id": 57, "source_group": "domain_example", "test": "tests::reader_and_writer_failures_are_static_driver_errors"},
    {
        "id": 58,
        "source_group": "domain_example",
        "test": "tests::request_and_unit_enum_alternative_serde_representations_are_invalid",
    },
]
ROOT_CATALOG = (
    ("bifrost-contracts", "bifrost_contracts", "lib", "crates/bifrost-contracts/src/lib.rs"),
    ("bifrost-domain", "bifrost_domain", "lib", "crates/bifrost-domain/src/lib.rs"),
    ("bifrost-db", "bifrost_db", "lib", "crates/bifrost-db/src/lib.rs"),
    ("bifrost-core", "bifrost_core", "lib", "crates/bifrost-core/src/lib.rs"),
    (
        "bifrost-contracts",
        "runtime_control_vectors",
        "example",
        "crates/bifrost-contracts/examples/runtime_control_vectors.rs",
    ),
    ("bifrost-db", "workflow_sql_vectors", "example", "crates/bifrost-db/examples/workflow_sql_vectors.rs"),
    (
        "bifrost-domain",
        "workflow_domain_vectors",
        "example",
        "crates/bifrost-domain/examples/workflow_domain_vectors.rs",
    ),
)
FEATURE_CATALOG = (
    "alloc",
    "arbitrary_precision",
    "default",
    "float_roundtrip",
    "indexmap",
    "preserve_order",
    "raw_value",
    "std",
    "unbounded_depth",
)
REQUIRED_ROOTS = ((0,), (0,), (2,), (5,), (6,), (0, 1, 2, 3), (4, 5, 6), (5,))
NATIVE_LABELS = (
    "contracts_default_lib",
    "contracts_raw_lib",
    "db_selected_lib",
    "db_selected_example",
    "domain_raw_example",
    "workspace_all_lib",
    "workspace_all_examples",
    "release",
)
TARGET_DIRECTORIES = (
    "contracts_default",
    "contracts_raw",
    "db_selected",
    "db_selected",
    "domain_raw",
    "all_features",
    "all_features",
    "release",
)
COMMON_TEST = ["cargo", "test", "--locked", "--offline", "--message-format=json"]
NATIVE_ARGV = (
    [*COMMON_TEST, "-p", "bifrost-contracts", "--lib", "runtime::tests::shared_wire_vectors", "--", "--exact"],
    [
        *COMMON_TEST,
        "-p",
        "bifrost-contracts",
        "--features",
        "serde_json/raw_value",
        "--lib",
        "runtime::tests::shared_wire_vectors",
        "--",
        "--exact",
    ],
    [
        *COMMON_TEST,
        "-p",
        "bifrost-db",
        "--features",
        "workflow-sql-parity",
        "--lib",
        "workflow_parity::result_error_tests::checked_prepared_json_rejects_class_and_private_profile_drift",
        "--",
        "--exact",
    ],
    [
        *COMMON_TEST,
        "-p",
        "bifrost-db",
        "--features",
        "workflow-sql-parity",
        "--example",
        "workflow_sql_vectors",
        "tests::ordinary_object_keys_and_duplicate_depth_rejections",
        "--",
        "--exact",
    ],
    [
        *COMMON_TEST,
        "-p",
        "bifrost-domain",
        "--features",
        "serde_json/raw_value",
        "--example",
        "workflow_domain_vectors",
        "tests::marker_wrappers_fail_preflight_and_original_typed_decode",
        "--",
        "--exact",
    ],
    [*COMMON_TEST, "--workspace", "--all-features", "--lib"],
    [*COMMON_TEST, "--workspace", "--all-features", "--examples"],
    [
        "cargo",
        "build",
        "--locked",
        "--offline",
        "--message-format=json",
        "--release",
        "-p",
        "bifrost-db",
        "--features",
        "workflow-sql-parity",
        "--example",
        "workflow_sql_vectors",
    ],
)
METADATA = [
    "cargo",
    "metadata",
    "--locked",
    "--offline",
    "--format-version",
    "1",
    "--filter-platform",
    "x86_64-unknown-linux-gnu",
    "--manifest-path",
    "Cargo.toml",
]


def census_shell():
    commands = (
        ("containers", "docker container ls --all --no-trunc --format '{{json .}}'"),
        ("volumes", "docker volume ls --format '{{json .}}'"),
        ("networks", "docker network ls --no-trunc --format '{{json .}}'"),
    )
    lines = ["first=0"]
    for label, command in commands:
        lines.extend(
            [
                f"printf 'bifrost-census/v1 {label}\n'",
                "frame=$?",
                'if [ "$first" -eq 0 ] && [ "$frame" -ne 0 ]; then first=$frame; fi',
                command,
                "status=$?",
                'if [ "$first" -eq 0 ] && [ "$status" -ne 0 ]; then first=$status; fi',
                f"printf 'bifrost-census-exit {label} %s\n' \"$status\"",
                "frame=$?",
                'if [ "$first" -eq 0 ] && [ "$frame" -ne 0 ]; then first=$frame; fi',
            ]
        )
    lines.append('exit "$first"')
    return ["sh", "-c", "\n".join(lines)]


def parse_census(raw):
    require(raw.endswith(b"\n"), "census-eof")
    lines = raw.decode("utf-8", "strict").splitlines()
    index = 0
    result = {}
    for kind in ("containers", "volumes", "networks"):
        require(index < len(lines) and lines[index] == f"bifrost-census/v1 {kind}", "census-frame")
        index += 1
        rows = []
        while index < len(lines) and lines[index] != f"bifrost-census-exit {kind} 0":
            rows.append(decode(lines[index].encode()))
            require(len(rows) <= 4096, "census-count")
            index += 1
        require(index < len(lines), "census-status")
        index += 1
        result[kind] = rows
    require(index == len(lines), "census-extra")
    return result


def project_objects(census, name):
    result = {"containers": [], "volumes": [], "networks": []}
    for kind, rows in census.items():
        for row in rows:
            require(type(row) is dict, "inventory-shape")
            labels = row.get("Labels", "")
            require(type(labels) is str, "inventory-labels")
            pairs = [part.split("=", 1) for part in labels.split(",") if "=" in part]
            require(len({pair[0] for pair in pairs}) == len(pairs), "inventory-duplicate")
            values = dict(pairs)
            if values.get("com.docker.compose.project") == name:
                identity = row.get("Name") if kind == "volumes" else row.get("ID")
                require(type(identity) is str and identity, "inventory-identity")
                if kind != "volumes":
                    require(HEX64.fullmatch(identity) is not None, "inventory-id")
                result[kind].append(identity)
    require(all(len(set(items)) == len(items) for items in result.values()), "inventory-duplicate")
    return {kind: sorted(items) for kind, items in result.items()}


def ast_map_literal(raw, name):
    tree = ast.parse(raw.decode("utf-8", "strict"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    require(len(nodes) == 1 and type(nodes[0].value) is ast.Dict, "source-literal-map")
    value = {}
    for key, item in zip(nodes[0].value.keys, nodes[0].value.values, strict=True):
        require(
            isinstance(key, ast.Constant)
            and type(key.value) is str
            and isinstance(item, ast.Constant)
            and type(item.value) is str,
            "source-literal-map",
        )
        require(key.value not in value, "source-literal-map")
        value[key.value] = item.value
    return value


def migration_heads(sources):
    revisions = {}
    parents = {}
    fields = {"revision", "down_revision", "branch_labels", "depends_on"}
    for path, raw in sources.items():
        if not path.startswith("api/alembic/versions/") or not path.endswith(".py"):
            continue
        require(not raw.startswith(b"\xef\xbb\xbf"), "migration-encoding")
        tree = ast.parse(raw.decode("utf-8", "strict"))
        declarations = {}
        admitted = set()
        for node in tree.body:
            target = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target = node.targets[0]
            elif (
                isinstance(node, ast.AnnAssign)
                and node.simple == 1
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                target = node.target
            if target is not None and target.id in fields:
                require(target.id not in declarations, "migration-duplicate")
                declarations[target.id] = node.value
                admitted.add(id(target))
        require(set(declarations) == fields, "migration-metadata")
        require(
            not any(
                (alias.asname or alias.name.split(".")[0]) in fields
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            ),
            "migration-metadata-import",
        )
        require(
            not any(
                isinstance(node, ast.Name)
                and node.id in fields
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and id(node) not in admitted
                for node in ast.walk(tree)
            ),
            "migration-dynamic",
        )
        for name in ("revision", "branch_labels", "depends_on"):
            require(isinstance(declarations[name], ast.Constant), "migration-literal")
        revision = declarations["revision"].value
        require(
            type(revision) is str and re.fullmatch(r"[A-Za-z0-9_]{1,32}", revision) and revision not in revisions,
            "migration-revision",
        )
        require(
            declarations["branch_labels"].value is None and declarations["depends_on"].value is None,
            "migration-semantics",
        )
        down = declarations["down_revision"]
        if isinstance(down, ast.Constant) and down.value is None:
            edges = []
        elif isinstance(down, ast.Constant) and type(down.value) is str:
            edges = [down.value]
        elif (
            isinstance(down, ast.Tuple)
            and len(down.elts) == 2
            and all(isinstance(item, ast.Constant) and type(item.value) is str for item in down.elts)
        ):
            edges = [item.value for item in down.elts]
        else:
            raise Failure("migration-parents")
        require(
            len(set(edges)) == len(edges)
            and all(re.fullmatch(r"[A-Za-z0-9_]{1,32}", edge) and edge != revision for edge in edges),
            "migration-parents",
        )
        revisions[revision] = path
        parents[revision] = edges
    require(
        revisions and all(edge in revisions for edges in parents.values() for edge in edges), "migration-missing-parent"
    )
    pending = dict(parents)
    visited = set()
    while pending:
        ready = [node for node, edges in pending.items() if set(edges) <= visited]
        require(ready, "migration-cycle")
        for node in ready:
            visited.add(node)
            del pending[node]
    heads = sorted(set(revisions) - {edge for edges in parents.values() for edge in edges})
    require(1 <= len(heads) <= 8, "migration-heads")
    return heads


def literal_assignment(raw, name):
    module = ast.parse(raw)
    matches = [
        node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    require(len(matches) == 1, "source-literal")
    return ast.literal_eval(matches[0])


def parse_tree(raw):
    require(raw.endswith(b"\0") and len(raw) <= STREAM_LIMIT, "git-tree-frame")
    result = {}
    for entry in raw[:-1].split(b"\0"):
        header, separator, path_bytes = entry.partition(b"\t")
        require(separator == b"\t", "git-tree-row")
        fields = header.decode("ascii").split(" ")
        path = path_bytes.decode("utf-8", "strict")
        require(
            len(fields) == 3
            and fields[0] in {"100644", "100755", "120000"}
            and fields[1] == "blob"
            and HEX40.fullmatch(fields[2]) is not None,
            "git-tree-kind",
        )
        require(
            path not in result
            and not path.startswith("/")
            and ".." not in path.split("/")
            and not any(ord(char) < 32 for char in path),
            "git-tree-path",
        )
        result[path] = (fields[0], fields[2])
    return result


def parse_blobs(raw, requested, tree):
    index = 0
    result = {}
    for path in requested:
        end = raw.find(b"\n", index)
        require(end > index, "git-blob-header")
        header = raw[index:end].decode("ascii").split(" ")
        require(
            len(header) == 3
            and header[0] == tree[path][1]
            and header[1] == "blob"
            and re.fullmatch(r"[0-9]+", header[2]) is not None,
            "git-blob-association",
        )
        length = int(header[2])
        require(length <= 4 * 1024 * 1024, "git-blob-bound")
        start = end + 1
        stop = start + length
        require(stop < len(raw) and raw[stop : stop + 1] == b"\n", "git-blob-size")
        value = raw[start:stop]
        require(
            hashlib.sha1(b"blob " + str(length).encode() + b"\0" + value).hexdigest() == header[0], "git-blob-object"
        )
        result[path] = value
        index = stop + 1
    require(index == len(raw), "git-blob-extra")
    return result


def source_preflight(controller):
    controller.begin("prepr")
    primary_diagnostic_controls()
    controller.run("git_fetch_main", ["git", "fetch", "--no-tags", "origin", "main"])
    identity, _, _ = controller.run(
        "git_candidate_identity", ["git", "rev-parse", "--show-toplevel", "HEAD", "HEAD^{tree}", "origin/main"]
    )
    rows = identity.decode("utf-8", "strict").splitlines()
    require(
        len(rows) == 4
        and all(HEX40.fullmatch(item) for item in rows[1:])
        and Path(rows[0]).resolve(strict=True) == ROOT,
        "candidate-identity",
    )
    controller.candidate = {"head": rows[1], "tree": rows[2]}
    controller.main = rows[3]
    clean, _, _ = controller.run("git_initial_clean", ["git", "status", "--porcelain=v1", "--untracked-files=all"])
    require(clean == b"", "candidate-dirty")
    controller.run("git_main_ancestry", ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"])
    tree_raw, _, _ = controller.run("git_source_tree", ["git", "ls-tree", "-r", "-z", "HEAD"])
    tree = parse_tree(tree_raw)
    core = sorted(path for path in tree if path.startswith("core-rs/"))
    require(1 <= len(core) <= 128 and all(tree[path][0] != "120000" for path in core), "core-roster")
    migrations = sorted(path for path in tree if path.startswith("api/alembic/versions/") and path.endswith(".py"))
    fixed = {
        "test.sh",
        "docker-compose.test.yml",
        ".dockerignore",
        "api/Dockerfile.dev",
        "api/entrypoint.sh",
        "scripts/stack_template_init.sh",
        "scripts/lib/test_helpers.sh",
        "scripts/lib/pre_pr_stage_evidence.py",
        "api/alembic.ini",
        "api/alembic/env.py",
        "scripts/ci/workflow-sql-source.sh",
        "api/scripts/check_github_action_pins.py",
        "AGENTS.md",
        "api/tests/conftest.py",
        "api/tests/parity/workflow_domain_harness.py",
        "api/tests/parity/workflow_sql_harness.py",
        "api/tests/parity/test_workflow_sql.py",
        "api/tests/parity/fixtures/workflow-result-v1.json",
        "api/src/core/database.py",
        "api/src/config.py",
        "api/src/core/execution_variable_safety.py",
        "api/src/repositories/executions.py",
        "api/src/services/execution/attempts.py",
        "api/src/jobs/consumers/workflow_execution.py",
        "api/src/models/orm/executions.py",
        "api/src/models/enums.py",
        "api/src/runtime_protocol/control.py",
        "api/tests/parity/fixtures/workflow-domain-v1.json",
        "scripts/ci/workflow-result-verify.sh",
        ".github/workflows/workflow-result-verify.yml",
    }
    requested = sorted(set(core) | set(migrations) | fixed | set(API_COPY_PINS))
    require(set(requested) <= set(tree) and all(tree[path][0] != "120000" for path in requested), "source-roster")
    requests = b"".join(tree[path][1].encode() + b"\n" for path in requested)
    raw, _, _ = controller.run("git_source_blobs", ["git", "cat-file", "--batch"], stdin=requests)
    blobs = parse_blobs(raw, requested, tree)
    hashes = {}
    for path, value in blobs.items():
        actual, _info = stable_file(ROOT / path, 4 * 1024 * 1024, allow_empty=True)
        require(actual == value, "source-disk-association")
        require(
            stat.S_IMODE((ROOT / path).stat().st_mode) & 0o111 == (0o111 if tree[path][0] == "100755" else 0),
            "source-mode",
        )
        hashes[path] = sha(value)
    for path, expected in API_COPY_PINS.items():
        require(hashes[path] == expected, "api-copy-pin")
    references = ast_map_literal(blobs["api/tests/parity/workflow_domain_harness.py"], "REFERENCE_HASHES")
    preparation = literal_assignment(blobs["api/tests/parity/workflow_sql_harness.py"], "PREPARATION")
    require(type(preparation) is tuple and all(type(path) is str for path in preparation), "preparation-roster")
    require(set(references) | set(preparation) <= set(blobs), "consumer-source-roster")
    require(all(hashes[path] == expected for path, expected in references.items()), "reference-source-pin")
    lock = tomllib.loads(blobs["core-rs/Cargo.lock"].decode("utf-8", "strict"))
    require(
        lock.get("version") == 4 and type(lock.get("package")) is list and 1 <= len(lock["package"]) <= 232,
        "lock-shape",
    )
    packages = {}
    for package in lock["package"]:
        name, version = package.get("name"), package.get("version")
        require(
            type(name) is str
            and re.fullmatch(r"[A-Za-z0-9_-]+", name) is not None
            and type(version) is str
            and re.fullmatch(r"[A-Za-z0-9.+_-]+", version) is not None,
            "lock-identity",
        )
        key = f"{name}@{version}"
        require(len(key) <= 256 and key not in packages, "lock-duplicate")
        packages[key] = package
    require("serde_json@1.0.151" in packages, "serde-lock")
    controller.tree = tree
    controller.blobs = blobs
    controller.source_map = hashes
    controller.lock_packages = packages
    controller.core_paths = core
    controller.heads = migration_heads({path: blobs[path] for path in migrations})
    root_digest = hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]
    controller.prepr_prefix = "result-prepr-" + OWNER
    controller.target_prefix = "result-target-" + OWNER
    controller.prepr_project = controller.prepr_prefix + "-" + root_digest
    controller.target_project = controller.target_prefix + "-" + root_digest
    controller.log_directory = Path("/tmp") / ("bifrost-" + controller.target_project)
    require(not controller.log_directory.exists(), "target-log-preexisting")
    require(not (Path.home() / ".netrc").exists(), "anonymous-netrc")
    return hashes


UID_PROBE = r"""
import json
import os
import sys
value = os.stat('/proc/self/ns/user')
sys.stdout.write(json.dumps({'schema':'bifrost.private.result-target-identity/v1','uid':os.geteuid(),'gid':os.getegid(),'user_namespace':{'dev':value.st_dev,'ino':value.st_ino}},sort_keys=True,separators=(',',':'))+'\n')
"""

GROUP_HELPER = r"""
import json
import os
import stat
import sys
fd = None
first = None
try:
    if len(sys.argv) != 2:
        raise ValueError('argc')
    raw = sys.argv[1].encode('ascii')
    if len(raw) > 2048:
        raise ValueError('bound')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    request = json.loads(raw.decode('ascii'),object_pairs_hook=pairs)
    if type(request) is not dict or set(request) != {'schema','path','file','target_gid','user_namespace'}:
        raise ValueError('shape')
    if request['schema'] != 'bifrost.private.result-group-acquire/v2' or request['path'] != '/bifrost-private/result-all-features.env':
        raise ValueError('scope')
    source = request['file']
    namespace = request['user_namespace']
    if type(source) is not dict or set(source) != {'dev','ino','owner_uid','gid'}:
        raise ValueError('file')
    if type(namespace) is not dict or set(namespace) != {'dev','ino'}:
        raise ValueError('namespace')
    if any(type(value) is not int or value < 0 for value in [*source.values(),*namespace.values(),request['target_gid']]):
        raise ValueError('types')
    actual_ns = os.stat('/proc/self/ns/user')
    if os.geteuid() != 0 or os.getegid() != 0 or {'dev':actual_ns.st_dev,'ino':actual_ns.st_ino} != namespace:
        raise ValueError('identity')
    fd = os.open(request['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != 0 or stat.S_IMODE(before.st_mode) != 0o640:
        raise ValueError('profile')
    if (before.st_dev,before.st_ino,before.st_uid,before.st_gid) != (source['dev'],source['ino'],source['owner_uid'],source['gid']):
        raise ValueError('association')
    if os.listxattr(fd):
        raise ValueError('attributes')
    os.fchown(fd,-1,request['target_gid'])
    after = os.fstat(fd)
    if (after.st_dev,after.st_ino,after.st_uid,after.st_nlink,after.st_size,stat.S_IMODE(after.st_mode),after.st_gid,after.st_mtime_ns) != (before.st_dev,before.st_ino,before.st_uid,1,0,0o640,request['target_gid'],before.st_mtime_ns) or os.listxattr(fd):
        raise ValueError('postcondition')
except BaseException as error:
    first = error
finally:
    if fd is not None:
        try:
            os.close(fd)
        except BaseException as error:
            if first is None:
                first = error
if first is not None:
    raise SystemExit(1)
"""

SOFTWARE_READER = r"""
import hashlib
import json
import os
import stat
import sys
# The request is in argv; the unchanged native three-key stdin belongs to observe-schema.
first = None
files = []
try:
    raw = sys.argv[1].encode('ascii')
    if len(raw) > 65536:
        raise ValueError('bound')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    request = json.loads(raw.decode('ascii'),object_pairs_hook=pairs)
    if type(request) is not dict or set(request) != {'schema','uid','gid','user_namespace','paths'} or request['schema'] != 'bifrost.private.result-software-request/v1':
        raise ValueError('shape')
    ns = os.stat('/proc/self/ns/user')
    if type(request['uid']) is not int or request['uid'] != 1000 or os.geteuid() != request['uid'] or type(request['gid']) is not int or os.getegid() != request['gid'] or request['user_namespace'] != {'dev':ns.st_dev,'ino':ns.st_ino}:
        raise ValueError('identity')
    paths = request['paths']
    if type(paths) is not list or not 1 <= len(paths) <= 142 or any(type(path) is not str or not path.isascii() or not 1 <= len(path) <= 256 for path in paths) or paths != sorted(set(paths)):
        raise ValueError('paths')
    def identity(info):
        return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    for path in paths:
        pieces = path.split('/')
        if pieces[0] != '' or any(part in ('','.','..') for part in pieces[1:]) or len(pieces) > 9 or not (path.startswith('/targets/') or path == '/tmp/bifrost/workflow-result-parity/driver'):
            raise ValueError('path-scope')
        owned = []
        ancestors = []
        failure = None
        digest = hashlib.sha256()
        try:
            parent = os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            owned.append(parent)
            ancestors.append((parent,identity(os.fstat(parent))))
            for part in pieces[1:-1]:
                child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
                owned.append(child)
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('ancestor')
                ancestors.append((child,identity(info)))
                parent = child
            before = os.stat(pieces[-1],dir_fd=parent,follow_symlinks=False)
            fd = os.open(pieces[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent)
            owned.append(fd)
            if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) > 0o777 or before.st_nlink != 1 or not 1 <= before.st_size <= 64*1024*1024 or identity(before) != identity(os.fstat(fd)):
                raise ValueError('file')
            count = 0
            while True:
                block = os.read(fd,65536)
                if not block:
                    break
                count += len(block)
                if count > before.st_size:
                    raise ValueError('growth')
                digest.update(block)
            if count != before.st_size or identity(before) != identity(os.fstat(fd)) or identity(before) != identity(os.stat(pieces[-1],dir_fd=parent,follow_symlinks=False)):
                raise ValueError('stable')
            if any(identity(os.fstat(handle)) != expected for handle,expected in ancestors):
                raise ValueError('ancestor-change')
            # Rewalk the actual absolute path while every original ancestor remains retained.
            probe = os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            owned.append(probe)
            if identity(os.fstat(probe)) != ancestors[0][1]:
                raise ValueError('root-change')
            for index,part in enumerate(pieces[1:-1],1):
                following = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=probe)
                owned.append(following)
                if identity(os.fstat(following)) != ancestors[index][1]:
                    raise ValueError('ancestor-path-change')
                probe = following
            if identity(os.stat(pieces[-1],dir_fd=probe,follow_symlinks=False)) != identity(before):
                raise ValueError('file-path-change')
            observed = {'path':path,'dev':before.st_dev,'ino':before.st_ino,'mode':stat.S_IMODE(before.st_mode),'nlink':before.st_nlink,'size':before.st_size,'mtime_ns':before.st_mtime_ns,'ctime_ns':before.st_ctime_ns,'sha256':digest.hexdigest()}
        except BaseException as error:
            failure = error
        finally:
            for handle in reversed(owned):
                try:
                    os.close(handle)
                except BaseException as error:
                    if failure is None:
                        failure = error
        if failure is not None:
            raise failure
        files.append(observed)
    body = json.dumps({'schema':'bifrost.private.result-software-observed/v1','files':files},ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')).encode('ascii')
    if len(body) > 131072:
        raise ValueError('output-bound')
    frame = b'bifrost-software-readback/v1 '+str(len(body)).encode('ascii')+b'\n'+body+b'\n'
    offset = 0
    while offset < len(frame):
        written = os.write(1,frame[offset:])
        if written <= 0:
            raise ValueError('output')
        offset += written
    os.execv('/tmp/bifrost/workflow-result-parity/driver',['/tmp/bifrost/workflow-result-parity/driver','observe-schema'])
except BaseException:
    raise SystemExit(1)
"""


def inspect_many(controller, label, identifiers, *, kind=None):
    require(identifiers and len(identifiers) == len(set(identifiers)), "inspect-identities")
    argv = ["docker", "inspect"] + (["--type", kind] if kind is not None else []) + list(identifiers)
    raw, _, _ = controller.run(label, argv)
    rows = decode(raw)
    require(
        type(rows) is list and len(rows) == len(identifiers) and all(type(row) is dict for row in rows),
        "inspect-cardinality",
    )
    indexed = {}
    for row in rows:
        identity = row.get("Id") if kind != "volume" else row.get("Name")
        require(type(identity) is str and identity not in indexed, "inspect-identity")
        indexed[identity] = row
    require(set(indexed) == set(identifiers), "inspect-association")
    return indexed


def owner_labels(controller, purpose):
    return {
        "bifrost.result.owner": OWNER,
        "bifrost.result.head": controller.candidate["head"],
        "bifrost.result.tree": controller.candidate["tree"],
        "bifrost.result.purpose": purpose,
        "com.docker.compose.project": controller.target_project,
    }


def mount_argument(kind, source, destination, readonly):
    require(
        kind in {"bind", "volume"}
        and all(
            type(value) is str and value and "," not in value and "\n" not in value for value in (source, destination)
        ),
        "mount-argument",
    )
    return f"type={kind},src={source},dst={destination}" + (",readonly" if readonly else "")


def create_native(
    controller,
    purpose,
    image,
    command,
    *,
    network="none",
    mounts=(),
    environment=None,
    env_file=None,
    entrypoint=None,
    user=None,
    caps=None,
    stdin=False,
    workdir=None,
    readonly=False,
):
    require(purpose not in controller.resources and IMAGE_ID.fullmatch(image) is not None, "native-create-scope")
    name = "result-" + OWNER + "-" + purpose.replace("_", "-")
    labels = owner_labels(controller, purpose)
    argv = ["docker", "create", "--name", name, "--network", network]
    if readonly:
        argv.append("--read-only")
    for key, value in labels.items():
        argv.extend(["--label", key + "=" + value])
    if stdin:
        argv.append("--interactive")
    if entrypoint is not None:
        argv.extend(["--entrypoint", entrypoint])
    if user is not None:
        argv.extend(["--user", user])
    if workdir is not None:
        argv.extend(["--workdir", workdir])
    if caps is not None:
        argv.extend(["--cap-drop", "ALL"])
        for capability in caps:
            argv.extend(["--cap-add", capability])
    for kind, source, destination, readonly in mounts:
        argv.extend(["--mount", mount_argument(kind, source, destination, readonly)])
    for key, value in (environment or {}).items():
        require(key in {"CARGO_HOME", "CARGO_TARGET_DIR", "CARGO_TERM_COLOR"}, "native-env-scope")
        argv.extend(["--env", key + "=" + value])
    if env_file is not None:
        argv.extend(["--env-file", str(env_file)])
    argv.extend([image, *command])
    record = {
        "name": name,
        "cid": None,
        "image": image,
        "command": list(command),
        "labels": labels,
        "network": network,
        "mounts": tuple(mounts),
        "environment": dict(environment or {}),
        "entrypoint": entrypoint,
        "user": user,
        "caps": None if caps is None else tuple(caps),
        "stdin": stdin,
        "readonly": readonly,
        "workdir": workdir,
        "env_file": env_file,
        "created": False,
        "started": False,
        "terminal": False,
        "exit": None,
        "removed": False,
        "baseline": None,
    }
    controller.resources[purpose] = record
    raw, _, _ = controller.run("create_" + purpose, argv)
    cid = raw.decode("ascii", "strict").removesuffix("\n")
    require(re.fullmatch(r"[0-9a-f]{64}", cid) is not None, "native-cid")
    record["cid"] = cid
    record["created"] = True
    return record


def native_network_association(controller, record, row, *, require_association):
    if record["network"] not in {"none", "bridge"}:
        require(record["network"] == controller.network_name, "native-network-selected")
        networks = row.get("NetworkSettings", {}).get("Networks")
        require(type(networks) is dict and set(networks) == {record["network"]}, "native-network-membership")
        endpoint = networks[record["network"]]
        require(type(endpoint) is dict, "native-network-endpoint")
        actual_id = endpoint.get("NetworkID")
        if actual_id:
            require(actual_id == controller.network_id, "native-network-identity")
            record["network_id"] = actual_id
        else:
            require(not require_association and not record["started"], "native-network-identity-unavailable")
        if require_association:
            require(record.get("network_id") == controller.network_id, "native-network-terminal-association")


def validate_native(controller, record, row, *, terminal=False):
    try:
        validate_native_facts(controller, record, row, terminal=terminal)
    except BaseException:
        record["custody_failed"] = True
        raise


def validate_native_facts(controller, record, row, *, terminal=False):
    require(
        row.get("Id") == record["cid"]
        and row.get("Name") == "/" + record["name"]
        and row.get("Image") == record["image"],
        "native-identity",
    )
    config, host, state = row.get("Config"), row.get("HostConfig"), row.get("State")
    require(type(config) is dict and type(host) is dict and type(state) is dict, "native-config")
    image_config = controller.qualified_images[record["image"]]["Config"]
    expected_labels = dict(image_config.get("Labels") or {}) | record["labels"]
    require(config.get("Labels") == expected_labels, "native-labels")
    require(
        config.get("Image") == record["image"]
        and config.get("Cmd") == record["command"]
        and config.get("OpenStdin") is record["stdin"],
        "native-command",
    )
    require(
        host.get("ReadonlyRootfs") is record["readonly"]
        and host.get("Privileged") is False
        and host.get("NetworkMode") == record["network"]
        and host.get("UsernsMode") == ""
        and host.get("PidMode") == ""
        and host.get("IpcMode") == "private",
        "native-isolation",
    )
    if record["caps"] is None:
        require(host.get("CapDrop") is None and host.get("CapAdd") is None, "native-capabilities-original")
    else:
        require(
            host.get("CapDrop") == ["ALL"] and (host.get("CapAdd") or []) == list(record["caps"]), "native-capabilities"
        )
    require(
        config.get("Entrypoint")
        == ([record["entrypoint"]] if record["entrypoint"] is not None else image_config.get("Entrypoint")),
        "native-entrypoint",
    )
    require(
        config.get("User") == (record["user"] if record["user"] is not None else image_config.get("User")),
        "native-user",
    )
    require(
        config.get("WorkingDir")
        == (record["workdir"] if record["workdir"] is not None else image_config.get("WorkingDir")),
        "native-workdir",
    )
    mounts = row.get("Mounts")
    require(type(mounts) is list and len(mounts) == len(record["mounts"]), "native-mount-count")
    by_destination = {mount.get("Destination"): mount for mount in mounts if type(mount) is dict}
    require(len(by_destination) == len(mounts), "native-mount-duplicate")
    for kind, source, destination, readonly in record["mounts"]:
        mount = by_destination.get(destination)
        require(type(mount) is dict and mount.get("Type") == kind and mount.get("RW") is (not readonly), "native-mount")
        require((mount.get("Source") if kind == "bind" else mount.get("Name")) == source, "native-mount-source")
    values = config.get("Env")
    require(type(values) is list and all(type(value) is str and "=" in value for value in values), "native-environment")
    pairs = [value.split("=", 1) for value in values]
    require(len({key for key, _value in pairs}) == len(pairs), "native-environment-duplicate")
    environment = dict(pairs)
    require(
        all(environment.get(key) == value for key, value in record["environment"].items()),
        "native-environment-selected",
    )
    require(
        not any(key in environment for key in ("GH_TOKEN", "GITHUB_TOKEN", "BIFROST_ACTION_PIN_TOKEN_FILE")),
        "native-credentials",
    )
    require(not any(key.startswith("PGSSL") for key in environment), "native-transport-environment-profile")
    if record["env_file"] is not None:
        require(environment.get("BIFROST_RUST_TEST_DATABASE_URL") == controller.native_url, "native-dsn")
    else:
        require("BIFROST_RUST_TEST_DATABASE_URL" not in environment, "native-dsn-unexpected")
    native_network_association(controller, record, row, require_association=terminal)
    immutable = {key: row.get(key) for key in ("Id", "Name", "Image", "Config", "HostConfig", "Mounts")}
    if record["baseline"] is None:
        record["baseline"] = immutable
    else:
        require(immutable == record["baseline"], "native-config-drift")
    if terminal:
        require(
            state.get("Status") == "exited"
            and state.get("Running") is False
            and state.get("Paused") is False
            and state.get("Restarting") is False
            and state.get("Dead") is False
            and state.get("OOMKilled") is False
            and state.get("ExitCode") == record["exit"] == 0
            and integer(state.get("Pid"), 0, 0),
            "native-terminal",
        )
        record["terminal"] = True
    else:
        require(
            state.get("Status") == "created" and state.get("Running") is False and state.get("Pid") == 0,
            "native-created",
        )


def inspect_native(controller, label, purposes, *, terminal=False):
    records = [controller.resources[purpose] for purpose in purposes]
    require(all(record["created"] for record in records), "native-inspect-created")
    indexed = inspect_many(controller, label, [record["cid"] for record in records], kind="container")
    for record in records:
        validate_native(controller, record, indexed[record["cid"]], terminal=terminal)
    return indexed


def start_native(controller, purpose, *, stdin=b"", cap=None):
    record = controller.resources[purpose]
    require(record["baseline"] is not None and not record["started"], "native-start-admission")
    record["started"] = True
    raw, error, exit_code = controller.run(
        "start_" + purpose, ["docker", "start", "--attach", "--interactive", record["cid"]], stdin=stdin, cap=cap
    )
    record["exit"] = exit_code
    return raw, error, exit_code


def workspace_graph(controller, raw, configuration):
    value = decode(raw)
    require(
        type(value) is dict and value.get("version") == 1 and value.get("workspace_root") == "/workspace/core-rs",
        "metadata-root",
    )
    packages, resolve = value.get("packages"), value.get("resolve")
    require(
        type(packages) is list
        and 1 <= len(packages) <= 232
        and type(resolve) is dict
        and type(resolve.get("nodes")) is list
        and 1 <= len(resolve["nodes"]) <= 232,
        "metadata-cardinality",
    )
    by_id = {}
    by_name = {}
    private = {}
    projected_packages = []
    for package in packages:
        require(type(package) is dict and type(package.get("id")) is str, "metadata-package")
        name, version = package.get("name"), package.get("version")
        identity = f"{name}@{version}"
        require(
            identity in controller.lock_packages and package["id"] not in by_id and identity not in by_name,
            "metadata-lock-membership",
        )
        source = package.get("source")
        manifest = package.get("manifest_path")
        require(
            type(manifest) is str
            and type(package.get("features")) is dict
            and type(package.get("dependencies")) is list
            and type(package.get("targets")) is list,
            "metadata-domains",
        )
        if source is None:
            require(
                manifest.startswith("/workspace/core-rs/crates/") and manifest.endswith("/Cargo.toml"),
                "metadata-workspace-source",
            )
            relative = "core-rs/" + manifest.removeprefix("/workspace/core-rs/")
            require(
                relative in controller.blobs and "source" not in controller.lock_packages[identity],
                "metadata-workspace-identity",
            )
            original = tomllib.loads(controller.blobs[relative].decode("utf-8", "strict"))
            require(original.get("package", {}).get("name") == name, "metadata-manifest-name")
            source_kind = "workspace"
        else:
            require(
                source == controller.lock_packages[identity].get("source")
                and source.startswith("registry+")
                and manifest.startswith("/cargo/registry/src/")
                and manifest.endswith("/Cargo.toml"),
                "metadata-registry-source",
            )
            source_kind = "locked_registry"
        domains = set(package["features"])
        require(
            all(
                type(feature) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", feature) is not None
                for feature in domains
            ),
            "metadata-feature-domain",
        )
        aliases = set()
        for dependency in package["dependencies"]:
            require(
                type(dependency) is dict
                and type(dependency.get("name")) is str
                and type(dependency.get("optional")) is bool,
                "metadata-dependency",
            )
            alias = dependency.get("rename") or dependency["name"]
            require(type(alias) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", alias) is not None, "metadata-alias")
            if dependency["optional"]:
                aliases.add(alias)
        by_id[package["id"]] = identity
        by_name[identity] = package
        private[identity] = {"features": domains | aliases, "package": package}
        projected_packages.append({"name": name, "version": version, "source_kind": source_kind})
    require(set(value.get("workspace_members", [])) <= set(by_id), "metadata-workspace-members")
    nodes = []
    selected = set()
    features = set()
    for node in resolve["nodes"]:
        require(
            type(node) is dict
            and node.get("id") in by_id
            and node["id"] not in selected
            and type(node.get("features")) is list
            and type(node.get("deps")) is list,
            "metadata-node",
        )
        selected.add(node["id"])
        identity = by_id[node["id"]]
        observed = node["features"]
        require(
            all(type(feature) is str for feature in observed)
            and observed == sorted(set(observed))
            and set(observed) <= private[identity]["features"],
            "metadata-resolved-features",
        )
        require(
            identity != "serde_json@1.0.151" or not {"float_roundtrip", "arbitrary_precision"} & set(observed),
            "metadata-forbidden",
        )
        features.update(identity + "/" + feature for feature in observed)
        edges = []
        seen = set()
        for edge in node["deps"]:
            require(
                type(edge) is dict
                and edge.get("pkg") in by_id
                and type(edge.get("name")) is str
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", edge["name"]) is not None
                and type(edge.get("dep_kinds")) is list
                and edge["dep_kinds"],
                "metadata-edge",
            )
            target = by_id[edge["pkg"]]
            edge_key = (target, edge["name"])
            require(edge_key not in seen, "metadata-edge-duplicate")
            seen.add(edge_key)
            kinds = []
            for kind in edge["dep_kinds"]:
                require(
                    type(kind) is dict
                    and kind.get("kind") in {None, "build", "dev"}
                    and (kind.get("target") is None or type(kind["target"]) is str),
                    "metadata-edge-kind",
                )
                kinds.append("normal" if kind["kind"] is None else kind["kind"])
            edges.append({"package": target, "name": edge["name"], "kinds": sorted(set(kinds))})
        nodes.append(
            {
                "package": identity,
                "features": observed,
                "edges": sorted(edges, key=lambda row: (row["package"], row["name"])),
            }
        )
    require("serde_json@1.0.151" in by_name, "metadata-serde")
    serde = by_name["serde_json@1.0.151"]
    require(private["serde_json@1.0.151"]["features"] == set(FEATURE_CATALOG), "serde-manifest-feature-domain")
    projection = {
        "schema": "bifrost.test.workflow-result-workspace-metadata/v1",
        "candidate": controller.candidate,
        "configuration": configuration,
        "target": "x86_64-unknown-linux-gnu",
        "packages": sorted(projected_packages, key=lambda row: (row["name"], row["version"])),
        "nodes": sorted(nodes, key=lambda row: row["package"]),
    }
    encoded = encode(projection)
    require(len(encoded) <= 1024 * 1024, "metadata-projection-bound")
    controller.package_domains.update(private)
    controller.serde_manifest = serde
    return {"sha256": sha(encoded), "packages": sorted(by_name), "features": sorted(features)}


def artifact_path(path, configuration):
    prefix = f"/targets/{configuration}/"
    require(
        type(path) is str
        and path.isascii()
        and 1 <= len(path) <= 256
        and path.startswith(prefix)
        and not any(ord(char) < 32 or char == "\\" for char in path),
        "artifact-path",
    )
    parts = path.split("/")
    require(
        all(part not in {"", ".", ".."} for part in parts[1:]) and parts[3] in {"debug", "release"},
        "artifact-profile-path",
    )
    return path


def unit_profile(value):
    closed(value, {"opt_level", "debuginfo", "debug_assertions", "overflow_checks", "test"}, "cargo-profile")
    require(
        type(value["test"]) is bool
        and type(value["debug_assertions"]) is bool
        and type(value["overflow_checks"]) is bool
        and value["opt_level"] in {"0", "1", "2", "3", "s", "z"}
        and (
            value["debuginfo"] is None
            or integer(value["debuginfo"], 0, 2)
            or value["debuginfo"] in {"line-directives-only", "line-tables-only"}
        ),
        "cargo-profile-types",
    )
    return [value["test"], value["opt_level"], value["debuginfo"], value["debug_assertions"], value["overflow_checks"]]


def cargo_artifacts(controller, raw, invocation):
    require(raw.endswith(b"\n"), "cargo-output-eof")
    configuration = TARGET_DIRECTORIES[invocation]
    root_records = {}
    units = []
    discriminators = set()
    text = []
    finished = False
    for line in raw.splitlines():
        if not line.startswith(b"{"):
            text.append(line.decode("utf-8", "strict"))
            continue
        row = decode(line)
        require(type(row) is dict and type(row.get("reason")) is str, "cargo-message")
        reason = row["reason"]
        if reason == "build-finished":
            require(not finished and row.get("success") is True, "cargo-build-finished")
            finished = True
            continue
        require(
            not finished or reason not in {"compiler-artifact", "compiler-message", "build-script-executed"},
            "cargo-post-finish",
        )
        require(reason in {"compiler-artifact", "compiler-message", "build-script-executed"}, "cargo-message-reason")
        if reason != "compiler-artifact":
            continue
        package_id = row.get("package_id")
        require(type(package_id) is str, "cargo-package-id")
        matched = [
            (identity, domain)
            for identity, domain in controller.package_domains.items()
            if domain["package"]["id"] == package_id
        ]
        require(len(matched) == 1, "cargo-package-association")
        identity, domain = matched[0]
        require(row.get("manifest_path") == domain["package"]["manifest_path"], "cargo-manifest-association")
        target = row.get("target")
        require(
            type(target) is dict
            and target in domain["package"]["targets"]
            and type(row.get("features")) is list
            and type(row.get("fresh")) is bool
            and type(row.get("filenames")) is list,
            "cargo-target-association",
        )
        observed = row["features"]
        require(
            all(type(feature) is str for feature in observed)
            and observed == sorted(set(observed))
            and set(observed) <= domain["features"],
            "cargo-unit-features",
        )
        profile = unit_profile(row.get("profile"))
        files = [artifact_path(path, configuration) for path in row["filenames"]]
        require(files and len(files) <= 16 and len(files) == len(set(files)), "cargo-unit-files")
        if identity == "serde_json@1.0.151":
            require(
                not {"float_roundtrip", "arbitrary_precision"} & set(observed)
                and ("raw_value" in observed) is (invocation != 0),
                "serde-universal-feature-gate",
            )
            require(row.get("executable") is None and len(files) <= 2, "serde-output-profile")
            if (
                target.get("name") == "serde_json"
                and target.get("kind") == ["lib"]
                and target.get("crate_types") == ["lib"]
            ):
                target_index = 0
                require(all(path.endswith((".rlib", ".rmeta")) for path in files), "serde-library-output")
            else:
                require(
                    target.get("name") == "build-script-build"
                    and target.get("kind") == ["custom-build"]
                    and target.get("crate_types") == ["bin"],
                    "serde-build-output",
                )
                target_index = 1
            association = {
                "package": identity,
                "package_id": package_id,
                "manifest": row["manifest_path"],
                "target": target,
                "features": observed,
                "profile": row["profile"],
                "files": sorted(files),
                "executable": row.get("executable"),
                "role": "serde_unit",
            }
            discriminator = encode(association)
            require(discriminator not in discriminators, "serde-unit-duplicate-or-contradiction")
            discriminators.add(discriminator)
            units.append(
                {
                    "discriminator": discriminator,
                    "association": association,
                    "paths": files,
                    "safe": [
                        target_index,
                        profile,
                        [FEATURE_CATALOG.index(feature) for feature in observed],
                        row["fresh"],
                    ],
                }
            )
        for root_id in REQUIRED_ROOTS[invocation]:
            name, target_name, kind, source = ROOT_CATALOG[root_id]
            if identity != name + "@0.1.0" or target.get("name") != target_name or target.get("kind") != [kind]:
                continue
            require(target.get("src_path") == "/workspace/core-rs/" + source, "cargo-root-source")
            if profile[0] is not (invocation != 7):
                continue
            executable = artifact_path(row.get("executable"), configuration)
            require(executable in files and root_id not in root_records, "cargo-root-executable")
            root_records[root_id] = {
                "path": executable,
                "artifact": row,
                "association": {
                    "package": identity,
                    "package_id": package_id,
                    "manifest": row["manifest_path"],
                    "target": target,
                    "features": observed,
                    "profile": row["profile"],
                    "files": sorted(files),
                    "executable": executable,
                    "role": "selected_root",
                },
            }
    require(finished and units and set(root_records) == set(REQUIRED_ROOTS[invocation]), "cargo-complete-roots")
    require(
        sum(len(value["units"]) for value in controller.invocations.values()) + len(units) <= 64, "serde-total-bound"
    )
    observation = {
        "id": invocation,
        "units": sorted(units, key=lambda value: value["discriminator"]),
        "roots": root_records,
        "completed": [],
        "complete": False,
        "text": text,
    }
    controller.invocations[invocation] = observation
    return observation


EXPECTED_COMPLETED = ((0,), (0,), (35,), (43,), (52,), tuple(range(38)), tuple(range(38, 59)), ())
TEST_ROOTS = {
    "contracts_lib": 0,
    "domain_lib": 1,
    "db_numeric": 2,
    "db_lib": 2,
    "db_live": 2,
    "db_example": 5,
    "domain_example": 6,
}


def cargo_test_completion(controller, observation, stderr):
    invocation = observation["id"]
    require(invocation < 7, "cargo-test-invocation")
    running = []
    for line in stderr.decode("utf-8", "strict").splitlines():
        match = re.fullmatch(r"\s*Running (?:unittests|tests) [^\n]* \((/targets/[^() ]+)\)", line)
        if match:
            running.append(artifact_path(match[1], TARGET_DIRECTORIES[invocation]))
    by_executable = {value["path"]: root_id for root_id, value in observation["roots"].items()}
    require(len(running) == len(set(running)) and set(running) == set(by_executable), "cargo-running-roots")
    blocks = []
    current = None
    for line in observation["text"]:
        match = re.fullmatch(r"running ([0-9]+) tests?", line)
        if match:
            require(current is None, "cargo-harness-overlap")
            current = {"count": int(match[1]), "tests": {}, "summary": None}
            require(current["count"] <= 59, "cargo-harness-count")
            continue
        test = re.fullmatch(r"test ([A-Za-z0-9_:]+) \.\.\. (ok|FAILED|ignored)", line)
        if test:
            require(current is not None and test[1] not in current["tests"], "cargo-test-identity")
            current["tests"][test[1]] = test[2]
            continue
        summary = re.fullmatch(
            r"test result: (ok|FAILED)\. ([0-9]+) passed; ([0-9]+) failed; ([0-9]+) ignored; ([0-9]+) measured; ([0-9]+) filtered out; finished in ([0-9.]+)s",
            line,
        )
        if summary:
            require(current is not None, "cargo-summary-without-harness")
            count_values = [int(summary[index]) for index in range(2, 7)]
            require(
                summary[1] == "ok"
                and count_values[1:4] == [0, 0, 0]
                and count_values[0] == current["count"] == len(current["tests"])
                and all(status == "ok" for status in current["tests"].values()),
                "cargo-test-terminal-outcome",
            )
            elapsed = float(summary[7])
            require(math.isfinite(elapsed) and elapsed >= 0, "cargo-test-time")
            current["summary"] = count_values
            blocks.append(current)
            current = None
            continue
        require(not line.startswith(("test ", "running ", "test result:")), "cargo-unmatched-harness-line")
    require(current is None and len(blocks) == len(running), "cargo-test-block-association")
    completed = []
    catalog = {(TEST_ROOTS[row["source_group"]], row["test"]): row["id"] for row in TEST_CATALOG}
    for executable, block in zip(running, blocks, strict=True):
        root_id = by_executable[executable]
        for name in block["tests"]:
            require((root_id, name) in catalog, "cargo-unrecognized-test")
            completed.append(catalog[root_id, name])
        required = {
            identifier
            for identifier in EXPECTED_COMPLETED[invocation]
            if TEST_ROOTS[TEST_CATALOG[identifier]["source_group"]] == root_id
        }
        actual = {catalog[root_id, name] for name in block["tests"]}
        require(actual == required, "cargo-root-completed-roster")
    require(
        len(completed) == len(set(completed)) and sorted(completed) == list(EXPECTED_COMPLETED[invocation]),
        "cargo-completed-roster",
    )
    observation["completed"] = sorted(completed)
    observation["test_settled"] = True


def schema_observation(raw, case_id, phase):
    require(raw.endswith(b"\n") and raw.count(b"\n") == 1 and len(raw) <= 4096, "schema-output-frame")
    value = decode(raw, 4096)
    closed(
        value,
        {"schema", "case_id", "phase", "server_major", "heads", "device_floor", "catalog", "complete"},
        "schema-observed-shape",
    )
    require(
        value["schema"] == "bifrost.test.workflow-schema-observed/v1"
        and value["case_id"] == case_id
        and value["phase"] == phase
        and type(value["server_major"]) is int
        and value["server_major"] == 16
        and value["device_floor"] is True
        and value["complete"] is True,
        "schema-observed-profile",
    )
    heads = value["heads"]
    require(
        type(heads) is list
        and 1 <= len(heads) <= 8
        and all(type(head) is str and re.fullmatch(r"[A-Za-z0-9_]{1,32}", head) is not None for head in heads)
        and heads == sorted(set(heads)),
        "schema-observed-heads",
    )
    catalog = value["catalog"]
    closed(catalog, {"relations", "columns", "constraints", "indexes", "combined_sha256"}, "schema-catalog")
    for name in ("relations", "columns", "constraints", "indexes"):
        closed(catalog[name], {"count", "sha256"}, "schema-catalog-component")
        require(
            integer(catalog[name]["count"], 0, 1024)
            and type(catalog[name]["sha256"]) is str
            and HEX64.fullmatch(catalog[name]["sha256"]) is not None,
            "schema-catalog-facts",
        )
    require(
        type(catalog["combined_sha256"]) is str and HEX64.fullmatch(catalog["combined_sha256"]) is not None,
        "schema-catalog-digest",
    )
    return value


def software_observation(controller, raw, requested):
    match = re.match(rb"bifrost-software-readback/v1 ([0-9]{1,6})\n", raw)
    require(match is not None, "software-frame")
    length = int(match[1])
    require(1 <= length <= 131072, "software-frame-bound")
    end = match.end() + length
    require(raw[end : end + 1] == b"\n", "software-frame-eof")
    value = decode(raw[match.end() : end], 131072)
    closed(value, {"schema", "files"}, "software-observed-root")
    require(
        value["schema"] == "bifrost.private.result-software-observed/v1"
        and type(value["files"]) is list
        and len(value["files"]) == len(requested),
        "software-observed-membership",
    )
    result = {}
    for row in value["files"]:
        closed(
            row,
            {"path", "dev", "ino", "mode", "nlink", "size", "mtime_ns", "ctime_ns", "sha256"},
            "software-observed-file",
        )
        path = row["path"]
        require(type(path) is str and path in requested and path not in result, "software-observed-path")
        require(
            integer(row["dev"], 0, 2**64 - 1)
            and integer(row["ino"], 1, 2**64 - 1)
            and integer(row["mode"], 0, 0o777)
            and row["nlink"] == 1
            and type(row["nlink"]) is int
            and integer(row["size"], 1, 64 * 1024 * 1024)
            and integer(row["mtime_ns"], -(2**63), 2**63 - 1)
            and integer(row["ctime_ns"], -(2**63), 2**63 - 1)
            and type(row["sha256"]) is str
            and HEX64.fullmatch(row["sha256"]) is not None,
            "software-observed-types",
        )
        result[path] = row
    require(list(result) == requested, "software-observed-order")
    copied = "/tmp/bifrost/workflow-result-parity/driver"
    require(result[copied]["sha256"] == controller.binary["sha256"], "software-copied-release")
    for observation in controller.invocations.values():
        for root_id, root in observation["roots"].items():
            root["sha256"] = result[root["path"]]["sha256"]
            if observation["id"] == 7:
                require(root_id == 5 and root["sha256"] == controller.binary["sha256"], "software-release-root")
        for unit in observation["units"]:
            require(all(path in result for path in unit["paths"]), "software-unit-closure")
        observation["complete"] = observation["id"] == 7 or observation.get("test_settled") is True
    return raw[end + 1 :]


def copy_input_admission(controller):
    require(
        controller.source_map["api/Dockerfile.dev"]
        == "54c271f4dd2f95c2b1644c7a7cce11aa5f21ca47126b8afe8ee2dff1c27bca2c",
        "api-recipe",
    )
    require(
        controller.source_map[".dockerignore"] == "992cfb524cc7b8e762fd0e57f3317cdf0980cd99cde1c5d2cdfeaa5cf530cf5d",
        "api-ignore",
    )
    require(not os.path.lexists(ROOT / "api/Dockerfile.dev.dockerignore"), "api-specific-ignore")
    matched = set()
    for source in API_COPY:
        if source.endswith("/"):
            directory = ROOT / source.removesuffix("/")
            require(stat.S_ISDIR(os.stat(directory, follow_symlinks=False).st_mode), "copy-directory")
            pending = [directory]
            walked = 0
            while pending:
                current = pending.pop()
                for child in current.iterdir():
                    walked += 1
                    require(walked <= 4096, "copy-member-bound")
                    info = os.stat(child, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(child)
                    else:
                        require(stat.S_ISREG(info.st_mode), "copy-member-kind")
                        matched.add(child.relative_to(ROOT).as_posix())
        elif "*" in source:
            parent, pattern = source.rsplit("/", 1)
            candidates = [child for child in (ROOT / parent).iterdir() if fnmatch.fnmatchcase(child.name, pattern)]
            require(candidates, "copy-wildcard-empty")
            for child in candidates:
                require(stat.S_ISREG(os.stat(child, follow_symlinks=False).st_mode), "copy-wildcard-kind")
                matched.add(child.relative_to(ROOT).as_posix())
        else:
            matched.add(source)
    require(matched == set(API_COPY_PINS), "copy-physical-membership")
    for path in sorted(matched):
        raw, info = stable_file(ROOT / path, 4 * 1024 * 1024, allow_empty=True)
        require(
            sha(raw) == API_COPY_PINS[path] == controller.source_map[path]
            and stat.S_IMODE(info.st_mode) & 0o111 == (0o111 if controller.tree[path][0] == "100755" else 0),
            "copy-physical-bytes",
        )


def write_private_record(controller, path, raw, *, mode=0o600, limit=1024 * 1024):
    require(type(raw) is bytes and 0 < len(raw) <= limit and not os.path.lexists(path), "private-record-bound")
    fd = None
    original = None
    identity = None
    controller.owned_files.append(
        {"path": path, "identity": None, "removed": None, "fd": None, "close_attempted": False, "closed": None}
    )
    owned = controller.owned_files[-1]
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        owned["fd"] = fd
        owned["closed"] = False
        acquired = os.fstat(fd)
        owned["identity"] = (acquired.st_dev, acquired.st_ino)
        require(
            stat.S_ISREG(acquired.st_mode)
            and acquired.st_uid == os.geteuid()
            and acquired.st_nlink == 1
            and acquired.st_size == 0,
            "private-record-acquisition",
        )
        os.fchmod(fd, mode)
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            require(count > 0, "private-record-write")
            view = view[count:]
        os.fsync(fd)
        identity = os.fstat(fd)
        require(
            identity.st_size == len(raw)
            and stat.S_IMODE(identity.st_mode) == mode
            and (identity.st_dev, identity.st_ino) == owned["identity"],
            "private-record-stable",
        )
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            owned["close_attempted"] = True
            try:
                os.close(fd)
                owned["closed"] = True
            except BaseException as error:
                owned["closed"] = None
                if original is None:
                    original = error
            finally:
                owned["fd"] = None  # A raised close leaves unknown disposition, never a retryable number.
    if original is not None:
        raise original
    actual = os.stat(path, follow_symlinks=False)
    require(file_identity(actual) == file_identity(identity), "private-record-path")
    return actual


def publish_decision(controller, family, *, rejected=False, reason="frontend_unverified"):
    require(
        family in {"endpoint", "after"}
        and reason in {"frontend_unverified", "source_association", "deadline", "cleanup"},
        "decision-scope",
    )
    schema = (
        "bifrost.private.result-endpoint-decision/v1"
        if family == "endpoint"
        else "bifrost.private.result-after-decision/v1"
    )
    phase = "frontend_observed_before_sql" if family == "endpoint" else "frontend_observed_after_last_actor"
    value = {
        "schema": schema,
        "candidate": controller.candidate,
        "invocation_uuid": OWNER,
        "phase": phase,
        "decision": "reject" if rejected else "admit",
    }
    if rejected:
        value["reason"] = reason
    suffix = "failed" if rejected else "admit"
    other = "admit" if rejected else "failed"
    base = ".result-endpoint-" if family == "endpoint" else ".result-after-"
    final = controller.log_directory / f"{base}{OWNER}-{suffix}.json"
    temporary = controller.log_directory / f"{base}{OWNER}-{suffix}.part.json"
    require(not os.path.lexists(controller.log_directory / f"{base}{OWNER}-{other}.json"), "decision-conflict")
    directory = os.stat(controller.log_directory, follow_symlinks=False)
    require(
        stat.S_ISDIR(directory.st_mode) and directory.st_uid == 1000 and stat.S_IMODE(directory.st_mode) == 0o777,
        "shared-directory",
    )
    raw = encode(value)
    info = write_private_record(controller, temporary, raw, mode=0o644, limit=4096)
    owned = {"path": final, "identity": None, "removed": None}
    controller.owned_files.append(owned)
    os.link(temporary, final, follow_symlinks=False)
    owned["identity"] = (info.st_dev, info.st_ino)
    observed = os.stat(final, follow_symlinks=False)
    require(
        stat.S_ISREG(observed.st_mode)
        and observed.st_nlink == 2
        and observed.st_uid == os.geteuid()
        and stat.S_IMODE(observed.st_mode) == 0o644
        and (observed.st_dev, observed.st_ino) == owned["identity"],
        "decision-publication",
    )
    os.unlink(temporary)
    controller.owned_files[-2]["removed"] = True
    observed = os.stat(final, follow_symlinks=False)
    require(observed.st_nlink == 1 and (observed.st_dev, observed.st_ino) == owned["identity"], "decision-no-replace")
    return value


FRONTEND_PROCESS = r"""
set -eu
record_file() {
  tag=$1; path=$2; bound=$3
  length=$(dd if="$path" bs="$((bound+1))" count=1 2>/dev/null | wc -c)
  case "$length" in ''|*[!0-9]*) exit 1;; esac
  [ "$length" -le "$bound" ] || exit 1
  printf 'bifrost-proc/v1 %s %s\n' "$tag" "$length"
  dd if="$path" bs="$((bound+1))" count=1 2>/dev/null
  printf '\n'
}
record_value() {
  tag=$1; value=$2
  [ "${#value}" -le 4096 ] || exit 1
  printf 'bifrost-proc/v1 %s %s\n%s\n' "$tag" "${#value}" "$value"
}
record_file stat /proc/1/stat 4096
record_file cmdline /proc/1/cmdline 4096
exe=$(readlink /proc/1/exe)
cwd=$(readlink /proc/1/cwd)
netns=$(readlink /proc/1/ns/net)
[ "$exe" = /usr/bin/pgbouncer ] || exit 1
record_value exe "$exe"
record_value cwd "$cwd"
record_value netns "$netns"
size=$(stat -Lc '%s' /proc/1/exe)
case "$size" in ''|*[!0-9]*) exit 1;; esac
[ "$size" -gt 0 ] && [ "$size" -le 67108864 ] || exit 1
record_value exe_bytes "$size"
hash=$(sha256sum /proc/1/exe)
hash=${hash%% *}
record_value exe_sha256 "$hash"
set -- /proc/1/fd/*
[ "$#" -le 2048 ] || exit 1
sockets=''
count=0
for path do
  target=$(readlink "$path")
  case "$target" in
    socket:\[*\])
      count=$((count+1)); [ "$count" -le 128 ] || exit 1
      sockets="${sockets}${target}
";;
  esac
done
record_value sockets "$sockets"
printf 'bifrost-proc-end/v1\n'
"""

FRONTEND_TCP = r"""
set -eu
for table in tcp tcp6; do
  file=/proc/1/net/$table
  length=$(dd if="$file" bs=65537 count=1 2>/dev/null | wc -c)
  case "$length" in ''|*[!0-9]*) exit 1;; esac
  [ "$length" -le 65536 ] || exit 1
  printf 'bifrost-tcp/v1 %s %s\n' "$table" "$length"
  dd if="$file" bs=65537 count=1 2>/dev/null
  printf '\n'
done
printf 'bifrost-tcp-end/v1\n'
"""

FRONTEND_CONFIG = r"""
set -eu
opened=0
trap 'first=$?; if [ "$opened" -eq 1 ]; then if exec 3<&-; then closed=0; else closed=$?; fi; if [ "$first" -eq 0 ] && [ "$closed" -ne 0 ]; then first=$closed; fi; fi; exit "$first"' EXIT
path=$1
[ "$path" = /etc/pgbouncer/pgbouncer.ini ] || exit 1
for ancestor in /etc /etc/pgbouncer; do
  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1
done
[ -f "$path" ] && [ ! -L "$path" ] || exit 1
format=$(printf '%%d\t%%i\t%%f\t%%h\t%%s\t%%y\t%%z')
exec 3< "$path"
opened=1
before=$(stat -Lc "$format" /proc/self/fd/3)
actual=$(stat -Lc "$format" "$path")
[ "$before" = "$actual" ] || exit 1
for ancestor in /etc /etc/pgbouncer; do
  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1
done
size=$(stat -Lc '%s' /proc/self/fd/3)
links=$(stat -Lc '%h' /proc/self/fd/3)
case "$size" in ''|*[!0-9]*) exit 1;; esac
[ "$size" -gt 0 ] && [ "$size" -le 65536 ] && [ "$links" = 1 ] || exit 1
printf 'bifrost-config-read/v1\nbefore %s\n%s\npayload %s\n' "${#before}" "$before" "$size"
dd bs=65537 count=1 <&3 2>/dev/null
printf '\n'
after=$(stat -Lc "$format" /proc/self/fd/3)
actual=$(stat -Lc "$format" "$path")
[ "$before" = "$after" ] && [ "$before" = "$actual" ] && [ ! -L "$path" ] || exit 1
for ancestor in /etc /etc/pgbouncer; do
  [ -d "$ancestor" ] && [ ! -L "$ancestor" ] || exit 1
done
exec 3<&-
opened=0
printf 'after %s\n%s\nend\n' "${#after}" "$after"
"""

FRONTEND_ENTRYPOINT = r"""
set -eu
[ -f /entrypoint.sh ] && [ ! -L /entrypoint.sh ] || exit 1
size=$(stat -c '%s' /entrypoint.sh)
case "$size" in ''|*[!0-9]*) exit 1;; esac
[ "$size" -gt 0 ] && [ "$size" -le 16384 ] || exit 1
hash=$(sha256sum /entrypoint.sh)
hash=${hash%% *}
printf '%s %s\n' "$size" "$hash"
"""

DNS_READER = r"""
import json
import socket
import sys
host = sys.argv[1]
if not host.isascii() or not 1 <= len(host) <= 253:
    raise SystemExit(1)
addresses = sorted({row[4][0] for row in socket.getaddrinfo(host,None,socket.AF_INET,socket.SOCK_STREAM)})
if not 1 <= len(addresses) <= 8:
    raise SystemExit(1)
sys.stdout.write(json.dumps(addresses,ensure_ascii=True,separators=(',',':'))+'\n')
"""


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


def frontend_projection(value):
    require(
        closed(value, {"cid", "image_id", "version", "network_id", "evidence", "process", "listener"}),
        "frontend-projection",
    )
    require(
        type(value["cid"]) is str
        and HEX64.fullmatch(value["cid"]) is not None
        and type(value["network_id"]) is str
        and HEX64.fullmatch(value["network_id"]) is not None
        and type(value["image_id"]) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]) is not None
        and value["version"] == "1.26.0"
        and value["evidence"] == "Docker_API_same_CID",
        "frontend-projection-identity",
    )
    process = value["process"]
    require(
        closed(process, {"docker_state_pid", "container_pid", "start_ticks", "netns_inode", "exe_sha256", "exe_bytes"})
        and process["container_pid"] == 1
        and type(process["container_pid"]) is int
        and all(integer(process[key], 1, 2**63 - 1) for key in ("docker_state_pid", "start_ticks", "netns_inode"))
        and integer(process["exe_bytes"], 1, 64 * 1024 * 1024)
        and type(process["exe_sha256"]) is str
        and HEX64.fullmatch(process["exe_sha256"]) is not None,
        "frontend-projection-process",
    )
    listener = value["listener"]
    require(
        closed(listener, {"socket_inode", "owner_container_pid", "family", "address_kind", "port"})
        and integer(listener["socket_inode"], 1, 2**63 - 1)
        and type(listener["owner_container_pid"]) is int
        and listener["owner_container_pid"] == 1
        and listener["family"] == "ipv4"
        and listener["address_kind"] in {"wildcard", "owned_endpoint"}
        and integer(listener["port"], 1, 65535),
        "frontend-projection-listener",
    )
    return value


def frontend_state(row, cid, image):
    require(type(row) is dict and row.get("Id") == cid and row.get("Image") == image, "frontend-container-identity")
    state = row.get("State")
    require(
        type(state) is dict
        and state.get("Running") is True
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
        and integer(state.get("Pid"), 1, 2**63 - 1)
        and row.get("RestartCount") == 0
        and type(row.get("RestartCount")) is int
        and type(state.get("StartedAt")) is str
        and state["StartedAt"] not in {"", "0001-01-01T00:00:00Z"},
        "frontend-container-state",
    )
    return (state["Pid"], state["StartedAt"], row["RestartCount"])


def frontend_top(raw, pid):
    require(0 < len(raw) <= 16384, "frontend-top-bound")
    lines = raw.decode("ascii").splitlines()
    require(lines and lines[0].split() == ["PID", "PPID"] and 1 <= len(lines) - 1 <= 128, "frontend-top-header")
    rows = []
    for line in lines[1:]:
        fields = line.split()
        require(
            len(fields) == 2 and all(re.fullmatch(r"[0-9]+", item) is not None for item in fields), "frontend-top-row"
        )
        values = tuple(int(item) for item in fields)
        require(all(integer(item, 1, 2**63 - 1) for item in values), "frontend-top-types")
        rows.append(values)
    require(
        sum(row[0] == pid for row in rows) == 1 and len({row[0] for row in rows}) == len(rows),
        "frontend-top-association",
    )


def frontend_image(controller, row):
    image = row.get("Image")
    require(type(image) is str and IMAGE_ID.fullmatch(image) is not None, "frontend-image-id")
    measured = controller.stack_images.get(image)
    require(
        type(measured) is dict
        and measured.get("Id") == image
        and measured.get("Os") == "linux"
        and measured.get("Architecture") == "amd64",
        "frontend-image-association",
    )
    digests = measured.get("RepoDigests")
    require(
        type(digests) is list
        and any(
            type(item) is str
            and re.fullmatch(r"(?:docker.io/)?edoburu/pgbouncer@sha256:[0-9a-f]{64}", item) is not None
            for item in digests
        ),
        "frontend-publisher",
    )
    config = measured.get("Config")
    require(
        type(config) is dict
        and config.get("Entrypoint") == ["/entrypoint.sh"]
        and config.get("Cmd") == ["/usr/bin/pgbouncer", "/etc/pgbouncer/pgbouncer.ini"]
        and config.get("User") == "postgres",
        "frontend-image-profile",
    )
    return image


def frontend_prefix(controller, phase):
    require(phase in {"before", "after"}, "frontend-phase")
    cid = controller.frontend_cid
    labels = [f"frontend_{phase}_{index}" for index in range(1, 9)]
    raw, _, _ = controller.front_run(labels[0], ["docker", "inspect", "--type", "container", cid])
    rows = decode(raw)
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "frontend-inspect")
    first = rows[0]
    image = frontend_image(controller, first)
    state = frontend_state(first, cid, image)
    config = first.get("Config")
    host = first.get("HostConfig")
    require(
        type(config) is dict
        and type(host) is dict
        and type(config.get("Labels")) is dict
        and config["Labels"].get("com.docker.compose.project") == controller.target_project
        and config["Labels"].get("com.docker.compose.service") == "pgbouncer"
        and host.get("Privileged") is False
        and host.get("PidMode") == ""
        and host.get("UsernsMode") == ""
        and first.get("Path") == "/entrypoint.sh"
        and first.get("Args") == ["/usr/bin/pgbouncer", "/etc/pgbouncer/pgbouncer.ini"],
        "frontend-container-profile",
    )
    raw, _, _ = controller.front_run(labels[1], ["docker", "top", cid, "-eo", "pid,ppid"])
    frontend_top(raw, state[0])
    raw, _, _ = controller.front_run(labels[2], ["docker", "exec", cid, "/bin/sh", "-c", FRONTEND_PROCESS])
    process, sockets = parse_process(raw, state[0])
    raw, _, _ = controller.front_run(labels[3], ["docker", "exec", cid, "/bin/sh", "-c", FRONTEND_TCP])
    listener = parse_listener(raw, sockets, controller.frontend_address, controller.endpoint_port)
    raw, _, _ = controller.front_run(
        labels[4], ["docker", "exec", cid, "/bin/sh", "-c", FRONTEND_CONFIG, "observer", "/etc/pgbouncer/pgbouncer.ini"]
    )
    config_bytes, config_identity = parse_config_readback(raw)
    raw, _, _ = controller.front_run(labels[5], ["docker", "inspect", "--type", "network", controller.network_id])
    rows = decode(raw)
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "frontend-network")
    network = rows[0]
    require(
        network.get("Id") == controller.network_id
        and type(network.get("Labels")) is dict
        and network["Labels"].get("com.docker.compose.project") == controller.target_project
        and type(network.get("Containers")) is dict,
        "frontend-network-owner",
    )
    members = network["Containers"]
    require(set(members) <= controller.allowed_project_cids and cid in members, "frontend-network-exclusion")
    endpoint = members[cid]
    require(
        type(endpoint) is dict
        and endpoint.get("IPv4Address") == controller.frontend_address + "/" + controller.network_prefix,
        "frontend-network-endpoint",
    )
    if phase == "before":
        raw, _, _ = controller.front_run("frontend_version", ["docker", "exec", cid, "/proc/1/exe", "--version"])
        require(
            len(raw) <= 4096
            and re.fullmatch(
                rb"PgBouncer 1\.26\.0\nlibevent [^\x00-\x20\x7f]+\nadns: [^\x00-\x1f\x7f]{1,256}\ntls: [^\x00-\x1f\x7f]{1,256}\n(?:systemd: yes\n)?",
                raw,
            )
            is not None,
            "frontend-installed-version",
        )
        raw, _, _ = controller.front_run(
            "frontend_entrypoint_digest", ["docker", "exec", cid, "/bin/sh", "-c", FRONTEND_ENTRYPOINT]
        )
        require(
            raw == b"8449 9d9d23849f0180d7fb25263dca3870955c39e0fcf0211529b10238f280143333\n",
            "frontend-entrypoint-source",
        )
    return {
        "first": first,
        "state": state,
        "config": config_bytes,
        "config_identity": config_identity,
        "network": network,
        "projection": frontend_projection(
            {
                "cid": cid,
                "image_id": image,
                "version": "1.26.0",
                "network_id": controller.network_id,
                "evidence": "Docker_API_same_CID",
                "process": process,
                "listener": listener,
            }
        ),
    }


def frontend_suffix(controller, phase, retained, hostname, port, username):
    require(phase in {"before", "after"} and port == controller.endpoint_port, "frontend-endpoint-port")
    strict_frontend_config(retained["config"], controller.frontend_address, port, username)
    raw, _, _ = controller.front_run(
        f"frontend_{phase}_7", ["docker", "exec", controller.runner_cid, "python", "-c", DNS_READER, hostname]
    )
    require(len(raw) <= 4096, "frontend-dns-bound")
    addresses = decode(raw)
    require(
        type(addresses) is list
        and 1 <= len(addresses) <= 8
        and addresses == sorted(set(addresses))
        and all(
            type(item) is str
            and re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", item) is not None
            and all(int(part) <= 255 for part in item.split("."))
            for item in addresses
        )
        and addresses == [controller.frontend_address],
        "frontend-dns-association",
    )
    if phase == "after":
        require(controller.runner_cid in retained["network"]["Containers"], "frontend-live-runner-network")
    raw, _, _ = controller.front_run(
        f"frontend_{phase}_8", ["docker", "inspect", "--type", "container", controller.frontend_cid]
    )
    rows = decode(raw)
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "frontend-last-inspect")
    last = rows[0]
    require(
        frontend_state(last, controller.frontend_cid, retained["projection"]["image_id"]) == retained["state"]
        and all(
            last.get(key) == retained["first"].get(key) for key in ("Id", "Image", "Config", "HostConfig", "Mounts")
        ),
        "frontend-bracket-drift",
    )
    projection = retained["projection"]
    if phase == "before":
        controller.frontend_before = projection
        controller.frontend_retained_before = retained
    else:
        controller.frontend_after = projection
        before = controller.frontend_retained_before
        require(
            projection == controller.frontend_before
            and retained["state"] == before["state"]
            and retained["config"] == before["config"]
            and retained["config_identity"] == before["config_identity"]
            and all(
                retained["first"].get(key) == before["first"].get(key)
                for key in ("Id", "Image", "Config", "HostConfig", "Mounts")
            ),
            "frontend-lifetime-drift",
        )
    return projection


def junit_roster(controller):
    fixture_path = "api/tests/parity/fixtures/workflow-result-v1.json"
    fixture = decode(controller.blobs[fixture_path])
    require(
        type(fixture) is dict and type(fixture.get("cases")) is list and type(fixture.get("controls")) is dict,
        "junit-fixture-source",
    )
    ordered = [("test_queued_cancel_emitted_update_order", None)]
    for case in fixture["cases"]:
        require(type(case) is dict and type(case.get("case_id")) is str, "junit-case-source")
        ordered.append(("test_result_nonfault_paired", case["case_id"]))
    for category, identifiers in fixture["controls"].items():
        require(
            type(category) is str and type(identifiers) is list and all(type(item) is str for item in identifiers),
            "junit-control-source",
        )
        if category != "Feature-preservation":
            ordered.extend(("test_result_nonfault_control", category + "-" + identity) for identity in identifiers)
    require(len(ordered) == 299 and len(ordered) == len(set(ordered)), "junit-roster-count")
    result = []
    for name, parameter in ordered:
        test_name = name + ("[" + parameter + "]" if parameter is not None else "")
        nodeid = "tests/parity/test_workflow_sql.py::" + test_name
        require(nodeid.isascii() and 1 <= len(nodeid) <= 256, "junit-roster-name")
        result.append(("tests.parity.test_workflow_sql", test_name, nodeid))
    return result


def final_observer(controller, value, nodeid):
    keys = {
        "schema",
        "candidate",
        "invocation_uuid",
        "phase",
        "fixture_constructions",
        "functions_entered",
        "functions_completed",
        "production_lifetimes",
        "production_closed",
        "loaded_bindings_verified",
        "fixture_gate_removed",
        "session_wrappers_restored",
        "metadata_absent",
        "poisoned",
        "cleanup_failed",
        "complete",
        "export_item",
        "dsn_binding",
    }
    require(
        closed(value, keys)
        and value["schema"] == "bifrost.private.result-source-observer-final/v2"
        and value["candidate"] == controller.candidate
        and value["invocation_uuid"] == OWNER
        and value["phase"] == "observer_session_finalizer_after_owned_cleanup"
        and value["export_item"] == nodeid,
        "junit-final-association",
    )
    for name in (
        "fixture_constructions",
        "functions_entered",
        "functions_completed",
        "production_lifetimes",
        "production_closed",
    ):
        require(integer(value[name], 0, 1 if name == "fixture_constructions" else 299), "junit-final-count")
    require(
        value["functions_completed"] <= value["functions_entered"]
        and value["production_closed"] <= value["production_lifetimes"] <= value["functions_entered"],
        "junit-final-count-relation",
    )
    checks = ("loaded_bindings_verified", "fixture_gate_removed", "session_wrappers_restored", "metadata_absent")
    require(
        all(value[name] is None or type(value[name]) is bool for name in checks)
        and all(type(value[name]) is bool for name in ("poisoned", "cleanup_failed", "complete")),
        "junit-final-types",
    )
    binding = value["dsn_binding"]
    require(
        closed(binding, {"fixture_match", "production_match", "driver_match", "complete"})
        and all(
            binding[name] is None or type(binding[name]) is bool
            for name in ("fixture_match", "production_match", "driver_match")
        )
        and type(binding["complete"]) is bool
        and (
            not binding["complete"]
            or all(binding[name] is True for name in ("fixture_match", "production_match", "driver_match"))
        ),
        "junit-final-dsn",
    )
    complete = (
        value["fixture_constructions"] == 1
        and value["functions_entered"] > 0
        and value["functions_completed"] == value["functions_entered"]
        and value["production_closed"] == value["production_lifetimes"]
        and all(value[name] is True for name in checks)
        and not value["poisoned"]
        and not value["cleanup_failed"]
        and binding["complete"]
    )
    require(value["complete"] is complete and len(encode(value)) <= 2048, "junit-final-completion")
    return value


def elapsed_xml(value):
    require(type(value) is str and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is not None, "junit-time-shape")
    result = float(value)
    require(math.isfinite(result) and 0 <= result <= 2700, "junit-time-bound")
    return result


def junit_projection(controller, raw):
    require(
        0 < len(raw) <= 1024 * 1024 and b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(),
        "junit-input-bound",
    )
    root = ET.fromstring(raw)
    require(root.tag == "testsuites" and len(root) == 1 and root[0].tag == "testsuite", "junit-root")
    suite = root[0]
    roster = junit_roster(controller)
    expected = {(classname, name): nodeid for classname, name, nodeid in roster}
    cases = []
    counts = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    observed = set()
    final = None
    final_item = None
    fixture = None
    for element in suite:
        require(element.tag == "testcase", "junit-suite-child")
        identity = (element.get("classname"), element.get("name"))
        require(identity in expected and identity not in observed, "junit-test-identity")
        observed.add(identity)
        status = "passed"
        outcomes = [child for child in element if child.tag in {"failure", "error", "skipped"}]
        require(
            len(outcomes) <= 1
            and all(
                child.tag in {"properties", "failure", "error", "skipped", "system-out", "system-err"}
                for child in element
            ),
            "junit-test-shape",
        )
        if outcomes:
            status = outcomes[0].tag
        properties = {}
        containers = [child for child in element if child.tag == "properties"]
        require(len(containers) <= 1, "junit-property-container")
        if containers:
            for item in containers[0]:
                require(
                    item.tag == "property" and set(item.attrib) == {"name", "value"} and len(item) == 0,
                    "junit-property-shape",
                )
                name, value = item.get("name"), item.get("value")
                require(name not in properties, "junit-property-duplicate")
                if name == "result_source_observer_final":
                    require(final is None and len(value.encode("utf-8")) <= 2048, "junit-final-duplicate")
                    final = final_observer(controller, decode(value.encode("utf-8")), expected[identity])
                    final_item = identity
                elif name == "result_feature_fixture":
                    require(
                        identity[1] == "test_result_nonfault_control[Decode-parity gate-p-005]"
                        and fixture is None
                        and len(value.encode("utf-8")) <= 512,
                        "junit-feature-association",
                    )
                    fixture = decode(value.encode("utf-8"))
                    require(
                        closed(
                            fixture,
                            {
                                "schema",
                                "case_id",
                                "python_completed",
                                "native_completed",
                                "result",
                                "variables",
                                "context",
                            },
                        )
                        and fixture["schema"] == "bifrost.test.workflow-result-feature-fixture/v1"
                        and fixture["case_id"] == "syntheticFeatureMarker"
                        and all(
                            type(fixture[key]) is bool
                            for key in ("python_completed", "native_completed", "result", "variables", "context")
                        ),
                        "junit-feature-shape",
                    )
                    properties[name] = fixture
                elif name in {"result_clock_controls", "result_source_observer_controls"}:
                    require(
                        identity[1] == "test_result_nonfault_control[Decode-parity gate-p-005]"
                        and value == ("6" if name == "result_clock_controls" else "8"),
                        "junit-pure-count",
                    )
                    properties[name] = int(value)
                elif name == "queued_cancel_update_order":
                    require(
                        identity[1] == "test_queued_cancel_emitted_update_order"
                        and value == "executions,workflow_execution_attempts",
                        "junit-queued-observation",
                    )
                    properties[name] = value
                elif name == "result_independent_readback":
                    require(
                        identity[1].startswith("test_result_nonfault_paired[") and value == "complete",
                        "junit-independent-readback",
                    )
                    properties[name] = value
                elif name == "result_measured_transaction":
                    require(
                        identity[1].startswith("test_result_nonfault_paired[")
                        and value in {"committed", "rolled_back", "unknown"},
                        "junit-measured-transaction",
                    )
                    properties[name] = value
                elif name == "rust_event_adapter":
                    require(
                        value == "absent-held" and identity[1].startswith("test_result_nonfault_paired["),
                        "junit-event-limit",
                    )
                    properties[name] = value
                elif name in {
                    "result_case_id",
                    "result_control_id",
                    "result_control_category",
                    "result_reference",
                    "result_native_kind",
                }:
                    require(
                        value.isascii()
                        and 1 <= len(value) <= 128
                        and not any(ord(char) < 32 or ord(char) == 127 for char in value),
                        "junit-property-bound",
                    )
                    # Only source-derived finite labels are admitted below, never arbitrary property text.
                    allowed = controller.junit_property_domains[name]
                    require(value in allowed, "junit-property-domain")
                    properties[name] = value
                else:
                    raise Failure("junit-unknown-property")
        counts["tests"] += 1
        counts[{"failure": "failures", "error": "errors"}.get(status, status)] += 1
        cases.append(
            {
                "nodeid": expected[identity],
                "status": status,
                "seconds": elapsed_xml(element.get("time")),
                "properties": properties,
            }
        )
    require(observed == set(expected) and counts["tests"] == 299, "junit-roster-complete")
    for key in ("tests", "failures", "errors", "skipped"):
        require(suite.get(key) == str(counts[key]), "junit-observed-count")
    require(final is not None and final_item == roster[-1][:2] and fixture is not None, "junit-final-required")
    controller.fixture_feature = fixture
    controller.observer_final = final
    return {
        "schema": "bifrost.test.workflow-result-junit/v1",
        "candidate": controller.candidate,
        "invocation_uuid": OWNER,
        "counts": counts,
        "cases": sorted(cases, key=lambda item: item["nodeid"]),
        "observer_final": final,
    }


def initial_image_inventory(raw):
    require(len(raw) <= STREAM_LIMIT and (not raw or raw.endswith(b"\n")), "image-inventory-bound")
    rows = []
    for line in raw.splitlines():
        row = decode(line)
        require(
            type(row) is dict and type(row.get("ID")) is str and IMAGE_ID.fullmatch(row["ID"]) is not None,
            "image-inventory-id",
        )
        rows.append(row)
        require(len(rows) <= 4096, "image-inventory-count")
    return rows


def qualified_image(controller, purpose, row, *, source_association):
    require(
        type(row) is dict
        and IMAGE_ID.fullmatch(row.get("Id", "")) is not None
        and row.get("Os") == "linux"
        and row.get("Architecture") == "amd64"
        and type(row.get("Config")) is dict
        and source_association is True,
        "image-qualified-source",
    )
    identity = row["Id"]
    controller.qualified_images[identity] = row
    require(purpose not in controller.images, "image-purpose-repeat")
    controller.images[purpose] = {
        "purpose": purpose,
        "image_id": identity,
        "platform": "linux/amd64",
        "source_association": True,
    }
    return identity


def docker_admission(controller):
    raw, _, _ = controller.run("docker_version", ["docker", "version", "--format", "{{json .}}"])
    version = decode(raw)
    require(
        type(version) is dict
        and type(version.get("Client")) is dict
        and type(version.get("Server")) is dict
        and version["Server"].get("Os") == "linux"
        and version["Server"].get("Arch") == "amd64",
        "docker-platform",
    )
    raw, _, _ = controller.run("docker_context", ["docker", "info", "--format", "{{json .}}"])
    info = decode(raw)
    require(
        type(info) is dict
        and info.get("OSType") == "linux"
        and info.get("Architecture") in {"x86_64", "amd64"}
        and type(info.get("SecurityOptions")) is list
        and not any("rootless" in item or "userns" in item for item in info["SecurityOptions"] if type(item) is str),
        "docker-local-identity-profile",
    )
    require(
        not os.environ.get("DOCKER_HOST")
        and not os.environ.get("DOCKER_CONTEXT")
        and not os.environ.get("DOCKER_TLS_VERIFY")
        and not os.environ.get("DOCKER_CERT_PATH"),
        "docker-remote-profile",
    )
    raw, _, _ = controller.run("compose_version", ["docker", "compose", "version", "--format", "json"])
    compose = decode(raw)
    require(
        type(compose) is dict
        and type(compose.get("version")) is str
        and re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?", compose["version"]) is not None,
        "compose-version-profile",
    )
    controller.docker_versions = (version, info, compose)


def owned_volume(controller, row, name, purpose):
    require(
        type(row) is dict
        and row.get("Name") == name
        and row.get("Driver") == "local"
        and row.get("Scope") == "local"
        and row.get("Labels") == owner_labels(controller, purpose)
        and row.get("Options") in (None, {}),
        "owned-volume-association",
    )
    return name


def cargo_volume_acquisition(controller):
    controller.cargo_home = "result-" + OWNER + "-cargo-home"
    controller.targets = "result-" + OWNER + "-targets"
    for label, name, purpose in (
        ("cargo_home_create", controller.cargo_home, "cargo_home"),
        ("targets_create", controller.targets, "targets"),
    ):
        require(
            not any(row.get("Name") == name for row in controller.initial_census["volumes"]), "volume-initial-exclusion"
        )
        # Retain the exact name before creation; failed/partial acquisition remains unknown.
        controller.volume_resources[name] = {"purpose": purpose, "created": False, "admitted": False, "removed": None}
        argv = ["docker", "volume", "create"]
        for key, value in owner_labels(controller, purpose).items():
            argv.extend(["--label", key + "=" + value])
        raw, _, _ = controller.run(label, [*argv, name])
        require(raw == name.encode() + b"\n", "volume-create-response")
        controller.volume_resources[name]["created"] = True
    rows = inspect_many(controller, "cargo_volumes_inspect", [controller.cargo_home, controller.targets], kind="volume")
    for name, record in controller.volume_resources.items():
        owned_volume(controller, rows[name], name, record["purpose"])
        record["baseline"] = rows[name]
        record["admitted"] = True


def cargo_mounts(controller):
    require(all(record["admitted"] for record in controller.volume_resources.values()), "cargo-volume-admitted")
    return (
        ("bind", str(ROOT / "core-rs"), "/workspace/core-rs", True),
        ("volume", controller.cargo_home, "/cargo", False),
        ("volume", controller.targets, "/targets", False),
    )


def cargo_environment(configuration):
    require(configuration in {"default", *TARGET_DIRECTORIES}, "cargo-target-configuration")
    return {"CARGO_HOME": "/cargo", "CARGO_TARGET_DIR": "/targets/" + configuration, "CARGO_TERM_COLOR": "never"}


def terminal_absence(controller, purpose, removal_label, absence_label):
    record = controller.resources[purpose]
    require(record["terminal"] and record["baseline"] is not None, "native-removal-admission")
    controller.run(removal_label, ["docker", "rm", "--force", record["cid"]])
    raw, _, _ = controller.run(
        absence_label,
        [
            "docker",
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            "id=" + record["cid"],
            "--format",
            "{{json .}}",
        ],
    )
    require(raw == b"", "native-removal-absence")
    record["removed"] = True


def dsn_profile(controller, value):
    require(
        type(value) is str
        and 1 <= len(value.encode("utf-8")) <= 4000
        and value.startswith("postgresql+asyncpg://")
        and not any(character in value for character in ("\0", "\r", "\n", "\ufeff", '"', "'")),
        "dsn-original-profile",
    )
    parsed = urlsplit(value)
    require(
        parsed.scheme == "postgresql+asyncpg"
        and type(parsed.hostname) is str
        and parsed.hostname.isascii()
        and 1 <= len(parsed.hostname) <= 253
        and parsed.port is not None
        and 1 <= parsed.port <= 65535
        and parsed.username is not None
        and parsed.password is not None
        and parsed.path.startswith("/")
        and parsed.path != "/"
        and not parsed.fragment
        and parsed.query in {"", "sslmode=disable"},
        "dsn-original-supported-options",
    )
    username = unquote(parsed.username, encoding="utf-8", errors="strict")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", username) is not None, "dsn-principal-profile")
    native = "postgresql://" + value[len("postgresql+asyncpg://") :]
    controller.original_url = value
    controller.native_url = native
    controller.endpoint_hostname = parsed.hostname
    controller.endpoint_port = parsed.port
    controller.endpoint_username = username
    return native


def identity_probe_work(controller):
    create_native(
        controller, "uid_probe", controller.api_image, ["python3", "-I", "-S", "-B", "-c", UID_PROBE], readonly=True
    )
    inspect_native(controller, "inspect_pre_uid", ["uid_probe"])
    raw, stderr, _ = start_native(controller, "uid_probe", cap=8)
    require(stderr == b"" and len(raw) <= 2048, "uid-probe-output")
    value = decode(raw)
    require(
        closed(value, {"schema", "uid", "gid", "user_namespace"})
        and value["schema"] == "bifrost.private.result-target-identity/v1"
        and type(value["uid"]) is int
        and value["uid"] == 1000
        and integer(value["gid"], 0, 2**31 - 1)
        and closed(value["user_namespace"], {"dev", "ino"})
        and integer(value["user_namespace"]["dev"], 0, 2**64 - 1)
        and integer(value["user_namespace"]["ino"], 1, 2**64 - 1),
        "uid-probe-identity",
    )
    namespace = os.stat("/proc/self/ns/user")
    require(value["user_namespace"] == {"dev": namespace.st_dev, "ino": namespace.st_ino}, "uid-probe-same-userns")
    inspect_native(controller, "inspect_post_uid", ["uid_probe"], terminal=True)
    terminal_absence(controller, "uid_probe", "remove_native_uid", "absence_uid")
    controller.target_identity = value


def identity_probe(controller):
    original_end = controller.end
    controller.end = min(time.monotonic() + 10, original_end)
    try:
        identity_probe_work(controller)
    finally:
        controller.end = original_end


def private_dsn_file(controller):
    require(controller.native_url is not None and type(controller.target_identity) is dict, "dsn-file-prerequisites")
    directory = controller.private / "dsn"
    controller.secret_directory = directory
    controller.secret_directory_acquired = False
    require(not os.path.lexists(directory), "dsn-directory-preexists")
    directory.mkdir(mode=0o700)
    controller.secret_directory_acquired = True
    parent = os.stat(directory, follow_symlinks=False)
    require(
        stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700,
        "dsn-directory-private",
    )
    path = directory / "result-all-features.env"
    record = {"path": path, "identity": None, "removed": None, "fd": None}
    controller.owned_files.append(record)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    record["fd"] = fd
    before = os.fstat(fd)
    record["identity"] = (before.st_dev, before.st_ino)
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == os.geteuid()
        and before.st_nlink == 1
        and before.st_size == 0
        and not os.listxattr(fd),
        "dsn-new-file",
    )
    os.fchmod(fd, 0o640)
    provisioned = os.fstat(fd)
    require(
        file_identity(provisioned) == file_identity(os.stat(path, follow_symlinks=False))
        and provisioned.st_uid == before.st_uid
        and provisioned.st_gid == before.st_gid
        and stat.S_IMODE(provisioned.st_mode) == 0o640
        and provisioned.st_size == 0
        and not os.listxattr(fd),
        "dsn-initial-provision",
    )
    parent = os.stat(directory, follow_symlinks=False)
    require(
        stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700,
        "dsn-provisioned-directory-private",
    )
    target_gid = controller.target_identity["gid"]
    if provisioned.st_gid != target_gid:
        if target_gid in {os.getegid(), *os.getgroups()}:
            os.fchown(fd, -1, target_gid)
        else:
            request = {
                "schema": "bifrost.private.result-group-acquire/v2",
                "path": "/bifrost-private/result-all-features.env",
                "file": {
                    "dev": provisioned.st_dev,
                    "ino": provisioned.st_ino,
                    "owner_uid": provisioned.st_uid,
                    "gid": provisioned.st_gid,
                },
                "target_gid": target_gid,
                "user_namespace": controller.target_identity["user_namespace"],
            }
            require(len(encode(request)) <= 2048, "group-helper-request-bound")
            original_end = controller.end
            helper_end = min(time.monotonic() + 5, original_end)
            controller.end = helper_end
            first = None
            try:
                create_native(
                    controller,
                    "group_helper",
                    controller.api_image,
                    ["-I", "-S", "-B", "-c", GROUP_HELPER, encode(request).decode("ascii")],
                    mounts=(("bind", str(path), "/bifrost-private/result-all-features.env", False),),
                    entrypoint="/usr/local/bin/python3",
                    user="0:0",
                    caps=("CHOWN", "DAC_OVERRIDE"),
                    readonly=True,
                )
                inspect_native(controller, "inspect_pre_helper", ["group_helper"])
                stdout, stderr, _ = start_native(controller, "group_helper", cap=4)
                require(stdout == stderr == b"", "group-helper-empty-output")
                inspect_native(controller, "inspect_post_helper", ["group_helper"], terminal=True)
                terminal_absence(controller, "group_helper", "remove_native_helper", "absence_helper")
            except BaseException as error:
                first = error
            finally:
                controller.end = original_end
            if first is not None:
                raise first
    acquired = os.fstat(fd)
    actual_parent = os.stat(directory, follow_symlinks=False)
    namespace = os.stat("/proc/self/ns/user")
    require(
        (
            acquired.st_dev,
            acquired.st_ino,
            acquired.st_uid,
            acquired.st_gid,
            acquired.st_nlink,
            acquired.st_size,
            stat.S_IMODE(acquired.st_mode),
            acquired.st_mtime_ns,
        )
        == (
            provisioned.st_dev,
            provisioned.st_ino,
            provisioned.st_uid,
            target_gid,
            1,
            0,
            0o640,
            provisioned.st_mtime_ns,
        )
        and file_identity(acquired) == file_identity(os.stat(path, follow_symlinks=False))
        and file_identity(parent) == file_identity(actual_parent)
        and not os.listxattr(fd)
        and controller.target_identity["user_namespace"] == {"dev": namespace.st_dev, "ino": namespace.st_ino},
        "dsn-presecret-custody",
    )
    raw = ("BIFROST_RUST_TEST_DATABASE_URL=" + controller.native_url + "\n").encode("utf-8")
    require(1 <= len(raw) <= 4096 and raw.count(b"\n") == 1, "dsn-file-grammar")
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        require(count > 0, "dsn-file-write")
        view = view[count:]
    os.fsync(fd)
    final = os.fstat(fd)
    require(
        (
            final.st_dev,
            final.st_ino,
            final.st_uid,
            final.st_gid,
            final.st_nlink,
            final.st_size,
            stat.S_IMODE(final.st_mode),
        )
        == (provisioned.st_dev, provisioned.st_ino, provisioned.st_uid, target_gid, 1, len(raw), 0o640)
        and file_identity(final) == file_identity(os.stat(path, follow_symlinks=False))
        and not os.listxattr(fd),
        "dsn-final-custody",
    )
    controller.secret_file = path
    controller.private_descriptor = {
        "schema": "bifrost.private.result-dsn-input/v1",
        "path": "/bifrost-private/result-all-features.env",
        "dev": final.st_dev,
        "ino": final.st_ino,
        "owner_uid": final.st_uid,
        "gid": final.st_gid,
        "mode": 0o640,
        "size": final.st_size,
        "nlink": 1,
        "user_namespace": controller.target_identity["user_namespace"],
    }


RUNNER_SOURCE_READER = r"""
import hashlib
import json
import os
import stat
import sys
request = json.loads(sys.argv[1])
if type(request) is not dict or set(request) != {'paths'} or type(request['paths']) is not list or not 1 <= len(request['paths']) <= 128 or request['paths'] != sorted(set(request['paths'])):
    raise SystemExit(1)
result = {}
for path in request['paths']:
    if type(path) is not str or not path.isascii() or not 1 <= len(path) <= 256 or not path.startswith(('/app/','/api/','/core-rs/','/tmp/bifrost/workflow-result-parity/')) or any(part in ('','.','..') for part in path.split('/')[1:]):
        raise SystemExit(1)
    fd = None
    first = None
    digest = hashlib.sha256()
    try:
        fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 <= before.st_size <= 64*1024*1024:
            raise ValueError('profile')
        count = 0
        while True:
            block = os.read(fd,65536)
            if not block:
                break
            count += len(block)
            if count > before.st_size:
                raise ValueError('growth')
            digest.update(block)
        def identity(value):
            return (value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid,value.st_nlink,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
        if count != before.st_size or identity(before) != identity(os.fstat(fd)) or identity(before) != identity(os.stat(path,follow_symlinks=False)):
            raise ValueError('stability')
    except BaseException as error:
        first = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                if first is None:
                    first = error
    if first is not None:
        raise SystemExit(1)
    result[path] = digest.hexdigest()
namespace = os.stat('/proc/self/ns/user')
value = {'schema':'bifrost.private.result-runner-source/v1','uid':os.geteuid(),'gid':os.getegid(),'user_namespace':{'dev':namespace.st_dev,'ino':namespace.st_ino},'sources':result}
raw = json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('ascii')
if len(raw) > 65536:
    raise SystemExit(1)
sys.stdout.buffer.write(raw+b'\n')
"""


def runner_readback(controller):
    raw, _, _ = controller.run(
        "runner_discovery", ["docker", "container", "ls", "--all", "--no-trunc", "--format", "{{json .}}"]
    )
    require(len(raw) <= STREAM_LIMIT, "runner-inventory-bound")
    candidates = []
    for line in raw.splitlines():
        row = decode(line)
        require(type(row) is dict and type(row.get("Labels")) is str, "runner-inventory-shape")
        pairs = [item.split("=", 1) for item in row["Labels"].split(",") if "=" in item]
        require(len(pairs) == len({item[0] for item in pairs}), "runner-inventory-labels")
        labels = dict(pairs)
        if (
            labels.get("com.docker.compose.project") == controller.target_project
            and labels.get("com.docker.compose.service") == "test-runner"
        ):
            require(
                labels.get("com.docker.compose.oneoff") == "True"
                and type(row.get("ID")) is str
                and HEX64.fullmatch(row["ID"]) is not None,
                "runner-oneoff",
            )
            candidates.append(row["ID"])
    require(len(candidates) == 1, "runner-discovery-identity")
    controller.runner_cid = candidates[0]
    controller.allowed_project_cids.add(controller.runner_cid)
    rows = inspect_many(controller, "runner_config", [controller.runner_cid], kind="container")
    row = rows[controller.runner_cid]
    config, host, state = row.get("Config"), row.get("HostConfig"), row.get("State")
    require(
        row.get("Image") == controller.api_image
        and type(config) is dict
        and type(host) is dict
        and type(state) is dict
        and state.get("Running") is True
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
        and host.get("Privileged") is False
        and host.get("UsernsMode") == ""
        and host.get("PidMode") == "",
        "runner-live-config",
    )
    labels = config.get("Labels")
    require(
        type(labels) is dict
        and labels.get("com.docker.compose.project") == controller.target_project
        and labels.get("com.docker.compose.service") == "test-runner"
        and labels.get("com.docker.compose.oneoff") == "True",
        "runner-label-association",
    )
    require(
        config.get("Entrypoint") == controller.qualified_images[controller.api_image]["Config"].get("Entrypoint")
        and config.get("User") == controller.base_config["services"]["test-runner"].get("user", ""),
        "runner-original-entrypoint",
    )
    command = config.get("Cmd")
    require(
        command
        == [
            "pytest",
            "tests/parity/test_workflow_sql.py",
            "-v",
            "--durations=25",
            "--junitxml=/tmp/bifrost/test-results.xml",
        ],
        "runner-original-selection",
    )
    mounts = row.get("Mounts")
    require(type(mounts) is list and all(type(item) is dict for item in mounts), "runner-mounts")
    indexed = {item.get("Destination"): item for item in mounts}
    require(len(indexed) == len(mounts), "runner-mount-duplicates")
    expected = controller.derived_config["services"]["test-runner"]["volumes"]
    require(len(mounts) == len(expected), "runner-mount-membership")
    for item in expected:
        require(type(item) is dict and item.get("type") in {"bind", "volume"}, "runner-source-mount-type")
        actual = indexed.get(item["target"])
        require(
            type(actual) is dict
            and actual.get("Type") == item["type"]
            and actual.get("RW") is (not item.get("read_only", False)),
            "runner-source-mount-association",
        )
        if item["type"] == "bind":
            require(actual.get("Source") == item["source"], "runner-source-bind")
        else:
            require(
                "source" not in item
                and type(actual.get("Name")) is str
                and re.fullmatch(r"[0-9a-f]{64}", actual["Name"]) is not None,
                "runner-image-anonymous-volume",
            )
            controller.runner_anonymous_volumes.append(actual["Name"])
    environment = config.get("Env")
    require(type(environment) is list and all(type(item) is str and "=" in item for item in environment), "runner-env")
    pairs = [item.split("=", 1) for item in environment]
    require(len(pairs) == len({item[0] for item in pairs}), "runner-env-duplicates")
    environment = dict(pairs)
    require(
        environment.get("BIFROST_DATABASE_URL") == controller.original_url
        and environment.get("BIFROST_RESULT_OBSERVER_CONTEXT") == controller.context_text
        and not any(environment.get(key) for key in ("GITHUB_TOKEN", "GH_TOKEN", "BIFROST_ACTION_PIN_TOKEN_FILE")),
        "runner-env-association",
    )
    network = row.get("NetworkSettings", {}).get("Networks")
    require(
        type(network) is dict
        and len(network) == 1
        and next(iter(network.values())).get("NetworkID") == controller.network_id,
        "runner-endpoint-network",
    )
    source_paths = {}
    for path, digest in controller.receipt_sources.items():
        if path.startswith("api/"):
            source_paths["/api/" + path[4:]] = digest
            if path.startswith(("api/src/", "api/tests/")):
                source_paths["/app/" + path[4:]] = digest
        elif path.startswith("core-rs/"):
            source_paths["/core-rs/" + path[8:]] = digest
    source_paths["/tmp/bifrost/workflow-result-parity/driver"] = controller.binary["sha256"]
    source_paths["/tmp/bifrost/workflow-result-parity/receipt.json"] = controller.receipt_sha
    require(len(source_paths) <= 128, "runner-source-count")
    raw, stderr, _ = controller.run(
        "runner_source_readback",
        [
            "docker",
            "exec",
            controller.runner_cid,
            "python",
            "-I",
            "-S",
            "-B",
            "-c",
            RUNNER_SOURCE_READER,
            encode({"paths": sorted(source_paths)}).decode("ascii"),
        ],
    )
    require(stderr == b"" and len(raw) <= 65536, "runner-source-output")
    value = decode(raw)
    require(
        closed(value, {"schema", "uid", "gid", "user_namespace", "sources"})
        and value["schema"] == "bifrost.private.result-runner-source/v1"
        and value["uid"] == controller.target_identity["uid"]
        and type(value["uid"]) is int
        and value["gid"] == controller.target_identity["gid"]
        and type(value["gid"]) is int
        and value["user_namespace"] == controller.target_identity["user_namespace"]
        and value["sources"] == source_paths,
        "runner-physical-source",
    )
    controller.runner_retained = row


def shared_request(controller, family):
    require(family in {"endpoint", "after"}, "shared-request-family")
    name = "request" if family == "endpoint" else "ready"
    path = controller.log_directory / f".result-{family}-{OWNER}-{name}.json"
    if not os.path.lexists(path):
        return None
    raw, info = stable_file(path, 4096, expected_uid=1000, expected_mode=0o644)
    require(info.st_nlink == 1, "shared-request-links")
    value = decode(raw)
    record = {"path": path, "identity": (info.st_dev, info.st_ino), "removed": None}
    controller.owned_files.append(record)
    if family == "endpoint":
        require(
            closed(
                value,
                {
                    "schema",
                    "invocation_uuid",
                    "candidate",
                    "phase",
                    "fixture_source_sha256",
                    "provider",
                    "construction_index",
                    "hostname",
                    "port",
                    "drivername",
                },
            )
            and value["schema"] == "bifrost.private.result-endpoint/v1"
            and value["invocation_uuid"] == OWNER
            and value["candidate"] == controller.candidate
            and value["phase"] == "fixture_constructed_before_sql"
            and value["fixture_source_sha256"] == controller.source_map["api/tests/conftest.py"]
            and value["provider"] == "conftest_NullPool"
            and type(value["construction_index"]) is int
            and value["construction_index"] == 1
            and value["drivername"] == "postgresql+asyncpg"
            and type(value["hostname"]) is str
            and value["hostname"] == controller.endpoint_hostname
            and type(value["port"]) is int
            and value["port"] == controller.endpoint_port,
            "endpoint-request-association",
        )
    else:
        sources = {
            path: controller.source_map[path]
            for path in ("api/tests/parity/workflow_sql_harness.py", "api/tests/parity/test_workflow_sql.py")
        }
        require(
            closed(
                value,
                {
                    "schema",
                    "candidate",
                    "invocation_uuid",
                    "phase",
                    "sources",
                    "functions_entered",
                    "functions_completed",
                    "last_item",
                },
            )
            and value["schema"] == "bifrost.private.result-after-ready/v1"
            and value["candidate"] == controller.candidate
            and value["invocation_uuid"] == OWNER
            and value["phase"] == "last_selected_actor_settled_before_observer_cleanup"
            and value["sources"] == sources
            and type(value["functions_entered"]) is int
            and type(value["functions_completed"]) is int
            and value["functions_entered"] == value["functions_completed"] == 299
            and value["last_item"] == junit_roster(controller)[-1][2],
            "after-ready-association",
        )
    return value


def target_tick(controller, process):
    require(process.poll() is None, "target-live-handoff")
    deadline(controller.end)
    if not controller.endpoint_admitted:
        request = shared_request(controller, "endpoint")
        if request is None:
            return
        try:
            runner_readback(controller)
            frontend_suffix(
                controller,
                "before",
                controller.frontend_pending_before,
                request["hostname"],
                request["port"],
                controller.endpoint_username,
            )
            require(process.poll() is None, "target-live-endpoint")
            deadline(controller.end)
            publish_decision(controller, "endpoint")
            controller.endpoint_admitted = True
        except BaseException:
            try:
                publish_decision(controller, "endpoint", rejected=True)
            except BaseException:
                controller.cleanup_failed = True
            raise
        return
    if not controller.after_admitted:
        ready = shared_request(controller, "after")
        if ready is None:
            return
        try:
            pending = frontend_prefix(controller, "after")
            frontend_suffix(
                controller,
                "after",
                pending,
                controller.endpoint_hostname,
                controller.endpoint_port,
                controller.endpoint_username,
            )
            require(process.poll() is None, "target-live-after")
            deadline(controller.end)
            publish_decision(controller, "after")
            controller.after_admitted = True
        except BaseException:
            try:
                publish_decision(controller, "after", rejected=True)
            except BaseException:
                controller.cleanup_failed = True
            raise


def source_receipt(controller):
    references = ast_map_literal(controller.blobs["api/tests/parity/workflow_domain_harness.py"], "REFERENCE_HASHES")
    preparation = literal_assignment(controller.blobs["api/tests/parity/workflow_sql_harness.py"], "PREPARATION")
    required = (
        set(references)
        | set(preparation)
        | {
            "api/src/core/database.py",
            "api/src/config.py",
            "api/tests/parity/test_workflow_sql.py",
            "api/tests/parity/fixtures/workflow-result-v1.json",
            "api/tests/conftest.py",
            "api/src/models/enums.py",
            "core-rs/crates/bifrost-db/src/workflow_parity.rs",
            "core-rs/crates/bifrost-db/src/workflow_numeric.rs",
            "core-rs/crates/bifrost-db/examples/workflow_sql_vectors.rs",
            "core-rs/crates/bifrost-db/Cargo.toml",
            "core-rs/Cargo.lock",
        }
    )
    required |= set(controller.core_paths)
    require(required <= set(controller.source_map), "receipt-source-admission")
    controller.receipt_sources = {path: controller.source_map[path] for path in sorted(required)}
    require(
        controller.binary is not None and set(controller.graphs) == {"default", "selected", "all_features"},
        "receipt-build-admission",
    )
    receipt = {
        "schema": "bifrost.test.workflow-result-source/v2",
        "candidate": controller.candidate,
        "sources": controller.receipt_sources,
        "binary": controller.binary,
        "graphs": controller.graphs,
    }
    raw = encode(receipt) + b"\n"
    require(len(raw) <= 1024 * 1024, "receipt-byte-bound")
    write_private_record(controller, controller.exchange_directory / "receipt.json", raw, mode=0o644)
    controller.receipt = receipt
    controller.receipt_sha = sha(raw)
    return receipt


def target_configuration(controller):
    require(controller.endpoint_admitted is False and controller.after_admitted is False, "target-not-replayed")
    remaining = min(controller.end, NORMAL_END, WHOLE_END) - time.monotonic()
    require(0 < remaining <= 900, "target-launch-bound")
    context = {
        "schema": "bifrost.private.result-observer-context/v2",
        "candidate": controller.candidate,
        "invocation_uuid": OWNER,
        "parent_uid": os.geteuid(),
        "target_remaining_seconds": remaining,
        "private_input": controller.private_descriptor,
    }
    controller.context_text = encode(context).decode("ascii")
    derived = json.loads(encode(controller.base_config))
    runner = derived["services"]["test-runner"]
    require(
        type(runner.get("volumes")) is list
        and type(runner.get("environment")) is dict
        and "BIFROST_RESULT_OBSERVER_CONTEXT" not in runner["environment"],
        "target-config-source-shape",
    )
    additions = (
        (str(ROOT / "api"), "/api"),
        (str(ROOT / "core-rs"), "/core-rs"),
        (str(controller.exchange_directory), "/tmp/bifrost/workflow-result-parity"),
        (str(controller.secret_file), "/bifrost-private/result-all-features.env"),
    )
    require(
        not {destination for _source, destination in additions}
        & {item.get("target") for item in runner["volumes"] if type(item) is dict},
        "target-config-shadow",
    )
    for source, destination in additions:
        runner["volumes"].append(
            {
                "type": "bind",
                "source": source,
                "target": destination,
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        )
    runner["environment"]["BIFROST_RESULT_OBSERVER_CONTEXT"] = controller.context_text
    # Invert only the selected additions; every original configuration member must survive exactly.
    inverse = json.loads(encode(derived))
    inverse["services"]["test-runner"]["volumes"] = inverse["services"]["test-runner"]["volumes"][:-4]
    del inverse["services"]["test-runner"]["environment"]["BIFROST_RESULT_OBSERVER_CONTEXT"]
    require(inverse == controller.base_config, "target-config-inverse")
    controller.derived_config = derived
    controller.derived_config_path = controller.private / "target-compose.json"
    write_private_record(controller, controller.derived_config_path, encode(derived) + b"\n")


def setup_checks(controller):
    controller.begin("checks")
    copy_input_admission(controller)
    tag = "bifrost-result-toolchain:" + OWNER
    controller.toolchain_pending = tag
    argv = ["docker", "build", "--target", "toolchain", "-f", "core-rs/Dockerfile", "--tag", tag]
    for key, value in owner_labels(controller, "toolchain").items():
        argv.extend(["--label", key + "=" + value])
    controller.run("toolchain_build", [*argv, "core-rs"])
    raw, _, _ = controller.run("toolchain_inspect", ["docker", "image", "inspect", tag])
    rows = decode(raw)
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "toolchain-image-inspect")
    row = rows[0]
    require(
        type(row.get("Config")) is dict
        and row["Config"].get("Labels") == owner_labels(controller, "toolchain")
        and row["Config"].get("WorkingDir") == "/workspace/core-rs"
        and row["Id"] not in controller.initial_image_ids,
        "toolchain-build-association",
    )
    for path in controller.core_paths:
        actual, _info = stable_file(ROOT / path, 4 * 1024 * 1024, allow_empty=True)
        require(actual == controller.blobs[path], "toolchain-input-after-build")
    controller.toolchain_image = qualified_image(controller, "toolchain", row, source_association=True)
    controller.toolchain_pending = None
    cargo_volume_acquisition(controller)
    commands = {
        "fetch": (["sh", "-c", "rustc -vV && cargo -V && exec cargo fetch --locked"], "default", "bridge"),
        "fmt": (["cargo", "fmt", "--all", "--check"], "default", "none"),
        "clippy": (
            [
                "cargo",
                "clippy",
                "--locked",
                "--offline",
                "--workspace",
                "--all-targets",
                "--all-features",
                "--",
                "-D",
                "warnings",
            ],
            "all_features",
            "none",
        ),
        "default_tests": (
            ["cargo", "test", "--locked", "--offline", "--workspace", "--message-format=json"],
            "default",
            "none",
        ),
        "metadata_default": (METADATA, "default", "none"),
        "metadata_selected": ([*METADATA, "--features", "bifrost-db/workflow-sql-parity"], "db_selected", "none"),
        "metadata_all_features": ([*METADATA, "--all-features"], "all_features", "none"),
    }
    for purpose, (command, configuration, network) in commands.items():
        create_native(
            controller,
            purpose,
            controller.toolchain_image,
            command,
            network=network,
            mounts=cargo_mounts(controller),
            environment=cargo_environment(configuration),
            workdir="/workspace/core-rs",
        )
    inspect_native(controller, "inspect_pre_setup", list(commands))
    stdout, _stderr, _ = start_native(controller, "fetch")
    require(len(stdout) <= STREAM_LIMIT, "toolchain-version-bound")
    lines = stdout.decode("utf-8", "strict").splitlines()
    require(
        len(lines) >= 8
        and re.fullmatch(r"rustc 1\.98\.1 \([0-9a-f]{9,40} [0-9]{4}-[0-9]{2}-[0-9]{2}\)", lines[0]) is not None,
        "toolchain-rustc-version",
    )
    host = [line for line in lines[1:] if line.startswith("host: ")]
    release = [line for line in lines[1:] if line.startswith("release: ")]
    cargo = [line for line in lines[1:] if line.startswith("cargo ")]
    require(
        host == ["host: x86_64-unknown-linux-gnu"]
        and release == ["release: 1.98.1"]
        and len(cargo) == 1
        and re.fullmatch(r"cargo 1\.98\.1 \([0-9a-f]{9,40} [0-9]{4}-[0-9]{2}-[0-9]{2}\)", cargo[0]) is not None,
        "toolchain-cargo-host",
    )
    inspect_native(controller, "inspect_post_fetch", ["fetch"], terminal=True)
    outputs = {}
    for purpose in list(commands)[1:]:
        outputs[purpose] = start_native(controller, purpose)
    inspect_native(controller, "inspect_post_checks", list(commands)[1:], terminal=True)
    for name in ("default", "selected", "all_features"):
        controller.graphs[name] = workspace_graph(controller, outputs["metadata_" + name][0], name)
    default_test_admission(controller, *outputs["default_tests"][:2])
    controller.finish()


def release_build(controller):
    controller.begin("binary")
    create_native(
        controller,
        "release",
        controller.toolchain_image,
        NATIVE_ARGV[7],
        mounts=cargo_mounts(controller),
        environment=cargo_environment("release"),
        workdir="/workspace/core-rs",
    )
    inspect_native(controller, "inspect_pre_release", ["release"])
    stdout, _stderr, _ = start_native(controller, "release")
    observation = cargo_artifacts(controller, stdout, 7)
    inspect_native(controller, "inspect_post_release", ["release"], terminal=True)
    require(
        observation["roots"][5]["path"] == "/targets/release/release/examples/workflow_sql_vectors",
        "release-artifact-path",
    )
    directory = controller.private / "software"
    controller.exchange_directory = directory
    controller.exchange_directory_acquired = False
    require(not os.path.lexists(directory), "software-directory-preexists")
    directory.mkdir(mode=0o755)
    controller.exchange_directory_acquired = True
    info = os.stat(directory, follow_symlinks=False)
    require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o755,
        "software-directory-mode",
    )
    driver = directory / "driver"
    owned = {"path": driver, "identity": None, "removed": None}
    controller.owned_files.append(owned)
    controller.run(
        "release_binary_copy",
        ["docker", "cp", controller.resources["release"]["cid"] + ":" + observation["roots"][5]["path"], str(driver)],
    )
    raw, info = stable_file(driver, 64 * 1024 * 1024)
    owned["identity"] = (info.st_dev, info.st_ino)
    require(
        info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) <= 0o777 and stat.S_IMODE(info.st_mode) & 0o111 != 0,
        "release-copy-executable",
    )
    controller.driver_path = driver
    controller.binary = {
        "sha256": sha(raw),
        "source_paths": controller.core_paths,
        "build_head": controller.candidate["head"],
        "build_tree": controller.candidate["tree"],
    }
    controller.finish()


DEFAULT_BOOTSTRAP = (
    "configuration_preserves_python_database_conventions",
    "invalid_configuration_fails_without_values",
    "liveness_and_database_readiness_are_distinct",
    "graceful_shutdown_stops_http_and_closes_database_pool",
    "graceful_shutdown_drains_an_inflight_readiness_request",
    "request_spans_do_not_record_caller_secrets",
)


def harness_blocks(lines, maximum):
    blocks = []
    active = None
    for line in lines:
        match = re.fullmatch(r"running ([0-9]+) tests?", line)
        if match:
            require(active is None and int(match[1]) <= maximum, "test-harness-start")
            active = {"count": int(match[1]), "tests": {}}
            continue
        match = re.fullmatch(r"test ([A-Za-z0-9_:]+) \.\.\. (ok|FAILED|ignored)", line)
        if match:
            require(active is not None and match[1] not in active["tests"], "test-harness-identity")
            active["tests"][match[1]] = match[2]
            continue
        match = re.fullmatch(
            r"test result: (ok|FAILED)\. ([0-9]+) passed; ([0-9]+) failed; ([0-9]+) ignored; ([0-9]+) measured; ([0-9]+) filtered out; finished in ([0-9.]+)s",
            line,
        )
        if match:
            require(
                active is not None
                and match[1] == "ok"
                and [int(match[index]) for index in (3, 4, 5)] == [0, 0, 0]
                and int(match[2]) == active["count"] == len(active["tests"])
                and all(value == "ok" for value in active["tests"].values()),
                "test-harness-terminal",
            )
            elapsed = float(match[7])
            require(math.isfinite(elapsed) and 0 <= elapsed <= 480, "test-harness-time")
            active["filtered"] = int(match[6])
            blocks.append(active)
            active = None
            continue
        require(not line.startswith(("test ", "running ", "test result:")), "test-harness-unmatched")
    require(active is None, "test-harness-unfinished")
    return blocks


def default_test_admission(controller, stdout, stderr):
    require(stdout.endswith(b"\n"), "default-output-eof")
    executable_sources = {}
    text = []
    finished = False
    for line in stdout.splitlines():
        if not line.startswith(b"{"):
            text.append(line.decode("utf-8", "strict"))
            continue
        value = decode(line)
        require(
            type(value) is dict
            and value.get("reason")
            in {"compiler-artifact", "compiler-message", "build-script-executed", "build-finished"},
            "default-cargo-message",
        )
        if value["reason"] == "build-finished":
            require(not finished and value.get("success") is True, "default-build-finished")
            finished = True
        elif value["reason"] == "compiler-artifact":
            require(not finished, "default-post-finish-artifact")
            executable = value.get("executable")
            if executable is None:
                continue
            profile = unit_profile(value.get("profile"))
            target = value.get("target")
            require(type(target) is dict, "default-test-artifact")
            path = artifact_path(executable, "default")
            source = target.get("src_path")
            require(
                type(source) is str
                and source.startswith("/workspace/core-rs/")
                and source.removeprefix("/workspace/") in controller.source_map,
                "default-test-source",
            )
            domains = [
                domain
                for domain in controller.package_domains.values()
                if domain["package"]["id"] == value.get("package_id")
            ]
            require(
                len(domains) == 1
                and target in domains[0]["package"]["targets"]
                and value.get("manifest_path") == domains[0]["package"]["manifest_path"],
                "default-compiled-source-target",
            )
            if profile[0] is False:
                # Cargo's compile-only bin/example artifacts are not completed test identities.
                require(target.get("kind") in (["bin"], ["example"]), "default-compile-only-target")
                continue
            require(path not in executable_sources, "default-test-artifact-duplicate")
            executable_sources[path] = source.removeprefix("/workspace/core-rs/")
    require(finished, "default-build-complete")
    unit_sources = {
        "crates/bifrost-contracts/src/lib.rs": {
            row["test"] for row in TEST_CATALOG if row["source_group"] == "contracts_lib"
        },
        "crates/bifrost-domain/src/lib.rs": {
            row["test"] for row in TEST_CATALOG if row["source_group"] == "domain_lib"
        },
        "crates/bifrost-db/src/lib.rs": set(),
        "crates/bifrost-core/src/lib.rs": set(),
        "crates/bifrost-core/src/main.rs": set(),
        "crates/bifrost-core/tests/bootstrap.rs": set(DEFAULT_BOOTSTRAP),
    }
    require(set(executable_sources.values()) == set(unit_sources), "default-compiled-roster")
    running = []
    docs = []
    for line in stderr.decode("utf-8", "strict").splitlines():
        match = re.fullmatch(r"\s*Running (?:unittests|tests) [^\n]* \((/targets/[^() ]+)\)", line)
        if match:
            path = artifact_path(match[1], "default")
            require(path in executable_sources, "default-running-binary")
            running.append(executable_sources[path])
        match = re.fullmatch(r"\s*Doc-tests (bifrost_contracts|bifrost_domain|bifrost_db|bifrost_core)", line)
        if match:
            docs.append(match[1])
    require(
        len(running) == len(set(running))
        and set(running) == set(unit_sources)
        and len(docs) == 4
        and set(docs) == {"bifrost_contracts", "bifrost_domain", "bifrost_db", "bifrost_core"},
        "default-running-complete",
    )
    blocks = harness_blocks(text, 27)
    require(len(blocks) == len(running) + len(docs), "default-harness-association")
    actual = []
    for source, block in zip(running, blocks[: len(running)], strict=True):
        require(
            set(block["tests"]) == unit_sources[source] and block["filtered"] == 0, "default-actual-completed-roster"
        )
        actual.extend((source, name) for name in block["tests"])
    require(
        len(actual) == 27 and all(block["count"] == 0 and block["filtered"] == 0 for block in blocks[len(running) :]),
        "default-full-completion",
    )
    controller.default_completed = sorted(actual)


def dsn_guard(controller):
    records = [record for record in controller.owned_files if record.get("path") == controller.secret_file]
    require(len(records) == 1 and type(records[0].get("fd")) is int, "dsn-retained-descriptor")
    fd = records[0]["fd"]
    before = os.fstat(fd)
    descriptor = controller.private_descriptor
    require(
        stat.S_ISREG(before.st_mode)
        and stat.S_IMODE(before.st_mode) == 0o640
        and (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_nlink, before.st_size)
        == tuple(descriptor[key] for key in ("dev", "ino", "owner_uid", "gid", "nlink", "size"))
        and file_identity(before) == file_identity(os.stat(controller.secret_file, follow_symlinks=False))
        and not os.listxattr(fd),
        "dsn-use-custody",
    )
    raw = os.pread(fd, descriptor["size"] + 1, 0)
    require(
        raw == ("BIFROST_RUST_TEST_DATABASE_URL=" + controller.native_url + "\n").encode("utf-8")
        and file_identity(before) == file_identity(os.fstat(fd))
        and file_identity(before) == file_identity(os.stat(controller.secret_file, follow_symlinks=False)),
        "dsn-use-bytes",
    )
    namespace = os.stat("/proc/self/ns/user")
    require(descriptor["user_namespace"] == {"dev": namespace.st_dev, "ino": namespace.st_ino}, "dsn-use-namespace")


def schema_native_create(controller, purpose, *, reader_request=None):
    dsn_guard(controller)
    mounts = (("bind", str(controller.exchange_directory), "/tmp/bifrost/workflow-result-parity", True),)
    if reader_request is None:
        command = ["observe-schema"]
        entrypoint = "/tmp/bifrost/workflow-result-parity/driver"
    else:
        require(len(encode(reader_request)) <= 65536, "software-request-bound")
        command = ["-I", "-S", "-B", "-c", SOFTWARE_READER, encode(reader_request).decode("ascii")]
        entrypoint = "/usr/local/bin/python3"
        mounts += (("volume", controller.targets, "/targets", True),)
    create_native(
        controller,
        purpose,
        controller.api_image,
        command,
        network=controller.network_name,
        mounts=mounts,
        env_file=controller.secret_file,
        entrypoint=entrypoint,
        user="1000:" + str(controller.target_identity["gid"]),
        caps=(),
        stdin=True,
        readonly=True,
    )


def schema_before(controller):
    schema_native_create(controller, "schema_before")
    inspect_native(controller, "inspect_pre_schema_before", ["schema_before"])
    request = {
        "schema": "bifrost.test.workflow-schema-observation/v1",
        "case_id": "schemaBefore",
        "phase": "before_target",
    }
    raw, stderr, _ = start_native(controller, "schema_before", stdin=encode(request))
    require(stderr == b"", "schema-before-stderr")
    value = schema_observation(raw, "schemaBefore", "before_target")
    controller.before_schema = value
    inspect_native(controller, "inspect_post_schema_before", ["schema_before"], terminal=True)
    dsn_guard(controller)
    require(value["heads"] == controller.heads, "schema-before-source-heads")


def native_phase(controller):
    controller.begin("native")
    require(controller.target_complete and controller.after_admitted, "native-after-real-target")
    for invocation in range(7):
        purpose = NATIVE_LABELS[invocation]
        live = invocation in {5, 6}
        if live:
            dsn_guard(controller)
        create_native(
            controller,
            purpose,
            controller.toolchain_image,
            NATIVE_ARGV[invocation],
            network=controller.network_name if live else "none",
            mounts=cargo_mounts(controller),
            environment=cargo_environment(TARGET_DIRECTORIES[invocation]),
            env_file=controller.secret_file if live else None,
            workdir="/workspace/core-rs",
        )
    inspect_native(controller, "inspect_pre_native", list(NATIVE_LABELS[:7]))
    for invocation in range(7):
        stdout, stderr, _ = start_native(controller, NATIVE_LABELS[invocation])
        observation = cargo_artifacts(controller, stdout, invocation)
        cargo_test_completion(controller, observation, stderr)
        if invocation in {5, 6}:
            dsn_guard(controller)
    associations = {}
    for observation in controller.invocations.values():
        references = [([root["path"]], root["association"]) for root in observation["roots"].values()] + [
            (unit["paths"], unit["association"]) for unit in observation["units"]
        ]
        for files, association in references:
            encoded = encode(association)
            for path in files:
                previous = associations.get(path)
                require(previous is None or previous == encoded, "software-conflicting-producer")
                associations[path] = encoded
    controller.software_associations = associations
    paths = {"/tmp/bifrost/workflow-result-parity/driver", *associations}
    require(1 <= len(paths) <= 142, "software-roster-bound")
    request = {
        "schema": "bifrost.private.result-software-request/v1",
        "uid": 1000,
        "gid": controller.target_identity["gid"],
        "user_namespace": controller.target_identity["user_namespace"],
        "paths": sorted(paths),
    }
    # Derive paths only from all actual complete compiler captures; reader creation performs no byte read.
    schema_native_create(controller, "schema_after", reader_request=request)
    purposes = [*NATIVE_LABELS[:7], "schema_after"]
    records = [controller.resources[purpose] for purpose in purposes]
    rows = inspect_many(controller, "git_final_identity", [record["cid"] for record in records], kind="container")
    for purpose in NATIVE_LABELS[:7]:
        record = controller.resources[purpose]
        validate_native(controller, record, rows[record["cid"]], terminal=True)
    reader = controller.resources["schema_after"]
    validate_native(controller, reader, rows[reader["cid"]])
    original_request = {
        "schema": "bifrost.test.workflow-schema-observation/v1",
        "case_id": "schemaAfter",
        "phase": "after_native",
    }
    stdout, stderr, _ = start_native(controller, "schema_after", stdin=encode(original_request))
    require(stderr == b"", "schema-after-stderr")
    native_raw = software_observation(controller, stdout, sorted(paths))
    value = schema_observation(native_raw, "schemaAfter", "after_native")
    controller.after_schema = value
    controller.teardown_partial_census = project_census(controller, "census_target_teardown")
    snapshot = teardown_readback(
        controller, controller.teardown_partial_census, "inspect_post_native", terminal_purposes=purposes
    )
    controller.teardown_partial_snapshot = snapshot
    if snapshot["first_error"] is not None:
        raise snapshot["first_error"]
    controller.teardown_snapshot = snapshot
    dsn_guard(controller)
    require(
        value["heads"] == controller.heads
        and controller.before_schema is not None
        and all(
            value[key] == controller.before_schema[key]
            for key in ("server_major", "heads", "device_floor", "catalog", "complete")
        ),
        "schema-restoration-match",
    )
    controller.finish()


def feature_export(controller):
    invocations = []
    for identity in range(8):
        actual = controller.invocations.get(identity)
        if actual is None:
            invocations.append({"id": identity, "units": [], "roots": [], "completed": [], "complete": False})
            continue
        roots = [
            [root_id, record["sha256"]] for root_id, record in sorted(actual["roots"].items()) if "sha256" in record
        ]
        invocations.append(
            {
                "id": identity,
                "units": [unit["safe"] for unit in actual["units"]],
                "roots": roots,
                "completed": actual["completed"],
                "complete": actual["complete"],
            }
        )
    require(sum(len(record["units"]) for record in invocations) <= 64, "feature-export-total-units")
    required = ((0, 1, 5), (0, 1, 5), (0, 1, 5), (0, 1, 5), (0, 1, 5), (2, 3, 5, 6), (4, 6), (8,))
    venues = []
    fixture = getattr(controller, "fixture_feature", None)
    for dependencies in required:
        observations = []
        for invocation in dependencies:
            if invocation == 8:
                complete = fixture is not None and all(
                    fixture[name] is True
                    for name in ("python_completed", "native_completed", "result", "variables", "context")
                )
                outcome = "pass" if complete else "missing" if fixture is None else "failure"
                observations.append([8, complete, outcome, fixture])
                continue
            actual = controller.invocations.get(invocation)
            complete = actual is not None and actual["complete"] is True
            purpose = NATIVE_LABELS[invocation]
            command = next((record for record in controller.commands if record["label"] == "start_" + purpose), None)
            if complete:
                outcome = "pass"
            elif command is None:
                outcome = "missing"
            else:
                outcome = "error"
            observations.append([invocation, complete, outcome])
        venues.append(observations)
    value = {
        "schema": "bifrost.test.workflow-result-feature-venues/v2",
        "candidate": controller.candidate,
        "invocation_uuid": OWNER,
        "source_receipt_sha256": getattr(controller, "receipt_sha", None),
        "binary_sha256": None if controller.binary is None else controller.binary["sha256"],
        "invocations": invocations,
        "venues": venues,
    }
    require(len(encode(value) + b"\n") <= ARTIFACT_CAPS["features.json"], "feature-export-bound")
    return value


def project_census(controller, label):
    raw, _, _ = controller.run(label, census_shell())
    return parse_census(raw)


def project_inspection(controller, label, objects, *, include_images=()):
    identifiers = [*objects["containers"], *objects["volumes"], *objects["networks"], *include_images]
    require(len(identifiers) == len(set(identifiers)) and len(identifiers) <= 128, "project-inspect-roster")
    if not identifiers:
        return {}
    raw, _, _ = controller.run(label, ["docker", "inspect", *identifiers])
    rows = decode(raw)
    require(
        type(rows) is list and len(rows) == len(identifiers) and all(type(row) is dict for row in rows),
        "project-inspect-response",
    )
    indexed = {}
    for row in rows:
        identity = row.get("Id", row.get("Name"))
        require(identity in identifiers and identity not in indexed, "project-inspect-identity")
        indexed[identity] = row
    require(set(indexed) == set(identifiers), "project-inspect-complete")
    return indexed


def project_owned(controller, name, objects, rows):
    for kind, identifiers in objects.items():
        initial = controller.initial_census[kind]
        initial_ids = {row.get("Name") if kind == "volumes" else row.get("ID") for row in initial}
        for identity in identifiers:
            require(identity not in initial_ids and identity in rows, "project-initial-exclusion")
            row = rows[identity]
            if kind == "containers":
                config = row.get("Config")
                require(
                    type(config) is dict and type(row.get("State")) is dict and type(config.get("Labels")) is dict,
                    "project-container-shape",
                )
                labels = config["Labels"]
            else:
                require(
                    type(row.get("Labels")) is dict and row.get("Driver") in {"local", "bridge"},
                    "project-resource-shape",
                )
                labels = row["Labels"]
            require(labels.get("com.docker.compose.project") == name, "project-owned-label")
            if kind == "containers" and labels.get("bifrost.result.owner") != OWNER:
                require(
                    labels.get("com.docker.compose.service") in controller.base_config["services"],
                    "project-selected-service",
                )
    return objects


def teardown_readback(controller, census, label, *, terminal_purposes=()):
    scopes = (controller.target_project, controller.prepr_project)
    objects = {scope: project_objects(census, scope) for scope in scopes}
    kinds = {}

    def include(identity, kind):
        require(type(identity) is str and identity, "teardown-object-identity")
        require(identity not in kinds or kinds[identity] == kind, "teardown-cross-type-collision")
        kinds[identity] = kind

    for scope in scopes:
        for kind, identities in objects[scope].items():
            for identity in identities:
                include(identity, kind)
    for record in controller.resources.values():
        if record["cid"] is not None and not record["removed"]:
            include(record["cid"], "containers")
    for name, record in controller.volume_resources.items():
        if not record["removed"]:
            include(name, "volumes")
    images = controller.stack_images | controller.qualified_images
    for identity in images:
        include(identity, "images")
    require(len(kinds) <= 128, "teardown-typed-bound")
    rows = {}
    if kinds:
        raw, _, _ = controller.run(label, ["docker", "inspect", *sorted(kinds)])
        measured = decode(raw)
        require(type(measured) is list and len(measured) == len(kinds), "teardown-typed-cardinality")
        for row in measured:
            require(type(row) is dict, "teardown-typed-row")
            identity = row.get("Id", row.get("Name"))
            require(identity in kinds and identity not in rows, "teardown-typed-membership")
            kind = kinds[identity]
            if kind == "containers":
                require(
                    type(row.get("State")) is dict and type(row.get("HostConfig")) is dict, "teardown-container-type"
                )
            elif kind == "images":
                require(
                    "State" not in row and type(row.get("RootFS")) is dict and type(row.get("Config")) is dict,
                    "teardown-image-type",
                )
            elif kind == "volumes":
                require(
                    row.get("Name") == identity and row.get("Scope") == "local" and "State" not in row,
                    "teardown-volume-type",
                )
            else:
                require(
                    type(row.get("Containers")) is dict and type(row.get("IPAM")) is dict and "State" not in row,
                    "teardown-network-type",
                )
            rows[identity] = row
        require(set(rows) == set(kinds), "teardown-typed-complete")
    # Actual returned bytes are required. Anchors never substitute for the complete current response.
    dispositions = {}
    first = None

    def admit(identity, operation):
        nonlocal first
        try:
            operation()
            dispositions[identity] = True
        except BaseException as error:
            dispositions[identity] = False
            if first is None:
                first = error

    for identity, anchor in images.items():

        def admit_image(identity=identity, anchor=anchor):
            require(
                all(
                    rows[identity].get(key) == anchor.get(key)
                    for key in ("Id", "Config", "RootFS", "Os", "Architecture")
                ),
                "teardown-image-source-drift",
            )

        admit(identity, admit_image)
    for name, record in controller.volume_resources.items():
        if not record["removed"]:

            def admit_volume(name=name, record=record):
                require(record["admitted"], "teardown-cargo-volume-unverified")
                owned_volume(controller, rows[name], name, record["purpose"])
                require(rows[name] == record["baseline"], "teardown-cargo-volume-drift")

            admit(name, admit_volume)
    for purpose in terminal_purposes:
        record = controller.resources[purpose]
        admit(
            record["cid"],
            lambda record=record: validate_native(controller, record, rows[record["cid"]], terminal=True),
        )
    for record in controller.resources.values():
        if not record["removed"] and dispositions.get(record["image"]) is False:
            record["custody_failed"] = True
    admitted = set()
    for scope in scopes:
        anchors = controller.project_anchors.get(scope, {})
        scoped = []
        for kind, identities in objects[scope].items():
            for identity in identities:
                scoped.append(identity)

                def admit_project_object(identity=identity, kind=kind, scope=scope, anchors=anchors):
                    project_owned(controller, scope, {kind: [identity]}, rows)
                    current = rows[identity]
                    native = next(
                        (record for record in controller.resources.values() if record["cid"] == identity), None
                    )
                    if kind == "containers" and native is not None:
                        require(
                            native["baseline"] is not None
                            and not native["removed"]
                            and not native.get("custody_failed", False),
                            "teardown-native-unverified",
                        )
                        require(
                            {key: current.get(key) for key in native["baseline"]} == native["baseline"],
                            "teardown-native-drift",
                        )
                        native_network_association(controller, native, current, require_association=native["started"])
                        require(dispositions.get(native["image"]) is True, "teardown-native-image-unverified")
                        return
                    if kind == "volumes" and identity in controller.volume_resources:
                        require(dispositions.get(identity) is True, "teardown-cargo-volume-unverified")
                        return
                    anchor = anchors.get(identity)
                    if kind == "containers" and identity == getattr(controller, "runner_cid", None):
                        anchor = getattr(controller, "runner_retained", None)
                    require(type(anchor) is dict, "teardown-source-acquisition-missing")
                    if kind == "containers":
                        require(
                            all(
                                current.get(key) == anchor.get(key)
                                for key in ("Id", "Name", "Image", "Config", "HostConfig", "Mounts")
                            ),
                            "teardown-source-container-drift",
                        )
                        image = current.get("Image")
                        require(
                            image in rows and kinds[image] == "images" and dispositions.get(image) is True,
                            "teardown-source-image-missing",
                        )
                        configured = current["Config"].get("Image")
                        service = current["Config"]["Labels"].get("com.docker.compose.service")
                        require(service in controller.base_config["services"], "teardown-source-service")
                        expected = controller.base_config["services"][service].get("image")
                        require(
                            configured == expected
                            or (identity == getattr(controller, "runner_cid", None) and image == controller.api_image),
                            "teardown-configured-image",
                        )
                        if scope == controller.target_project:
                            networks = current.get("NetworkSettings", {}).get("Networks")
                            require(
                                type(networks) is dict and set(networks) == {controller.network_name},
                                "teardown-source-network-membership",
                            )
                            endpoint = networks[controller.network_name]
                            require(
                                type(endpoint) is dict and endpoint.get("NetworkID") == controller.network_id,
                                "teardown-source-network-id",
                            )
                    elif kind == "volumes":
                        require(current == anchor, "teardown-source-volume-drift")
                    else:
                        require(
                            all(
                                current.get(key) == anchor.get(key)
                                for key in (
                                    "Id",
                                    "Name",
                                    "Driver",
                                    "Scope",
                                    "IPAM",
                                    "Labels",
                                    "Options",
                                    "Internal",
                                    "EnableIPv6",
                                )
                            ),
                            "teardown-source-network-drift",
                        )
                        require(
                            set(current["Containers"]) <= set(objects[scope]["containers"]),
                            "teardown-foreign-network-member",
                        )
                        if scope == controller.target_project:
                            require(
                                identity == controller.network_id and current.get("Name") == controller.network_name,
                                "teardown-source-network-identity",
                            )

                # Failed terminal admission is never overwritten by a later weaker project check.
                if dispositions.get(identity) is not False:
                    admit(identity, admit_project_object)
        if all(dispositions.get(identity) is True for identity in scoped):
            admitted.add(scope)
    return {
        "census": census,
        "objects": objects,
        "rows": rows,
        "admitted_scopes": admitted,
        "dispositions": dispositions,
        "first_error": first,
        "acquired_at": len(controller.commands),
    }


def source_environment(controller, scope, *, derived=False, skip_build=False, clean_boot=False):
    require(scope in {"prepr", "target"}, "source-environment-scope")
    extra = {
        "BIFROST_PROJECT_PREFIX": controller.prepr_prefix if scope == "prepr" else controller.target_prefix,
        "COMPOSE_FILE": str(controller.derived_config_path) if derived else "docker-compose.test.yml",
    }
    if skip_build:
        require(controller.api_image in controller.qualified_images, "skip-build-qualified-image")
        extra["BIFROST_SKIP_BUILD"] = "1"
    if clean_boot:
        require(
            scope == "target" and derived and not os.path.lexists(controller.log_directory / ".clean-boot-consumed"),
            "sole-clean-boot-selection",
        )
        extra["BIFROST_TEST_USE_CLEAN_BOOT"] = "1"
    return extra


def prepr_phase(controller):
    source_preflight(controller)
    controller.stages["source"].update(started=True, finished=True, native_exit=0, admitted=True)
    docker_admission(controller)
    raw, _, _ = controller.run(
        "compose_base_config",
        ["docker", "compose", "-f", "docker-compose.test.yml", "config", "--format", "json"],
        extra={"COMPOSE_PROJECT_NAME": controller.target_project, "LOG_DIR": str(controller.log_directory)},
    )
    config = decode(raw)
    require(
        type(config) is dict
        and type(config.get("services")) is dict
        and "test-runner" in config["services"]
        and "api" in config["services"]
        and "pgbouncer" in config["services"],
        "compose-selected-source",
    )
    controller.base_config = config
    services = config["services"]
    require(type(services["test-runner"].get("environment")) is dict, "compose-selected-environment")
    dsn_profile(controller, services["test-runner"]["environment"].get("BIFROST_DATABASE_URL"))
    api = services["api"]
    require(
        type(api.get("image")) is str
        and type(api.get("build")) is dict
        and Path(api["build"].get("context", "")).resolve(strict=True) == ROOT
        and api["build"].get("dockerfile") == "api/Dockerfile.dev",
        "compose-api-source-recipe",
    )
    controller.api_tag = api["image"]
    controller.initial_census = project_census(controller, "census_initial")
    require(
        all(
            not identifiers
            for scope in (controller.prepr_project, controller.target_project)
            for identifiers in project_objects(controller.initial_census, scope).values()
        ),
        "projects-initially-empty",
    )
    raw, _, _ = controller.run("images_initial", ["docker", "image", "ls", "--no-trunc", "--format", "{{json .}}"])
    initial = initial_image_inventory(raw)
    controller.initial_image_ids = {row["ID"] for row in initial}
    tags = {row.get("Repository", "") + ":" + row.get("Tag", "") for row in initial}
    configured_build_tags = {
        service["image"]
        for service in services.values()
        if type(service) is dict and type(service.get("build")) is dict and type(service.get("image")) is str
    }
    require(not configured_build_tags & tags, "configured-build-tags-initial-exclusion")
    copy_input_admission(controller)
    controller.prepr_started = True
    controller.prepr_image_pending = True
    controller.run("literal_prepr", ["./test.sh", "pre-pr"], extra=source_environment(controller, "prepr"))
    controller.prepr_complete = True
    copy_input_admission(controller)
    raw, _, _ = controller.run("git_post_prepr_clean", ["git", "status", "--porcelain=v1", "--untracked-files=all"])
    require(raw == b"", "candidate-post-prepr-clean")
    raw, _, _ = controller.run("git_post_prepr_identity", ["git", "rev-parse", "HEAD", "HEAD^{tree}"])
    require(
        raw.decode("ascii").splitlines() == [controller.candidate["head"], controller.candidate["tree"]],
        "candidate-post-prepr-identity",
    )
    raw, _, _ = controller.run("images_post_prepr", ["docker", "image", "ls", "--no-trunc", "--format", "{{json .}}"])
    current = initial_image_inventory(raw)
    built = sorted(
        {row["ID"] for row in current if row.get("Repository", "") + ":" + row.get("Tag", "") in configured_build_tags}
    )
    require(built and not set(built) & controller.initial_image_ids, "prepr-image-initial-exclusion")
    raw, _, _ = controller.run("images_prepr_inspect", ["docker", "image", "inspect", *built])
    image_rows = decode(raw)
    require(
        type(image_rows) is list
        and len(image_rows) == len(built)
        and {row.get("Id") for row in image_rows} == set(built),
        "prepr-image-association",
    )
    controller.prepr_images = image_rows
    for index, row in enumerate(image_rows):
        require(
            type(row.get("RepoTags")) is list and set(row["RepoTags"]) & configured_build_tags,
            "prepr-inspected-build-tag",
        )
        qualified_image(controller, "prepr_build_" + str(index), row, source_association=True)
    controller.prepr_image_pending = False
    census = project_census(controller, "census_prepr_teardown")
    objects = project_objects(census, controller.prepr_project)
    rows = project_inspection(controller, "inspect_prepr_owned", objects)
    project_owned(controller, controller.prepr_project, objects, rows)
    controller.prepr_objects = objects
    controller.project_anchors[controller.prepr_project] = rows
    controller.run("prepr_stack_down", ["./test.sh", "stack", "down"], extra=source_environment(controller, "prepr"))
    absence = project_census(controller, "census_prepr_absent")
    require(
        all(not identifiers for identifiers in project_objects(absence, controller.prepr_project).values()),
        "prepr-independent-empty",
    )
    controller.finish()


def stack_phase(controller):
    controller.begin("stack")
    copy_input_admission(controller)
    raw, _, before_exit = controller.run(
        "api_before_build", ["docker", "image", "inspect", controller.api_tag], allow_exit=True
    )
    controller.api_before = None
    if before_exit == 0:
        rows = decode(raw)
        require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "api-before-image")
        controller.api_before = rows[0]
        require(rows[0].get("Id") in controller.qualified_images, "api-before-known-source")
    controller.api_build_pending = True
    controller.run(
        "api_build",
        ["docker", "compose", "-f", "docker-compose.test.yml", "build", "api"],
        extra={"COMPOSE_PROJECT_NAME": controller.target_project, "LOG_DIR": str(controller.log_directory)},
    )
    copy_input_admission(controller)
    raw, _, _ = controller.run("api_after_build", ["docker", "image", "inspect", controller.api_tag])
    rows = decode(raw)
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "api-after-image")
    row = rows[0]
    require(
        row.get("Id") not in controller.initial_image_ids
        and type(row.get("RepoTags")) is list
        and controller.api_tag in row["RepoTags"]
        and type(row.get("Config")) is dict
        and row["Config"].get("Entrypoint") == ["/entrypoint.sh"]
        and row["Config"].get("WorkingDir") == "/app"
        and row["Config"].get("User") in {"", "0", "root"},
        "api-qualified-source-profile",
    )
    controller.api_image = qualified_image(controller, "api_dev", row, source_association=True)
    controller.api_build_pending = False
    controller.target_started = True
    controller.run(
        "target_stack_up", ["./test.sh", "stack", "up"], extra=source_environment(controller, "target", skip_build=True)
    )
    census = project_census(controller, "census_stack_ready")
    objects = project_objects(census, controller.target_project)
    require(objects["containers"] and len(objects["networks"]) == 1, "stack-object-membership")
    # Container-list image references are observed inputs. The same counted inspect
    # associates each immutable image row to the actual container Image, not its tag alone.
    references = sorted({row.get("Image") for row in census["containers"] if row.get("ID") in objects["containers"]})
    require(all(type(item) is str and item for item in references) and len(references) <= 16, "stack-image-references")
    identifiers = [*objects["containers"], *objects["volumes"], *objects["networks"], *references]
    require(len(identifiers) == len(set(identifiers)) and len(identifiers) <= 128, "stack-inspect-bound")
    raw, _, _ = controller.run("inspect_stack_owned", ["docker", "inspect", *identifiers])
    rows = decode(raw)
    require(
        type(rows) is list and len(rows) == len(identifiers) and all(type(row) is dict for row in rows),
        "stack-inspect-shape",
    )
    indexed = {}
    image_rows = []
    for observed in rows:
        if "RootFS" in observed and "State" not in observed:
            image_rows.append(observed)
        else:
            identity = observed.get("Id", observed.get("Name"))
            require(
                identity in set(identifiers) - set(references) and identity not in indexed, "stack-inspect-identity"
            )
            indexed[identity] = observed
    require(
        set(indexed) == set(identifiers) - set(references) and len(image_rows) == len(references),
        "stack-inspect-complete",
    )
    matched_references = set()
    for observed in image_rows:
        identity = observed.get("Id")
        require(
            type(identity) is str
            and IMAGE_ID.fullmatch(identity) is not None
            and type(observed.get("RepoTags")) is list,
            "stack-image-row",
        )
        matches = (set(observed["RepoTags"]) | {identity}) & set(references)
        require(matches and not matches & matched_references, "stack-image-tag-association")
        matched_references.update(matches)
        if identity in controller.stack_images:
            require(controller.stack_images[identity] == observed, "stack-image-contradiction")
        controller.stack_images[identity] = observed
    require(matched_references == set(references), "stack-image-membership")
    project_owned(controller, controller.target_project, objects, indexed)
    controller.stack_objects = objects
    controller.allowed_project_cids = set(objects["containers"])
    controller.network_id = objects["networks"][0]
    network = indexed[controller.network_id]
    require(
        type(network.get("Labels")) is dict
        and network["Labels"].get("com.docker.compose.network") == "default"
        and type(network.get("Name")) is str
        and type(network.get("Containers")) is dict,
        "stack-network-profile",
    )
    controller.network_name = network["Name"]
    frontends = []
    for cid in objects["containers"]:
        observed = indexed[cid]
        labels = observed["Config"]["Labels"]
        if labels.get("bifrost.result.owner") == OWNER:
            require(
                any(record.get("cid") == cid for record in controller.resources.values()), "stack-native-registration"
            )
            continue
        service = labels["com.docker.compose.service"]
        require(observed.get("Image") in controller.stack_images, "stack-container-image")
        if service in {"api", "api-replica", "worker", "scheduler", "init"}:
            require(observed["Image"] == controller.api_image, "stack-api-image-agreement")
        if service == "pgbouncer":
            frontends.append(observed)
    controller.project_anchors[controller.target_project] = indexed
    require(len(frontends) == 1, "stack-frontend-identity")
    frontend = frontends[0]
    controller.frontend_cid = frontend["Id"]
    require(controller.frontend_cid in network["Containers"], "stack-frontend-network")
    address = network["Containers"][controller.frontend_cid].get("IPv4Address")
    require(
        type(address) is str and re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}", address) is not None,
        "stack-frontend-address",
    )
    controller.frontend_address, controller.network_prefix = address.split("/")
    require(
        all(int(part) <= 255 for part in controller.frontend_address.split("."))
        and 1 <= int(controller.network_prefix) <= 32,
        "stack-frontend-address-types",
    )
    identity_probe(controller)
    private_dsn_file(controller)
    schema_before(controller)
    controller.allowed_project_cids.update(
        record["cid"] for record in controller.resources.values() if record["cid"] is not None
    )
    controller.frontend_pending_before = frontend_prefix(controller, "before")
    require(not os.path.lexists(controller.log_directory / ".clean-boot-consumed"), "initial-clean-boot-marker")
    controller.finish()


def manifest_phase(controller):
    controller.begin("manifest")
    source_receipt(controller)
    fixture = decode(controller.blobs["api/tests/parity/fixtures/workflow-result-v1.json"])
    roster = junit_roster(controller)
    controller.junit_property_domains = {
        "result_case_id": {case["case_id"] for case in fixture["cases"]},
        "result_control_id": {
            identifier
            for category, identifiers in fixture["controls"].items()
            if category != "Feature-preservation"
            for identifier in identifiers
        },
        "result_control_category": set(fixture["controls"]) - {"Feature-preservation"},
        "result_reference": {"returned", "missing_fence", "source_width_failure", "numeric_range"},
        "result_native_kind": {"applied", "rejected", "infrastructure_failure"},
    }
    require(
        len(roster) == 299 and len(controller.junit_property_domains["result_case_id"]) == 233, "manifest-source-counts"
    )
    for family, initial in (("endpoint", "request"), ("after", "ready")):
        for suffix in (initial, "admit", "failed", initial + ".part", "admit.part", "failed.part"):
            name = f".result-{family}-{OWNER}-{suffix}.json"
            require(not os.path.lexists(controller.log_directory / name), "manifest-shared-preexists")
    controller.finish()


def target_phase(controller):
    controller.begin("target")
    target_configuration(controller)
    junit_path = controller.log_directory / "test-results.xml"
    require(not os.path.lexists(junit_path), "target-junit-preexists")
    require(not os.path.lexists(controller.log_directory / ".clean-boot-consumed"), "target-clean-boot-preexists")
    controller.target_started = True
    _stdout, _stderr, exit_code = controller.run(
        "target_pytest",
        ["./test.sh", "tests/parity/test_workflow_sql.py", "-v"],
        extra=source_environment(controller, "target", derived=True, skip_build=True, clean_boot=True),
        tick=lambda process: target_tick(controller, process),
    )
    require(exit_code == 0 and controller.endpoint_admitted and controller.after_admitted, "target-native-and-brackets")
    raw, info = stable_file(junit_path, 1024 * 1024, expected_uid=1000)
    controller.junit_input_identity = (info.st_dev, info.st_ino)
    controller.owned_files.append({"path": junit_path, "identity": controller.junit_input_identity, "removed": None})
    controller.junit = junit_projection(controller, raw)
    require(
        controller.junit["counts"] == {"tests": 299, "passed": 299, "failures": 0, "errors": 0, "skipped": 0},
        "target-all-identities-passed",
    )
    require(
        controller.observer_final["complete"] and controller.observer_final["functions_entered"] == 299,
        "target-observer-complete",
    )
    for case in controller.junit["cases"]:
        properties = case["properties"]
        name = case["nodeid"].split("::", 1)[1]
        if name.startswith("test_result_nonfault_paired["):
            require(
                properties.get("result_case_id") == name.removeprefix("test_result_nonfault_paired[").removesuffix("]")
                and properties.get("result_independent_readback") == "complete"
                and properties.get("result_measured_transaction") in {"committed", "rolled_back"},
                "target-paired-observation",
            )
        elif name.startswith("test_result_nonfault_control["):
            require(
                name
                == "test_result_nonfault_control["
                + properties.get("result_control_category", "")
                + "-"
                + properties.get("result_control_id", "")
                + "]",
                "target-control-observation",
            )
    require(
        all(
            controller.fixture_feature[key] is True
            for key in ("python_completed", "native_completed", "result", "variables", "context")
        ),
        "target-feature-fixture-complete",
    )
    controller.target_complete = True
    controller.finish()


def cleanup_attempt(controller, operation):
    try:
        return operation()
    except BaseException as error:
        controller.cleanup_failed = True
        if controller.cleanup_first is None:
            controller.cleanup_first = error
        return None


def settle_parent_processes(controller):
    for session in controller.sessions:
        process = session["process"]
        failed = False
        if process is not None:
            present = cleanup_attempt(controller, lambda process=process: group_exists(process.pid))
            failed |= present is None
            if present is True:

                def kill_group(process=process):
                    os.killpg(process.pid, signal.SIGKILL)
                    return True

                failed |= cleanup_attempt(controller, kill_group) is not True

            def reap(process=process):
                process.wait(timeout=max(0, controller.end - time.monotonic()))
                while group_exists(process.pid):
                    deadline(controller.end)
                    time.sleep(0.01)
                return True

            failed |= cleanup_attempt(controller, reap) is not True
        for index, handle in enumerate(session["handles"]):
            if session["handle_registered"][index]:

                def unregister_handle(handle=handle, index=index, session=session):
                    controller.selector.unregister(handle)
                    session["handle_registered"][index] = False
                    return True

                failed |= cleanup_attempt(controller, unregister_handle) is not True
            if not session["handle_closed"][index]:

                def close_handle(handle=handle, index=index, session=session):
                    handle.close()
                    session["handle_closed"][index] = True
                    return True

                failed |= cleanup_attempt(controller, close_handle) is not True
        for index, fd in enumerate(session["capture_fds"]):
            if not session["capture_closed"][index]:

                def close_capture(fd=fd, index=index, session=session):
                    require(not session["capture_close_attempted"][index], "cleanup-capture-close-unknown")
                    session["capture_close_attempted"][index] = True
                    os.close(fd)
                    session["capture_closed"][index] = True
                    return True

                failed |= cleanup_attempt(controller, close_capture) is not True
        session["retired"] = True
        group_absent = (
            True
            if process is None
            else cleanup_attempt(controller, lambda process=process: not group_exists(process.pid))
        )
        failed |= group_absent is not True
        session["settled"] = not failed and (process is None or process.returncode is not None)


def remove_owned_file(record):
    path = record["path"]
    if not os.path.lexists(path):
        record["removed"] = True
        return True
    require(record["identity"] is not None, "cleanup-file-unverified")
    observed = os.stat(path, follow_symlinks=False)
    require(
        stat.S_ISREG(observed.st_mode)
        and observed.st_nlink == 1
        and (observed.st_dev, observed.st_ino) == record["identity"],
        "cleanup-file-identity",
    )
    os.unlink(path)
    record["removed"] = not os.path.lexists(path)
    require(record["removed"], "cleanup-file-absence")
    return True


def exchange_disposal(controller):
    result = {}
    directory = getattr(controller, "log_directory", None)
    for family, initial, exported in (("endpoint", "request", "endpoint"), ("after", "ready", "after_actor")):
        observation = {"request": None, "admit": None, "failed": None, "temporary": None}
        if directory is not None:
            for suffix, key in ((initial, "request"), ("admit", "admit"), ("failed", "failed")):
                path = directory / f".result-{family}-{OWNER}-{suffix}.json"
                observation[key] = not os.path.lexists(path)
            observation["temporary"] = all(
                not os.path.lexists(directory / f".result-{family}-{OWNER}-{suffix}.part.json")
                for suffix in (initial, "admit", "failed")
            )
        result[exported] = observation
    return result


def dispose_private(controller):
    for record in controller.owned_files:
        fd = record.get("fd")
        if fd is not None:

            def close_file(record=record, fd=fd):
                require(not record.get("close_attempted", False), "cleanup-file-close-unknown")
                record["close_attempted"] = True
                try:
                    os.close(fd)
                    record["closed"] = True
                    return True
                except BaseException:
                    record["closed"] = None
                    raise
                finally:
                    record["fd"] = None

            cleanup_attempt(controller, close_file)
        if record.get("close_attempted", False) and record.get("closed") is not True:
            controller.cleanup_failed = True
        cleanup_attempt(controller, lambda record=record: remove_owned_file(record))
    # Shared files can have been published by the genuine child before parent admission failed.
    directory = getattr(controller, "log_directory", None)
    if directory is not None:
        for family, initial in (("endpoint", "request"), ("after", "ready")):
            for suffix in (initial, "admit", "failed", initial + ".part", "admit.part", "failed.part"):
                path = directory / f".result-{family}-{OWNER}-{suffix}.json"
                if os.path.lexists(path):

                    def register_shared(path=path):
                        raw, observed = stable_file(path, 4096, expected_mode=0o644)
                        require(observed.st_uid in {1000, os.geteuid()}, "cleanup-shared-owner")
                        value = decode(raw)
                        require(
                            type(value) is dict
                            and value.get("candidate") == controller.candidate
                            and value.get("invocation_uuid") == OWNER,
                            "cleanup-shared-association",
                        )
                        record = {"path": path, "identity": (observed.st_dev, observed.st_ino), "removed": None}
                        controller.owned_files.append(record)
                        return remove_owned_file(record)

                    cleanup_attempt(controller, register_shared)
    if controller.selector is not None:
        cleanup_attempt(controller, controller.selector.close)
    capture_ok = all(session["settled"] and all(session["capture_closed"]) for session in controller.sessions)
    if capture_ok and controller.capture_acquired:

        def remove_captures():
            expected = {
                f"{index}-{suffix}" for index in range(1, len(controller.commands) + 1) for suffix in ("out", "err")
            }
            actual = {path.name for path in controller.capture_directory.iterdir()}
            require(actual <= expected, "cleanup-capture-roster")
            for name in sorted(actual):
                path = controller.capture_directory / name
                observed = os.stat(path, follow_symlinks=False)
                command_index, suffix = name.split("-", 1)
                associated = [
                    session for session in controller.sessions if session["command_index"] == int(command_index)
                ]
                require(len(associated) == 1, "cleanup-capture-command-association")
                session = associated[0]
                stream_index = 0 if suffix == "out" else 1
                require(
                    stat.S_ISREG(observed.st_mode)
                    and observed.st_uid == os.geteuid()
                    and observed.st_nlink == 1
                    and stat.S_IMODE(observed.st_mode) == 0o600,
                    "cleanup-capture-profile",
                )
                require(
                    stream_index < len(session["capture_identities"])
                    and (observed.st_dev, observed.st_ino) == session["capture_identities"][stream_index],
                    "cleanup-capture-identity",
                )
                os.unlink(path)
            controller.capture_directory.rmdir()
            return not os.path.lexists(controller.capture_directory)

        capture_ok = cleanup_attempt(controller, remove_captures) is True
    else:
        capture_ok = capture_ok and not controller.capture_acquired
    credentials_ok = all(
        record.get("fd") is None
        and (not record.get("close_attempted", False) or record.get("closed") is True)
        and record["removed"] is True
        for record in controller.owned_files
    )
    if controller.docker_acquired:

        def remove_docker_configuration():
            observed = os.stat(controller.docker_directory, follow_symlinks=False)
            original = controller.docker_directory_identity
            require(
                (observed.st_dev, observed.st_ino, observed.st_uid, stat.S_IMODE(observed.st_mode))
                == (original[0], original[1], os.geteuid(), 0o700)
                and shutil.rmtree.avoids_symlink_attacks is True,
                "cleanup-docker-directory-identity",
            )
            count = 0
            total = 0
            for directory, children, files in os.walk(controller.docker_directory, followlinks=False):
                require(
                    len(Path(directory).relative_to(controller.docker_directory).parts) <= 8,
                    "cleanup-docker-directory-depth",
                )
                for name in (*children, *files):
                    current = os.stat(Path(directory) / name, follow_symlinks=False)
                    count += 1
                    total += current.st_size if stat.S_ISREG(current.st_mode) else 0
                    require(
                        count <= 128
                        and total <= STREAM_LIMIT
                        and current.st_uid == os.geteuid()
                        and (
                            stat.S_ISDIR(current.st_mode)
                            or stat.S_ISREG(current.st_mode)
                            or stat.S_ISLNK(current.st_mode)
                        ),
                        "cleanup-docker-directory-profile",
                    )
            shutil.rmtree(controller.docker_directory)
            require(not os.path.lexists(controller.docker_directory), "cleanup-docker-directory-absence")
            return True

        credentials_ok &= cleanup_attempt(controller, remove_docker_configuration) is True
    for name, acquired in (
        ("exchange_directory", "exchange_directory_acquired"),
        ("secret_directory", "secret_directory_acquired"),
    ):
        path = getattr(controller, name, None)
        if path is not None and getattr(controller, acquired, False):

            def remove_directory(path=path):
                observed = os.stat(path, follow_symlinks=False)
                require(stat.S_ISDIR(observed.st_mode) and observed.st_uid == os.geteuid(), "cleanup-private-directory")
                require(not any(path.iterdir()), "cleanup-private-directory-notempty")
                path.rmdir()
                return not os.path.lexists(path)

            credentials_ok &= cleanup_attempt(controller, remove_directory) is True
    if controller.private_acquired:

        def remove_outer():
            observed = os.stat(controller.private, follow_symlinks=False)
            require(
                (observed.st_dev, observed.st_ino, observed.st_uid, stat.S_IMODE(observed.st_mode))
                == (controller.private_identity[0], controller.private_identity[1], os.geteuid(), 0o700),
                "cleanup-private-outer-identity",
            )
            require(not any(controller.private.iterdir()), "cleanup-private-outer-notempty")
            controller.private.rmdir()
            return not os.path.lexists(controller.private)

        credentials_ok &= cleanup_attempt(controller, remove_outer) is True
    return {"credentials": credentials_ok, "captures": capture_ok}


def cleanup_native_resources(controller):
    known = {
        purpose: record
        for purpose, record in controller.resources.items()
        if record["cid"] is not None and record["baseline"] is not None and not record["removed"]
    }
    unknown = any(record["cid"] is None or record["baseline"] is None for record in controller.resources.values())
    if unknown:
        controller.cleanup_failed = True
    admitted = {
        purpose for purpose, record in known.items() if record["terminal"] and not record.get("custody_failed", False)
    }
    if known and any(not record["terminal"] for record in known.values()):

        def failure_readback():
            rows = inspect_many(controller, "failure_native_state", [record["cid"] for record in known.values()])
            active = []
            admitted.clear()
            for purpose, record in known.items():

                def admit_record(record=record):
                    require(not record.get("custody_failed", False), "cleanup-native-prior-guard-failed")
                    row = rows[record["cid"]]
                    require(
                        {key: row.get(key) for key in record["baseline"]} == record["baseline"],
                        "cleanup-native-config-drift",
                    )
                    native_network_association(controller, record, row, require_association=record["started"])
                    state = row.get("State")
                    require(type(state) is dict and type(state.get("Running")) is bool, "cleanup-native-state")
                    if state["Running"]:
                        active.append(record["cid"])
                    return True

                if cleanup_attempt(controller, admit_record) is True:
                    admitted.add(purpose)
                else:
                    record["custody_failed"] = True
            require(len(active) <= 1, "cleanup-active-native-cardinality")
            if active:
                controller.run("failure_active_native_kill", ["docker", "kill", "--signal", "KILL", active[0]])
            return True

        # A failed batch read admits no object; per-object failures preserve other genuine admissions.
        admitted.clear()
        cleanup_attempt(controller, failure_readback)
    controller.native_cleanup_unverified = unknown or set(known) != admitted
    if controller.native_cleanup_unverified:
        controller.cleanup_failed = True
    if admitted:

        def remove_native():
            controller.run(
                "remove_native_remaining",
                ["docker", "rm", "--force", *sorted(known[purpose]["cid"] for purpose in admitted)],
            )
            for purpose in admitted:
                known[purpose]["removed"] = True
            return True

        cleanup_attempt(controller, remove_native)


def cleanup_project_resources(controller):
    controller.project_cleanup_unverified = True
    controller.cleanup_image_snapshot = None
    if controller.initial_census is None or not hasattr(controller, "target_project"):
        controller.cleanup_failed = True
        return

    def failure_snapshot():
        require(
            "failure_residual_census" not in controller.used and "failure_residual_inspect" not in controller.used,
            "cleanup-fresh-fallback-unavailable",
        )
        census = project_census(controller, "failure_residual_census")
        snapshot = teardown_readback(controller, census, "failure_residual_inspect")
        if snapshot["first_error"] is not None:
            controller.cleanup_failed = True
            if controller.cleanup_first is None:
                controller.cleanup_first = snapshot["first_error"]
        return snapshot

    snapshot = controller.teardown_snapshot
    if snapshot is None:
        snapshot = cleanup_attempt(controller, failure_snapshot)
    if snapshot is None:
        return
    controller.cleanup_image_snapshot = snapshot

    def permitted(snapshot):
        return {
            scope
            for scope in snapshot["admitted_scopes"]
            if not (scope == controller.target_project and getattr(controller, "native_cleanup_unverified", True))
        }

    admitted_scopes = permitted(snapshot)
    controller.project_cleanup_unverified = controller.target_project not in admitted_scopes
    for scope, label, started, environment in (
        (controller.target_project, "target_stack_down", controller.target_started, "target"),
        (controller.prepr_project, "prepr_stack_down", controller.prepr_started, "prepr"),
    ):
        if scope in admitted_scopes and started and label not in controller.used:
            cleanup_attempt(
                controller,
                lambda label=label, environment=environment: controller.run(
                    label, ["./test.sh", "stack", "down"], extra=source_environment(controller, environment)
                ),
            )
    failed_scopes = {}
    for index, command in enumerate(controller.commands, 1):
        if command["label"] in {"target_stack_down", "prepr_stack_down"} and (
            command["exit"] != 0 or not command["complete"]
        ):
            scope = controller.target_project if command["label"] == "target_stack_down" else controller.prepr_project
            failed_scopes[scope] = index
    if not failed_scopes:
        return
    # Any actual partial source teardown invalidates the prior residual snapshot.
    if any(index > snapshot["acquired_at"] for index in failed_scopes.values()):
        controller.project_cleanup_unverified = True
        controller.cleanup_image_snapshot = None
        snapshot = cleanup_attempt(controller, failure_snapshot)
        if snapshot is None:
            return
        controller.cleanup_image_snapshot = snapshot
    current_permitted = permitted(snapshot)
    controller.project_cleanup_unverified = controller.target_project not in current_permitted
    admitted_scopes = current_permitted & set(failed_scopes)
    native_ids = {record["cid"] for record in controller.resources.values() if record["cid"] is not None}
    for kind, label, argv in (
        ("containers", "failure_project_container_remove", ["docker", "rm", "--force"]),
        ("volumes", "failure_project_volume_remove", ["docker", "volume", "rm"]),
        ("networks", "failure_project_network_remove", ["docker", "network", "rm"]),
    ):
        identifiers = sorted({identity for scope in admitted_scopes for identity in snapshot["objects"][scope][kind]})
        if kind == "containers":
            identifiers = [identity for identity in identifiers if identity not in native_ids]
        elif kind == "volumes":
            identifiers = [identity for identity in identifiers if identity not in controller.volume_resources]
        if identifiers:
            cleanup_attempt(
                controller,
                lambda label=label, argv=argv, identifiers=identifiers: controller.run(label, [*argv, *identifiers]),
            )


def cleanup_phase(controller):
    controller.cleanup_first = None
    controller.begin("cleanup")
    cleanup_attempt(controller, lambda: settle_parent_processes(controller))
    if not hasattr(controller, "docker_versions"):
        # No Docker acquisition is authorized before the measured local profile; unavailable inventory stays unknown.
        controller.image_absence = {}
        controller.inspection_exit = None
        controller.private_absence = dispose_private(controller)
        controller.exchange_absence = exchange_disposal(controller)
        return
    cleanup_attempt(controller, lambda: cleanup_native_resources(controller))
    cleanup_attempt(controller, lambda: cleanup_project_resources(controller))
    names = sorted(name for name, record in controller.volume_resources.items() if record["admitted"])
    if getattr(controller, "native_cleanup_unverified", True) or getattr(
        controller, "project_cleanup_unverified", True
    ):
        names = []  # Dependent Cargo volumes cannot bypass an unknown native custody guard.
    if names:
        cleanup_attempt(controller, lambda: controller.run("cargo_volumes_remove", ["docker", "volume", "rm", *names]))
    if any(not record["admitted"] for record in controller.volume_resources.values()):
        controller.cleanup_failed = True
    snapshot = getattr(controller, "cleanup_image_snapshot", None)
    image_ids = sorted(
        identity
        for identity in controller.qualified_images
        if snapshot is not None
        and snapshot["dispositions"].get(identity) is True
        and identity not in controller.initial_image_ids
    )
    if set(image_ids) != set(controller.qualified_images):
        controller.cleanup_failed = True  # Missing, contradicted or invalidated current custody stays retained/RED.
    if image_ids:
        cleanup_attempt(
            controller, lambda: controller.run("owned_images_remove", ["docker", "image", "rm", *image_ids])
        )

    def final_images():
        raw, _, _ = controller.run("images_final", ["docker", "image", "ls", "--no-trunc", "--format", "{{json .}}"])
        remaining = {row["ID"] for row in initial_image_inventory(raw)}
        controller.image_absence = {identity: identity not in remaining for identity in image_ids}
        require(all(controller.image_absence.values()), "cleanup-images-notempty")
        return True

    controller.image_absence = {identity: None for identity in image_ids}
    cleanup_attempt(controller, final_images)
    if controller.prepr_started and (not controller.prepr_complete or getattr(controller, "prepr_image_pending", True)):
        controller.cleanup_failed = True  # Partial acquisition never becomes owned merely because an alias disappeared.
    if getattr(controller, "toolchain_pending", None) is not None or getattr(controller, "api_build_pending", False):
        controller.cleanup_failed = True

    def final_census():
        census = project_census(controller, "census_final")
        selected = project_objects(census, controller.target_project)
        previous = project_objects(census, controller.prepr_project)
        controller.project_final = {kind: sorted(set(selected[kind]) | set(previous[kind])) for kind in selected}
        all_containers = {row.get("ID") for row in census["containers"]}
        all_volumes = {row.get("Name") for row in census["volumes"]}
        require(
            not any(controller.project_final.values())
            and not any(
                record["cid"] in all_containers for record in controller.resources.values() if record["cid"] is not None
            )
            and not set(controller.volume_resources) & all_volumes
            and not set(controller.runner_anonymous_volumes) & all_volumes,
            "cleanup-independent-notempty",
        )
        for record in controller.resources.values():
            if record["cid"] is not None and record["baseline"] is not None:
                record["removed"] = True
        for record in controller.volume_resources.values():
            if record["admitted"]:
                record["removed"] = True
        controller.inspection_exit = 0
        return True

    controller.inspection_exit = None
    cleanup_attempt(controller, final_census)
    # Cleanup commands also own process/capture resources; settle those before removing the private captures.
    cleanup_attempt(controller, lambda: settle_parent_processes(controller))
    controller.private_absence = cleanup_attempt(controller, lambda: dispose_private(controller))
    controller.exchange_absence = cleanup_attempt(controller, lambda: exchange_disposal(controller))


class CapturedSignal(BaseException):
    def __init__(self, number):
        self.number = number


def primary_exit(error):
    if error is None:
        return 0
    if isinstance(error, CapturedSignal):
        return 128 + error.number
    if isinstance(error, KeyboardInterrupt):
        return 130
    return 1


PRIMARY_DIAGNOSTIC_LABELS = frozenset(
    {
        "read-bound",
        "duplicate-json",
        "nonfinite-json",
        "compose-selected-source",
        "compose-selected-environment",
        "dsn-original-profile",
        "dsn-original-supported-options",
        "dsn-principal-profile",
        "compose-api-source-recipe",
    }
)


def primary_diagnostic_projection(first):
    if first is None:
        return None
    kind, label = "unknown", None
    if type(first) is Failure and type(first.label) is str and first.label in PRIMARY_DIAGNOSTIC_LABELS:
        kind, label = "guard", first.label
    elif type(first) in {CapturedSignal, KeyboardInterrupt, SystemExit}:
        kind = "control"
    return {"schema": "bifrost.test.workflow-result-primary-failure/v1", "kind": kind, "label": label}


def emit_primary_diagnostic(first, writer, check_deadline):
    # Diagnostic availability cannot replace the already selected original error or exit.
    try:
        value = primary_diagnostic_projection(first)
        if value is None:
            return first
        check_deadline()
        raw = encode(value) + b"\n"
        require(len(raw) <= 512 and raw.isascii(), "primary-diagnostic-bound")
        text = raw.decode("ascii")
        check_deadline()
        require(writer.write(text) == len(text), "primary-diagnostic-short-write")
        check_deadline()
        writer.flush()
        check_deadline()
    except BaseException:
        pass
    return first


def primary_diagnostic_controls():
    class FailureSubclass(Failure):
        pass

    class SignalSubclass(CapturedSignal):
        pass

    class KeyboardSubclass(KeyboardInterrupt):
        pass

    class SystemExitSubclass(SystemExit):
        pass

    class Writer:
        def __init__(self, mode=None):
            self.mode = mode
            self.lines = []
            self.flushes = 0

        def write(self, value):
            self.lines.append(value)
            if self.mode == "write":
                raise KeyboardInterrupt()
            return len(value) - (self.mode == "short")

        def flush(self):
            self.flushes += 1
            if self.mode == "flush":
                raise SystemExit()

    def no_deadline():
        return None

    for label in sorted(PRIMARY_DIAGNOSTIC_LABELS):
        original = Failure(label)
        projected = primary_diagnostic_projection(original)
        require(projected["kind"] == "guard" and projected["label"] == label, "primary-diagnostic-control")
        writer = Writer()
        require(emit_primary_diagnostic(original, writer, no_deadline) is original, "primary-diagnostic-control")
        require(
            len(writer.lines) == 1
            and len(writer.lines[0].encode("ascii")) <= 512
            and writer.lines[0].endswith("\n")
            and writer.flushes == 1,
            "primary-diagnostic-control",
        )
    for original in (
        Failure("unknown"),
        Failure("secret-like-untrusted-value"),
        Failure(7),
        FailureSubclass("read-bound"),
        ValueError(),
        SignalSubclass(15),
        KeyboardSubclass(),
        SystemExitSubclass(),
    ):
        projected = primary_diagnostic_projection(original)
        require(projected["kind"] == "unknown" and projected["label"] is None, "primary-diagnostic-control")
    for original in (CapturedSignal(15), KeyboardInterrupt(), SystemExit()):
        projected = primary_diagnostic_projection(original)
        require(projected["kind"] == "control" and projected["label"] is None, "primary-diagnostic-control")
    for original in (Failure("read-bound"), CapturedSignal(15), KeyboardInterrupt(), SystemExit()):
        selected_exit = primary_exit(original)
        for mode in ("write", "short", "flush"):
            writer = Writer(mode)
            require(emit_primary_diagnostic(original, writer, no_deadline) is original, "primary-diagnostic-control")
            require(primary_exit(original) == selected_exit and len(writer.lines) == 1, "primary-diagnostic-control")

        def expired():
            raise Failure("deadline")

        writer = Writer()
        require(emit_primary_diagnostic(original, writer, expired) is original, "primary-diagnostic-control")
        require(not writer.lines and not writer.flushes, "primary-diagnostic-control")
    writer = Writer()
    require(primary_diagnostic_projection(None) is None, "primary-diagnostic-control")
    require(emit_primary_diagnostic(None, writer, no_deadline) is None, "primary-diagnostic-control")
    require(not writer.lines and not writer.flushes, "primary-diagnostic-control")


def disposal_record(controller):
    processes = {
        "started": sum(session["process"] is not None for session in controller.sessions),
        "settled": sum(session["process"] is not None and session["settled"] for session in controller.sessions),
    }
    project = {
        "name": getattr(controller, "target_project", None),
        "containers": None,
        "volumes": None,
        "networks": None,
    }
    if controller.project_final is not None:
        project.update(controller.project_final)
    private = getattr(controller, "private_absence", None) or {"credentials": None, "captures": None}
    exchange = getattr(controller, "exchange_absence", None) or {
        family: {key: None for key in ("request", "admit", "failed", "temporary")}
        for family in ("endpoint", "after_actor")
    }
    images = getattr(controller, "image_absence", {})
    complete = (
        not controller.cleanup_failed
        and processes["started"] == processes["settled"]
        and all(project[kind] == [] for kind in ("containers", "volumes", "networks"))
        and all(value is True for value in images.values())
        and all(value is True for value in private.values())
        and all(value is True for observation in exchange.values() for value in observation.values())
        and getattr(controller, "inspection_exit", None) == 0
        and all(record["removed"] for record in controller.resources.values())
        and all(record["removed"] is True for record in controller.volume_resources.values())
    )
    return {
        "schema": "bifrost.test.workflow-result-disposal/v2",
        "candidate": controller.candidate,
        "invocation_uuid": OWNER,
        "primary_exit": primary_exit(controller.first),
        "cleanup_exit": 0 if complete else 1,
        "processes": processes,
        "project": project,
        "images": images,
        "private": private,
        "exchange": exchange,
        "inspection_exit": getattr(controller, "inspection_exit", None),
        "complete": complete,
    }


def producer_record(controller):
    final = controller.observer_final
    frontend_matched = None
    if controller.frontend_before is not None and controller.frontend_after is not None:
        frontend_matched = controller.after_admitted and controller.endpoint_admitted and not controller.frontend_failed
    schema_matched = None
    if controller.before_schema is not None and controller.after_schema is not None:
        schema_matched = controller.stages["native"]["admitted"]
    feature_complete = (
        all(controller.invocations.get(index, {}).get("complete") is True for index in range(8))
        and controller.fixture_feature is not None
        and all(
            controller.fixture_feature[key] is True
            for key in ("python_completed", "native_completed", "result", "variables", "context")
        )
    )
    admission = {
        "source": True if controller.stages["source"]["admitted"] else None,
        "binary": True if controller.stages["binary"]["admitted"] else None,
        "mounts": True if controller.endpoint_admitted else None,
        "loaded_originals": None if final is None else final["loaded_bindings_verified"],
        "constructor": None if final is None else final["dsn_binding"]["complete"],
        "frontend": frontend_matched,
        "graphs": True if set(controller.graphs) == {"default", "selected", "all_features"} else None,
        "feature_venues": feature_complete if controller.stages["native"]["started"] else None,
        "junit": True if controller.target_complete else None,
        "observer_final": None if final is None else final["complete"],
        "schema": {
            "source_heads": getattr(controller, "heads", None),
            "before": controller.before_schema,
            "after": controller.after_schema,
            "matched": schema_matched,
        },
        "frontend_evidence": {
            "before": controller.frontend_before,
            "after": controller.frontend_after,
            "matched": frontend_matched,
        },
    }
    images = []
    for key, record in controller.images.items():
        projected = dict(record)
        if re.fullmatch(r"prepr_build_[0-9]+", key) is not None:
            projected["purpose"] = "prepr_build"
        elif key == "api_dev":
            projected["purpose"] = "api"
        images.append(projected)
    require(
        len(images) <= 16
        and all(
            record["purpose"] in {"toolchain", "api", "test_runner", "frontend", "prepr_build"} for record in images
        )
        and len({(record["purpose"], record["image_id"]) for record in images}) == len(images),
        "producer-image-purpose",
    )
    return {
        "schema": "bifrost.test.workflow-result-producer/v3",
        "candidate": controller.candidate,
        "main": controller.main,
        "invocation_uuid": OWNER,
        "source_map": controller.source_map,
        "binary": controller.binary,
        "graphs": controller.graphs,
        "images": images,
        "commands": controller.commands,
        "stages": controller.stages,
        "limits": {
            "whole": 2700,
            "stages": 2460,
            "reserve": 240,
            **STAGE_CAPS,
            "commands": 128,
            "stream_bytes": STREAM_LIMIT,
            "capture_bytes": AGGREGATE_LIMIT,
            "frontend_children": 20,
            "frontend_child_seconds": 2,
            "frontend_seconds": 40,
        },
        "admission": admission,
    }


def publish_evidence(controller):
    require(controller.evidence_acquired, "publication-evidence-unavailable")
    first = None
    values = {
        "receipt.json": getattr(
            controller,
            "receipt",
            {
                "schema": "bifrost.test.workflow-result-source/v2",
                "candidate": controller.candidate,
                "sources": {},
                "binary": None,
                "graphs": {},
            },
        ),
        "junit.json": controller.junit
        or {
            "schema": "bifrost.test.workflow-result-junit/v1",
            "candidate": controller.candidate,
            "invocation_uuid": OWNER,
            "counts": None,
            "cases": [],
            "observer_final": None,
        },
    }
    # Each safe artifact is attempted independently; an unavailable projection remains missing and the producer stays nonzero.
    for name, factory in (
        ("features.json", lambda: feature_export(controller)),
        ("junit.json", lambda: values["junit.json"]),
        ("receipt.json", lambda: values["receipt.json"]),
        ("disposal.json", lambda: disposal_record(controller)),
        ("producer.json", lambda: producer_record(controller)),
    ):
        try:
            controller.write_artifact(name, factory())
            deadline(WHOLE_END)
        except BaseException as error:
            if first is None:
                first = error
    if first is not None:
        raise first
    print(
        encode(
            {
                "schema": "bifrost.test.workflow-result-milestone/v1",
                "phase": "publication_complete",
                "elapsed_seconds": time.monotonic() - START,
            }
        ).decode("ascii"),
        flush=True,
    )
    deadline(WHOLE_END)


def main():
    controller = Controller.__new__(Controller)
    first = None
    previous = {}

    def interrupted(number, _frame):
        raise CapturedSignal(number)

    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, interrupted)
        controller.__init__()
        for operation in (
            prepr_phase,
            setup_checks,
            release_build,
            stack_phase,
            manifest_phase,
            target_phase,
            native_phase,
        ):
            operation(controller)
        raw, _, _ = controller.run("git_final_clean", FINAL_GIT)
        require(
            raw
            == b"bifrost-git-final/v1\nstatus-begin\n\nstatus-exit 0\nidentity-begin\n"
            + controller.candidate["head"].encode()
            + b"\n"
            + controller.candidate["tree"].encode()
            + b"\n\nidentity-exit 0\nend\n",
            "final-source-drift",
        )
    except BaseException as error:
        first = error
    finally:
        if hasattr(controller, "first"):
            controller.first = first
            try:
                cleanup_phase(controller)
            except BaseException as error:
                controller.cleanup_failed = True
                if first is None:
                    first = error
            try:
                if first is None and not disposal_record(controller)["complete"]:
                    first = Failure("cleanup-incomplete")
            except BaseException as error:
                controller.cleanup_failed = True
                if first is None:
                    first = error
            controller.first = first
            try:
                publish_evidence(controller)
            except BaseException as error:
                if first is None:
                    first = error
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except BaseException as error:
                if first is None:
                    first = error
    emit_primary_diagnostic(first, sys.stdout, lambda: deadline(WHOLE_END))
    return primary_exit(first)


if __name__ == "__main__":
    raise SystemExit(main())
PY
