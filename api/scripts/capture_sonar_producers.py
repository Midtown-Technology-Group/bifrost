"""Retain bounded runtime producer identities without process arguments or env."""

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SERVICES = ("api", "api-replica", "worker", "scheduler", "scheduler-fixtures")
FIELDS = {
    "service": '(index .Config.Labels "com.docker.compose.service")',
    "hostname": ".Config.Hostname",
    "container_id": ".Id",
    "state": ".State.Status",
    "host_init_pid": ".State.Pid",
    "exit_code": ".State.ExitCode",
    "oom_killed": ".State.OOMKilled",
    "started_at": ".State.StartedAt",
    "finished_at": ".State.FinishedAt",
}
INSPECT_FORMAT = "{" + ",".join(
    json.dumps(key) + ":{{json " + expression + "}}"
    for key, expression in FIELDS.items()
) + "}"
# /proc stat fields only: namespace PID/parent, state and start ticks. Never
# collect cmdline, environ, cwd, open files, process names or application output.
PROCESS_QUERY = """
import json
from pathlib import Path
records = []
for path in sorted(Path('/proc').iterdir(), key=lambda p: p.name):
    if not path.name.isdecimal():
        continue
    try:
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        records.append({'pid': int(path.name), 'ppid': int(fields[1]),
                        'state': fields[0], 'start_ticks': int(fields[19])})
    except (OSError, IndexError, ValueError):
        continue  # A process can exit during this read-only snapshot.
    if len(records) >= 512:
        break
print(json.dumps(records))
"""


def _run(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=20, check=False)


def snapshot(compose_file: str, phase: str) -> dict:
    """Map the five task services to coverage hostnames and namespace PIDs."""
    listed = _run(["docker", "compose", "-f", compose_file, "ps", "-aq", *SERVICES])
    if listed.returncode:
        raise RuntimeError("Cannot list task runtime producers")
    records = []
    for container_id in listed.stdout.split():
        inspected = _run(["docker", "inspect", "--format", INSPECT_FORMAT, container_id])
        if inspected.returncode:
            raise RuntimeError("Cannot inspect task runtime producer")
        source = json.loads(inspected.stdout)
        record = {key: source[key] for key in FIELDS}
        if record["service"] not in SERVICES:
            raise ValueError("Producer outside task service boundary")
        record["processes"] = []
        if record["state"] == "running":
            processes = _run(["docker", "exec", container_id, "python", "-c", PROCESS_QUERY])
            record["process_snapshot_exit_code"] = processes.returncode
            if processes.returncode == 0:
                record["processes"] = [
                    {key: item[key] for key in ("pid", "ppid", "state", "start_ticks")}
                    for item in json.loads(processes.stdout)
                ]
        records.append(record)
    if {record["service"] for record in records} != set(SERVICES):
        raise ValueError("Incomplete task runtime producer inventory")
    return {
        "schema": 1,
        "phase": phase,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "containers": records,
        "limitations": [
            "Process snapshots are instantaneous, not historical child-exit records.",
            "Container exit state and shard presence do not prove a child flushed coverage.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-file", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("started", "before-stop", "after-stop"), required=True)
    args = parser.parse_args()
    data = snapshot(args.compose_file, args.phase)
    target = args.output_dir / f"sonar-runtime-producers-{args.phase}.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(data, output, indent=2)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
