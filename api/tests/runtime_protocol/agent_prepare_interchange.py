"""Bounded synthetic peer exchange through actual agent codecs."""

from __future__ import annotations
import io
import json
import re
import sys
from pathlib import Path
from src.runtime_protocol.agent_prepare import (
    AgentPrepareCodec,
    validate_prepared_binding,
)
from src.runtime_protocol.control import ProtocolError, _pairs

LIMIT = 1024 * 1024
CORPUS = (
    Path(__file__).resolve().parents[3]
    / "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/agent-prepare-vectors.json"
)


def positives():
    raw = CORPUS.read_bytes()
    if len(raw) > LIMIT:
        raise ValueError("synthetic corpus bound")
    corpus = json.loads(raw, object_pairs_hook=_pairs)
    if (
        corpus["schema"] != "bifrost.test.agent-prepare-vectors/v1"
        or corpus["synthetic"] is not True
        or len(corpus["wire"]) > 256
    ):
        raise ValueError("synthetic corpus shape")
    codec = AgentPrepareCodec()
    result = {}
    for vector in corpus["wire"]:
        try:
            decoded = codec.decode_json(vector["json"].encode("utf-8"))
        except ProtocolError as error:
            if vector["expected"] != error.code.value:
                raise ValueError("synthetic error mismatch") from None
        else:
            if vector["expected"] != "ok" or vector["name"] in result:
                raise ValueError("synthetic outcome mismatch")
            result[vector["name"]] = decoded
    binding = corpus["binding"]
    validate_prepared_binding(
        result[binding["prepared"]],
        result[binding["prepare"]],
        result[binding["hello"]],
    )
    return result


def main():
    codec = AgentPrepareCodec()
    values = positives()
    if sys.argv[1:] == ["emit"]:
        frames = []
        for name, decoded in sorted(values.items()):
            output = io.BytesIO()
            codec.write_frame(output, codec.encode_retained(decoded))
            frames.append({"name": name, "frame_hex": output.getvalue().hex()})
        payload = json.dumps(
            {"profile": "agent_prepare_profile/v1", "frames": frames},
            separators=(",", ":"),
        ).encode()
        if len(payload) > LIMIT:
            raise ValueError("synthetic exchange bound")
        sys.stdout.buffer.write(payload)
    elif sys.argv[1:] == ["validate"]:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("synthetic exchange bound")
        data = json.loads(raw, object_pairs_hook=_pairs)
        if (
            set(data) != {"profile", "frames"}
            or data["profile"] != "agent_prepare_profile/v1"
            or len(data["frames"]) != len(values)
        ):
            raise ValueError("synthetic exchange shape")
        for item in data["frames"]:
            if set(item) != {"name", "frame_hex"} or item["name"] not in values:
                raise ValueError("synthetic exchange identity")
            if (
                not isinstance(item["frame_hex"], str)
                or len(item["frame_hex"]) % 2
                or re.fullmatch(r"[0-9a-f]*", item["frame_hex"]) is None
            ):
                raise ValueError("synthetic hex shape")
            raw = bytes.fromhex(item["frame_hex"])
            stream = io.BytesIO(raw)
            decoded = codec.read_frame(stream)
            if (
                decoded is None
                or stream.read(1)
                or decoded.payload_sha256 != values.pop(item["name"]).payload_sha256
            ):
                raise ValueError("synthetic custody mismatch")
            output = io.BytesIO()
            codec.write_frame(output, codec.encode_retained(decoded))
            if output.getvalue() != raw:
                raise ValueError("synthetic reemit mismatch")
        if values:
            raise ValueError("synthetic exchange incomplete")
    else:
        raise ValueError("synthetic exchange mode")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("invalid synthetic agent interchange", file=sys.stderr)
        raise SystemExit(1) from None
