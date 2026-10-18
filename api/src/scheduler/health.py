"""Event-loop heartbeat used by the scheduler container liveness probe."""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import time
from pathlib import Path

HEARTBEAT_PATH = Path(
    os.environ.get(
        "BIFROST_RUNTIME_DIR",
        Path.home() / ".cache" / "bifrost" / "runtime",
    )
) / "scheduler-heartbeat"
READY_PATH = HEARTBEAT_PATH.parent / "scheduler-ready"


def _ensure_private_runtime_directory() -> None:
    runtime_directory = HEARTBEAT_PATH.parent
    try:
        runtime_directory.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        if not runtime_directory.is_dir():
            raise

    directory_stat = runtime_directory.lstat()
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or directory_stat.st_mode & 0o077
    ):
        raise PermissionError(f"Scheduler runtime directory is not private: {runtime_directory}")


def _open_heartbeat(flags: int, *, path: Path | None = None) -> int:
    _ensure_private_runtime_directory()
    descriptor = os.open(
        path if path is not None else HEARTBEAT_PATH,
        flags | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    heartbeat_stat = os.fstat(descriptor)
    if (
        not stat.S_ISREG(heartbeat_stat.st_mode)
        or heartbeat_stat.st_uid != os.geteuid()
        or heartbeat_stat.st_nlink != 1
    ):
        os.close(descriptor)
        raise PermissionError(f"Scheduler heartbeat is not a private regular file: {HEARTBEAT_PATH}")
    return descriptor


def write_heartbeat() -> None:
    descriptor = _open_heartbeat(os.O_WRONLY | os.O_CREAT)
    try:
        os.utime(descriptor)
    finally:
        os.close(descriptor)


def clear_ready() -> None:
    READY_PATH.unlink(missing_ok=True)


def mark_ready() -> None:
    descriptor = _open_heartbeat(os.O_WRONLY | os.O_CREAT, path=READY_PATH)
    os.close(descriptor)


async def heartbeat_loop(interval_seconds: float = 10) -> None:
    while True:
        write_heartbeat()
        await asyncio.sleep(interval_seconds)


def heartbeat_is_fresh(max_age_seconds: float = 60) -> bool:
    try:
        descriptor = _open_heartbeat(os.O_RDONLY)
    except OSError:
        return False
    try:
        age = time.time() - os.fstat(descriptor).st_mtime
        return age <= max_age_seconds
    finally:
        os.close(descriptor)


def is_ready(max_age_seconds: float = 60) -> bool:
    """Ready once startup gates have passed and the event loop is still alive."""
    try:
        descriptor = _open_heartbeat(os.O_RDONLY, path=READY_PATH)
    except OSError:
        return False
    os.close(descriptor)
    return heartbeat_is_fresh(max_age_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age", type=float, default=60)
    parser.add_argument(
        "--ready",
        action="store_true",
        help="Readiness check: also require startup gates to have passed.",
    )
    args = parser.parse_args()
    check = is_ready if args.ready else heartbeat_is_fresh
    return 0 if check(args.max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
