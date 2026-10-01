"""Closed, bounded control frames over private binary pipes.

Only the C1-P0 control subset is implemented. Prepare, workload, result and
credential contracts remain gated. Errors never include supplied frame bytes.
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from typing import BinaryIO, ClassVar, NoReturn, TypeVar

PROTOCOL = "bifrost.runtime/v1"
CONTROL_CAPABILITY = "control_profile/v1"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 64
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class ErrorCode(str, Enum):
    INVALID_JSON = "InvalidJson"
    INVALID_FRAME = "InvalidFrame"
    UNSUPPORTED_PROTOCOL = "UnsupportedProtocol"
    UNSUPPORTED_FRAME = "UnsupportedFrame"
    FRAME_TOO_LARGE = "FrameTooLarge"
    TRUNCATED_FRAME = "TruncatedFrame"
    IO = "Io"
    INVALID_BINDING = "InvalidBinding"
    INVALID_TRANSITION = "InvalidTransition"


class ProtocolError(ValueError):
    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(f"runtime control protocol: {code.value}")


def _fail(code: ErrorCode = ErrorCode.INVALID_FRAME) -> NoReturn:
    raise ProtocolError(code)


def canonical_uuid(value: object) -> str:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        _fail()
    assert isinstance(value, str)
    return value


def integer(value: object, *, positive: bool = False) -> int:
    if type(value) is not int or not (int(positive) <= value <= MAX_SAFE_INTEGER):
        _fail()
    assert isinstance(value, int)
    return value


def _text(value: object, *, name: bool = False) -> str:
    if not isinstance(value, str) or (name and not value):
        _fail()
    assert isinstance(value, str)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        _fail(ErrorCode.INVALID_JSON)
    return value


_T = TypeVar("_T")


def _nullable(value: object, parser: Callable[[object], _T]) -> _T | None:
    return None if value is None else parser(value)


def _fields(value: object, names: tuple[str, ...]) -> dict:
    if not isinstance(value, dict) or set(value) != set(names):
        _fail()
    assert isinstance(value, dict)
    return value


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail()
    assert isinstance(value, list)
    names = tuple(_text(item, name=True) for item in value)
    if len(names) != len(set(names)):
        _fail()
    return names


@dataclass(frozen=True)
class Interpreter:
    implementation: str
    version: str


@dataclass(frozen=True)
class Sdk:
    distribution: str | None
    version: str | None


@dataclass(frozen=True)
class RuntimeArtifact:
    artifact_id: str
    image_digest: str | None
    interpreter: Interpreter
    sdk: Sdk
    requirements_lock_sha256: str | None
    runtime_protocol: str


@dataclass(frozen=True)
class RuntimeError:
    type: str
    message: str
    traceback: str | None


@dataclass(frozen=True)
class Hello:
    TYPE: ClassVar[str] = "Hello"
    runtime_incarnation_id: str
    supported_protocols: tuple[str, ...]
    capabilities: tuple[str, ...]
    artifact: RuntimeArtifact


@dataclass(frozen=True)
class Start:
    TYPE: ClassVar[str] = "Start"
    prepare_message_id: str
    committed_start_id: str
    parent_duration_seconds: int


@dataclass(frozen=True)
class Heartbeat:
    TYPE: ClassVar[str] = "Heartbeat"
    start_message_id: str | None
    state: str
    monotonic_elapsed_ms: int


@dataclass(frozen=True)
class Cancel:
    TYPE: ClassVar[str] = "Cancel"
    cancel_id: str
    reason: str
    grace_ms: int


@dataclass(frozen=True)
class Stopped:
    TYPE: ClassVar[str] = "Stopped"
    start_message_id: str | None
    cancel_id: str | None
    reason: str
    result_message_id: str | None
    error: RuntimeError | None


Body = Hello | Start | Heartbeat | Cancel | Stopped


@dataclass(frozen=True)
class Frame:
    session_id: str
    message_id: str
    sequence: int
    correlation_id: str | None
    body: Body


def _artifact(value: object) -> RuntimeArtifact:
    obj = _fields(
        value,
        (
            "artifact_id",
            "image_digest",
            "interpreter",
            "sdk",
            "requirements_lock_sha256",
            "runtime_protocol",
        ),
    )
    interpreter = _fields(obj["interpreter"], ("implementation", "version"))
    sdk = _fields(obj["sdk"], ("distribution", "version"))
    lock = _nullable(obj["requirements_lock_sha256"], _text)
    if lock is not None and _HASH.fullmatch(lock) is None:
        _fail()
    if obj["runtime_protocol"] != PROTOCOL:
        _fail()
    return RuntimeArtifact(
        _text(obj["artifact_id"], name=True),
        _nullable(obj["image_digest"], lambda item: _text(item, name=True)),
        Interpreter(
            _text(interpreter["implementation"], name=True),
            _text(interpreter["version"], name=True),
        ),
        Sdk(
            _nullable(sdk["distribution"], lambda item: _text(item, name=True)),
            _nullable(sdk["version"], lambda item: _text(item, name=True)),
        ),
        lock,
        PROTOCOL,
    )


def _runtime_error(value: object) -> RuntimeError:
    obj = _fields(value, ("type", "message", "traceback"))
    return RuntimeError(
        _text(obj["type"], name=True),
        _text(obj["message"]),
        _nullable(obj["traceback"], _text),
    )


def _enum(value: object, choices: tuple[str, ...]) -> str:
    if value not in choices or not isinstance(value, str):
        _fail()
    return value


def _body(kind: str, value: object) -> Body:
    if kind == "Hello":
        obj = _fields(
            value,
            (
                "runtime_incarnation_id",
                "supported_protocols",
                "capabilities",
                "artifact",
            ),
        )
        return Hello(
            canonical_uuid(obj["runtime_incarnation_id"]),
            _names(obj["supported_protocols"]),
            _names(obj["capabilities"]),
            _artifact(obj["artifact"]),
        )
    if kind == "Start":
        obj = _fields(
            value,
            ("prepare_message_id", "committed_start_id", "parent_duration_seconds"),
        )
        return Start(
            canonical_uuid(obj["prepare_message_id"]),
            canonical_uuid(obj["committed_start_id"]),
            integer(obj["parent_duration_seconds"], positive=True),
        )
    if kind == "Heartbeat":
        obj = _fields(value, ("start_message_id", "state", "monotonic_elapsed_ms"))
        return Heartbeat(
            _nullable(obj["start_message_id"], canonical_uuid),
            _enum(obj["state"], ("prepared", "executing", "cancelling")),
            integer(obj["monotonic_elapsed_ms"]),
        )
    if kind == "Cancel":
        obj = _fields(value, ("cancel_id", "reason", "grace_ms"))
        return Cancel(
            canonical_uuid(obj["cancel_id"]),
            _enum(
                obj["reason"],
                ("requested", "deadline", "authority_revoked", "supervisor_shutdown"),
            ),
            integer(obj["grace_ms"]),
        )
    if kind == "Stopped":
        obj = _fields(
            value,
            ("start_message_id", "cancel_id", "reason", "result_message_id", "error"),
        )
        return Stopped(
            _nullable(obj["start_message_id"], canonical_uuid),
            _nullable(obj["cancel_id"], canonical_uuid),
            _enum(
                obj["reason"],
                ("completed", "cancelled", "prepare_rejected", "protocol_error"),
            ),
            _nullable(obj["result_message_id"], canonical_uuid),
            _nullable(obj["error"], _runtime_error),
        )
    _fail(ErrorCode.UNSUPPORTED_FRAME)
    raise AssertionError("unreachable")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            _fail(ErrorCode.INVALID_JSON)
        obj[key] = value
    return obj


def _constant(_value: str) -> None:
    _fail(ErrorCode.INVALID_JSON)


def _integer_token(value: str) -> int | float:
    # serde_json classifies lexical -0 as a float. Preserve that distinction
    # so UInt/Positive reject it as InvalidFrame rather than accepting integer 0.
    return -0.0 if value == "-0" else int(value)


def _check_tree(value: object) -> None:
    stack = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            if depth >= MAX_DEPTH:
                _fail(ErrorCode.INVALID_JSON)
            if isinstance(node, dict):
                stack.extend((key, depth + 1) for key in node)
                stack.extend((child, depth + 1) for child in node.values())
            else:
                stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, str):
            _text(node)
        elif isinstance(node, float) and not math.isfinite(node):
            _fail(ErrorCode.INVALID_JSON)


def decode_json(data: bytes) -> Frame:
    if not data:
        _fail(ErrorCode.INVALID_JSON)
    if len(data) > MAX_FRAME_BYTES:
        _fail(ErrorCode.FRAME_TOO_LARGE)
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_int=_integer_token,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ProtocolError):
            raise
        raise ProtocolError(ErrorCode.INVALID_JSON) from None
    _check_tree(value)
    obj = _fields(
        value,
        (
            "protocol",
            "type",
            "session_id",
            "message_id",
            "sequence",
            "correlation_id",
            "body",
        ),
    )
    if _text(obj["protocol"]) != PROTOCOL:
        _fail(ErrorCode.UNSUPPORTED_PROTOCOL)
    kind = _text(obj["type"], name=True)
    frame = Frame(
        canonical_uuid(obj["session_id"]),
        canonical_uuid(obj["message_id"]),
        integer(obj["sequence"], positive=True),
        _nullable(obj["correlation_id"], canonical_uuid),
        _body(kind, obj["body"]),
    )
    if isinstance(frame.body, Start):
        if frame.correlation_id != frame.body.prepare_message_id:
            _fail()
    elif frame.correlation_id is not None:
        _fail()
    return frame


def encode_frame(frame: Frame) -> bytes:
    obj = {
        "protocol": PROTOCOL,
        "type": frame.body.TYPE,
        "session_id": frame.session_id,
        "message_id": frame.message_id,
        "sequence": frame.sequence,
        "correlation_id": frame.correlation_id,
        "body": asdict(frame.body),
    }
    try:
        data = json.dumps(
            obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (UnicodeError, ValueError, TypeError):
        raise ProtocolError(ErrorCode.INVALID_FRAME) from None
    decode_json(data)
    return struct.pack(">I", len(data)) + data


def _read_exact(
    stream: BinaryIO, length: int, *, initial: bool = False
) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < length:
        try:
            chunk = stream.read(length - len(chunks))
        except InterruptedError:
            continue
        except OSError:
            raise ProtocolError(ErrorCode.IO) from None
        if not isinstance(chunk, bytes) or len(chunk) > length - len(chunks):
            _fail(ErrorCode.IO)
        if not chunk:
            if initial and not chunks:
                return None
            _fail(ErrorCode.TRUNCATED_FRAME)
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(stream: BinaryIO) -> Frame | None:
    prefix = _read_exact(stream, 4, initial=True)
    if prefix is None:
        return None
    length = struct.unpack(">I", prefix)[0]
    if length == 0:
        _fail()
    if length > MAX_FRAME_BYTES:
        _fail(ErrorCode.FRAME_TOO_LARGE)
    data = _read_exact(stream, length)
    assert data is not None
    return decode_json(data)


def write_frame(stream: BinaryIO, frame: Frame) -> None:
    data = encode_frame(frame)
    offset = 0
    while offset < len(data):
        try:
            written = stream.write(data[offset:])
        except InterruptedError:
            continue
        except OSError:
            raise ProtocolError(ErrorCode.IO) from None
        if type(written) is not int or not (0 < written <= len(data) - offset):
            _fail(ErrorCode.IO)
        offset += written
