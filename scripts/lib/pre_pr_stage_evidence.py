#!/usr/bin/env python3
"""Small, dependency-free evidence ledger for resumable pre-PR stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(repo: Path, compose_file: str, env_file: str, stage: str | None = None) -> dict[str, object]:
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], repo)
    head = run(["git", "rev-parse", "HEAD"], repo)
    command = ["docker", "compose", "-f", compose_file]
    profile = {"client": "client-check", "client-unit": "client-check", "browser": "client", "mcp": "test"}.get(stage)
    if profile:
        command += ["--profile", profile]
    compose = run([*command, "config"], repo)
    names = run([*command, "config", "--images"], repo)
    if stage == "image" and names != "unavailable":
        names += f"\nbifrost-local-api-candidate:{head[:12]}"
    # Resolve configured tags, not just running containers: another checkout can
    # rebuild a shared test-image tag between two local gate invocations.
    images = run(["docker", "image", "inspect", "--format", "{{.Id}}", *names.splitlines()], repo) if names and names != "unavailable" else "unavailable"
    return {
        "head": head,
        "status": status,
        "compose_sha256": hashlib.sha256(compose.encode()).hexdigest(),
        "compose_available": compose != "unavailable",
        "compose_images": sorted(set(images.splitlines())) if images != "unavailable" else [],
        "env_sha256": digest(repo / env_file),
        "docker_version": run(["docker", "version", "--format", "{{.Server.Version}}"], repo),
        "compose_version": run(["docker", "compose", "version", "--short"], repo),
        "python_version": sys.version.split()[0],
        "node_version": run(["node", "--version"], repo),
        "browser_config_sha256": digest(repo / "client" / "playwright.config.ts"),
    }


def signature(value: dict[str, object], stage: str) -> dict[str, object]:
    keys = {"head", "status", "compose_sha256", "env_sha256", "docker_version", "compose_version", "python_version", "node_version"}
    if stage != "repository":
        keys.add("compose_images")
    if stage == "browser":
        keys.update({"compose_images", "browser_config_sha256"})
    return {key: value[key] for key in sorted(keys)}


def invariant_signature(value: dict[str, object], stage: str) -> dict[str, object]:
    result = signature(value, stage)
    if stage in {"client", "stack", "quality", "browser", "image"}:
        result.pop("compose_images", None)
    return result


def read_state(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def atomic_write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["snapshot", "start", "success", "failed", "reuse", "fresh"])
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--stage")
    parser.add_argument("--context", default="")
    parser.add_argument("--compose-file", default="docker-compose.test.yml")
    parser.add_argument("--env-file", default=".env.test")
    args = parser.parse_args()
    current = snapshot(args.repo, args.compose_file, args.env_file, args.stage)

    if args.action == "snapshot":
        print(json.dumps(current, sort_keys=True))
        return 0
    if args.action == "fresh":
        args.state.unlink(missing_ok=True)
        return 0
    if not args.stage:
        parser.error("--stage is required for stage actions")
    state = read_state(args.state)
    stages = state.setdefault("stages", {})
    if args.action == "success" and current["status"]:
        return 1
    previous = stages.get(args.stage, {})
    if args.action == "success" and previous.get("signature") != invariant_signature(current, args.stage):
        return 1
    if args.action == "reuse":
        record = stages.get(args.stage, {})
        available = current["compose_available"] and current["docker_version"] != "unavailable" and current["compose_version"] != "unavailable" and current["node_version"] != "unavailable"
        if args.stage != "repository":
            available = available and bool(current["compose_images"])
        reusable = available and record.get("status") == "complete" and record.get("context") == args.context and record.get("signature") == signature(current, args.stage) and not current["status"]
        return 0 if reusable else 1
    stored_signature = invariant_signature(current, args.stage) if args.action == "start" else signature(current, args.stage)
    stages[args.stage] = {"status": {"start": "running", "success": "complete", "failed": "failed"}[args.action], "context": args.context, "signature": stored_signature}
    atomic_write(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
