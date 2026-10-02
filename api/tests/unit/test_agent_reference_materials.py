"""Material generation tests; certificates do not establish runtime custody."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import ssl
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = (
    Path("/repo/scripts")
    if Path("/repo/scripts").is_dir()
    else Path(__file__).resolve().parents[3] / "scripts"
)
SPEC = importlib.util.spec_from_file_location(
    "agent_reference_materials", SCRIPTS / "prepare-agent-reference-materials.py"
)
assert SPEC and SPEC.loader
materials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(materials)
LANE = "1" * 32
CASE = "2" * 32
MARKER = "synthetic-secret-do-not-disclose"


@pytest.fixture
def context(tmp_path):
    directory = tmp_path / "owned"
    directory.mkdir(mode=0o700)
    root = str(materials.REPOSITORY_ROOT)
    owner = {
        "root": root,
        "project": "bifrost-agent-reference-"
        + hashlib.sha256(root.encode()).hexdigest()[:8],
        "source": "a" * 40,
        "lane_id": LANE,
    }
    (directory / "owner.json").write_text(json.dumps(owner))
    (directory / "owner.json").chmod(0o600)
    return directory


def _argv(context):
    return ["--context", str(context), "--lane-id", LANE, "--case-id", CASE]


def _deny_tool(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("unsafe context reached the standard tool")

    monkeypatch.setattr(materials, "_tool_identity", unexpected)


@pytest.mark.parametrize("name", materials.FILES)
def test_exclusive_preexisting_file_denies_without_changes(context, monkeypatch, name):
    _deny_tool(monkeypatch)
    existing = context / name
    existing.write_bytes(b"retained")
    before = existing.stat()
    with pytest.raises(materials.MaterialsError, match="^material_generation_failed$"):
        materials.generate(context, LANE, CASE)
    assert existing.read_bytes() == b"retained"
    assert existing.stat().st_ino == before.st_ino
    assert {path.name for path in context.iterdir()} == {"owner.json", name}


@pytest.mark.parametrize("kind", ["symlink", "fifo", "empty", "hardlink"])
def test_unsafe_existing_material_is_not_followed(context, tmp_path, monkeypatch, kind):
    _deny_tool(monkeypatch)
    target = tmp_path / "outside"
    target.write_bytes(b"outside")
    existing = context / materials.FILES[0]
    if kind == "symlink":
        existing.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(existing, 0o600)
    elif kind == "hardlink":
        os.link(target, existing)
    else:
        existing.touch(mode=0o600)
    with pytest.raises(materials.MaterialsError):
        materials.generate(context, LANE, CASE)
    assert target.read_bytes() == b"outside"


@pytest.mark.parametrize(
    "kind", ["symlink", "fifo", "hardlink", "writable", "oversize"]
)
def test_unsafe_owner_denies_before_reservation(context, tmp_path, monkeypatch, kind):
    _deny_tool(monkeypatch)
    owner = context / "owner.json"
    if kind in {"symlink", "fifo", "hardlink"}:
        outside = tmp_path / "owner-outside"
        outside.write_bytes(owner.read_bytes())
        owner.unlink()
        if kind == "symlink":
            owner.symlink_to(outside)
        elif kind == "fifo":
            os.mkfifo(owner, 0o600)
        else:
            os.link(outside, owner)
    elif kind == "writable":
        owner.chmod(0o622)
    else:
        owner.write_bytes(b"x" * 4097)
    with pytest.raises((materials.MaterialsError, OSError)):
        materials.generate(context, LANE, CASE)
    assert {path.name for path in context.iterdir()} == {"owner.json"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("root", "/wrong"),
        ("project", "wrong"),
        ("lane_id", "3" * 32),
        ("source", "A" * 40),
        ("source", True),
        ("extra", "unaccepted"),
    ],
)
def test_owner_custody_shape_is_closed(context, monkeypatch, field, value):
    _deny_tool(monkeypatch)
    owner = context / "owner.json"
    value_dict = json.loads(owner.read_bytes())
    value_dict[field] = value
    owner.write_text(json.dumps(value_dict))
    with pytest.raises(materials.MaterialsError):
        materials.generate(context, LANE, CASE)


def test_duplicate_owner_key_is_rejected(context, monkeypatch):
    _deny_tool(monkeypatch)
    owner = context / "owner.json"
    owner.write_bytes(owner.read_bytes()[:-1] + b',"lane_id":"' + LANE.encode() + b'"}')
    with pytest.raises(materials.MaterialsError):
        materials.generate(context, LANE, CASE)


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o600])
def test_context_requires_private_directory_mode(context, monkeypatch, mode):
    _deny_tool(monkeypatch)
    context.chmod(mode)
    with pytest.raises(materials.MaterialsError):
        materials.generate(context, LANE, CASE)


def test_context_requires_current_owner(context, monkeypatch):
    _deny_tool(monkeypatch)
    actual_uid = os.getuid()
    monkeypatch.setattr(materials.os, "getuid", lambda: actual_uid + 1)
    with pytest.raises(materials.MaterialsError):
        materials.generate(context, LANE, CASE)


def test_context_symlink_in_any_component_denies(context, tmp_path, monkeypatch):
    _deny_tool(monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(context.parent, target_is_directory=True)
    with pytest.raises(OSError):
        materials.generate(alias / context.name, LANE, CASE)
    direct = tmp_path / "direct"
    direct.symlink_to(context, target_is_directory=True)
    with pytest.raises(OSError):
        materials.generate(direct, LANE, CASE)


@pytest.mark.parametrize(
    "lane,case", [("A" * 32, CASE), (LANE, "2" * 31), (True, CASE)]
)
def test_identifiers_are_exact(context, monkeypatch, lane, case):
    _deny_tool(monkeypatch)
    with pytest.raises(materials.MaterialsError):
        materials.generate(context, lane, case)


def _version_only(monkeypatch):
    identity = SimpleNamespace(st_dev=1, st_ino=2)
    monkeypatch.setattr(materials, "_tool_identity", lambda: identity)
    calls = []

    def tool(arguments, descriptors, received_identity):
        assert received_identity is identity
        assert arguments == ["version"] and descriptors == ()
        calls.append(arguments)
        return b"OpenSSL 3.0.13 30 Jan 2024\n"

    monkeypatch.setattr(materials, "_openssl", tool)
    return calls


def test_secret_collision_fails_without_resampling_or_retry(
    context, monkeypatch, capsys
):
    calls = _version_only(monkeypatch)
    random_calls = []

    def same_secret(size):
        random_calls.append(size)
        return "b" * 64

    monkeypatch.setattr(materials.secrets, "token_hex", same_secret)
    assert materials.main(_argv(context)) == 1
    assert random_calls == [32] * 5
    assert calls == [["version"]]
    assert all((context / name).stat().st_size == 0 for name in materials.FILES)
    inodes = [(context / name).stat().st_ino for name in materials.FILES]
    assert materials.main(_argv(context)) == 1
    assert calls == [["version"]] and random_calls == [32] * 5
    assert inodes == [(context / name).stat().st_ino for name in materials.FILES]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == materials.FAILURE * 2


@pytest.mark.parametrize(
    "version",
    [b"LibreSSL 3.0", b"OpenSSL 1.1.1", b"OpenSSL 3.0.13\n" + MARKER.encode()],
)
def test_unsupported_tool_version_has_static_cli_diagnostic(
    context, monkeypatch, capsys, version
):
    _version_only(monkeypatch)
    monkeypatch.setattr(materials, "_openssl", lambda *_args: version)
    assert materials.main(_argv(context)) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == materials.FAILURE
    assert MARKER not in captured.err


def test_missing_tool_is_static_and_partial_context_cannot_retry(
    context, monkeypatch, capsys
):
    def missing():
        raise FileNotFoundError(MARKER)

    monkeypatch.setattr(materials, "_tool_identity", missing)
    assert materials.main(_argv(context)) == 1
    assert all((context / name).is_file() for name in materials.FILES)
    _deny_tool(monkeypatch)
    assert materials.main(_argv(context)) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == materials.FAILURE * 2


def test_subprocess_failure_strips_ambient_environment(context, monkeypatch, capsys):
    identity = SimpleNamespace(st_dev=1, st_ino=2)
    monkeypatch.setattr(materials, "_tool_identity", lambda: identity)
    monkeypatch.setenv("AMBIENT_SECRET", MARKER)
    invocations = []

    def failed(argv, **kwargs):
        invocations.append((argv, kwargs))
        raise subprocess.TimeoutExpired(MARKER, 10, output=MARKER)

    monkeypatch.setattr(materials.subprocess, "Popen", failed)
    assert materials.main(_argv(context)) == 1
    argv, kwargs = invocations[0]
    assert argv == ["/usr/bin/openssl", "version"]
    assert kwargs["env"] == {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "OPENSSL_CONF": "/dev/null",
    }
    assert (
        kwargs["stdin"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
    )
    assert (
        kwargs["pass_fds"] == () and kwargs["close_fds"] and kwargs["start_new_session"]
    )
    assert materials.STAGE_SECONDS == 10 and materials.MAX_TOOL_OUTPUT == 8192
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == materials.FAILURE


def test_later_tool_failure_retains_partial_secrets_without_exposing_them(
    context, monkeypatch, capsys
):
    _version_only(monkeypatch)
    values = [str(number) * 64 for number in range(3, 8)]
    remaining = iter(values)
    monkeypatch.setattr(materials.secrets, "token_hex", lambda _size: next(remaining))
    calls = []

    def failed(arguments, descriptors, _identity):
        calls.append((arguments, descriptors))
        if arguments == ["version"]:
            return b"OpenSSL 3.0.13 30 Jan 2024\n"
        raise ValueError(values[0] + MARKER)

    monkeypatch.setattr(materials, "_openssl", failed)
    assert materials.main(_argv(context)) == 1
    assert len(calls) == 2
    assert calls[1][0][:3] == ["genpkey", "-algorithm", "EC"]
    assert len(calls[1][1]) == 1
    assert all(value not in json.dumps(calls) for value in values)
    assert (context / "observer-ingest-key").read_text() == values[0]
    assert (context / "observer-server-key.pem").stat().st_size == 0
    assert materials.main(_argv(context)) == 1
    assert len(calls) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == materials.FAILURE * 2


@pytest.mark.parametrize("failure", ["oversize", "deadline", "nonzero"])
def test_tool_stage_bounds_kill_and_close_without_raw_diagnostics(monkeypatch, failure):
    identity = SimpleNamespace(st_dev=1, st_ino=2)
    monkeypatch.setattr(materials, "_tool_identity", lambda: identity)
    stream = SimpleNamespace(fileno=lambda: 42, close=lambda: closed.append(True))
    closed, killed, waits = [], [], []
    state = {"returncode": None}

    def wait(timeout):
        waits.append(timeout)
        state["returncode"] = 1 if failure == "nonzero" else -9
        return state["returncode"]

    process = SimpleNamespace(
        pid=4242,
        stdout=stream,
        poll=lambda: state["returncode"],
        wait=wait,
    )
    monkeypatch.setattr(
        materials.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(
        materials.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )

    class Selector:
        def __init__(self):
            self.active = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def register(self, *_args):
            return None

        def get_map(self):
            return {42: True} if self.active else {}

        def select(self, timeout):
            assert 0 < timeout <= materials.STAGE_SECONDS
            return [] if failure == "deadline" else [(42, True)]

        def unregister(self, _stream):
            self.active = False

    monkeypatch.setattr(materials.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(
        materials.os,
        "read",
        lambda _fd, size: b"x" * size if failure == "oversize" else b"",
    )
    with pytest.raises(materials.MaterialsError, match="^material_generation_failed$"):
        materials._openssl(["version"], (), identity)
    assert closed == [True]
    assert len(waits) == 1 and 0 <= waits[0] <= materials.STAGE_SECONDS
    assert killed == (
        [] if failure == "nonzero" else [(4242, materials.signal.SIGKILL)]
    )


@pytest.mark.parametrize("extra", [["--unknown", MARKER], ["--lane-id", MARKER]])
def test_closed_cli_does_not_echo_unknown_or_duplicate_input(context, capsys, extra):
    assert materials.main(_argv(context) + extra) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == materials.FAILURE
    assert not any((context / name).exists() for name in materials.FILES)


def _public_tool(*arguments):
    completed = subprocess.run(
        [str(materials.OPENSSL), *map(str, arguments)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "OPENSSL_CONF": "/dev/null"},
        check=False,
    )
    assert completed.returncode == 0, "standard public certificate verification failed"
    return completed.stdout


def test_real_standard_tool_creates_exact_fresh_certificates_and_safe_metadata(
    context, capsys
):
    # Mandatory supported-lane proof: unavailable/unsupported OpenSSL fails, never skips.
    assert materials.main(_argv(context)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    metadata = json.loads(captured.out)
    assert set(metadata) == {
        "schema",
        "files",
        "public_certificate_fingerprints",
        "openssl",
    }
    assert metadata["schema"] == "bifrost.agent-reference.materials/v1"
    assert {entry["name"] for entry in metadata["files"]} == set(materials.FILES)
    assert metadata["openssl"]["path"] == "/usr/bin/openssl"
    assert set(metadata["openssl"]) == {"path", "version", "device", "inode"}
    for entry in metadata["files"]:
        assert set(entry) == {"name", "mode", "uid", "gid", "device", "inode"}
        found = (context / entry["name"]).lstat()
        assert stat.S_ISREG(found.st_mode) and found.st_nlink == 1
        assert stat.S_IMODE(found.st_mode) == 0o600 and found.st_uid == os.getuid()
        assert entry == {
            "name": entry["name"],
            "mode": "0600",
            "uid": found.st_uid,
            "gid": found.st_gid,
            "device": found.st_dev,
            "inode": found.st_ino,
        }
    model = materials.wire.decode_private(
        "model_input", (context / "model-oracle-input.json").read_bytes()
    )
    assert model["lane_id"] == LANE and model["case_id"] == CASE
    secret_values = [(context / name).read_text() for name in materials.FILES[:2]]
    secret_values += [model[key] for key in ("model_key", "password", "visa")]
    assert len(set(secret_values)) == 5
    assert all(
        len(value) == 64 and all(char in "0123456789abcdef" for char in value)
        for value in secret_values
    )
    assert all(value not in captured.out for value in secret_values)
    assert "PRIVATE KEY" not in captured.out
    ca, leaf = context / "observer-ca.pem", context / "observer-server.pem"
    text = _public_tool("x509", "-in", leaf, "-text", "-noout").decode("ascii")
    assert "ecdsa-with-SHA256" in text and "ASN1 OID: prime256v1" in text
    assert "CA:FALSE" in text and "TLS Web Server Authentication" in text
    san = _public_tool("x509", "-in", leaf, "-ext", "subjectAltName", "-noout").decode(
        "ascii"
    )
    assert san.splitlines()[1].strip() == "DNS:scheduler-fixtures, IP Address:127.0.0.1"
    ca_text = _public_tool("x509", "-in", ca, "-text", "-noout").decode("ascii")
    assert "CA:TRUE, pathlen:0" in ca_text and "ASN1 OID: prime256v1" in ca_text
    assert "ecdsa-with-SHA256" in ca_text
    for certificate in (ca, leaf):
        dates = (
            _public_tool("x509", "-in", certificate, "-dates", "-noout")
            .decode("ascii")
            .splitlines()
        )
        parsed = [
            datetime.strptime(line.split("=", 1)[1], "%b %d %H:%M:%S %Y GMT").replace(
                tzinfo=UTC
            )
            for line in dates
        ]
        assert parsed[1] - parsed[0] == timedelta(days=1)
        der = ssl.PEM_cert_to_DER_cert(certificate.read_text())
        assert (
            metadata["public_certificate_fingerprints"][certificate.name]
            == "sha256:" + hashlib.sha256(der).hexdigest()
        )
    _public_tool(
        "verify",
        "-CAfile",
        ca,
        "-purpose",
        "sslserver",
        "-verify_hostname",
        "scheduler-fixtures",
        leaf,
    )
    _public_tool("verify", "-CAfile", ca, "-verify_ip", "127.0.0.1", leaf)
    # Compare key public portions only; never emit or fingerprint private material.
    for key_name, certificate in (
        ("observer-ca-key.pem", ca),
        ("observer-server-key.pem", leaf),
    ):
        key_public = _public_tool("pkey", "-in", context / key_name, "-pubout")
        cert_public = _public_tool("x509", "-in", certificate, "-pubkey", "-noout")
        assert key_public == cert_public
    retained = {name: (context / name).read_bytes() for name in materials.FILES}
    assert materials.main(_argv(context)) == 1
    assert {name: (context / name).read_bytes() for name in materials.FILES} == retained
    retry = capsys.readouterr()
    assert retry.out == "" and retry.err == materials.FAILURE
