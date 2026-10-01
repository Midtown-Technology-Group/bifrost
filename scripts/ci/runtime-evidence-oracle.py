#!/usr/bin/env python3
"""Synthetic, tests-only CPython oracle; never imports backend/runtime code."""

import hashlib
import json
import math
import struct
import sys
from pathlib import Path

RANDOM_COUNT = 1_000_000
SEED = 0xB1F2057C1A02
MASK = (1 << 64) - 1
MANTISSAS = (
    0,
    1,
    2,
    3,
    (1 << 51) - 1,
    1 << 51,
    (1 << 51) + 1,
    (1 << 52) - 3,
    (1 << 52) - 2,
    (1 << 52) - 1,
)


def pinned_python():
    if sys.version_info[:3] != (3, 14, 7):
        raise SystemExit("oracle requires the captured CPython 3.14.7")


def bits(value):
    return struct.unpack(">Q", struct.pack(">d", value))[0]


def number(raw):
    return struct.unpack(">d", raw.to_bytes(8, "big"))[0]


def patterns():
    # Exponent/mantissa boundaries and both signs; retain duplicates explicitly.
    for exponent in range(2047):
        for mantissa in MANTISSAS:
            for sign in (0, 1 << 63):
                yield "exponent", sign | (exponent << 52) | mantissa
    for power in range(-324, 309):
        center = float(f"1e{power}")
        if not math.isfinite(center):
            continue
        for offset in range(-32, 33):
            raw = bits(center) + offset
            if 0 <= raw < (0x7FF << 52):
                for sign in (0, 1 << 63):
                    yield "decimal-neighbor", raw | sign
    # Fixed xorshift64 sequence: no platform random state, time or vendor input.
    state = SEED
    for _ in range(RANDOM_COUNT):
        state ^= (state << 13) & MASK
        state ^= state >> 7
        state ^= (state << 17) & MASK
        state &= MASK
        yield "random", state
    for raw in (
        0x7FF0000000000000,
        0xFFF0000000000000,
        0x7FF8000000000000,
        0xFFF8000000000001,
    ):
        yield "explicit-nonfinite", raw


def string_cases():
    samples = [
        "".join(map(chr, range(128))),
        "\u0080\u009f\u00a0",
        "\ud7ff\ue000\uffff\U00010000\U0010ffff",
        "\u2028\u2029",
        "e\u0301\u00e9",
        '"\\/',
        "\U00010000\ue000",
    ]
    for index, text in enumerate(samples):
        # A supplementary key sorts after the BMP private-use key in scalar order.
        entries = [
            ["\U00010000", {"kind": "string", "value": text}],
            ["\ue000", {"kind": "string", "value": "neighbor"}],
        ]
        obj = {"\U00010000": text, "\ue000": "neighbor"}
        for profile, ascii_only in (
            ("finite_utf8", False),
            ("sorted_ascii_fixture", True),
        ):
            yield f"string-{index}-{profile}", profile, entries, obj, ascii_only


def cases():
    for index, (category, raw) in enumerate(patterns()):
        value = number(raw)
        request = {
            "id": f"numeric-{index}",
            "profile": "finite_utf8",
            "input": {
                "kind": "object",
                "entries": [
                    ["workflow_value", {"kind": "f64", "bits_be_hex": f"{raw:016x}"}]
                ],
            },
        }
        expected = (
            None
            if not math.isfinite(value)
            else json.dumps(
                {"workflow_value": value},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        )
        yield request, expected, category
    for identity, profile, entries, obj, ascii_only in string_cases():
        request = {
            "id": identity,
            "profile": profile,
            "input": {"kind": "object", "entries": entries},
        }
        expected = json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=ascii_only,
            allow_nan=False,
        ).encode()
        yield request, expected, "string"


def emit():
    for request, _, _ in cases():
        print(json.dumps(request, ensure_ascii=True, separators=(",", ":")))


def validate(path):
    counts = {}
    digest = hashlib.sha256()
    with Path(path).open(encoding="utf-8") as peer:
        for request, expected, category in cases():
            line = peer.readline()
            if not line:
                raise SystemExit(f"missing candidate outcome: {request['id']}")
            result = json.loads(line)
            if result.get("id") != request["id"]:
                raise SystemExit(f"candidate identity drift: {request['id']}")
            if expected is None:
                if result != {
                    "id": request["id"],
                    "outcome": "error",
                    "error": "NonFinite",
                }:
                    raise SystemExit(f"nonfinite disposition drift: {request['id']}")
                counts["nonfinite"] = counts.get("nonfinite", 0) + 1
            else:
                actual = result.get("utf8", "").encode()
                if (
                    result.get("outcome") != "encoded"
                    or actual != expected
                    or result.get("hex") != expected.hex()
                ):
                    # Only synthetic IEEE bits/identity are reported, never workload values.
                    raw = request["input"]["entries"][0][1].get(
                        "bits_be_hex", "string-case"
                    )
                    raise SystemExit(f"exact-byte drift: {request['id']} bits={raw}")
                digest.update(len(actual).to_bytes(8, "big"))
                digest.update(actual)
                counts["finite"] = counts.get("finite", 0) + 1
            counts[category] = counts.get(category, 0) + 1
        if peer.read():
            raise SystemExit("unexpected additional candidate outcomes")
    print(
        json.dumps(
            {
                "schema": "bifrost.test.evidence-oracle/v1",
                "python": sys.version,
                "random_seed": f"{SEED:016x}",
                "counts": counts,
                "matched_bytes_digest": digest.hexdigest(),
                "scope": "sampled numeric/string byte compatibility; no runtime authority",
            },
            sort_keys=True,
        )
    )


def main():
    pinned_python()
    if len(sys.argv) == 2 and sys.argv[1] == "emit":
        emit()
    elif len(sys.argv) == 3 and sys.argv[1] == "validate":
        validate(sys.argv[2])
    else:
        raise SystemExit(
            "usage: runtime-evidence-oracle.py emit | validate PEER_NDJSON"
        )


if __name__ == "__main__":
    main()
