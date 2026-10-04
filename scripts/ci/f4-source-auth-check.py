#!/usr/bin/env python3
"""Bounded, secretless execution of the pinned adapter's unchanged controls."""

import hashlib
import json
import os
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE_SHA256 = "75ded3022b1ddb8896127e6571419b3817fd089253b5da3ee4bc21a644c58a9f"
ENTRYPOINT = "main().catch(() => { process.exitCode = 1; });\n"
MUTANTS = [
    {
        "id": "status",
        "old": "need(response.statusCode === expectedStatus, 'http_failed');",
        "new": "need(true, 'http_failed');",
        "expected_assertion_stack_lines": [446],
    },
    {
        "id": "complete",
        "old": "need(response.complete === true, 'http_failed');",
        "new": "need(true, 'http_failed');",
        "expected_assertion_stack_lines": [446],
    },
    {
        "id": "response-limit",
        "old": "need(Buffer.isBuffer(chunk) && size + chunk.length <= 65536, 'body_invalid');",
        "new": "need(Buffer.isBuffer(chunk), 'body_invalid');",
        "expected_assertion_stack_lines": [446],
    },
    {
        "id": "aggregate-limit",
        "old": "need(state.bodyBytes + bytes <= state.bodyLimit, 'body_invalid');",
        "new": "need(true, 'body_invalid');",
        "expected_assertion_stack_lines": [262, 268],
    },
    {
        "id": "abort",
        "old": "response.once('aborted', () => fail(new Fault('http_failed')));",
        "new": "response.once('aborted', () => {});",
        "expected_assertion_stack_lines": [446],
    },
    {
        "id": "utf8",
        "old": "new TextDecoder('utf-8', { fatal: true }).decode(raw)",
        "new": "new TextDecoder('utf-8', { fatal: false }).decode(raw)",
        "expected_assertion_stack_lines": [454],
    },
    {
        "id": "association",
        "old": "need(value.id === expected && value.full_name === 'MTG-Thomas/bifrost-workspace', 'association_failed');",
        "new": "need(true, 'association_failed');",
        "expected_assertion_stack_lines": [263, 306],
    },
    {
        "id": "permissions",
        "old": "need(value.contents === 'read', 'permission_failed');",
        "new": "need(true, 'permission_failed');",
        "expected_assertion_stack_lines": [262, 267],
    },
    {
        "id": "identity",
        "old": "need(Object.keys(actual).every(key => actual[key] === (key === 'size' ? size : expected[key])));",
        "new": "need(true);",
        "expected_assertion_stack_lines": [263, 350],
    },
    {
        "id": "write-progress",
        "old": "need(Number.isSafeInteger(count) && count > 0 && count <= raw.length - offset, 'write_failed'); offset += count;",
        "new": "need(Number.isSafeInteger(count) && count > 0, 'write_failed'); offset += count;",
        "expected_assertion_stack_lines": [263, 357],
    },
]


class Failure(Exception):
    def __init__(self, category):
        super().__init__("source quality failed")
        self.category = category


def require(condition, category):
    if not condition:
        raise Failure(category)


def wrapper(expected, last_line):
    # All original definitions and controls keep their original line positions.
    return r"""
try {
  await controls();
} catch (error) {
  try {
    const expected = EXPECTED;
    let detected = false;
    if (expected !== null && error instanceof Fault && error.code === 'input_invalid'
        && typeof error.stack === 'string' && Buffer.byteLength(error.stack) <= 65536) {
      const url = import.meta.url;
      const frames = [];
      let valid = true;
      for (const line of error.stack.split('\n').slice(1)) {
        if (!line.includes(url)) continue;
        const before = line.indexOf(url);
        const suffix = line.slice(before + url.length);
        const match = /^:(\d+):(\d+)\)?$/.exec(suffix);
        if (!match || !/^\s+at /.test(line) || line.indexOf(url, before + url.length) !== -1) {
          valid = false; break;
        }
        const number = Number(match[1]);
        if (!Number.isSafeInteger(number) || number < 1) { valid = false; break; }
        if (number > LAST_LINE) continue; // Only the replacement entrypoint is later.
        if (number === 11 || number === 13) continue; // Original Fault / need.
        frames.push(number);
      }
      detected = valid && JSON.stringify(frames) === JSON.stringify(expected);
    }
    if (detected) {
      const marker = Buffer.from('guard-detected\n', 'ascii');
      if (fs.writeSync(1, marker, 0, marker.length) !== marker.length) process.exitCode = 43;
      else process.exitCode = 42;
    } else process.exitCode = 43;
  } catch { process.exitCode = 43; }
}
""".replace("EXPECTED", json.dumps(expected, separators=(",", ":"))).replace("LAST_LINE", str(last_line))


class Runner:
    def __init__(self):
        self.start = time.monotonic()
        self.work_end = self.start + 120
        self.final_end = self.start + 130
        self.cleanup_end = None
        self.launched = 0
        self.settled = 0
        self.observed = 0
        self.children = []
        self.first = None
        self.category = None

    def fail(self, error, category):
        if self.first is None:
            self.first = error
            self.category = category
            self.cleanup_end = min(self.final_end, time.monotonic() + 10)

    @staticmethod
    def group_absent(pid):
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        return False

    def run(self, argv, category):
        require(self.first is None and time.monotonic() < self.work_end, "deadline")
        require(self.launched < 24, "closure")
        self.launched += 1  # Count attempts before Popen; never reuse a slot.
        row = {"process": None, "pipes": [], "settled": False}
        self.children.append(row)
        selector = None
        selector_closed = True
        outputs = [bytearray(), bytearray()]
        counts = [0, 0]
        eof = [False, False]
        closed = [False, False]
        first = None
        first_category = category
        end = min(self.work_end, time.monotonic() + 10)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env={key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}},
            )
            row["process"] = process  # Retain before any fallible configuration.
            row["pipes"] = [process.stdout, process.stderr]
            selector = selectors.DefaultSelector()
            selector_closed = False
            for index, pipe in enumerate(row["pipes"]):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, index)
            while not all(eof) or process.poll() is None:
                if time.monotonic() >= end:
                    raise Failure("deadline")
                for key, _ in selector.select(min(0.05, max(0, end - time.monotonic()))):
                    index = key.data
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        eof[index] = True
                        selector.unregister(key.fileobj)
                        continue
                    counts[index] += len(chunk)
                    self.observed += len(chunk)
                    if counts[index] <= 65536 and self.observed <= 1048576:
                        outputs[index].extend(chunk)
                    else:
                        raise Failure("capture")
            process.wait(timeout=max(0, end - time.monotonic()))
        except BaseException as error:
            first = error
            first_category = error.category if isinstance(error, Failure) else category
            self.fail(error, first_category)
        finally:
            process = row["process"]
            # Each cleanup operation is independent; retain the original object.
            cleanup_end = self.cleanup_end if self.cleanup_end is not None else min(self.final_end, end)
            if process is not None:
                try:
                    if not self.group_absent(process.pid):
                        if first is None:
                            first, first_category = Failure("closure"), "closure"
                            self.fail(first, "closure")
                            cleanup_end = self.cleanup_end
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    if first is None:
                        first, first_category = error, "closure"
                        self.fail(error, "closure")
                try:
                    # Discard-drain and account even over-limit bytes during settlement.
                    while time.monotonic() < cleanup_end and (not all(eof) or process.poll() is None):
                        for index, pipe in enumerate(row["pipes"]):
                            if eof[index]:
                                continue
                            try:
                                os.set_blocking(pipe.fileno(), False)
                                chunk = os.read(pipe.fileno(), 8192)
                                if chunk:
                                    counts[index] += len(chunk)
                                    self.observed += len(chunk)
                                    if counts[index] > 65536 or self.observed > 1048576:
                                        error = Failure("capture")
                                        if first is None:
                                            first, first_category = error, "capture"
                                            self.fail(error, "capture")
                                else:
                                    eof[index] = True
                            except BlockingIOError:
                                pass
                            except BaseException as error:
                                if first is None:
                                    first, first_category = error, "closure"
                                    self.fail(error, "closure")
                        time.sleep(min(0.01, max(0, cleanup_end - time.monotonic())))
                except BaseException as error:
                    if first is None:
                        first, first_category = error, "closure"
                        self.fail(error, "closure")
                try:
                    process.wait(timeout=max(0, cleanup_end - time.monotonic()))
                except BaseException as error:
                    if first is None:
                        first, first_category = error, "closure"
                        self.fail(error, "closure")
            if selector is not None:
                try:
                    selector.close()
                    selector_closed = True
                except BaseException as error:
                    if first is None:
                        first, first_category = error, "closure"
                        self.fail(error, "closure")
            for index, pipe in enumerate(row["pipes"]):
                try:
                    pipe.close()
                    closed[index] = True
                except BaseException as error:
                    if first is None:
                        first, first_category = error, "closure"
                        self.fail(error, "closure")
            try:
                row["settled"] = process is not None and (
                    process.returncode is not None
                    and all(eof)
                    and all(closed)
                    and selector_closed
                    and self.group_absent(process.pid)
                )
                if not row["settled"]:
                    raise Failure("closure")
                self.settled += 1
            except BaseException as error:
                if first is None:
                    first, first_category = error, "closure"
                    self.fail(error, "closure")
        if first is not None:
            raise first
        return process.returncode, bytes(outputs[0]), bytes(outputs[1])


def main():
    runner = Runner()
    candidate = os.environ.get("GITHUB_SHA", "")
    summary = {
        "schema": "bifrost.private.f4-source-quality/v1",
        "candidate": candidate,
        "source_sha256": SOURCE_SHA256,
        "environment": {"os": "unknown", "node": "unknown", "python": platform.python_version()},
        "syntax": "not_run",
        "baseline": "not_run",
        "mutants": [{"id": item["id"], "outcome": "not_run"} for item in MUTANTS],
        "processes": {},
        "status": "fail",
        "primary": None,
        "cleanup": "unknown",
    }
    directory = None
    directory_attempted = False
    files = []
    file_unknown = False
    handlers = {}
    handlers_restored = True
    stage = "environment"
    try:
        require(re.fullmatch("[0-9a-f]{40}", candidate) is not None, "source")
        require(sys.argv[1:] == [], "environment")
        for number in (signal.SIGINT, signal.SIGTERM):
            handlers[number] = signal.getsignal(number)
            signal.signal(number, lambda signum, frame: (_ for _ in ()).throw(SystemExit(1)))
        release = platform.freedesktop_os_release()
        require(release.get("ID") == "ubuntu" and release.get("VERSION_ID") == "24.04", "environment")
        summary["environment"]["os"] = release["ID"] + " " + release["VERSION_ID"]
        status, out, err = runner.run(["node", "--version"], "environment")
        require(status == 0 and out == b"v24.21.0\n" and err == b"", "environment")
        summary["environment"]["node"] = out[:-1].decode("ascii")
        stage = "source"
        source_path = Path(__file__).resolve().parents[2] / "scripts/ci/f4-source-auth/acquire.mjs"
        source_fd = None
        source_first = None
        try:
            source_fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            actual = os.fstat(source_fd)
            require(stat.S_ISREG(actual.st_mode) and actual.st_size <= 65536, "source")
            pieces = []
            size = 0
            while True:
                require(time.monotonic() < runner.work_end, "deadline")
                chunk = os.read(source_fd, min(8192, 65537 - size))
                if not chunk:
                    break
                size += len(chunk)
                require(size <= 65536, "source")
                pieces.append(chunk)
            raw = b"".join(pieces)
        except BaseException as error:
            source_first = error
        finally:
            if source_fd is not None:
                try:
                    os.close(source_fd)
                except BaseException as error:
                    file_unknown = True
                    if source_first is None:
                        source_first = error
        if source_first is not None:
            raise source_first
        require(hashlib.sha256(raw).hexdigest() == SOURCE_SHA256, "source")
        source = raw.decode("utf-8")
        require(source.count(ENTRYPOINT) == 1 and source.endswith(ENTRYPOINT), "source")
        definition, controls = source.split("async function controls()", 1)
        require(source.count("async function controls()") == 1, "source")
        for item in MUTANTS:
            require(definition.count(item["old"]) == 1, "source")
            require(item["old"].count("\n") == item["new"].count("\n"), "source")
        stage = "syntax"
        summary["syntax"] = "fail"
        status, out, err = runner.run(["node", "--check", str(source_path)], "syntax")
        require(status == 0 and out == err == b"", "syntax")
        directory_attempted = True
        try:
            directory = Path(tempfile.mkdtemp(prefix="f4-source-quality-"))
        except BaseException:
            file_unknown = True
            raise
        require(stat.S_IMODE(directory.stat().st_mode) == 0o700, "closure")
        variants = [(None, source)] + [
            (item, definition.replace(item["old"], item["new"], 1) + "async function controls()" + controls)
            for item in MUTANTS
        ]
        for index, (item, variant) in enumerate(variants):
            stage = "baseline" if item is None else "mutation"
            if item is None:
                summary["baseline"] = "fail"
            else:
                summary["mutants"][index - 1]["outcome"] = "fail"
            text = variant[: -len(ENTRYPOINT)] + wrapper(
                None if item is None else item["expected_assertion_stack_lines"], len(source.splitlines()) - 1
            )
            path = directory / f"control-{index}.mjs"
            # Register the path before creation; never recursive-delete unknown entries.
            record = {"path": path, "identity": None, "closed": False}
            files.append(record)
            descriptor = None
            first = None
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                actual = os.fstat(descriptor)
                record["identity"] = (actual.st_dev, actual.st_ino)
                require(stat.S_ISREG(actual.st_mode) and stat.S_IMODE(actual.st_mode) == 0o600, "closure")
                payload = text.encode("utf-8")
                offset = 0
                while offset < len(payload):
                    require(time.monotonic() < runner.work_end, "deadline")
                    count = os.write(descriptor, payload[offset:])
                    require(0 < count <= len(payload) - offset, "closure")
                    offset += count
            except BaseException as error:
                first = error
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                        record["closed"] = True
                    except BaseException as error:
                        file_unknown = True
                        if first is None:
                            first = error
            if first is not None:
                raise first
            summary["syntax"] = "fail"
            status, out, err = runner.run(["node", "--check", str(path)], "syntax")
            require(status == 0 and out == err == b"", "syntax")
            summary["syntax"] = "pass"
            status, out, err = runner.run(["node", str(path)], stage)
            if item is None:
                require(status == 0 and out == err == b"", "baseline")
                summary["baseline"] = "pass"
            else:
                require(status == 42 and out == b"guard-detected\n" and err == b"", "mutation")
                summary["mutants"][index - 1]["outcome"] = "detected"
            require(runner.children[-1]["settled"], "closure")
            actual = path.lstat()
            require(record["identity"] == (actual.st_dev, actual.st_ino) and record["closed"], "closure")
            require(time.monotonic() < runner.work_end, "deadline")
            path.unlink()
            files.remove(record)
        require(runner.launched == runner.settled == 24 and time.monotonic() < runner.work_end, "deadline")
    except BaseException as error:
        runner.fail(error, error.category if isinstance(error, Failure) else stage)
    finally:
        # Cleanup cannot replace the actual first failure or claim an unknown child settled.
        if all(row["settled"] for row in runner.children):
            for record in files:
                try:
                    require(time.monotonic() < (runner.cleanup_end or runner.final_end), "deadline")
                    require(record["identity"] is not None and record["closed"], "closure")
                    actual = record["path"].lstat()
                    require(record["identity"] == (actual.st_dev, actual.st_ino), "closure")
                    record["path"].unlink()
                except BaseException as error:
                    file_unknown = True
                    runner.fail(error, "closure")
            if directory is not None and not file_unknown:
                try:
                    require(time.monotonic() < (runner.cleanup_end or runner.final_end), "deadline")
                    directory.rmdir()
                    directory = None
                except BaseException as error:
                    runner.fail(error, "closure")
        for number, handler in handlers.items():
            try:
                signal.signal(number, handler)
            except BaseException as error:
                handlers_restored = False
                runner.fail(error, "closure")
    complete = (
        all(row["settled"] for row in runner.children)
        and directory is None
        and not file_unknown
        and handlers_restored
        and (not directory_attempted or runner.launched >= 3)
    )
    summary["cleanup"] = "complete" if complete else "unknown"
    summary["processes"] = {
        "launched": runner.launched,
        "settled": runner.settled,
        "observed_bytes": runner.observed,
        "complete": complete,
    }
    summary["primary"] = runner.category
    passed = (
        runner.first is None
        and complete
        and runner.launched == runner.settled == 24
        and summary["syntax"] == summary["baseline"] == "pass"
        and all(item["outcome"] == "detected" for item in summary["mutants"])
    )
    summary["status"] = "pass" if passed else "fail"
    try:
        require(re.fullmatch("[0-9a-f]{40}", candidate) is not None, "source")
        require(
            all(type(value) is str and len(value) <= 128 for value in summary["environment"].values()), "environment"
        )
        payload = (json.dumps(summary, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        require(len(payload) <= 8192 and time.monotonic() < runner.final_end, "deadline")
        require(sys.stdout.buffer.write(payload) == len(payload), "closure")
        sys.stdout.buffer.flush()
    except BaseException:
        return 1
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    except BaseException:
        exit_code = 1  # No default formatter may publish private paths or captures.
    raise SystemExit(exit_code)
