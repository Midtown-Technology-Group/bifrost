"""Independent proposed-profile Python stream codec; no runtime authority.

Only locally packaged shared schemas are loaded. This module imports neither the
proposal oracle nor platform/Rust implementation code. No session is implemented.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from jsonschema import Draft202012Validator, FormatChecker, validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

PROTOCOL = "bifrost.runtime/v1"
MAX_FRAME = 16 * 1024 * 1024
MAX_DEPTH = 64
MAX_SAFE = 9_007_199_254_740_991
ENVELOPE = frozenset(("protocol", "type", "session_id", "message_id", "sequence", "correlation_id", "body"))
IDENTITY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z\Z")
SCHEMAS = Path(__file__).resolve().parent.parent / "executionprofile/schemas"


class CodecError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"proposed runtime codec: {code}")


@dataclass(frozen=True)
class Decoded:
    frame: dict
    _payload: bytes

    def payload_sha256(self) -> str:
        return hashlib.sha256(self._payload).hexdigest()


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in items:
        if name in result:
            raise CodecError("InvalidJson")
        result[name] = value
    return result


def _integer(token: str) -> int | float:
    # Retain P0's i64/u64-to-f64 fallback and lexical -0 distinction.
    value = int(token)
    if token == "-0":
        return -0.0
    if -(1 << 63) <= value < (1 << 64):
        return value
    return float(token)


def _constant(_: str) -> None:
    raise CodecError("InvalidJson")


def _bounded_tree(root: object) -> None:
    pending = [(root, 0)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list)):
            if depth >= MAX_DEPTH:
                raise CodecError("InvalidJson")
            children = list(value.items()) if isinstance(value, dict) else enumerate(value)
            for name, child in children:
                if isinstance(name, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in name):
                    raise CodecError("InvalidJson")
                pending.append((child, depth + 1))
        elif isinstance(value, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                raise CodecError("InvalidJson")
        elif isinstance(value, float) and not math.isfinite(value):
            raise CodecError("InvalidJson")


def _schema_validator():
    # Schema's mathematical integer admits 1.0, which control fields forbid.
    checker = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int
    )

    def exact_pattern(_validator, pattern, value, _schema):
        if isinstance(value, str) and re.fullmatch(pattern, value) is None:
            from jsonschema.exceptions import ValidationError
            yield ValidationError("invalid control string")

    cls = validators.extend(Draft202012Validator, {"pattern": exact_pattern}, type_checker=checker)
    formats = FormatChecker()

    @formats.checks("date-time", raises=ValueError)
    def timestamp(value):
        if not isinstance(value, str):
            return True
        if UTC.fullmatch(value) is None:
            return False
        datetime.fromisoformat(value)
        return True

    documents = [json.loads((SCHEMAS / name).read_text()) for name in
                 ("profile.schema.json", "binding.schema.json", "artifact.schema.json")]
    for document in documents:
        cls.check_schema(document)
    # A referenced document's $schema would make jsonschema evolve back to its
    # default class, dropping strict lexical overrides. Validate each original
    # dialect above, then keep the explicit same dialect and our class through
    # references without mutating the published schema files/global registry.
    for document in documents:
        if document.pop("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError("unreviewed schema dialect")
    registry = Registry().with_resources(
        (document["$id"], Resource.from_contents(document, default_specification=DRAFT202012))
        for document in documents
    )
    return cls(documents[0], registry=registry, format_checker=formats)


class Codec:
    def __init__(self):
        self._validator = _schema_validator()

    def matches_schema(self, document: object) -> bool:
        return self._validator.is_valid(document)

    def decode(self, payload: bytes) -> Decoded:
        if len(payload) > MAX_FRAME:
            raise CodecError("FrameTooLarge")
        try:
            frame = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                               parse_int=_integer, parse_constant=_constant)
            _bounded_tree(frame)
        except CodecError:
            raise
        except (UnicodeError, ValueError, RecursionError, OverflowError):
            raise CodecError("InvalidJson") from None
        if not isinstance(frame, dict) or frame.keys() != ENVELOPE:
            raise CodecError("InvalidFrame")
        if not isinstance(frame["protocol"], str):
            raise CodecError("InvalidFrame")
        if frame["protocol"] != PROTOCOL:
            raise CodecError("UnsupportedProtocol")
        kind = frame["type"]
        if not isinstance(kind, str) or not kind:
            raise CodecError("InvalidFrame")
        for name in ("session_id", "message_id", "correlation_id"):
            value = frame[name]
            if name == "correlation_id" and value is None:
                continue
            if not isinstance(value, str) or IDENTITY.fullmatch(value) is None:
                raise CodecError("InvalidFrame")
        sequence = frame["sequence"]
        if type(sequence) is not int or not 1 <= sequence <= MAX_SAFE:
            raise CodecError("InvalidFrame")
        if kind not in self._validator.schema["$defs"]:
            raise CodecError("UnsupportedFrame")
        if not self._validator.is_valid(frame):
            raise CodecError("InvalidFrame")
        correlation_field = {
            "Prepared": "prepare_message_id", "Start": "prepare_message_id",
            "Provision": "prepare_message_id", "LogBatch": "start_message_id",
            "Usage": "start_message_id", "Result": "start_message_id",
            "ResultReceipt": "result_message_id",
        }.get(kind)
        correlation = frame["correlation_id"]
        if correlation_field is not None and correlation != frame["body"][correlation_field]:
            raise CodecError("InvalidFrame")
        if kind in ("Offer", "Heartbeat", "Cancel", "Stopped") and correlation is not None:
            raise CodecError("InvalidFrame")
        if kind in ("Select", "Prepare") and correlation is None:
            raise CodecError("InvalidFrame")
        return Decoded(frame, bytes(payload))

    def read(self, stream: BinaryIO) -> Decoded | None:
        prefix = _read_exact(stream, 4, allow_eof=True)
        if prefix is None:
            return None
        size = struct.unpack(">I", prefix)[0]
        if size == 0:
            raise CodecError("InvalidFrame")
        if size > MAX_FRAME:
            raise CodecError("FrameTooLarge")
        return self.decode(_read_exact(stream, size))

    def write(self, stream: BinaryIO, frame: dict) -> None:
        try:
            payload = json.dumps(frame, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8")
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise CodecError("InvalidFrame") from None
        self.decode(payload)
        pending = memoryview(struct.pack(">I", len(payload)) + payload)
        while pending:
            try:
                count = stream.write(pending)
            except InterruptedError:
                continue
            except OSError:
                raise CodecError("Io") from None
            if type(count) is not int or not 0 < count <= len(pending):
                raise CodecError("Io")
            pending = pending[count:]


def _read_exact(stream: BinaryIO, size: int, *, allow_eof: bool = False) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            value = stream.read(size - len(chunks))
        except InterruptedError:
            continue
        except OSError:
            raise CodecError("Io") from None
        if not isinstance(value, bytes) or len(value) > size - len(chunks):
            raise CodecError("Io")
        if not value:
            if allow_eof and not chunks:
                return None
            raise CodecError("TruncatedFrame")
        chunks.extend(value)
    return bytes(chunks)
