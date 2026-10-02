#!/usr/bin/env python3
"""Generate fresh host-owned reference materials; this proves no runtime custody."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "api"))
from scripts import agent_reference_contract as wire

OPENSSL = Path("/usr/bin/openssl")
STAGE_SECONDS = 10
MAX_TOOL_OUTPUT = 8192
FAILURE = "Agent reference material generation failed; retain private context.\n"
FILES = (
    "observer-ingest-key",
    "observer-control-key",
    "model-oracle-input.json",
    "observer-ca-key.pem",
    "observer-server-key.pem",
    "observer-ca.pem",
    "observer-server.pem",
    "observer-server.csr.pem",
    "observer-server.ext.conf",
)
LEAF_EXTENSIONS = (
    "[server]\n"
    "basicConstraints=critical,CA:FALSE\n"
    "keyUsage=critical,digitalSignature\n"
    "extendedKeyUsage=serverAuth\n"
    "subjectAltName=DNS:scheduler-fixtures,IP:127.0.0.1\n"
).encode("ascii")


class MaterialsError(ValueError):
    """Only a static diagnostic crosses the CLI boundary."""


def require(condition: bool) -> None:
    if not condition:
        raise MaterialsError("material_generation_failed")


def _open_directory(context: Path) -> int:
    require(context.is_absolute() and str(context) == os.path.normpath(context))
    require(".." not in context.parts)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in context.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        require(metadata.st_uid == os.getuid())
        require(stat.S_IMODE(metadata.st_mode) == 0o700)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _regular(descriptor: int, mode: int = 0o600) -> os.stat_result:
    metadata = os.fstat(descriptor)
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1)
    require(metadata.st_uid == os.getuid() and stat.S_IMODE(metadata.st_mode) == mode)
    return metadata


def _read_owner(directory: int, lane_id: str) -> dict:
    descriptor = os.open(
        "owner.json",
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=directory,
    )
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1)
        require(metadata.st_uid == os.getuid() and not metadata.st_mode & 0o022)
        require(metadata.st_size <= 4096)
        raw = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
    require(len(raw) <= 4096)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    owner = json.loads(raw, object_pairs_hook=unique)
    require(
        type(owner) is dict and set(owner) == {"root", "source", "project", "lane_id"}
    )
    root = str(REPOSITORY_ROOT)
    project = "bifrost-agent-reference-" + hashlib.sha256(root.encode()).hexdigest()[:8]
    require(owner["root"] == root and owner["project"] == project)
    require(owner["lane_id"] == lane_id)
    require(
        type(owner["source"]) is str
        and re.fullmatch(r"[0-9a-f]{40}", owner["source"]) is not None
    )
    return owner


def _tool_identity() -> os.stat_result:
    metadata = OPENSSL.lstat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0)
    require(not metadata.st_mode & 0o022 and bool(metadata.st_mode & 0o111))
    return metadata


def _openssl(
    arguments: list[str], descriptors: tuple[int, ...], identity: os.stat_result
) -> bytes:
    current = _tool_identity()
    require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino))
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "OPENSSL_CONF": "/dev/null"}
    output = bytearray()
    process = None
    try:
        process = subprocess.Popen(
            [str(OPENSSL), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            pass_fds=descriptors,
            close_fds=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + STAGE_SECONDS
        with selectors.DefaultSelector() as selector:
            require(process.stdout is not None)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0)
                require(bool(selector.select(remaining)))
                chunk = os.read(
                    process.stdout.fileno(), MAX_TOOL_OUTPUT + 1 - len(output)
                )
                output.extend(chunk)
                require(len(output) <= MAX_TOOL_OUTPUT)
                if not chunk:
                    selector.unregister(process.stdout)
            require(process.wait(timeout=max(0, deadline - time.monotonic())) == 0)
    except Exception as error:
        raise MaterialsError("material_generation_failed") from error
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)
            if process.stdout is not None:
                process.stdout.close()
    return bytes(output)


def _write(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        count = os.write(descriptor, view)
        require(count > 0)
        view = view[count:]
    os.fsync(descriptor)


def generate(context: Path, lane_id: str, case_id: str) -> dict:
    require(type(lane_id) is str and re.fullmatch(r"[0-9a-f]{32}", lane_id) is not None)
    require(type(case_id) is str and re.fullmatch(r"[0-9a-f]{32}", case_id) is not None)
    with ExitStack() as stack:
        directory = _open_directory(context)
        stack.callback(os.close, directory)
        _read_owner(directory, lane_id)
        for name in FILES:
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise MaterialsError("material_generation_failed")
        handles = {}
        for name in FILES:
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            stack.callback(os.close, descriptor)
            os.fchmod(descriptor, 0o600)
            _regular(descriptor)
            handles[name] = descriptor
        # Exclusive empty reservations remain on ANY failure; never retry this context.
        identity = _tool_identity()
        version = _openssl(["version"], (), identity).decode("ascii").strip()
        require(
            re.fullmatch(
                r"OpenSSL 3\.[0-9]+\.[0-9]+[a-z]? [0-9]{1,2} [A-Za-z]{3} [0-9]{4}"
                r"(?: \(Library: OpenSSL 3\.[0-9]+\.[0-9]+[a-z]? [0-9]{1,2} [A-Za-z]{3} [0-9]{4}\))?",
                version,
            )
            is not None
        )
        values = [secrets.token_hex(32) for _ in range(5)]
        require(
            all(
                type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None
                for value in values
            )
        )
        require(len(set(values)) == 5)
        model = {
            "schema": "bifrost.agent-reference.model-oracle-input/v1",
            "lane_id": lane_id,
            "case_id": case_id,
            "model_key": values[2],
            "password": values[3],
            "visa": values[4],
            "partner_name": "C1R Synthetic Partner",
            "username": "c1r-reference@example.invalid",
        }
        encoded_model = wire.encode_private("model_input", model)
        _write(handles["observer-ingest-key"], values[0].encode("ascii"))
        _write(handles["observer-control-key"], values[1].encode("ascii"))
        _write(handles["model-oracle-input.json"], encoded_model)
        _write(handles["observer-server.ext.conf"], LEAF_EXTENSIONS)

        def reference(name: str) -> str:
            return f"/proc/self/fd/{handles[name]}"

        def run(arguments: list[str], *names: str) -> None:
            require(
                not _openssl(
                    arguments, tuple(handles[name] for name in names), identity
                )
            )

        for name in ("observer-ca-key.pem", "observer-server-key.pem"):
            run(
                [
                    "genpkey",
                    "-algorithm",
                    "EC",
                    "-pkeyopt",
                    "ec_paramgen_curve:prime256v1",
                    "-out",
                    reference(name),
                ],
                name,
            )
        run(
            [
                "req",
                "-new",
                "-x509",
                "-key",
                reference("observer-ca-key.pem"),
                "-sha256",
                "-days",
                "1",
                "-subj",
                "/CN=Bifrost Agent Reference CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-out",
                reference("observer-ca.pem"),
            ],
            "observer-ca-key.pem",
            "observer-ca.pem",
        )
        run(
            [
                "req",
                "-new",
                "-key",
                reference("observer-server-key.pem"),
                "-sha256",
                "-subj",
                "/CN=scheduler-fixtures",
                "-out",
                reference("observer-server.csr.pem"),
            ],
            "observer-server-key.pem",
            "observer-server.csr.pem",
        )
        run(
            [
                "x509",
                "-req",
                "-in",
                reference("observer-server.csr.pem"),
                "-CA",
                reference("observer-ca.pem"),
                "-CAkey",
                reference("observer-ca-key.pem"),
                "-set_serial",
                "1",
                "-days",
                "1",
                "-sha256",
                "-extfile",
                reference("observer-server.ext.conf"),
                "-extensions",
                "server",
                "-out",
                reference("observer-server.pem"),
            ],
            "observer-server.csr.pem",
            "observer-ca.pem",
            "observer-ca-key.pem",
            "observer-server.ext.conf",
            "observer-server.pem",
        )
        fingerprints = {}
        for name in ("observer-ca.pem", "observer-server.pem"):
            der = _openssl(
                ["x509", "-in", reference(name), "-outform", "DER"],
                (handles[name],),
                identity,
            )
            require(bool(der))
            fingerprints[name] = "sha256:" + hashlib.sha256(der).hexdigest()
        metadata = []
        for name, descriptor in handles.items():
            found = _regular(descriptor)
            require(
                0
                < found.st_size
                <= (2048 if name == "model-oracle-input.json" else 8192)
            )
            linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
            require((found.st_dev, found.st_ino) == (linked.st_dev, linked.st_ino))
            os.fsync(descriptor)
            metadata.append(
                {
                    "name": name,
                    "mode": "0600",
                    "uid": found.st_uid,
                    "gid": found.st_gid,
                    "device": found.st_dev,
                    "inode": found.st_ino,
                }
            )
        os.fsync(directory)
        return {
            "schema": "bifrost.agent-reference.materials/v1",
            "files": metadata,
            "public_certificate_fingerprints": fingerprints,
            "openssl": {
                "path": str(OPENSSL),
                "version": version,
                "device": identity.st_dev,
                "inode": identity.st_ino,
            },
        }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise MaterialsError("material_generation_failed")


def _generate_for_cli(context: Path, lane_id: str, case_id: str) -> dict:
    try:
        return generate(context, lane_id, case_id)
    except Exception as error:
        raise MaterialsError("material_generation_failed") from error


def main(argv: list[str] | None = None) -> int:
    try:
        supplied = sys.argv[1:] if argv is None else argv
        if supplied != ["--help"]:
            require(
                len(supplied) == 6
                and set(supplied[::2]) == {"--context", "--lane-id", "--case-id"}
            )
        parser = _Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--context", type=Path, required=True)
        parser.add_argument("--lane-id", required=True)
        parser.add_argument("--case-id", required=True)
        arguments = parser.parse_args(supplied)
        result = _generate_for_cli(
            arguments.context, arguments.lane_id, arguments.case_id
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (MaterialsError, OSError, ValueError, KeyboardInterrupt):
        sys.stderr.write(FAILURE)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
