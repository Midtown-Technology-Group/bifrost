"""Tests-only exchange of synthetic golden encodings with the Rust example."""

import io
import json
import os
import re
import sys
from pathlib import Path

from src.runtime_protocol.control import (
    MAX_DEPTH,
    MAX_FRAME_BYTES,
    decode_json,
    encode_frame,
    read_frame,
)

PROFILE = "bifrost.runtime/v1/control_profile/v1"
MAX_EXCHANGE_BYTES = 1024 * 1024


def load_vectors():
    default = (
        Path(__file__).resolve().parents[3]
        / "core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json"
    )
    path = Path(os.environ.get("BIFROST_RUNTIME_VECTORS", str(default)))
    data = json.loads(path.read_text())
    if (
        data["profile"] != PROFILE
        or data["max_frame_bytes"] != MAX_FRAME_BYTES
        or data["max_depth"] != MAX_DEPTH
    ):
        raise ValueError("invalid synthetic control fixture")
    return data


def golden():
    frames = {}
    for vector in load_vectors()["wire"]:
        if vector["expected"] != "ok":
            continue
        if vector["name"] in frames:
            raise ValueError("duplicate synthetic control fixture")
        frames[vector["name"]] = decode_json(vector["json"].encode())
    if not frames:
        raise ValueError("missing synthetic control fixture")
    return frames


def emit():
    return {
        "profile": PROFILE,
        "frames": [
            {"name": name, "frame_hex": encode_frame(frame).hex()}
            for name, frame in sorted(golden().items())
        ],
    }


def validate(path: Path) -> int:
    if path.stat().st_size > MAX_EXCHANGE_BYTES:
        raise ValueError("oversize synthetic interchange")
    exchange = json.loads(path.read_text())
    expected = golden()
    if (
        not isinstance(exchange, dict)
        or set(exchange) != {"profile", "frames"}
        or exchange["profile"] != PROFILE
        or not isinstance(exchange["frames"], list)
        or len(exchange["frames"]) != len(expected)
    ):
        raise ValueError("invalid synthetic interchange")
    seen = set()
    for item in exchange["frames"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "frame_hex"}
            or not isinstance(item["name"], str)
            or item["name"] not in expected
            or item["name"] in seen
        ):
            raise ValueError("unknown/duplicate synthetic interchange name")
        text = item["frame_hex"]
        if (
            not isinstance(text, str)
            or len(text) > (MAX_FRAME_BYTES + 4) * 2
            or len(text) % 2
            or re.fullmatch("[0-9a-f]*", text) is None
        ):
            raise ValueError("invalid synthetic interchange hex")
        seen.add(item["name"])
        stream = io.BytesIO(bytes.fromhex(text))
        if (
            read_frame(stream) != expected[item["name"]]
            or read_frame(stream) is not None
        ):
            raise ValueError("synthetic peer encoding mismatch")
    return len(expected)


def main():
    args = sys.argv[1:]
    if args == ["emit"]:
        json.dump(emit(), sys.stdout, ensure_ascii=False, separators=(",", ":"))
    elif len(args) == 2 and args[0] == "validate":
        print(f"validated {validate(Path(args[1]))} synthetic peer control encodings")
    else:
        raise ValueError("usage: interchange emit | validate FILE")


if __name__ == "__main__":
    main()
