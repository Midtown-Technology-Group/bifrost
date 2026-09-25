"""Run isolated API and workflow capacity sweeps against the Bifrost test stack."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

WORKFLOW_SOURCE = """from bifrost import workflow

@workflow(name="issue_890_small", description="Capacity-lab fixture")
async def issue_890_small(value: int) -> dict:
    return {"value": value, "ok": True}
"""

EXTERNAL_HTTP_SOURCE = """import httpx
from bifrost import workflow

@workflow(name="issue_890_external_http", description="Capacity-lab fixture")
async def issue_890_external_http(value: int, delay_ms: int) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            "http://scheduler-fixtures:8080/issue-890/external-http",
            json={"value": value, "delay_ms": delay_ms},
        )
        response.raise_for_status()
        return response.json()
"""

AGENT_TOOL_SOURCE = """from bifrost import workflow

@workflow(name="issue_890_agent_tool", description="Capacity-lab tool", is_tool=True)
async def issue_890_agent_tool(value: int) -> dict:
    return {"value": value, "ok": True}
"""


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 4)


def summarize(latencies: list[float], failures: int, elapsed: float) -> dict:
    successes = len(latencies)
    return {
        "completed": successes,
        "failed": failures,
        "throughput_per_second": round(successes / elapsed, 3) if elapsed else 0.0,
        "p50_seconds": percentile(latencies, 0.50),
        "p95_seconds": percentile(latencies, 0.95),
        "p99_seconds": percentile(latencies, 0.99),
        "wall_seconds": round(elapsed, 3),
    }


def attempt_intervals(attempt: dict) -> dict[str, float]:
    """Return persisted lifecycle gaps, in seconds, for one successful attempt."""
    points = {
        key: datetime.fromisoformat(attempt[key])
        for key in ("created_at", "published_at", "claimed_at", "started_at", "completed_at")
        if attempt.get(key)
    }
    return {
        name: round((points[end] - points[start]).total_seconds(), 4)
        for name, start, end in (
            ("dispatch", "created_at", "published_at"),
            ("queue", "published_at", "claimed_at"),
            ("claim_to_start", "claimed_at", "started_at"),
            ("running_to_terminal", "started_at", "completed_at"),
        )
        if start in points and end in points
    }


def summarize_fresh_pools(payload: dict, now: datetime) -> dict:
    """Aggregate only live worker heartbeats; never export worker identities."""
    pools = []
    for pool in payload["pools"]:
        try:
            heartbeat = datetime.fromisoformat(pool["last_heartbeat"])
            age = (now - heartbeat).total_seconds()
        except (AttributeError, TypeError, ValueError):
            continue
        if heartbeat.tzinfo is not None and 0 <= age <= 30:
            pools.append(pool)
    admission = [pool.get("admission") or {} for pool in pools]
    rejections: dict[str, int] = {}
    for item in admission:
        for reason, count in item.get("rejections", {}).items():
            rejections[reason] = rejections.get(reason, 0) + int(count)
    capacities = [pool.get("configured_capacity") for pool in pools]
    return {
        "fresh_pools": len(pools),
        "stale_pools": len(payload["pools"]) - len(pools),
        "capacity": sum(capacities)
        if pools and all(value is not None for value in capacities)
        else None,
        "busy": sum(pool["busy_count"] for pool in pools) if pools else None,
        "available": sum(pool["available_slots"] for pool in pools)
        if pools and all(pool.get("available_slots") is not None for pool in pools)
        else None,
        "admission_attempts": sum(item.get("attempts", 0) for item in admission),
        "admission_successes": sum(item.get("successes", 0) for item in admission),
        "admission_wait_seconds_total": round(
            sum(item.get("wait_seconds_total", 0) for item in admission), 4
        ),
        "admission_wait_seconds_max": max(
            (item.get("wait_seconds_max", 0) for item in admission), default=0
        ),
        "admission_rejections": rejections,
    }


def prepare(url: str, scenario: str) -> tuple[dict[str, str], dict[str, str], str, str]:
    from tests.e2e.conftest import write_and_register
    from tests.e2e.fixtures.setup import _register_and_authenticate_user
    from tests.e2e.fixtures.users import E2EUser

    suffix = uuid4().hex[:10]
    with httpx.Client(base_url=url, timeout=60.0) as client:
        admin = _register_and_authenticate_user(
            client,
            E2EUser(
                email=f"issue-890-admin-{suffix}@gobifrost.dev",
                password="BenchmarkPass123!",
                name="Issue 890 Admin",
            ),
        )
        if not admin.is_superuser:
            raise RuntimeError("The test database was not reset before the benchmark")
        org_response = client.post(
            "/api/organizations",
            headers=admin.headers,
            json={"name": f"Issue 890 {suffix}", "domain": "gobifrost.dev"},
        )
        org_response.raise_for_status()
        org_id = org_response.json()["id"]
        user = E2EUser(
            email=f"issue-890-user-{suffix}@gobifrost.dev",
            password="BenchmarkPass123!",
            name="Issue 890 User",
        )
        stub = client.post(
            "/api/users",
            headers=admin.headers,
            json={
                "email": user.email,
                "name": user.name,
                "organization_id": org_id,
                "is_superuser": False,
            },
        )
        stub.raise_for_status()
        user = _register_and_authenticate_user(client, user)
        is_agent = scenario == "agent"
        fixture = {
            "agent": ("issue_890_agent_tool.py", AGENT_TOOL_SOURCE, "issue_890_agent_tool"),
            "external-http": (
                "issue_890_external_http.py",
                EXTERNAL_HTTP_SOURCE,
                "issue_890_external_http",
            ),
        }.get(scenario, ("issue_890_small.py", WORKFLOW_SOURCE, "issue_890_small"))
        workflow = write_and_register(
            client,
            admin.headers,
            *fixture,
            organization_id=org_id,
        )
        profile = client.get("/api/profile", headers=user.headers)
        profile.raise_for_status()
        if profile.json()["organization_id"] != org_id:
            raise RuntimeError("The benchmark user has the wrong organization")
        if is_agent:
            connection = client.post(
                "/api/admin/ai/connections",
                headers=admin.headers,
                json={
                    "name": f"Issue 890 {suffix}",
                    "provider": "openai_compatible",
                    "api_key": "fixture-only",
                    "endpoint": "http://scheduler-fixtures:8080/v1",
                },
            )
            connection.raise_for_status()
            profile = client.post(
                "/api/admin/ai/profiles",
                headers=admin.headers,
                json={
                    "name": f"Issue 890 {suffix}",
                    "connection_id": connection.json()["id"],
                    "model": "issue-890-agent",
                    "enabled_for_chat": False,
                },
            )
            profile.raise_for_status()
            agent = client.post(
                "/api/agents",
                headers=admin.headers,
                json={
                    "name": f"Issue 890 agent {suffix}",
                    "system_prompt": "Call the provided tool once, then answer ok.",
                    "channels": [],
                    "access_level": "authenticated",
                    "organization_id": org_id,
                    "tool_ids": [workflow["id"]],
                    "llm_profile_id": profile.json()["id"],
                    "max_iterations": 3,
                },
            )
            agent.raise_for_status()
            return user.headers, admin.headers, agent.json()["name"], org_id
        return user.headers, admin.headers, workflow["id"], org_id


async def _sample_pool(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    samples: list[dict],
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            response = await client.get("/api/platform/workers", headers=headers)
            response.raise_for_status()
            now = datetime.now(UTC)
            samples.append(
                {"time": now.isoformat(), **summarize_fresh_pools(response.json(), now)}
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            samples.append({"error": type(exc).__name__})
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            pass


async def _run_workflow(
    client: httpx.AsyncClient,
    scenario: str,
    headers: dict[str, str],
    workflow_id: str,
    org_id: str,
    index: int,
) -> str:
    input_data = {"value": index}
    if scenario == "external-http":
        input_data["delay_ms"] = (20, 50, 100)[index % 3]
    response = await client.post(
        "/api/workflows/execute",
        headers=headers,
        json={
            "workflow_id": workflow_id,
            "input_data": input_data,
            "sync": True,
            "org_id": org_id,
        },
    )
    response.raise_for_status()
    result = response.json()
    deadline = time.monotonic() + 120
    while result.get("status") not in {"Success", "Failed", "Completed"}:
        if time.monotonic() >= deadline:
            raise TimeoutError("workflow completion exceeded 120 seconds")
        await asyncio.sleep(0.1)
        polled = await client.get(
            f"/api/executions/{result['execution_id']}", headers=headers
        )
        polled.raise_for_status()
        result = polled.json()
    if result["status"] not in {"Success", "Completed"}:
        raise ValueError(f"workflow ended as {result['status']}")
    expected = (
        {"value": index, "source": "fixture"}
        if scenario == "external-http"
        else {"value": index, "ok": True}
    )
    if result.get("result") != expected:
        raise ValueError("workflow returned the wrong result")
    return result["execution_id"]


async def _run_request(
    client: httpx.AsyncClient,
    scenario: str,
    read_headers: dict[str, str],
    admin_headers: dict[str, str],
    workflow_id: str,
    org_id: str,
    index: int,
) -> str | None:
    if scenario == "api-read":
        response = await client.get("/api/profile", headers=read_headers)
        response.raise_for_status()
        if response.json()["organization_id"] != org_id:
            raise ValueError("tenant mismatch in profile response")
        return None
    if scenario == "agent":
        response = await client.post(
            "/api/agent-runs/execute",
            headers=admin_headers,
            json={
                "agent_name": workflow_id,
                "input": {"task": f"Run tool for item {index}"},
                "timeout": 120,
            },
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "completed":
            raise ValueError(f"agent ended as {result.get('status')}")
        if result.get("iterations_used", 0) < 2:
            raise ValueError("agent did not complete a tool-call loop")
        return None
    return await _run_workflow(
        client, scenario, admin_headers, workflow_id, org_id, index
    )


async def _sample_attempt_stages(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    execution_ids: list[str],
) -> tuple[list[dict[str, float]], int]:
    samples: list[dict[str, float]] = []
    errors = 0
    if not execution_ids:
        return samples, errors
    stride = max(1, len(execution_ids) // 30)
    for execution_id in execution_ids[::stride][:30]:
        try:
            response = await client.get(f"/api/executions/{execution_id}", headers=headers)
            response.raise_for_status()
            for attempt in response.json()["attempt_history"]["attempts"]:
                if attempt["status"] == "succeeded":
                    samples.append(attempt_intervals(attempt))
        except (httpx.HTTPError, KeyError, ValueError):
            errors += 1
    return samples, errors


async def run_level(
    url: str,
    scenario: str,
    read_headers: dict[str, str],
    admin_headers: dict[str, str],
    workflow_id: str,
    org_id: str,
    concurrency: int,
    operations: int,
) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    failures = 0
    examples: list[str] = []
    pool_samples: list[dict] = []
    execution_ids: list[str] = []

    async with httpx.AsyncClient(
        base_url=url,
        timeout=120.0,
        limits=httpx.Limits(
            max_connections=concurrency,
            max_keepalive_connections=concurrency,
        ),
    ) as client:

        async def one(index: int) -> None:
            nonlocal failures
            async with semaphore:
                started = time.perf_counter()
                try:
                    execution_id = await _run_request(
                        client,
                        scenario,
                        read_headers,
                        admin_headers,
                        workflow_id,
                        org_id,
                        index,
                    )
                    if execution_id:
                        execution_ids.append(execution_id)
                    latencies.append(time.perf_counter() - started)
                except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as exc:
                    failures += 1
                    if len(examples) < 3:
                        examples.append(type(exc).__name__ + ": " + str(exc)[:160])

        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        client_cpu_started = time.process_time()
        stop_sampling = asyncio.Event()
        pool_task = (
            asyncio.create_task(
                _sample_pool(client, admin_headers, pool_samples, stop_sampling)
            )
            if scenario != "api-read"
            else None
        )
        await asyncio.gather(*(one(index) for index in range(operations)))
        if pool_task:
            stop_sampling.set()
            await pool_task
        elapsed = time.perf_counter() - started
        client_cpu_seconds = time.process_time() - client_cpu_started
        finished_at = datetime.now(UTC).isoformat()
        stage_samples, stage_errors = await _sample_attempt_stages(
            client, admin_headers, execution_ids
        )
        stage_summary = {
            name: {
                "p50_seconds": percentile(values, 0.50),
                "p95_seconds": percentile(values, 0.95),
            }
            for name in ("dispatch", "queue", "claim_to_start", "running_to_terminal")
            if (values := [sample[name] for sample in stage_samples if name in sample])
        }
    return {
        "concurrency": concurrency,
        "requested": operations,
        "started_at": started_at,
        "finished_at": finished_at,
        "client_cpu_seconds": round(client_cpu_seconds, 4),
        "pool_samples": pool_samples,
        "attempt_stage_samples": len(stage_samples),
        "attempt_stage_errors": stage_errors,
        "attempt_stages": stage_summary,
        **summarize(latencies, failures, elapsed),
        "failure_examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=("api-read", "workflow", "external-http", "agent"), required=True
    )
    parser.add_argument(
        "--concurrency", type=int, nargs="+", default=[1, 4, 16, 32, 64, 128]
    )
    parser.add_argument("--operations", type=int, default=1000)
    args = parser.parse_args()
    url = os.environ.get("TEST_API_URL", "")
    if (
        os.environ.get("BIFROST_ENVIRONMENT") != "testing"
        or urlparse(url).hostname != "api"
    ):
        parser.error(
            "run only inside the isolated Bifrost test-runner against http://api"
        )
    if args.operations < 1 or any(value < 1 for value in args.concurrency):
        parser.error("operations and concurrency must be positive")
    if args.operations < max(args.concurrency):
        parser.error("operations must be at least the highest concurrency level")

    read_headers, admin_headers, workflow_id, org_id = prepare(url, args.scenario)
    result = {
        "scenario": args.scenario,
        "source_sha": os.environ.get("BIFROST_BENCH_SOURCE_SHA", "unknown"),
        "resource_samples": os.environ.get("BIFROST_BENCH_RESOURCE_FILE"),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "api_processes": int(os.environ.get("BIFROST_BENCH_API_PROCESSES", "1")),
        "levels": [],
    }
    for concurrency in args.concurrency:
        level = asyncio.run(
            run_level(
                url,
                args.scenario,
                read_headers,
                admin_headers,
                workflow_id,
                org_id,
                concurrency,
                args.operations,
            )
        )
        result["levels"].append(level)
        print(json.dumps(level), flush=True)
    fd = os.open(
        Path("/bifrost-results/issue-890-load.json"),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "w") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    return 1 if any(level["failed"] for level in result["levels"]) else 0


if __name__ == "__main__":
    sys.exit(main())
