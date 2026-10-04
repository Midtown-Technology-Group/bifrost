"""Standalone synthetic transport diagnostic; no pytest, application or DB imports.

Runtime requires a separately admitted example binary and libtest binary. This module
never builds them or claims that a local digest authenticates their compiler/source.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import json
import os
import signal
import ssl
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

FIVE = 5.0
SUITE = 180.0
BODY_LIMIT = 1024
FILE_LIMIT = 65536
FILE_TOTAL = 1048576
CAPTURE_TOTAL = 131072
FIXTURE_NAMES = (
    "ca.der",
    "ca.pem",
    "ca-key.pem",
    "server.der",
    "server.pem",
    "server-key.der",
    "server-key.pem",
    "client-a.der",
    "client-a.pem",
    "client-a-key.der",
    "client-a-key.pem",
    "client-a-expiry.txt",
    "client-b.der",
    "client-b.pem",
    "client-b-key.pem",
    "unlisted.pem",
    "unlisted-key.pem",
    "wrong-ca.pem",
    "wrong-ca-key.pem",
    "wrong-eku.pem",
    "wrong-eku-key.pem",
    "expired.pem",
    "expired-key.pem",
    "untrusted-ca.pem",
    "untrusted-ca-key.pem",
)
NATIVE_TESTS = (
    "tests::both_inert_probes_exact_response",
    "tests::same_connection_rotation_and_generation_rejection",
    "tests::same_connection_real_certificate_expiry",
    "tests::strict_request_and_route_boundaries",
)
PYTHON_CASES = (
    "workflow_probe",
    "agent_probe",
    "missing_client",
    "wrong_ca",
    "unlisted_leaf",
    "wrong_eku",
    "expired_leaf",
    "server_trust",
    "server_hostname",
    "strict_duplicate",
    "strict_extra",
    "body_limit",
    "unknown_route",
    "redirect_no_second_destination",
    "stream_before_materialization",
    "ambient_proxy_ca_ignored",
)


class DiagnosticError(Exception):
    """Closed source label, never an arbitrary caught message."""


def require(condition: bool, label: str) -> None:
    if not condition:
        raise DiagnosticError(label)


def preserve(first: BaseException | None, error: BaseException) -> BaseException:
    return first if first is not None else error


async def close_owned(resource: Any, ledger: Ledger, end: float, first: BaseException | None) -> BaseException | None:
    """Protect the two actual close operations independently; unknown is irreversible."""
    try:
        resource.close()
    except BaseException as error:
        ledger.closure_unknown = True
        first = preserve(first, error)
    try:
        async with asyncio.timeout_at(end):
            await resource.wait_closed()
    except BaseException as error:
        ledger.closure_unknown = True
        first = preserve(first, error)
    return first


async def settle_owned_task(task: Any, ledger: Ledger, end: float, first: BaseException | None) -> BaseException | None:
    try:
        if task.done():
            task.result()  # Observe already-terminal exceptions, not only an inferred done flag.
        else:
            async with asyncio.timeout_at(min(asyncio.get_running_loop().time() + 2, end)):
                await asyncio.shield(task)
    except BaseException as error:
        ledger.closure_unknown = True
        first = preserve(first, error)
        pending = False
        try:
            pending = not task.done()
        except BaseException as secondary:
            first = preserve(first, secondary)
        if pending:
            cancelled = False
            try:
                task.cancel()
                cancelled = True
            except BaseException as secondary:
                first = preserve(first, secondary)
            try:
                async with asyncio.timeout_at(end):
                    await task
            except asyncio.CancelledError as secondary:
                if not cancelled:
                    first = preserve(first, secondary)
            except BaseException as secondary:
                first = preserve(first, secondary)
    return first


def signal_owned(
    pid: int | None,
    settled: bool,
    value: signal.Signals,
    end: float,
    clock: Any,
    killpg: Any,
    ledger: Ledger,
    first: BaseException | None,
) -> BaseException | None:
    # Zero is a read-only absence observation. No NEW signal authority survives expiry.
    if pid is None or settled:
        return first
    try:
        try:
            killpg(pid, 0)
        except ProcessLookupError:
            return first
        require(clock() < end, "signal_deadline")
        killpg(pid, value)
    except BaseException as error:
        ledger.closure_unknown = True
        first = preserve(first, error)
    return first


AMBIENT_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR")


async def ambient_scope(environment: Any, body: Any, ledger: Ledger) -> Any:
    old = {key: environment.get(key) for key in AMBIENT_KEYS}
    first: BaseException | None = None
    result: Any = None
    try:
        for key in AMBIENT_KEYS:
            environment[key] = "http://127.0.0.1:1" if "PROXY" in key else "/nonexistent/m1-ca"
        result = await body()
    except BaseException as error:
        first = error
    finally:
        for key in AMBIENT_KEYS:
            try:
                if old[key] is None:
                    environment.pop(key, None)
                else:
                    environment[key] = old[key]
            except BaseException as error:
                ledger.closure_unknown = True
                first = preserve(first, error)
    if first is not None:
        raise first
    return result


@dataclass(frozen=True)
class Identity:
    dev: int
    ino: int
    uid: int
    gid: int
    mode: int
    size: int
    links: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> Identity:
        return cls(value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode, value.st_size, value.st_nlink)


class Ledger:
    def __init__(self) -> None:
        self.file_bytes = 0
        self.capture_bytes = 0
        self.closure_unknown = False
        self.exposure_unknown = False

    def debit_file(self, count: int) -> None:
        self.file_bytes += count
        require(self.file_bytes <= FILE_TOTAL, "file_aggregate")

    def debit_capture(self, count: int) -> None:
        self.capture_bytes += count
        require(self.capture_bytes <= CAPTURE_TOTAL, "capture_aggregate")


class Fixtures:
    def __init__(self, ledger: Ledger, end: float) -> None:
        self.ledger = ledger
        self.end = end
        self.path: Path | None = None
        self.directory_identity: Identity | None = None
        self.identities: dict[str, Identity] = {}
        self.creation_unknown = False

    def check_time(self) -> None:
        require(asyncio.get_running_loop().time() < self.end, "suite_deadline")

    def acquire(self) -> None:
        self.check_time()
        self.creation_unknown = True
        self.path = Path(tempfile.mkdtemp(prefix="bifrost-m1-transport-"))
        observed = self.path.lstat()
        require(stat.S_ISDIR(observed.st_mode) and stat.S_IMODE(observed.st_mode) == 0o700, "directory_shape")
        require(observed.st_uid == os.getuid() == os.geteuid(), "directory_owner")
        require(os.getgid() == os.getegid(), "effective_group")
        spelling = str(self.path)
        require(self.path.is_absolute() and spelling.isascii() and len(spelling) <= 512, "directory_path")
        for ancestor in self.path.parents:
            require(stat.S_ISDIR(ancestor.lstat().st_mode), "directory_ancestor")
        self.directory_identity = Identity.from_stat(observed)
        self.creation_unknown = False

    def stable_directory(self) -> None:
        require(self.path is not None and self.directory_identity is not None, "directory_missing")
        current = Identity.from_stat(self.path.lstat())
        # A directory's size changes as this owner creates files; fixed identity/owner/mode is retained.
        original = self.directory_identity
        require(
            (current.dev, current.ino, current.uid, current.gid, current.mode)
            == (original.dev, original.ino, original.uid, original.gid, original.mode),
            "directory_changed",
        )

    def write(self, name: str, raw: bytes, private: bool) -> None:
        self.check_time()
        self.stable_directory()
        require(
            name in FIXTURE_NAMES and name not in self.identities and 0 < len(raw) <= FILE_LIMIT, "file_write_shape"
        )
        require(len(self.identities) < 32, "file_count")
        self.ledger.debit_file(len(raw))
        assert self.path is not None
        path = self.path / name
        mode = 0o600 if private else 0o644
        fd: int | None = None
        first: BaseException | None = None
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            # Exact fresh-file provision, never a rescue of an existing file.
            os.fchmod(fd, mode)
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == 0, "new_file_shape")
            require(before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == mode, "new_file_owner")
            self.identities[name] = Identity.from_stat(before)
            offset = 0
            while offset < len(raw):
                self.check_time()
                written = os.write(fd, raw[offset:])
                require(type(written) is int and 0 < written <= len(raw) - offset, "file_write_count")
                offset += written
            after = os.fstat(fd)
            require(
                after.st_size == len(raw) and after.st_ino == before.st_ino and after.st_dev == before.st_dev,
                "file_write_identity",
            )
            identity = Identity.from_stat(after)
            require(Identity.from_stat(path.lstat()) == identity, "file_path_identity")
            self.identities[name] = identity
        except BaseException as error:
            if name not in self.identities:
                self.ledger.exposure_unknown = True
            first = error
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    self.ledger.closure_unknown = True
                    first = preserve(first, error)
        if first is not None:
            raise first

    def cleanup(self, end: float) -> None:
        require(not self.ledger.closure_unknown and not self.ledger.exposure_unknown, "cleanup_unknown_handles")
        require(not self.creation_unknown, "cleanup_unknown_creation")
        if self.path is None:
            return
        self.stable_directory()
        first: BaseException | None = None
        for name, expected in list(self.identities.items()):
            try:
                require(asyncio.get_running_loop().time() < end, "cleanup_deadline")
                require(Identity.from_stat((self.path / name).lstat()) == expected, "cleanup_file_changed")
                os.unlink(self.path / name)
                require(not os.path.lexists(self.path / name), "cleanup_file_remaining")
                del self.identities[name]
            except BaseException as error:
                first = preserve(first, error)
        if first is None:
            require(asyncio.get_running_loop().time() < end, "cleanup_deadline")
            self.stable_directory()
            os.rmdir(self.path)
            require(not os.path.lexists(self.path), "cleanup_directory_remaining")
        if first is not None:
            raise first


def certificate(
    key: ec.EllipticCurvePrivateKey,
    issuer: x509.Name,
    issuer_key: ec.EllipticCurvePrivateKey,
    name: str,
    *,
    ca: bool = False,
    server: bool = False,
    expiry: datetime.datetime | None = None,
) -> x509.Certificate:
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(expiry or now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
    if server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


def populate(fixtures: Fixtures, short: bool) -> None:
    def key() -> ec.EllipticCurvePrivateKey:
        fixtures.check_time()
        return ec.generate_private_key(ec.SECP256R1())

    def public(name: str, cert: x509.Certificate, der: bool = False) -> None:
        fixtures.write(
            name, cert.public_bytes(serialization.Encoding.DER if der else serialization.Encoding.PEM), False
        )

    def private(name: str, value: ec.EllipticCurvePrivateKey, der: bool = False) -> None:
        fixtures.write(
            name,
            value.private_bytes(
                serialization.Encoding.DER if der else serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            True,
        )

    ca_key = key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-m1-root")])
    ca = certificate(ca_key, ca_name, ca_key, "synthetic-m1-root", ca=True)
    public("ca.der", ca, True)
    public("ca.pem", ca)
    private("ca-key.pem", ca_key)
    other_key = key()
    other_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-untrusted-root")])
    other = certificate(other_key, other_name, other_key, "synthetic-untrusted-root", ca=True)
    public("untrusted-ca.pem", other)
    private("untrusted-ca-key.pem", other_key)
    server_key = key()
    server = certificate(server_key, ca.subject, ca_key, "synthetic-server", server=True)
    public("server.der", server, True)
    public("server.pem", server)
    private("server-key.der", server_key, True)
    private("server-key.pem", server_key)
    for name in ("client-b", "unlisted", "wrong-ca", "wrong-eku", "expired"):
        client_key = key()
        signer, issuer = (other_key, other.subject) if name == "wrong-ca" else (ca_key, ca.subject)
        expiry = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1) if name == "expired" else None
        cert = certificate(client_key, issuer, signer, name, server=name == "wrong-eku", expiry=expiry)
        public(f"{name}.pem", cert)
        private(f"{name}-key.pem", client_key)
        if name == "client-b":
            public("client-b.der", cert, True)
    # Generate the genuinely short-lived client last, after compilation and all other fixture construction.
    a_key = key()
    expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=2) if short else None
    a = certificate(a_key, ca.subject, ca_key, "client-a", expiry=expiry)
    public("client-a.der", a, True)
    public("client-a.pem", a)
    private("client-a-key.der", a_key, True)
    private("client-a-key.pem", a_key)
    fixtures.write("client-a-expiry.txt", str(int(a.not_valid_after_utc.timestamp())).encode("ascii"), False)
    require(set(fixtures.identities) == set(FIXTURE_NAMES), "fixture_membership")


class Child:
    def __init__(self, ledger: Ledger, suite_end: float, ready: bool) -> None:
        self.ledger = ledger
        self.suite_end = suite_end
        self.signal_end = suite_end
        self.ready_expected = ready
        self.process: asyncio.subprocess.Process | None = None
        self.attempted = False
        self.readers: list[asyncio.Task[None]] = []
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.eof = [False, False]
        self.ready_event = asyncio.Event()
        self.ready_port: int | None = None
        self.first: BaseException | None = None
        self.settled = False

    async def launch(self, argv: tuple[str, ...], env: dict[str, str]) -> None:
        require(asyncio.get_running_loop().time() < self.suite_end, "suite_deadline")
        self.attempted = True
        try:
            async with asyncio.timeout_at(min(asyncio.get_running_loop().time() + FIVE, self.suite_end)):
                self.process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    start_new_session=True,
                )
            require(self.process.stdout is not None and self.process.stderr is not None, "child_pipes")
            for index, pipe in enumerate((self.process.stdout, self.process.stderr)):
                self.readers.append(asyncio.create_task(self.capture(index, pipe)))
        except BaseException:
            if self.process is None:
                self.ledger.exposure_unknown = True
            raise

    async def capture(self, index: int, pipe: asyncio.StreamReader) -> None:
        destination = self.stdout if index == 0 else self.stderr
        try:
            while True:
                chunk = await pipe.read(4096)
                if not chunk:
                    self.eof[index] = True
                    if index == 0:
                        self.ready_event.set()
                    return
                self.ledger.debit_capture(len(chunk))
                require(len(destination) + len(chunk) <= FILE_LIMIT, "capture_stream")
                destination.extend(chunk)
                if self.ready_expected and index == 0:
                    require(len(destination) <= 256, "ready_bound")
                    if b"\n" in destination:
                        self.ready_port = ready_decode(bytes(destination))
                        self.ready_event.set()
        except BaseException as error:
            self.first = preserve(self.first, error)
            self.ready_event.set()
            self.signal(signal.SIGKILL, self.signal_end)

    def signal(self, value: signal.Signals, end: float) -> None:
        self.first = signal_owned(
            None if self.process is None else self.process.pid,
            self.settled,
            value,
            min(end, self.signal_end, self.suite_end),
            asyncio.get_running_loop().time,
            os.killpg,
            self.ledger,
            self.first,
        )

    async def ready(self) -> int:
        end = min(asyncio.get_running_loop().time() + FIVE, self.suite_end)
        async with asyncio.timeout_at(end):
            await self.ready_event.wait()
        if self.first is not None:
            raise self.first
        require(self.ready_port is not None, "ready_missing")
        return self.ready_port

    async def wait(self, end: float) -> int:
        require(self.process is not None, "child_missing")
        async with asyncio.timeout_at(min(end, self.suite_end)):
            code = await self.process.wait()
            results = await asyncio.gather(*self.readers, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                self.first = preserve(self.first, result)
        require(all(self.eof), "capture_incomplete")
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            self.settled = True
        else:
            raise DiagnosticError("group_residual")
        if self.first is not None:
            raise self.first
        return code

    async def close(self, end: float, expect_zero: bool = True) -> None:
        self.signal_end = min(self.signal_end, end)
        first: BaseException | None = None
        if not self.attempted:
            return
        if self.process is None:
            self.ledger.exposure_unknown = True
            raise DiagnosticError("child_acquisition_unknown")
        if not self.settled:
            if self.process.returncode is None:
                self.signal(signal.SIGTERM, end)
            first = self.first
            try:
                code = await self.wait(min(asyncio.get_running_loop().time() + 2, end))
                require(not expect_zero or code == 0, "child_exit")
            except BaseException as error:
                first = preserve(first, error)
                self.signal(signal.SIGKILL, end)
                if self.first is not None:
                    first = preserve(first, self.first)
                try:
                    await self.wait(end)
                except BaseException as secondary:
                    first = preserve(first, secondary)
        if not self.settled or not all(self.eof):
            self.ledger.closure_unknown = True
            first = preserve(first, DiagnosticError("child_closure_unknown"))
        if self.ready_expected:
            try:
                ready_decode(bytes(self.stdout))
                labels = bytes(self.stderr).splitlines(keepends=True)
                require(
                    len(labels) <= 16
                    and all(
                        line in (b"startup_failed\n", b"transport_failed\n", b"cleanup_failed\n") for line in labels
                    ),
                    "stderr_shape",
                )
                require(not labels, "example_reported_failure")
            except BaseException as error:
                first = preserve(first, error)
        if first is not None:
            raise first


def unique_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            require(key not in result, "json_duplicate")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise DiagnosticError("json_constant")

    return json.loads(raw.decode("ascii", errors="strict"), object_pairs_hook=pairs, parse_constant=constant)


def ready_decode(raw: bytes) -> int:
    require(0 < len(raw) <= 256 and raw.endswith(b"\n") and raw.count(b"\n") == 1, "ready_frame")
    value = unique_json(raw[:-1])
    require(type(value) is dict and set(value) == {"schema", "port"}, "ready_keys")
    require(value["schema"] == "bifrost.test.m1-transport-ready/v1", "ready_schema")
    require(type(value["port"]) is int and 1 <= value["port"] <= 65535, "ready_port")
    require(json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n" == raw, "ready_compact")
    return value["port"]


def binary_path(raw: str) -> str:
    require(raw.isascii() and 0 < len(raw) <= 512 and not any(char in raw for char in "\0\r\n"), "binary_path")
    path = Path(raw)
    require(path.is_absolute() and stat.S_ISREG(path.lstat().st_mode), "binary_regular")
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), "binary_ancestor")
    require(os.access(path, os.X_OK), "binary_executable")
    # Actual Cargo compiler-artifact/source/immutable mount association belongs to the future parent venue.
    return str(path)


def child_environment(directory: Path) -> dict[str, str]:
    # No ambient credential, proxy, HOME, certificate, loader or arbitrary product environment.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "M1_PROTOCOL_FIXTURE_DIR": str(directory)}


def client_context(fixtures: Fixtures, client: str | None = "client-a", ca: str = "ca.pem") -> ssl.SSLContext:
    fixtures.stable_directory()
    assert fixtures.path is not None
    context = ssl.create_default_context(cafile=str(fixtures.path / ca))
    require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname, "ssl_context")
    if client is not None:
        context.load_cert_chain(str(fixtures.path / f"{client}.pem"), str(fixtures.path / f"{client}-key.pem"))
    return context


class HttpClients:
    def __init__(self, ledger: Ledger, suite_end: float) -> None:
        self.ledger = ledger
        self.suite_end = suite_end
        self.clients: list[httpx.AsyncClient] = []
        self.responses: list[httpx.Response] = []

    def acquire(self, context: ssl.SSLContext) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(verify=context, retries=0, trust_env=False)
        client = httpx.AsyncClient(
            verify=context, transport=transport, trust_env=False, follow_redirects=False, timeout=FIVE
        )
        self.clients.append(client)
        return client

    async def request(self, client: httpx.AsyncClient, origin: str, path: str, body: bytes) -> tuple[int, bytes]:
        end = min(asyncio.get_running_loop().time() + FIVE, self.suite_end)
        first: BaseException | None = None
        result: tuple[int, bytes] | None = None
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout_at(end):
                request = client.build_request(
                    "POST", origin + path, content=body, headers={"content-type": "application/json"}
                )
                response = await client.send(request, stream=True)
                self.responses.append(response)  # Retain before status, length or body validation.
                collected = bytearray()
                count = 0
                async for chunk in response.aiter_raw(chunk_size=256):
                    require(asyncio.get_running_loop().time() < end, "request_deadline")
                    count += len(chunk)
                    require(count <= BODY_LIMIT, "response_bound")
                    collected.extend(chunk)
                require(asyncio.get_running_loop().time() < end, "request_deadline")
                require(response.status_code < 300 or response.status_code >= 400, "redirect_denied")
                result = (response.status_code, bytes(collected))
        except BaseException as error:
            first = error
        finally:
            # Each Python request uses one owned client; native tests separately prove reuse.
            for resource in (response, client) if response is not None else (client,):
                try:
                    async with asyncio.timeout_at(end):
                        await resource.aclose()
                except BaseException as error:
                    self.ledger.closure_unknown = True
                    first = preserve(first, error)
        if first is not None:
            raise first
        require(result is not None, "response_missing")
        return result

    async def close(self, end: float) -> None:
        first: BaseException | None = None
        for resource in (*self.responses, *self.clients):
            try:
                async with asyncio.timeout_at(min(end, self.suite_end)):
                    await resource.aclose()
            except BaseException as error:
                self.ledger.closure_unknown = True
                first = preserve(first, error)
        if first is not None:
            raise first


class Responder:
    """In-process TLS negative fixture only; never substitutes Core acceptance."""

    def __init__(self, ledger: Ledger, suite_end: float, context: ssl.SSLContext, mode: str) -> None:
        self.ledger = ledger
        self.suite_end = suite_end
        self.context = context
        self.mode = mode
        self.server: asyncio.Server | None = None
        self.tasks: list[asyncio.Task[Any]] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.received = 0
        self.first: BaseException | None = None
        self.origin: str | None = None

    async def start(self) -> str:
        require(self.mode in ("redirect", "oversize"), "fixture_mode")
        self.server = await asyncio.start_server(
            self.connection,
            "127.0.0.1",
            0,
            ssl=self.context,
            ssl_handshake_timeout=FIVE,
        )
        require(self.server.sockets is not None and len(self.server.sockets) == 1, "fixture_socket")
        address = self.server.sockets[0].getsockname()
        require(address[0] == "127.0.0.1" and type(address[1]) is int and 1 <= address[1] <= 65535, "fixture_origin")
        self.origin = f"https://127.0.0.1:{address[1]}"
        return self.origin

    async def connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.tasks.append(task)
        self.writers.append(writer)
        first: BaseException | None = None
        end = min(asyncio.get_running_loop().time() + FIVE, self.suite_end)
        try:
            require(len(self.tasks) <= 8 and len(self.writers) <= 8, "fixture_connection_count")
            async with asyncio.timeout_at(end):
                raw = await reader.read(4096)
                require(bool(raw), "fixture_request_missing")
                self.received += 1
                if self.mode == "redirect":
                    require(self.origin is not None, "fixture_origin_missing")
                    response = (
                        f"HTTP/1.1 302 Found\r\nLocation: {self.origin}/second\r\n"
                        "Content-Length: 0\r\nConnection: close\r\n\r\n"
                    ).encode("ascii")
                else:
                    response = b"HTTP/1.1 200 OK\r\nContent-Length: 1025\r\nConnection: close\r\n\r\n" + b"a" * 1025
                writer.write(response)
                await writer.drain()
        except BaseException as error:
            first = error
        finally:
            first = await close_owned(writer, self.ledger, end, first)
            if first is not None:
                self.first = preserve(self.first, first)

    async def close(self, end: float) -> None:
        first = self.first
        if self.server is not None:
            first = await close_owned(self.server, self.ledger, end, first)
        for writer in self.writers:
            first = await close_owned(writer, self.ledger, end, first)
        for task in self.tasks:
            first = await settle_owned_task(task, self.ledger, end, first)
        if self.first is not None:
            first = preserve(first, self.first)
        if first is not None:
            raise first


def native_results(child: Child, expected: tuple[str, ...]) -> None:
    require(not child.stderr, "native_stderr")
    raw = bytes(child.stdout)
    require(raw.isascii(), "native_output_ascii")
    lines = raw.decode("ascii").splitlines()
    completed = [
        line.removeprefix("test ").removesuffix(" ... ok")
        for line in lines
        if line.startswith("test tests::") and line.endswith(" ... ok")
    ]
    require(len(completed) == len(set(completed)) and set(completed) == set(expected), "native_test_membership")
    require(all(name in NATIVE_TESTS for name in completed), "native_unknown_test")
    summaries = [line for line in lines if line.startswith("test result:")]
    require(len(summaries) == 1, "native_summary_count")
    prefix = f"test result: ok. {len(expected)} passed; 0 failed; 0 ignored; 0 measured; {len(NATIVE_TESTS) - len(expected)} filtered out;"
    require(summaries[0].startswith(prefix), "native_summary")
    require(not any(" ... FAILED" in line or " ... ignored" in line for line in lines), "native_failure")


def probe_body() -> bytes:
    return json.dumps(
        {"schema": "bifrost.test.m1-transport-probe/v1", "request_id": "a" * 64}, separators=(",", ":")
    ).encode("ascii")


def probe_response(raw: bytes, operation: str) -> None:
    value = unique_json(raw)
    require(type(value) is dict and set(value) == {"schema", "operation", "request_id"}, "response_keys")
    require(
        value
        == {"schema": "bifrost.test.m1-transport-probe-response/v1", "operation": operation, "request_id": "a" * 64},
        "response_identity",
    )


async def expected_transport_rejection(action: Any) -> None:
    try:
        await action
    except (httpx.TransportError, ssl.SSLError):
        return
    raise DiagnosticError("tls_negative_accepted")


async def python_cases(
    fixtures: Fixtures,
    origin: str,
    clients: HttpClients,
    responders: list[Responder],
    completed: list[str],
) -> None:
    for operation in ("workflow_probe", "agent_probe"):
        client = clients.acquire(client_context(fixtures))
        status, raw = await clients.request(client, origin, f"/_prototype/v1/{operation}", probe_body())
        require(status == 200, "probe_status")
        probe_response(raw, operation)
        completed.append(operation)
    for case, leaf in (
        ("missing_client", None),
        ("wrong_ca", "wrong-ca"),
        ("wrong_eku", "wrong-eku"),
        ("expired_leaf", "expired"),
    ):
        client = clients.acquire(client_context(fixtures, leaf))
        await expected_transport_rejection(
            clients.request(client, origin, "/_prototype/v1/workflow_probe", probe_body())
        )
        completed.append(case)
    unlisted = clients.acquire(client_context(fixtures, "unlisted"))
    status, _ = await clients.request(unlisted, origin, "/_prototype/v1/workflow_probe", probe_body())
    require(status == 403, "unlisted_status")
    completed.append("unlisted_leaf")
    client = clients.acquire(client_context(fixtures, ca="untrusted-ca.pem"))
    await expected_transport_rejection(clients.request(client, origin, "/_prototype/v1/workflow_probe", probe_body()))
    completed.append("server_trust")
    # Connect only to the actual loopback socket, with a deliberately wrong native hostname.
    port = int(origin.rsplit(":", 1)[1])
    context = client_context(fixtures)
    writer: asyncio.StreamWriter | None = None
    first: BaseException | None = None
    accepted = False
    try:
        end = min(asyncio.get_running_loop().time() + FIVE, clients.suite_end)
        async with asyncio.timeout_at(end):
            _, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname="wrong.invalid")
            accepted = True
    except ssl.SSLCertVerificationError:
        pass
    except BaseException as error:
        first = error
    finally:
        if writer is not None:
            first = await close_owned(writer, clients.ledger, end, first)
    if first is not None:
        raise first
    require(not accepted, "hostname_negative_accepted")
    completed.append("server_hostname")
    duplicate = probe_body().replace(b"{", b'{"schema":"bifrost.test.m1-transport-probe/v1",', 1)
    extra = probe_body()[:-1] + b',"extra":true}'
    for case, body, path, expected in (
        ("strict_duplicate", duplicate, "/_prototype/v1/workflow_probe", 400),
        ("strict_extra", extra, "/_prototype/v1/workflow_probe", 400),
        ("body_limit", probe_body() + b" " * 1025, "/_prototype/v1/workflow_probe", 400),
        ("unknown_route", probe_body(), "/_prototype/v1/unknown", 404),
    ):
        client = clients.acquire(client_context(fixtures))
        status, _ = await clients.request(client, origin, path, body)
        require(status == expected, "request_negative_status")
        completed.append(case)
    assert fixtures.path is not None
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(fixtures.path / "server.pem"), str(fixtures.path / "server-key.pem"))
    for mode, case, label in (
        ("redirect", "redirect_no_second_destination", "redirect_denied"),
        ("oversize", "stream_before_materialization", "response_bound"),
    ):
        responder = Responder(clients.ledger, clients.suite_end, server_context, mode)
        responders.append(responder)
        fixture_origin = await responder.start()
        client = clients.acquire(client_context(fixtures))
        try:
            await clients.request(client, fixture_origin, "/fixed", b"{}")
        except DiagnosticError as error:
            require(error.args == (label,), "fixture_wrong_rejection")
        else:
            raise DiagnosticError("fixture_negative_accepted")
        require(responder.received == 1, "fixture_retry_or_redirect")
        completed.append(case)

    async def ambient_body() -> None:
        client = clients.acquire(client_context(fixtures))
        status, raw = await clients.request(client, origin, "/_prototype/v1/agent_probe", probe_body())
        require(status == 200, "ambient_context")
        probe_response(raw, "agent_probe")
        completed.append("ambient_proxy_ca_ignored")

    await ambient_scope(os.environ, ambient_body, clients.ledger)


async def interface_controls(end: float) -> None:
    # Data-only calls to the SAME production decoders and first-error helper, no fixture authority.
    raw = b'{"schema":"bifrost.test.m1-transport-ready/v1","port":443}\n'
    require(ready_decode(raw) == 443, "control_ready_positive")
    for altered, label in (
        (raw[:-1], "ready_frame"),
        (raw + b"\n", "ready_frame"),
        (raw.replace(b"443", b"true"), "ready_port"),
        (raw.replace(b"443", b"0"), "ready_port"),
        (raw.replace(b"443", b"65536"), "ready_port"),
        (raw.replace(b"443", b"NaN"), "json_constant"),
        (raw.replace(b'{"schema"', b'{"port":443,"schema"'), "json_duplicate"),
        (raw.replace(b'"port":', b'"other":'), "ready_keys"),
        (raw.replace(b'"port":443', b'"port": 443'), "ready_compact"),
    ):
        try:
            ready_decode(altered)
        except DiagnosticError as error:
            require(error.args == (label,), "control_ready_reason")
        else:
            raise DiagnosticError("control_ready_missing_rejection")
    original = DiagnosticError("control_first")
    for secondary in (DiagnosticError("control_secondary"), SystemExit(7), KeyboardInterrupt()):
        require(preserve(original, secondary) is original, "control_first_identity")
        require(preserve(None, secondary) is secondary, "control_control_identity")

    # Inert resources exercise the actual close/result/restoration/signal compositions.
    # They never use real environment, OS signals, sockets, files, keys or native children.
    attempts: list[str] = []

    class ClosedResource:
        def __init__(
            self, name: str, failure: BaseException | None = None, await_failure: BaseException | None = None
        ) -> None:
            self.name = name
            self.failure = failure
            self.await_failure = await_failure

        def close(self) -> None:
            attempts.append(self.name + ":close")
            if self.failure is not None:
                raise self.failure

        async def wait_closed(self) -> None:
            attempts.append(self.name + ":wait")
            if self.await_failure is not None:
                raise self.await_failure

    class DoneTask:
        def __init__(self, failure: BaseException | None) -> None:
            self.failure = failure

        def done(self) -> bool:
            return True

        def result(self) -> None:
            attempts.append("task:result")
            if self.failure is not None:
                raise self.failure

    secondary = DiagnosticError("control_close_secondary")
    for original in (DiagnosticError("control_body"), SystemExit(7), KeyboardInterrupt()):
        ledger = Ledger()
        attempts.clear()
        first = await close_owned(ClosedResource("first", secondary), ledger, end, original)
        first = await close_owned(ClosedResource("second", await_failure=secondary), ledger, end, first)
        first = await settle_owned_task(DoneTask(secondary), ledger, end, first)
        require(first is original and ledger.closure_unknown, "control_closure_first")
        require(
            attempts == ["first:close", "first:wait", "second:close", "second:wait", "task:result"],
            "control_closure_independent",
        )
    ledger = Ledger()
    require(await settle_owned_task(DoneTask(secondary), ledger, end, None) is secondary, "control_done_task_observed")
    require(ledger.closure_unknown, "control_done_task_unknown")

    context: Any = None  # Never used by the inert close-only responder.
    for original in (DiagnosticError("control_responder_body"), SystemExit(7), KeyboardInterrupt()):
        attempts.clear()
        ledger = Ledger()
        try:
            raise original
        except BaseException as caught_original:
            require(caught_original is original, "control_responder_original")
            original_traceback = caught_original.__traceback__
        responder: Any = Responder(ledger, end, context, "redirect")
        responder.first = original
        responder.server = ClosedResource("server", secondary)
        responder.writers = [ClosedResource("writer1", await_failure=secondary), ClosedResource("writer2")]
        responder.tasks = [DoneTask(secondary), DoneTask(secondary)]
        try:
            await responder.close(end)
        except BaseException as error:
            if error is not original:
                raise
            traceback = error.__traceback__
            while traceback is not None and traceback is not original_traceback:
                traceback = traceback.tb_next
            require(traceback is original_traceback, "control_responder_traceback")
        else:
            raise DiagnosticError("control_responder_missing_first")
        require(ledger.closure_unknown, "control_responder_unknown")
        require(
            attempts
            == [
                "server:close",
                "server:wait",
                "writer1:close",
                "writer1:wait",
                "writer2:close",
                "writer2:wait",
                "task:result",
                "task:result",
            ],
            "control_responder_independent",
        )
    attempts.clear()
    ledger = Ledger()
    responder = Responder(ledger, end, context, "redirect")
    responder.server = ClosedResource("server")
    responder.writers = [ClosedResource("writer1"), ClosedResource("writer2")]
    responder.tasks = [DoneTask(None)]
    await responder.close(end)
    require(
        not ledger.closure_unknown
        and attempts
        == [
            "server:close",
            "server:wait",
            "writer1:close",
            "writer1:wait",
            "writer2:close",
            "writer2:wait",
            "task:result",
        ],
        "control_responder_baseline",
    )

    # One inert scoped OS binding exercises the REAL Child.signal wrapper and real loop clock.
    original_killpg = os.killpg
    first_control: BaseException | None = None
    calls: list[int] = []
    absent = False

    def inert_killpg(_pid: int, value: int) -> None:
        calls.append(value)
        if absent:
            raise ProcessLookupError

    class Process:
        pid = 101

    class CleanupChild(Child):
        async def close(self, _end: float) -> None:
            attempts.append("child:close")

    class FirstAwait:
        async def close(self, shared_end: float) -> None:
            attempts.append("first:await")
            for child in retained:
                require(child.signal_end <= shared_end, "control_shared_preawait_clip")
                child.signal(signal.SIGKILL, end)

    try:
        os.killpg = inert_killpg
        for settled, missing, expired, expected, unknown in (
            (False, False, False, [0, signal.SIGTERM], False),
            (False, False, True, [0], True),
            (False, True, True, [0], False),
            (True, False, True, [], False),
        ):
            calls.clear()
            absent = missing
            ledger = Ledger()
            child = Child(ledger, end, ready=False)
            process: Any = Process()
            child.process = process
            child.settled = settled
            child.signal_end = -1.0 if expired else end
            original = KeyboardInterrupt()
            child.first = original
            child.signal(signal.SIGTERM, end)
            require(
                child.first is original and calls == expected and ledger.closure_unknown is unknown,
                "control_child_wrapper",
            )
        absent = False
        calls.clear()
        attempts.clear()
        ledger = Ledger()
        retained = [CleanupChild(ledger, end, False), CleanupChild(ledger, end, False)]
        original = SystemExit(7)
        for child in retained:
            child.process = process
            child.first = original
        owner: Any = FirstAwait()
        result = await shutdown_owned(retained, [owner], owner, [], -1.0, original)
        require(result is original and calls == [0, 0, 0, 0] and ledger.closure_unknown, "control_shared_signal_expiry")
        require(
            attempts == ["first:await", "first:await", "child:close", "child:close"], "control_shared_cleanup_order"
        )
    except BaseException as error:
        first_control = error
    finally:
        try:
            os.killpg = original_killpg
        except BaseException as error:
            first_control = preserve(first_control, error)
    if first_control is not None:
        raise first_control
    require(os.killpg is original_killpg, "control_signal_binding_restored")

    class Environment(dict[str, str]):
        def __init__(self) -> None:
            super().__init__({key: "original-" + key for key in AMBIENT_KEYS[:3]})
            self.restoring = False
            self.restored: list[str] = []

        def __setitem__(self, key: str, value: str) -> None:
            if self.restoring:
                self.restored.append(key)
                if key == AMBIENT_KEYS[0]:
                    raise secondary
            super().__setitem__(key, value)

        def pop(self, key: str, default: Any = None) -> Any:
            if self.restoring:
                self.restored.append(key)
            return super().pop(key, default)

    for original in (DiagnosticError("control_environment_body"), SystemExit(7), KeyboardInterrupt()):
        environment = Environment()
        ledger = Ledger()

        async def body(environment: Environment = environment, original: BaseException = original) -> None:
            environment.restoring = True
            raise original

        caught: BaseException | None = None
        try:
            await ambient_scope(environment, body, ledger)
        except BaseException as error:
            if error is not original:
                raise
            caught = error
        require(caught is original and ledger.closure_unknown, "control_environment_first")
        require(environment.restored == list(AMBIENT_KEYS), "control_environment_independent")
        require(
            all(environment[key] == "original-" + key for key in AMBIENT_KEYS[1:3])
            and all(key not in environment for key in AMBIENT_KEYS[3:]),
            "control_environment_remaining",
        )

    baseline = {AMBIENT_KEYS[0]: "original", "unrelated": "untouched"}
    environment = dict(baseline)
    ledger = Ledger()
    marker = object()

    async def successful_body() -> Any:
        return marker

    require(await ambient_scope(environment, successful_body, ledger) is marker, "control_environment_success")
    require(environment == baseline and not ledger.closure_unknown, "control_environment_baseline")

    for settled, absent, now, expected_calls, expected_unknown in (
        (False, False, 5.0, [0, signal.SIGTERM], False),
        (False, False, 10.0, [0], True),
        (False, True, 10.0, [0], False),
        (True, False, 10.0, [], False),
    ):
        calls: list[int] = []

        def killpg(_pid: int, value: int, calls: list[int] = calls, absent: bool = absent) -> None:
            calls.append(value)
            if absent:
                raise ProcessLookupError

        ledger = Ledger()
        original = KeyboardInterrupt()
        first = signal_owned(101, settled, signal.SIGTERM, 10.0, lambda now=now: now, killpg, ledger, original)
        require(first is original and calls == expected_calls, "control_signal_first_calls")
        require(ledger.closure_unknown is expected_unknown, "control_signal_unknown")


async def shutdown_owned(
    children: list[Child],
    responders: list[Responder],
    clients: HttpClients,
    fixtures: list[Fixtures],
    end: float,
    first: BaseException | None,
) -> BaseException | None:
    # Clip EVERY retained capture's signal authority before the first cleanup await.
    for child in children:
        child.signal_end = min(child.signal_end, end)
    for responder in responders:
        try:
            await responder.close(end)
        except BaseException as error:
            first = preserve(first, error)
    try:
        await clients.close(end)
    except BaseException as error:
        first = preserve(first, error)
    for child in reversed(children):
        try:
            await child.close(end)
        except BaseException as error:
            first = preserve(first, error)
    for fixture in reversed(fixtures):
        try:
            fixture.cleanup(end)
        except BaseException as error:
            first = preserve(first, error)
    return first


async def run(binary: str, native_tests: str) -> None:
    loop = asyncio.get_running_loop()
    suite_end = loop.time() + SUITE
    ledger = Ledger()
    fixtures: list[Fixtures] = []
    children: list[Child] = []
    responders: list[Responder] = []
    clients = HttpClients(ledger, suite_end)
    completed: list[str] = []
    first: BaseException | None = None
    try:
        await interface_controls(suite_end)
        require(binary != native_tests and os.getuid() != 0, "nonroot_binary_venue")
        # Child1 expiry only: all binary compilation/admission must predate fixture generation.
        short = Fixtures(ledger, suite_end)
        fixtures.append(short)
        short.acquire()
        populate(short, short=True)
        assert short.path is not None
        expiry = Child(ledger, suite_end, ready=False)
        children.append(expiry)
        await expiry.launch(
            (native_tests, "--exact", NATIVE_TESTS[2], "--test-threads=1"), child_environment(short.path)
        )
        require(await expiry.wait(suite_end) == 0, "native_expiry_exit")
        native_results(expiry, (NATIVE_TESTS[2],))
        # Child2 remaining native tests, normal fixtures and exact complementary membership.
        normal = Fixtures(ledger, suite_end)
        fixtures.append(normal)
        normal.acquire()
        populate(normal, short=False)
        assert normal.path is not None
        native = Child(ledger, suite_end, ready=False)
        children.append(native)
        await native.launch(
            (native_tests, "--skip", NATIVE_TESTS[2], "--test-threads=1"), child_environment(normal.path)
        )
        require(await native.wait(suite_end) == 0, "native_remaining_exit")
        native_results(native, tuple(name for name in NATIVE_TESTS if name != NATIVE_TESTS[2]))
        # Child3 only example process; no startup of the application, database, services or Cargo.
        example = Child(ledger, suite_end, ready=True)
        children.append(example)
        await example.launch((binary, "--fixture-dir", str(normal.path)), child_environment(normal.path))
        port = await example.ready()
        origin = f"https://127.0.0.1:{port}"
        await python_cases(normal, origin, clients, responders, completed)
        require(len(completed) == len(set(completed)) and set(completed) == set(PYTHON_CASES), "python_case_membership")
    except BaseException as error:
        first = error
    finally:
        # ONE shared shutdown entry+5 clipped to suite; no reset between independent resources.
        shutdown_end = min(loop.time() + FIVE, suite_end)
        first = await shutdown_owned(children, responders, clients, fixtures, shutdown_end, first)
    if first is not None:
        raise first
    print("m1_transport_complete")


def main() -> int:
    try:
        require(len(sys.argv) == 5 and sys.argv[1] == "--binary" and sys.argv[3] == "--native-tests", "diagnostic_argv")
        binary = binary_path(sys.argv[2])
        native_tests = binary_path(sys.argv[4])
        asyncio.run(run(binary, native_tests))
    except BaseException:
        # No caught message, repr, traceback, paths, PEM, DER or ambient environment is emitted.
        print("m1_transport_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
