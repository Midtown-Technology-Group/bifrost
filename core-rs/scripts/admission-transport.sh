#!/usr/bin/env bash
# Isolated synthetic transport venue; never starts the application or database.
set -euo pipefail
test "$#" -eq 0
cd "${BASH_SOURCE[0]%/*}/../.."
exec python3 -I -B - <<'PY'
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

START = time.monotonic()
WORK_END, CLEANUP_END, PUBLICATION_END = START + 1080, START + 1140, START + 1200
STREAM_CAP, TOTAL_CAP = 16777216, 33554432
PHASES = ("source", "rust_build", "python_build", "compile", "artifacts", "execute", "cleanup", "publication")
LABELS = (
    "source.head",
    "source.clean",
    "source.tree",
    "source.blobs",
    "containers.initial",
    "daemon.profile",
    "rust.build",
    "rust.image",
    "python.build",
    "python.image",
    "rust.create",
    "rust.created",
    "rust.start",
    "rust.terminal",
    "example.records",
    "tests.records",
    "example.binary",
    "tests.binary",
    "python.create",
    "python.created",
    "python.start",
    "python.terminal",
    "containers.cleanup",
    "python.cleanup_before",
    "python.kill",
    "python.wait",
    "python.cleanup_terminal",
    "python.remove",
    "rust.cleanup_before",
    "rust.kill",
    "rust.wait",
    "rust.cleanup_terminal",
    "rust.remove",
    "containers.final",
)
ROOT = Path.cwd()
SUMMARY = ROOT / "m1-transport-venue.json"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
SOFTWARE_PATHS = ("/opt/bifrost-m1/example", "/opt/bifrost-m1/native-tests", "/opt/bifrost-m1/diagnostic.py")
COMPILE_SCRIPT = (
    "cargo build --locked --offline -p bifrost-core --example admission_transport_prototype "
    "--features admission-transport-prototype --message-format=json > /tmp/m1-example.json\n"
    "cargo test --locked --offline -p bifrost-core --example admission_transport_prototype "
    "--features admission-transport-prototype --no-run --message-format=json > /tmp/m1-tests.json"
)
# One PID1, fixed software paths only. No application or credential import.
READBACK = r"""
import hashlib,json,os,stat,sys
paths=('/opt/bifrost-m1/example','/opt/bifrost-m1/native-tests','/opt/bifrost-m1/diagnostic.py')
values={}; total=0; first=None
try:
 for name,path in zip(('example','native_tests','diagnostic'),paths):
  fd=None
  try:
   fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
   before=os.fstat(fd); actual=os.lstat(path)
   identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
   if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=67108864 or identity(before)!=identity(actual): raise ValueError()
   digest=hashlib.sha256(); count=0
   while True:
    chunk=os.read(fd,65536)
    if not chunk: break
    count+=len(chunk); total+=len(chunk)
    if count>before.st_size or total>67108864: raise ValueError()
    digest.update(chunk)
   if count!=before.st_size or identity(before)!=identity(os.fstat(fd)) or identity(before)!=identity(os.lstat(path)): raise ValueError()
   values[name]=digest.hexdigest()
  except BaseException as error:
   if first is None: first=error
  finally:
   if fd is not None:
    try: os.close(fd)
    except BaseException as error:
     if first is None: first=error
  if first is not None: break
 if first is not None: raise first
 raw=json.dumps({'schema':'bifrost.test.m1-transport-software/v1','software':values},separators=(',',':'),ensure_ascii=True).encode('ascii')+b'\n'
 if len(raw)>512: raise ValueError()
 sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
 os.execv('/usr/local/bin/python',('/usr/local/bin/python','-I','-B',paths[2],'--binary',paths[0],'--native-tests',paths[1]))
except BaseException:
 sys.exit(1)
"""


def require(value, label):
    if not value:
        raise ValueError(label)


def preserve(first, error):
    return first if first is not None else error


def unique_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("nonfinite_json")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class Venue:
    def __init__(self):
        # Retain partially acquired resources before any subsequent validation.
        self.stage = None
        self.stage_identity = None
        self.files = {}
        self.processes = []
        self.total = 0
        self.cli_unknown = False
        self.phase = "source"
        self.candidate = None
        self.source_checks = None
        self.suite = None
        self.images = {}
        self.containers = {}
        self.names = {}
        self.create_attempted = set()
        self.absent = None
        self.staging_disposal = None
        self.fixture_disposal = None
        self.labels = []
        self.env = None
        self.first = None
        self.failure_phase = None

    def acquire(self):
        require(not SUMMARY.exists() and not SUMMARY.is_symlink(), "summary_preexists")
        self.stage = Path(tempfile.mkdtemp(prefix="bifrost-m1-"))
        self.stage_identity = identity(self.stage.lstat())
        require(
            stat.S_ISDIR(self.stage.lstat().st_mode)
            and stat.S_IMODE(self.stage.lstat().st_mode) == 0o700
            and self.stage.lstat().st_uid == os.getuid(),
            "staging_owner",
        )
        config = self.stage / "docker-config"
        config.mkdir(mode=0o700)
        self.files[config] = identity(config.lstat())
        require(
            all(
                not os.environ.get(k)
                for k in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
            ),
            "daemon_override",
        )
        self.env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "DOCKER_CONFIG": str(config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }

    def run(self, label, argv, seconds=5, end=None, stream_cap=STREAM_CAP, combined_cap=None, stdin=None):
        end = min(WORK_END if end is None else end, time.monotonic() + seconds)
        require(
            label in LABELS and label not in self.labels and len(self.labels) < len(LABELS) and time.monotonic() < end,
            "operation_deadline_or_duplicate",
        )
        self.labels.append(label)
        process = None
        selector = None
        first = None
        result = [bytearray(), bytearray()]
        group_absent = False
        streams_complete = False
        close_attempted = set()
        popen_attempted = False
        try:
            # Only source batch input uses stdin, bounded before launching.
            require(stdin is None or len(stdin) <= 1048576, "stdin_bound")
            popen_attempted = True
            process = subprocess.Popen(
                argv,
                cwd=ROOT,
                env=self.env,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            self.processes.append(process)
            selector = selectors.DefaultSelector()
            for index, handle in enumerate((process.stdout, process.stderr)):
                os.set_blocking(handle.fileno(), False)
                selector.register(handle, selectors.EVENT_READ, index)
            if stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, 2)
            offset = 0
            while selector.get_map():
                require(time.monotonic() < end, "operation_deadline")
                for key, _ in selector.select(min(0.05, max(0, end - time.monotonic()))):
                    handle, index = key.fileobj, key.data
                    if index == 2:
                        written = os.write(handle.fileno(), stdin[offset : offset + 65536])
                        require(0 < written <= len(stdin) - offset, "stdin_write")
                        offset += written
                        if offset == len(stdin):
                            selector.unregister(handle)
                            close_attempted.add(id(handle))
                            try:
                                handle.close()
                            except BaseException:
                                self.cli_unknown = True
                                raise
                        continue
                    chunk = os.read(handle.fileno(), 65536)
                    if not chunk:
                        selector.unregister(handle)
                        continue
                    self.total += len(chunk)
                    require(self.total <= TOTAL_CAP and len(result[index]) + len(chunk) <= stream_cap, "capture_bound")
                    if combined_cap is not None:
                        require(sum(map(len, result)) + len(chunk) <= combined_cap, "capture_combined")
                    result[index].extend(chunk)
            streams_complete = True
            require(process.wait(timeout=max(0, end - time.monotonic())) == 0, "native_nonzero")
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                group_absent = True
            require(group_absent, "residual_native_group")
        except BaseException as error:
            first = error
        finally:
            if popen_attempted and process is None:
                self.cli_unknown = True
            if process is not None and not streams_complete:
                self.cli_unknown = True
            if process is not None and not group_absent:
                settle_end = min(time.monotonic() + 5, CLEANUP_END)
                try:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        group_absent = True
                    if not group_absent:
                        require(time.monotonic() < settle_end, "native_cleanup_deadline")
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=max(0, settle_end - time.monotonic()))
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        group_absent = True
                    require(group_absent, "native_group_unknown")
                except BaseException as error:
                    self.cli_unknown = True
                    first = preserve(first, error)
            # Independent FD closure, without retrying an uncertain numeric descriptor.
            if process is not None:
                for handle in (process.stdin, process.stdout, process.stderr):
                    if handle is not None and id(handle) not in close_attempted and not handle.closed:
                        close_attempted.add(id(handle))
                        try:
                            handle.close()
                        except BaseException as error:
                            self.cli_unknown = True
                            first = preserve(first, error)
            if selector is not None:
                try:
                    selector.close()
                except BaseException as error:
                    self.cli_unknown = True
                    first = preserve(first, error)
            if process is not None and (process.returncode is None or not group_absent):
                self.cli_unknown = True
        if first is not None:
            raise first
        return tuple(bytes(value) for value in result)

    def data(self, label, argv, **kwargs):
        stdout, stderr = self.run(label, argv, **kwargs)
        require(not stderr, "unexpected_metadata_stderr")
        return stdout

    def census(self, label, end=None):
        raw = self.data(label, ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"], end=end)
        rows = []
        for line in raw.splitlines():
            row = unique_json(line)
            require(
                type(row) is dict
                and type(row.get("ID")) is str
                and HEX64.fullmatch(row["ID"])
                and type(row.get("Names")) is str,
                "census_shape",
            )
            rows.append(row)
        require(len(rows) <= 1024 and len({x["ID"] for x in rows}) == len(rows), "census_bound_unique")
        return rows

    def image(self, purpose, tag):
        rows = unique_json(self.data(purpose + ".image", ["docker", "image", "inspect", tag]))
        require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "image_shape")
        value = rows[0]
        require(
            IMAGE_ID.fullmatch(value.get("Id", ""))
            and value.get("Os") == "linux"
            and value.get("Architecture") == "amd64",
            "image_identity_platform",
        )
        require(type(value.get("Config")) is dict and type(value["Config"].get("Env")) is list, "image_config")
        require(value["Config"].get("Volumes") in (None, {}), "image_implicit_volumes")
        self.images[purpose] = value
        return value["Id"]

    def inspect(self, label, cid, end=None):
        rows = unique_json(self.data(label, ["docker", "container", "inspect", cid], end=end))
        require(type(rows) is list and len(rows) == 1 and rows[0].get("Id") == cid, "container_identity")
        return rows[0]

    def qualify(self, purpose, value, state):
        expected = self.containers[purpose]
        config, host = value.get("Config"), value.get("HostConfig")
        require(value.get("Id") == expected["cid"] and value.get("Image") == expected["image"], "container_image")
        require(
            type(config) is dict
            and type(host) is dict
            and config.get("Labels") == expected["labels"]
            and config.get("Entrypoint") == expected["entrypoint"]
            and config.get("Cmd") == expected["cmd"]
            and config.get("User") == expected["user"]
            and config.get("WorkingDir") == expected["cwd"]
            and config.get("Env") == self.images[purpose]["Config"]["Env"],
            "container_config",
        )
        require(
            host.get("NetworkMode") == "none"
            and host.get("Privileged") is False
            and host.get("ReadonlyRootfs") is expected["readonly"]
            and host.get("CapAdd") in (None, [])
            and host.get("CapDrop") == ["ALL"]
            and host.get("SecurityOpt") == ["no-new-privileges"]
            and host.get("Devices") in (None, [])
            and host.get("PortBindings") in (None, {}),
            "container_host",
        )
        mounts = value.get("Mounts")
        require(type(mounts) is list, "container_mounts")
        binds = [m for m in mounts if m.get("Type") == "bind"]
        require(
            len(binds) == len(expected["binds"]) and len({m.get("Destination") for m in binds}) == len(binds),
            "mount_membership",
        )
        for destination, source in expected["binds"].items():
            matched = [m for m in binds if m.get("Destination") == destination]
            require(
                len(matched) == 1
                and matched[0].get("Source") == str(source)
                and matched[0].get("RW") is False
                and matched[0].get("Propagation") == "rprivate",
                "mount_join",
            )
        require(
            all(
                m.get("Type") == "bind"
                or (
                    purpose == "python"
                    and m.get("Type") == "tmpfs"
                    and m.get("Destination") == "/tmp"
                    and m.get("RW") is True
                )
                for m in mounts
            ),
            "unexpected_mount",
        )
        require(host.get("Tmpfs") == expected["tmpfs"], "tmpfs_config")
        actual = value.get("State")
        require(
            type(actual) is dict
            and actual.get("Status") == state
            and actual.get("Running") is (state == "running")
            and actual.get("Paused") is False
            and actual.get("Restarting") is False
            and actual.get("Dead") is False,
            "container_state",
        )
        if state == "exited":
            require(type(actual.get("ExitCode")) is int, "container_exit_type")
        return actual

    def create(self, purpose, image, cmd, *, binds=None):
        binds = {} if binds is None else binds
        labels = dict(self.images[purpose]["Config"].get("Labels") or {})
        labels.update({"bifrost.m1.task": self.task, "bifrost.m1.purpose": purpose})
        expected = {
            "cid": None,
            "image": image,
            "labels": labels,
            "entrypoint": ["/bin/sh"] if purpose == "rust" else ["/usr/local/bin/python"],
            "cmd": cmd,
            "user": "" if purpose == "rust" else "65532:65532",
            "cwd": "/workspace/core-rs" if purpose == "rust" else "/app",
            "readonly": purpose != "rust",
            "binds": binds,
            "tmpfs": None if purpose == "rust" else {"/tmp": "rw,nosuid,nodev,noexec,size=16777216,mode=1777"},
        }
        self.containers[purpose] = expected
        argv = [
            "docker",
            "create",
            "--name",
            self.names[purpose],
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            expected["entrypoint"][0],
        ]
        for key, val in labels.items():
            argv += ["--label", key + "=" + val]
        if purpose != "rust":
            argv += ["--read-only", "--user", expected["user"], "--tmpfs", "/tmp:" + expected["tmpfs"]["/tmp"]]
            for destination, source in binds.items():
                argv += ["--mount", "type=bind,src=" + str(source) + ",dst=" + destination + ",readonly"]
        argv += [image, *cmd]
        self.create_attempted.add(purpose)
        raw = self.data(purpose + ".create", argv)
        require(re.fullmatch(rb"[0-9a-f]{64}\n", raw), "created_cid_frame")
        raw = raw[:-1]
        # Immediately retain the returned identity before any later qualification.
        expected["cid"] = raw.decode("ascii", errors="strict")
        require(HEX64.fullmatch(expected["cid"]), "created_cid")
        observed = self.inspect(purpose + ".created", expected["cid"])
        self.qualify(purpose, observed, "created")
        return expected["cid"]

    def source(self):
        raw = self.data("source.head", ["git", "rev-parse", "HEAD", "HEAD^{tree}"])
        lines = raw.decode("ascii", errors="strict").splitlines()
        require(len(lines) == 2 and all(HEX40.fullmatch(x) for x in lines), "source_identity")
        self.candidate = lines[0]
        require(os.environ.get("GITHUB_SHA") == self.candidate, "candidate_identity")
        require(
            os.environ.get("GITHUB_REPOSITORY") == "Midtown-Technology-Group/bifrost"
            and os.environ.get("GITHUB_REF") == "refs/heads/test/m1-native-admission-transport"
            and os.environ.get("GITHUB_EVENT_NAME") == "push"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
            "supported_workflow_identity",
        )
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        require(re.fullmatch("[1-9][0-9]{0,19}", run_id), "run_identity")
        self.task = self.candidate + "-" + run_id + "-1"
        self.names = {p: "bifrost-m1-" + p + "-" + self.task for p in ("rust", "python")}
        require(
            not self.data("source.clean", ["git", "status", "--porcelain=v1", "--untracked-files=all"]),
            "candidate_dirty",
        )
        roster = self.data("source.tree", ["git", "ls-tree", "-rz", "--full-tree", "HEAD"])
        require(roster.endswith(b"\0"), "tree_framing")
        entries = {}
        for row in roster[:-1].split(b"\0"):
            meta, path = row.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split(" ")
            path = path.decode("utf-8", errors="strict")
            require(
                mode in ("100644", "100755", "120000")
                and kind == "blob"
                and HEX40.fullmatch(oid)
                and path not in entries
                and not path.startswith("/")
                and ".." not in PurePosixPath(path).parts
                and not any(c in path for c in "\0\r\n"),
                "tree_entry",
            )
            entries[path] = (mode, oid)
        explicit = {
            "api/Dockerfile.dev",
            "pyproject.toml",
            "requirements.lock",
            "requirements-pyright.lock",
            ".dockerignore",
            "api/tests/diagnostics/core_admission_transport.py",
            ".github/workflows/admission-transport-prototype.yml",
        }
        selected = sorted(path for path in entries if path.startswith("core-rs/") or path in explicit)
        require(explicit <= set(selected) and len(selected) <= 256, "source_membership")
        require(all(entries[path][0] in ("100644", "100755") for path in selected), "selected_regular")
        request = "".join(entries[path][1] + "\n" for path in selected).encode("ascii")
        objects = self.data("source.blobs", ["git", "cat-file", "--batch"], stdin=request)
        offset = 0
        self.source_pins = {}
        for path in selected:
            mode, oid = entries[path]
            newline = objects.find(b"\n", offset)
            require(newline >= offset, "blob_header")
            header = objects[offset:newline].decode("ascii").split(" ")
            require(
                len(header) == 3
                and header[0] == oid
                and header[1] == "blob"
                and re.fullmatch("0|[1-9][0-9]*", header[2]),
                "blob_identity",
            )
            count = int(header[2])
            require(count <= STREAM_CAP, "blob_bound")
            begin, finish = newline + 1, newline + 1 + count
            require(finish < len(objects) and objects[finish : finish + 1] == b"\n", "blob_framing")
            expected = objects[begin:finish]
            disk = ROOT / path
            for parent in disk.parents:
                require(stat.S_ISDIR(parent.lstat().st_mode), "source_ancestor")
                if parent == ROOT:
                    break
            fd = None
            first = None
            try:
                fd = os.open(disk, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                before = os.fstat(fd)
                require(
                    stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == count, "source_regular"
                )
                actual = bytearray()
                while len(actual) <= count:
                    chunk = os.read(fd, min(65536, count + 1 - len(actual)))
                    if not chunk:
                        break
                    actual.extend(chunk)
                require(
                    bytes(actual) == expected
                    and identity(before) == identity(os.fstat(fd))
                    and identity(before) == identity(disk.lstat()),
                    "source_bytes",
                )
            except BaseException as error:
                first = error
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except BaseException as error:
                        self.cli_unknown = True
                        first = preserve(first, error)
            if first is not None:
                raise first
            self.source_pins[path] = hashlib.sha256(expected).hexdigest()
            offset = finish + 1
        require(offset == len(objects), "extra_blob_output")
        context_files = {path for path in selected if path.startswith("core-rs/")}
        actual_context = set()
        for directory, directories, files in os.walk(ROOT / "core-rs", followlinks=False):
            require(time.monotonic() < WORK_END, "context_deadline")
            require(stat.S_ISDIR(Path(directory).lstat().st_mode), "context_directory")
            for name in directories:
                require(stat.S_ISDIR((Path(directory) / name).lstat().st_mode), "context_directory_symlink")
            for name in files:
                path = Path(directory) / name
                require(stat.S_ISREG(path.lstat().st_mode), "context_nonregular")
                actual_context.add(path.relative_to(ROOT).as_posix())
                require(len(actual_context) <= 256, "context_bound")
        require(actual_context == context_files, "context_extra_or_missing")
        self.diagnostic = ROOT / "api/tests/diagnostics/core_admission_transport.py"
        self.diagnostic_hash = self.source_pins["api/tests/diagnostics/core_admission_transport.py"]
        frozen = {
            "core-rs/Cargo.toml": "25b882aa69c5a4f83dd86091978f302cdd118edcae497b1a32d517d3165aa838",
            "core-rs/Cargo.lock": "9f6275d285d72fcc0a70573f2a0f3e0a4197920fb6060083440aff566a15ca52",
            "core-rs/crates/bifrost-core/Cargo.toml": "d6359485352834ed75ed3f2eb6f866dfa9eb020f807fbd5fbe12b4c8a0f2b020",
            "core-rs/crates/bifrost-core/examples/admission_transport_prototype.rs": "7723d6731695987e15b2286c2d371e5cab493e09696d7924a62ebe7135ec5f7f",
        }
        require(all(self.source_pins.get(path) == digest for path, digest in frozen.items()), "frozen_prototype")
        require(
            self.diagnostic_hash == "609a89986e45d0ddfcb98f371904fff2e12df3bdca5f41deaf1ca38c03f11822",
            "frozen_diagnostic",
        )
        initial = self.census("containers.initial")
        require(not any(row["Names"] in self.names.values() for row in initial), "container_preexists")
        daemon = unique_json(self.data("daemon.profile", ["docker", "info", "--format", "{{json .}}"]))
        require(
            type(daemon) is dict
            and daemon.get("OSType") == "linux"
            and daemon.get("Architecture") in ("x86_64", "amd64")
            and type(daemon.get("SecurityOptions")) is list
            and not any("rootless" in v or "userns" in v for v in daemon["SecurityOptions"]),
            "daemon_profile",
        )

    def rust_checks(self):
        self.phase = "rust_build"
        tag = "bifrost-m1-rust:" + self.task
        try:
            stdout, stderr = self.run(
                "rust.build",
                [
                    "docker",
                    "build",
                    "--progress=plain",
                    "--target",
                    "checks",
                    "-t",
                    tag,
                    "-f",
                    "core-rs/Dockerfile",
                    "core-rs",
                ],
                seconds=480,
            )
        except BaseException:
            # A build/transport failure cannot identify which check actually ran.
            self.source_checks = None
            raise
        lines = (stdout + b"\n" + stderr).decode("utf-8", errors="strict").splitlines()
        commands = (
            "cargo fmt --check",
            "cargo clippy --locked --all-targets --all-features -- -D warnings",
            "cargo test --locked --all",
        )
        selected_steps = []
        for command in commands:
            matches = [
                re.fullmatch(r"#([0-9]+) \[checks [0-9]+/[0-9]+\] RUN " + re.escape(command), line) for line in lines
            ]
            steps = [match[1] for match in matches if match is not None]
            require(len(steps) == 1, "check_step_identity")
            step = steps[0]
            selected_steps.append(step)
            require(not any(line == "#" + step + " CACHED" for line in lines), "cached_check")
            require(
                any(re.fullmatch("#" + step + r" DONE [0-9]+(?:\.[0-9]+)?s", line) for line in lines),
                "check_step_completion",
            )
        require(len(set(selected_steps)) == 3, "check_steps_unique")
        self.source_checks = True
        return self.image("rust", tag)

    def copy(self, label, cid, source, destination, limit, executable=False):
        require(not destination.exists() and not destination.is_symlink(), "copy_preexists")
        # Register an attempted destination; failed/partial copy cannot disappear from custody.
        self.files[destination] = None
        self.data(label, ["docker", "cp", cid + ":" + source, str(destination)])
        fd = None
        first = None
        digest = None
        try:
            fd = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            before = os.fstat(fd)
            self.files[destination] = identity(before)
            require(
                stat.S_ISREG(before.st_mode)
                and before.st_nlink == 1
                and before.st_uid == os.getuid()
                and 0 < before.st_size <= limit,
                "copied_regular",
            )
            count, hashed = 0, hashlib.sha256()
            while count <= before.st_size:
                require(time.monotonic() < WORK_END, "copy_read_deadline")
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                count += len(chunk)
                require(count <= before.st_size, "copy_size")
                hashed.update(chunk)
            require(
                count == before.st_size
                and identity(before) == identity(os.fstat(fd))
                and identity(before) == identity(destination.lstat()),
                "copy_stable",
            )
            digest = hashed.hexdigest()
        except BaseException as error:
            first = error
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    self.cli_unknown = True
                    first = preserve(first, error)
        if first is not None:
            raise first
        # Explicit initial software provisioning, not a fallback on unknown custody.
        os.chmod(destination, 0o755 if executable else 0o600, follow_symlinks=False)
        self.files[destination] = identity(destination.lstat())
        return digest

    def artifact(self, path, test):
        raw = self.read_owned(path, 4194304)
        require(0 < len(raw) <= 4194304 and raw.endswith(b"\n"), "artifact_capture")
        selected = []
        finished = []
        records = raw.splitlines()
        require(len(records) <= 4096, "artifact_record_bound")
        for line in records:
            require(len(line) <= 1048576, "artifact_line_bound")
            value = unique_json(line)
            require(type(value) is dict and type(value.get("reason")) is str, "cargo_record")
            require(
                value["reason"] in ("compiler-artifact", "compiler-message", "build-script-executed", "build-finished"),
                "cargo_reason",
            )
            if value["reason"] == "build-finished":
                require(type(value.get("success")) is bool and value["success"] is True, "cargo_finished")
                finished.append(value)
            if value["reason"] != "compiler-artifact":
                continue
            target, profile = value.get("target"), value.get("profile")
            require(type(target) is dict and type(profile) is dict, "cargo_artifact_shape")
            if target.get("name") != "admission_transport_prototype":
                continue
            require(
                target.get("kind") == ["example"]
                and target.get("crate_types") == ["bin"]
                and target.get("src_path")
                == "/workspace/core-rs/crates/bifrost-core/examples/admission_transport_prototype.rs"
                and value.get("package_id") == "path+file:///workspace/core-rs/crates/bifrost-core#0.1.0"
                and type(profile.get("test")) is bool
                and profile["test"] is test
                and value.get("manifest_path") == "/workspace/core-rs/crates/bifrost-core/Cargo.toml"
                and profile.get("opt_level") == "0"
                and value.get("features") == ["admission-transport-prototype"],
                "selected_artifact",
            )
            executable = value.get("executable")
            require(
                type(executable) is str
                and executable.startswith("/workspace/core-rs/target/debug/examples/")
                and re.fullmatch(r"/workspace/core-rs/target/debug/examples/[A-Za-z0-9_-]{1,160}", executable),
                "artifact_executable",
            )
            selected.append(executable)
        require(
            len(finished) == 1 and unique_json(records[-1]).get("reason") == "build-finished", "cargo_finished_last"
        )
        require(len(selected) == 1, "artifact_unique")
        return selected[0]

    def compile_and_execute(self, rust_image):
        self.phase = "python_build"
        tag = "bifrost-m1-python:" + self.task
        self.run(
            "python.build",
            ["docker", "build", "--progress=plain", "--target", "builder", "-t", tag, "-f", "api/Dockerfile.dev", "."],
            seconds=300,
        )
        python_image = self.image("python", tag)
        self.phase = "compile"
        compiler = self.create("rust", rust_image, ["-eu", "-c", COMPILE_SCRIPT])
        self.run("rust.start", ["docker", "start", "--attach", compiler], seconds=120)
        actual = self.qualify("rust", self.inspect("rust.terminal", compiler), "exited")
        require(actual["ExitCode"] == 0, "compile_exit")
        self.phase = "artifacts"
        example_json, tests_json = self.stage / "example.json", self.stage / "tests.json"
        self.copy("example.records", compiler, "/tmp/m1-example.json", example_json, 4194304)
        self.copy("tests.records", compiler, "/tmp/m1-tests.json", tests_json, 4194304)
        example_path, test_path = self.artifact(example_json, False), self.artifact(tests_json, True)
        example, tests = self.stage / "example", self.stage / "native-tests"
        hashes = {
            "example": self.copy("example.binary", compiler, example_path, example, 67108864, True),
            "native_tests": self.copy("tests.binary", compiler, test_path, tests, 67108864, True),
            "diagnostic": self.diagnostic_hash,
        }
        require(sum(p.stat().st_size for p in (example, tests, self.diagnostic)) <= 67108864, "software_total")
        self.phase = "execute"
        cmd = ["-I", "-B", "-c", READBACK]
        runtime = self.create(
            "python", python_image, cmd, binds=dict(zip(SOFTWARE_PATHS, (example, tests, self.diagnostic), strict=True))
        )
        stdout, stderr = self.run(
            "python.start",
            ["docker", "start", "--attach", runtime],
            seconds=180,
            stream_cap=131072,
            combined_cap=262144,
        )
        actual = self.qualify("python", self.inspect("python.terminal", runtime), "exited")
        require(actual["ExitCode"] == 0 and not stderr, "runtime_terminal")
        frame, separator, terminal = stdout.partition(b"\n")
        require(
            separator == b"\n" and len(frame) + 1 <= 512 and terminal == b"m1_transport_complete\n", "suite_terminal"
        )
        value = unique_json(frame.decode("ascii", errors="strict"))
        require(
            type(value) is dict
            and set(value) == {"schema", "software"}
            and value["schema"] == "bifrost.test.m1-transport-software/v1"
            and type(value["software"]) is dict
            and set(value["software"]) == set(hashes)
            and all(type(v) is str and HEX64.fullmatch(v) for v in value["software"].values())
            and value["software"] == hashes
            and json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii") == frame,
            "software_join",
        )
        self.fixture_disposal = True  # Source-enforced diagnostic/key/child completion, not host kernel FD proof.
        self.suite = {"native_tests": 4, "python_cases": 16, "native_children": 3, "complete": True}

    def read_owned(self, path, limit):
        fd, first, raw = None, None, bytearray()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            before = os.fstat(fd)
            require(
                identity(before) == self.files[path]
                and stat.S_ISREG(before.st_mode)
                and before.st_nlink == 1
                and 0 < before.st_size <= limit,
                "owned_read_identity",
            )
            while len(raw) <= limit:
                require(time.monotonic() < WORK_END, "owned_read_deadline")
                chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            require(
                len(raw) == before.st_size
                and identity(before) == identity(os.fstat(fd))
                and identity(before) == identity(path.lstat()),
                "owned_read_stable",
            )
        except BaseException as error:
            first = error
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    self.cli_unknown = True
                    first = preserve(first, error)
        if first is not None:
            raise first
        return bytes(raw)

    def dispose_containers(self):
        self.phase = "cleanup"
        if self.env is None:
            # No native launch is allowed to fall back to the inherited credential environment.
            return
        first = None
        # A single fresh discovery permits only lost-create identity recovery, never adoption.
        rows = None
        try:
            rows = self.census("containers.cleanup", end=CLEANUP_END)
        except BaseException as error:
            first = error
        for purpose in ("python", "rust"):
            if purpose not in self.create_attempted:
                continue
            expected = self.containers[purpose]
            try:
                if not expected["cid"]:
                    require(rows is not None, "lost_create_unknown")
                    matching = [row for row in rows if row["Names"] == self.names[purpose]]
                    require(len(matching) <= 1, "lost_create_ambiguous")
                    if not matching:
                        continue
                    expected["cid"] = matching[0]["ID"]
                cid = expected["cid"]
                require(HEX64.fullmatch(cid), "cleanup_cid")
                observed = self.inspect(purpose + ".cleanup_before", cid, CLEANUP_END)
                status = observed.get("State", {}).get("Status")
                require(status in ("created", "running", "exited"), "cleanup_state")
                self.qualify(purpose, observed, status)
                if status == "running":
                    self.data(purpose + ".kill", ["docker", "kill", "--signal", "SIGKILL", cid], end=CLEANUP_END)
                    waited = self.data(purpose + ".wait", ["docker", "wait", cid], end=CLEANUP_END)
                    require(re.fullmatch(rb"[0-9]+\n", waited), "cleanup_wait_exit")
                    state = self.qualify(
                        purpose, self.inspect(purpose + ".cleanup_terminal", cid, CLEANUP_END), "exited"
                    )
                    require(int(waited) == state["ExitCode"], "cleanup_exit_join")
                self.data(purpose + ".remove", ["docker", "rm", cid], end=CLEANUP_END)
            except BaseException as error:
                first = preserve(first, error)
        try:
            final = self.census("containers.final", end=CLEANUP_END)
            known = {value["cid"] for value in self.containers.values() if value["cid"] is not None}
            self.absent = not any(row["ID"] in known or row["Names"] in self.names.values() for row in final)
            require(self.absent, "containers_remaining")
            if "python" in self.create_attempted:
                # The only fixture backing is the measured execution container's private tmpfs.
                # Namespace removal is separate evidence from diagnostic-owned per-file cleanup.
                require(self.containers["python"]["cid"] is not None, "fixture_creation_unknown")
                self.fixture_disposal = True
            else:
                self.fixture_disposal = True  # No diagnostic can execute before its create/start.
        except BaseException as error:
            first = preserve(first, error)
        if first is not None:
            raise first

    def dispose_stage(self):
        if self.stage is None:
            return
        require(
            self.absent is True and not self.cli_unknown and all(p.returncode is not None for p in self.processes),
            "staging_consumers_unknown",
        )
        require(
            self.stage_identity is not None and identity(self.stage.lstat())[:5] == self.stage_identity[:5],
            "staging_identity",
        )
        # Only this fresh exclusive directory is eligible. No symlink following, foreign owners,
        # or unexpected hardlinks; trusted Docker config descendants are bounded, not a daemon quota.
        count = 0
        stack = [(self.stage, False, self.stage_identity[:5])]
        while stack:
            require(time.monotonic() < CLEANUP_END, "staging_cleanup_deadline")
            path, visited, expected = stack.pop()
            before = path.lstat()
            require(
                before.st_uid == os.getuid() and identity(before)[:5] == expected, "staging_foreign_owner_or_change"
            )
            if stat.S_ISDIR(before.st_mode):
                if visited:
                    require(not any(path.iterdir()), "staging_not_empty")
                    require(time.monotonic() < CLEANUP_END, "staging_cleanup_deadline")
                    path.rmdir()
                    continue
                stack.append((path, True, expected))
                with os.scandir(path) as iterator:
                    entries = list(iterator)
                count += len(entries)
                require(count <= 1024, "staging_entries_bound")
                for entry in entries:
                    child = Path(entry.path)
                    stack.append((child, False, identity(child.lstat())[:5]))
            else:
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "staging_nonregular")
                if path in self.files:
                    require(
                        self.files[path] is not None and identity(before) == self.files[path], "staging_file_changed"
                    )
                else:
                    # No other subsystem writes here except the source-qualified Docker config scope.
                    require(self.stage / "docker-config" in path.parents, "unexpected_staging_path")
                require(identity(path.lstat()) == identity(before), "staging_delete_changed")
                require(time.monotonic() < CLEANUP_END, "staging_cleanup_deadline")
                path.unlink()
            require(not os.path.lexists(path), "staging_delete_absence")
        require(not os.path.lexists(self.stage), "staging_final_absence")
        self.staging_disposal = True

    def publish(self):
        require(time.monotonic() < PUBLICATION_END, "publication_deadline")
        value = {
            "schema": "bifrost.test.m1-transport-venue/v1",
            "candidate": self.candidate,
            "source_checks": self.source_checks,
            "suite": self.suite,
            "cleanup": {
                "cli_settled": not self.cli_unknown and all(p.returncode is not None for p in self.processes),
                "containers_absent": self.absent,
                "fixture_disposal": self.fixture_disposal,
                "staging_disposal": self.staging_disposal,
            },
            "failure_phase": self.failure_phase,
        }
        require(
            set(value) == {"schema", "candidate", "source_checks", "suite", "cleanup", "failure_phase"}
            and self.failure_phase in (None, *PHASES),
            "safe_summary_keys",
        )
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
        require(len(raw) <= 8192, "safe_summary_bound")
        fd, first = None, None
        try:
            fd = os.open(SUMMARY, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            before = os.fstat(fd)
            require(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == os.getuid()
                and before.st_nlink == 1
                and before.st_size == 0,
                "summary_identity",
            )
            require(time.monotonic() < PUBLICATION_END, "publication_deadline")
            os.fchmod(fd, 0o644)
            offset = 0
            while offset < len(raw):
                require(time.monotonic() < PUBLICATION_END, "publication_deadline")
                written = os.write(fd, raw[offset:])
                require(0 < written <= len(raw) - offset, "summary_write")
                offset += written
            after = os.fstat(fd)
            require(
                after.st_dev == before.st_dev
                and after.st_ino == before.st_ino
                and after.st_nlink == 1
                and after.st_size == len(raw)
                and stat.S_IMODE(after.st_mode) == 0o644
                and identity(after) == identity(SUMMARY.lstat()),
                "summary_complete",
            )
        except BaseException as error:
            first = error
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    first = preserve(first, error)
        if first is not None:
            raise first


def main():
    venue = Venue()
    handlers = {}
    first = None

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
        venue.acquire()
        venue.source()
        rust = venue.rust_checks()
        require(venue.source_checks is True, "source_check_gate")
        venue.compile_and_execute(rust)
    except BaseException as error:
        first = error
        venue.failure_phase = venue.phase
    finally:
        try:
            venue.dispose_containers()
        except BaseException as error:
            if first is None:
                venue.failure_phase = "cleanup"
            first = preserve(first, error)
        try:
            venue.dispose_stage()
        except BaseException as error:
            if first is None:
                venue.failure_phase = "cleanup"
            first = preserve(first, error)
        for signum, original in handlers.items():
            try:
                signal.signal(signum, original)
            except BaseException as error:
                if first is None:
                    venue.failure_phase = "cleanup"
                first = preserve(first, error)
        try:
            venue.publish()
        except BaseException as error:
            first = preserve(first, error)
    if first is not None:
        print("m1_transport_venue_failed", file=sys.stderr)
        return 1
    if not (
        venue.source_checks is True
        and venue.suite is not None
        and venue.absent is True
        and venue.fixture_disposal is True
        and venue.staging_disposal is True
        and not venue.cli_unknown
    ):
        print("m1_transport_venue_failed", file=sys.stderr)
        return 1
    print("m1_transport_venue_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
