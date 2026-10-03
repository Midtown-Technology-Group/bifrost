from __future__ import annotations

import asyncio
import errno
import io
import json
import os
import traceback
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import (
    BaseHandler,
    HTTPSHandler,
    OpenerDirector,
    Request,
    build_opener,
)
from urllib.response import addinfourl

import pytest

from scripts import check_github_action_pins


# Synthetic, nonsecret credentials; never use the supported job's real token.
SYNTHETIC_TOKEN = "synthetic-action-check-token"
TOKEN_FILE_ENV = "BIFROST_ACTION_PIN_TOKEN_FILE"


@pytest.fixture(autouse=True)
def isolated_credential_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv(TOKEN_FILE_ENV, raising=False)


def _token_file(tmp_path: Path, data: bytes = SYNTHETIC_TOKEN.encode()) -> Path:
    path = tmp_path / "credential"
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def _metadata_response(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> list[Request]:
    requests: list[Request] = []

    def respond(request: Request, *, timeout: int) -> io.BytesIO:
        assert timeout == 15
        requests.append(request)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(check_github_action_pins, "urlopen", respond)
    monkeypatch.setattr(
        check_github_action_pins,
        "build_opener",
        lambda *handlers: SimpleNamespace(open=respond),
    )
    return requests


def test_flags_external_actions_without_full_sha(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "\n".join(
            [
                "steps:",
                "  - uses: actions/checkout@v6",
                "  - uses: owner/action@main",
                "  - uses: owner/no-ref",
            ]
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert [violation.action for violation in violations] == [
        "actions/checkout@v6",
        "owner/action@main",
        "owner/no-ref",
    ]


def test_allows_sha_pinned_local_and_docker_actions(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "\n".join(
            [
                "steps:",
                "  - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
                "  - uses: ./.github/actions/local-action",
                "  - uses: docker://ghcr.io/example/image:latest",
            ]
        ),
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_unpinned_actions([workflow])

    assert violations == []


def test_flags_sha_that_does_not_match_version_comment(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "steps:\n"
        "  - uses: owner/action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.2.3\n",
        encoding="utf-8",
    )

    violations = check_github_action_pins.find_mismatched_action_versions(
        [workflow],
        lambda repository, version: "b" * 40,
    )

    assert len(violations) == 1
    assert "owner/action@v1.2.3 resolves to" in violations[0].reason


def test_allows_sha_matching_version_comment_and_caches_resolution(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "workflow.yml"
    sha = "a" * 40
    workflow.write_text(
        "steps:\n"
        f"  - uses: owner/action@{sha} # v1.2.3\n"
        f"  - uses: owner/action/subpath@{sha} # v1.2.3\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def resolve(repository: str, version: str) -> str:
        calls.append((repository, version))
        return sha

    violations = check_github_action_pins.find_mismatched_action_versions(
        [workflow], resolve
    )

    assert violations == []
    assert calls == [("owner/action", "v1.2.3")]


@pytest.mark.parametrize("channel", ["anonymous", "standard", "file"])
def test_metadata_request_preserves_origin_and_authentication(
    channel: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if channel == "standard":
        monkeypatch.setenv("GITHUB_TOKEN", SYNTHETIC_TOKEN)
    elif channel == "file":
        monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    requests = _metadata_response(monkeypatch, {"sha": "a" * 40})

    assert check_github_action_pins.resolve_github_action_version(
        "owner/action", "v1.2.3"
    ) == ("a" * 40)
    assert len(requests) == 1
    assert (
        requests[0].full_url
        == "https://api.github.com/repos/owner/action/commits/v1.2.3"
    )
    authorized = requests[0].get_header("Authorization") == f"Bearer {SYNTHETIC_TOKEN}"
    assert authorized is (channel != "anonymous")


def test_safe_file_opens_with_guard_flags_and_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _token_file(tmp_path)
    monkeypatch.setenv(TOKEN_FILE_ENV, str(path))
    real_open = os.open
    opened: list[tuple[int, int]] = []

    def capture_open(path: str, flags: int) -> int:
        descriptor = real_open(path, flags)
        opened.append((descriptor, flags))
        return descriptor

    monkeypatch.setattr(os, "open", capture_open)
    matches = check_github_action_pins._github_action_token() == SYNTHETIC_TOKEN
    assert matches
    assert len(opened) == 1
    descriptor, flags = opened[0]
    required = os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    assert flags & required == required
    with pytest.raises(OSError) as closed:
        os.fstat(descriptor)
    assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "directory",
        "fifo",
        "special_file",
        "symlink",
        "hardlink",
        "empty",
        "oversize",
        "space",
        "newline",
        "tab",
        "non_ascii",
        "nul",
        "delete_character",
        "empty_pointer",
    ],
)
def test_unsafe_file_never_falls_back_or_requests_metadata(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "credential"
    if kind == "directory":
        path.mkdir(mode=0o700)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "special_file":
        # Linux supported lane: the reader rejects this device before any read.
        path = Path("/dev/null")
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(SYNTHETIC_TOKEN.encode())
        target.chmod(0o600)
        path.symlink_to(target)
    elif kind == "hardlink":
        _token_file(tmp_path)
        os.link(path, tmp_path / "second_link")
    elif kind not in {"missing", "empty_pointer"}:
        data = {
            "empty": b"",
            "oversize": b"S" * 4097,
            "space": SYNTHETIC_TOKEN.encode() + b" ",
            "newline": SYNTHETIC_TOKEN.encode() + b"\n",
            "tab": SYNTHETIC_TOKEN.encode() + b"\t",
            "non_ascii": SYNTHETIC_TOKEN.encode() + b"\x80",
            "nul": SYNTHETIC_TOKEN.encode() + b"\x00",
            "delete_character": SYNTHETIC_TOKEN.encode() + b"\x7f",
        }[kind]
        _token_file(tmp_path, data)
    monkeypatch.setenv(TOKEN_FILE_ENV, "" if kind == "empty_pointer" else str(path))

    def forbidden_transport(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid credential input attempted a metadata request")

    monkeypatch.setattr(check_github_action_pins, "urlopen", forbidden_transport)
    monkeypatch.setattr(check_github_action_pins, "build_opener", forbidden_transport)
    with pytest.raises(RuntimeError) as rejected:
        check_github_action_pins.resolve_github_action_version("owner/action", "v1.2.3")
    assert str(rejected.value) == "Invalid GitHub action credential file"
    assert SYNTHETIC_TOKEN not in "".join(traceback.format_exception(rejected.value))
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o644, 0o1600])
def test_file_requires_exact_private_mode(
    mode: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _token_file(tmp_path)
    path.chmod(mode)
    monkeypatch.setenv(TOKEN_FILE_ENV, str(path))
    with pytest.raises(RuntimeError, match="^Invalid GitHub action credential file$"):
        check_github_action_pins._github_action_token()


def test_file_owner_is_checked_on_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    real_fstat = os.fstat

    def foreign_owner(descriptor: int) -> os.stat_result:
        # Synthetic identity seam: an unprivileged runner cannot chown this file.
        fields = list(real_fstat(descriptor))
        fields[4] = os.geteuid() + 1
        return os.stat_result(fields)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", foreign_owner)
        with pytest.raises(
            RuntimeError, match="^Invalid GitHub action credential file$"
        ):
            check_github_action_pins._github_action_token()


def test_file_token_accepts_exact_byte_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path, b"S" * 4096)))
    token = check_github_action_pins._github_action_token()
    assert token is not None and len(token) == 4096 and set(token) == {"S"}


def test_actual_reads_are_bounded_if_file_grows_after_fstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _token_file(tmp_path)
    monkeypatch.setenv(TOKEN_FILE_ENV, str(path))
    real_read = os.read
    reads: list[int] = []

    def growing_file(descriptor: int, count: int) -> bytes:
        reads.append(count)
        if len(reads) == 1:
            with path.open("ab") as stream:
                stream.write(b"S" * 4097)
        return real_read(descriptor, count)

    monkeypatch.setattr(os, "read", growing_file)
    with pytest.raises(RuntimeError, match="^Invalid GitHub action credential file$"):
        check_github_action_pins._github_action_token()
    assert reads == [4097]


def test_bounded_reader_handles_short_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    real_read = os.read

    def short_read(descriptor: int, count: int) -> bytes:
        return real_read(descriptor, min(count, 3))

    monkeypatch.setattr(os, "read", short_read)
    matches = check_github_action_pins._github_action_token() == SYNTHETIC_TOKEN
    assert matches


def test_duplicate_credentials_are_rejected_before_file_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SYNTHETIC_TOKEN)
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))

    def forbidden_open(*args: object, **kwargs: object) -> None:
        pytest.fail("Conflicting credentials opened a file")

    monkeypatch.setattr(os, "open", forbidden_open)
    with pytest.raises(
        RuntimeError, match="^Conflicting GitHub action credential inputs$"
    ):
        check_github_action_pins._github_action_token()


def test_empty_standard_token_does_not_conflict_with_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    matches = check_github_action_pins._github_action_token() == SYNTHETIC_TOKEN
    assert matches


@pytest.mark.parametrize("fault", ["fstat", "read", "cancel"])
def test_descriptor_closes_on_validation_io_error_or_control_exception(
    fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    real_open = os.open
    real_fstat = os.fstat
    opened: list[int] = []
    error = (
        asyncio.CancelledError("synthetic control")
        if fault == "cancel"
        else OSError(SYNTHETIC_TOKEN)
    )

    def capture_open(path: str, flags: int) -> int:
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        return descriptor

    def fail(*args: object) -> None:
        raise error

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(os, "fstat" if fault == "fstat" else "read", fail)
    with pytest.raises(
        asyncio.CancelledError if fault == "cancel" else RuntimeError
    ) as rejected:
        check_github_action_pins._github_action_token()
    if fault == "cancel":
        assert rejected.value is error
    else:
        assert str(rejected.value) == "Invalid GitHub action credential file"
        assert SYNTHETIC_TOKEN not in "".join(
            traceback.format_exception(rejected.value)
        )
    assert len(opened) == 1
    with pytest.raises(OSError) as closed:
        real_fstat(opened[0])
    assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize("control", ["cancel", "interrupt", "exit"])
def test_original_control_survives_close_failure_after_real_descriptor_disposal(
    control: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    original = {
        "cancel": asyncio.CancelledError("synthetic control"),
        "interrupt": KeyboardInterrupt("synthetic control"),
        "exit": SystemExit("synthetic control"),
    }[control]
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    opened: list[int] = []
    closed: list[int] = []

    def capture_open(path: str, flags: int) -> int:
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        return descriptor

    def fail_read(*args: object) -> None:
        raise original

    def close_then_fail(descriptor: int) -> None:
        # Genuine descriptor disposal precedes a synthetic cleanup failure.
        real_close(descriptor)
        closed.append(descriptor)
        raise OSError(SYNTHETIC_TOKEN)

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(os, "read", fail_read)
    monkeypatch.setattr(os, "close", close_then_fail)
    # Direct call only: no task/thread lets these controls escape the assertion.
    with pytest.raises(type(original)) as rejected:
        check_github_action_pins._github_action_token()
    assert rejected.value is original
    assert len(opened) == 1 and closed == opened
    with pytest.raises(OSError) as disposed:
        real_fstat(opened[0])
    assert disposed.value.errno == errno.EBADF


def test_successful_read_close_failure_remains_nonzero_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    real_close = os.close
    real_fstat = os.fstat
    closed: list[int] = []

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        closed.append(descriptor)
        raise OSError(SYNTHETIC_TOKEN)

    monkeypatch.setattr(os, "close", close_then_fail)
    with pytest.raises(
        RuntimeError, match="^Cannot close GitHub action credential file$"
    ) as rejected:
        check_github_action_pins._github_action_token()
    assert SYNTHETIC_TOKEN not in "".join(traceback.format_exception(rejected.value))
    assert len(closed) == 1
    with pytest.raises(OSError) as disposed:
        real_fstat(closed[0])
    assert disposed.value.errno == errno.EBADF


@pytest.mark.parametrize("kind", ["header", "network", "http"])
def test_authenticated_transport_errors_do_not_disclose_credentials(
    kind: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SYNTHETIC_TOKEN)
    errors = {
        "header": ValueError(SYNTHETIC_TOKEN),
        "network": URLError(SYNTHETIC_TOKEN),
        "http": HTTPError(
            "https://api.github.com/", 401, SYNTHETIC_TOKEN, Message(), None
        ),
    }

    def fail(*args: object, **kwargs: object) -> None:
        raise errors[kind]

    monkeypatch.setattr(check_github_action_pins, "build_opener", fail)
    with pytest.raises(RuntimeError) as rejected:
        check_github_action_pins.resolve_github_action_version("owner/action", "v1.2.3")
    assert SYNTHETIC_TOKEN not in "".join(traceback.format_exception(rejected.value))
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


@pytest.mark.parametrize(
    "sha", [None, 1, "a" * 39, "g" * 40], ids=["null", "number", "short", "nonhex"]
)
def test_metadata_malformed_commit_sha_still_fails(
    sha: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _metadata_response(monkeypatch, {"sha": sha})
    with pytest.raises(ValueError, match="^GitHub returned no full commit SHA$"):
        check_github_action_pins.resolve_github_action_version("owner/action", "v1.2.3")


@pytest.mark.parametrize("channel", ["standard", "file"])
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("destination", ["same_origin", "foreign_origin"])
def test_authenticated_redirects_use_real_urllib_policy_without_second_request(
    channel: str,
    code: int,
    destination: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if channel == "standard":
        monkeypatch.setenv("GITHUB_TOKEN", SYNTHETIC_TOKEN)
    else:
        monkeypatch.setenv(TOKEN_FILE_ENV, str(_token_file(tmp_path)))
    requests: list[Request] = []
    bodies: list[io.BytesIO] = []

    class SyntheticRedirectTransport(HTTPSHandler):
        # Pure transport seam; actual urllib error/redirect handlers run unchanged.
        # No network request or runtime/authentication equivalence is claimed.
        def https_open(self, request: Request) -> addinfourl:
            requests.append(request)
            assert len(requests) == 1, "Redirect attempted a second request"
            headers = Message()
            headers["Location"] = (
                "https://api.github.com/redirected"
                if destination == "same_origin"
                else "https://synthetic.invalid/redirected"
            )
            body = io.BytesIO(b"synthetic redirect")
            bodies.append(body)
            response = addinfourl(body, headers, request.full_url, code)
            setattr(response, "msg", "synthetic redirect")
            return response

    def isolated_opener(*handlers: BaseHandler | type[BaseHandler]) -> OpenerDirector:
        return build_opener(SyntheticRedirectTransport(), *handlers)

    def forbidden_anonymous_request(*args: object, **kwargs: object) -> None:
        pytest.fail("Authenticated metadata used anonymous transport")

    monkeypatch.setattr(check_github_action_pins, "build_opener", isolated_opener)
    monkeypatch.setattr(
        check_github_action_pins, "urlopen", forbidden_anonymous_request
    )
    with pytest.raises(
        RuntimeError, match="^Authenticated GitHub metadata redirect refused$"
    ):
        check_github_action_pins.resolve_github_action_version("owner/action", "v1.2.3")
    assert len(requests) == 1 and len(bodies) == 1
    assert (
        requests[0].full_url
        == "https://api.github.com/repos/owner/action/commits/v1.2.3"
    )
    assert bodies[0].closed


def test_cli_credential_failure_is_nonzero_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _token_file(tmp_path, SYNTHETIC_TOKEN.encode() + b"\n")
    monkeypatch.setenv(TOKEN_FILE_ENV, str(path))
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "steps:\n  - uses: owner/action@" + "a" * 40 + " # v1.2.3\n", encoding="utf-8"
    )
    assert check_github_action_pins.main(["--verify-versions", str(workflow)]) == 1
    captured = capsys.readouterr()
    assert "Invalid GitHub action credential file" in captured.err
    assert SYNTHETIC_TOKEN not in captured.err and SYNTHETIC_TOKEN not in captured.out
