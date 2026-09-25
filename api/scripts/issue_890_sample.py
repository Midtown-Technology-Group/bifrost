"""Sample per-container cgroup CPU and memory during an isolated load sweep."""

import argparse
import json
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROLES = (
    "api",
    "api-replica",
    "worker",
    "scheduler",
    "postgres",
    "pgbouncer",
    "redis",
    "rabbitmq",
)


def read_usage(cgroup: Path) -> tuple[int, int]:
    cpu = next(
        int(line.split()[1])
        for line in (cgroup / "cpu.stat").read_text().splitlines()
        if line.startswith("usage_usec ")
    )
    memory = int((cgroup / "memory.current").read_text().strip())
    return cpu, memory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    cgroups = {}
    for role in ROLES:
        container = f"{args.project}-{role}-1"
        pid = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Pid}}", container], text=True
        ).strip()
        relative = Path(f"/proc/{pid}/cgroup").read_text().split("::", 1)[1].strip()
        cgroups[role] = Path("/sys/fs/cgroup") / relative.lstrip("/")

    running = True

    def stop(_signal: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    while running:
        timestamp = datetime.now(UTC).isoformat()
        for role, cgroup in cgroups.items():
            cpu, memory = read_usage(cgroup)
            print(
                json.dumps(
                    {
                        "time": timestamp,
                        "role": role,
                        "cpu_usage_usec": cpu,
                        "memory_bytes": memory,
                    }
                ),
                flush=True,
            )
        time.sleep(1)


if __name__ == "__main__":
    main()
