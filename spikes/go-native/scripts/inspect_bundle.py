"""Inspect an identified native bundle without extraction or workload execution."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import stat
import sys

from native_bundle import MAX_ARCHIVE, ORDER, VERSION, Rejected, digest, verify_archive


def expected_digest(value: str) -> str:
    if value.startswith("sha256:"):
        value = value[7:]
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("expected a lowercase SHA-256 digest")
    return value


def inspect(path: pathlib.Path, accepted_sha256: str) -> dict:
    # NONBLOCK avoids hanging on a FIFO before fstat; NOFOLLOW rejects a final
    # symlink. Only bounded regular-file bytes reach the existing verifier.
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE:
            raise Rejected("bounded regular bundle required")
        raw = stream.read(MAX_ARCHIVE + 1)
    entries = verify_archive(raw, accepted_sha256)
    # Report fixed recipe fields and byte identities only. Embedded validation
    # claims and arbitrary build metadata are not producer authentication.
    return {
        "artifact_id": "sha256:" + digest(raw),
        "bundle_recipe": VERSION,
        "runtime": "go-native/v1",
        "platform": {"os": "linux", "architecture": "amd64"},
        "archive_size_bytes": len(raw),
        "workflow_sha256": digest(entries["workflow"]),
        "adapter_sha256": digest(entries["adapter"]),
        "entries": [
            {
                "path": name,
                "sha256": digest(entries[name]),
                "size_bytes": len(entries[name]),
            }
            for name in ORDER
        ],
        "verification_scope": "archive bytes, canonical recipe and embedded hash consistency",
        "producer_authentication": "not evaluated",
        "deployment_admission": "not evaluated",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=pathlib.Path)
    parser.add_argument("--sha256", required=True, type=expected_digest)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.bundle, args.sha256)
    except (Rejected, OSError, ValueError, TypeError, KeyError, RecursionError):
        # Never copy untrusted archive metadata, file contents or paths into
        # errors. A rejected bundle produces no partial JSON success report.
        print("native bundle inspection rejected", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
