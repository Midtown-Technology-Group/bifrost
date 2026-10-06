#!/usr/bin/env python3
"""Print the e2e test files for one shard of an N-shard split.

Usage:
    scripts/e2e_shard.py --shard-id 1 --total 4

Balances every file by measured full-suite JUnit durations. New files receive
the measured median weight. Allocations are deterministic for the same inventory
and timing snapshot; timings never select or exclude tests.

Print one path per line on stdout, suitable for piping into ./test.sh.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "api"
TESTS_ROOT = API_ROOT / "tests" / "e2e"

# Refresh from a complete successful JUnit report when the shard spread drifts.
# Paths are relative to api/, matching pytest inside the test-runner.
TIMINGS = json.loads(Path(__file__).with_name("e2e-shard-weights.json").read_text())
WEIGHTS = TIMINGS["seconds_by_file"]
DEFAULT_WEIGHT = TIMINGS["default_seconds"]


def collect_test_files() -> list[str]:
    # Paths are relative to api/ so they are valid as pytest args inside the
    # test-runner container (CWD=/app, which maps to ./api/ on the host).
    out = []
    for p in sorted(TESTS_ROOT.rglob("test_*.py")):
        rel = p.relative_to(API_ROOT)
        out.append(rel.as_posix())
    return out


def split(files: list[str], total: int) -> list[list[str]]:
    """Assign every file, largest estimated runtime first, to the lightest shard."""
    if total < 1:
        raise ValueError("total must be positive")
    if len(files) != len(set(files)):
        raise ValueError("test files must be unique")
    shards: list[list[str]] = [[] for _ in range(total)]
    weights: list[int] = [0] * total

    weighted = sorted(
        ((WEIGHTS.get(f, DEFAULT_WEIGHT), f) for f in files),
        reverse=True,
    )

    # Largest estimate into the lightest shard, including previously unseen files.
    for w, f in weighted:
        i = min(range(total), key=lambda i: weights[i])
        shards[i].append(f)
        weights[i] += w

    for s in shards:
        s.sort()
    return shards


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard-id", type=int, required=True, help="1-indexed shard id")
    ap.add_argument("--total", type=int, required=True, help="total shard count")
    args = ap.parse_args()

    if args.shard_id < 1 or args.shard_id > args.total:
        print(f"shard-id must be in 1..{args.total}", file=sys.stderr)
        return 2

    files = collect_test_files()
    if not files:
        print(f"no test files found under {TESTS_ROOT}", file=sys.stderr)
        return 1

    shards = split(files, args.total)
    for f in shards[args.shard_id - 1]:
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
