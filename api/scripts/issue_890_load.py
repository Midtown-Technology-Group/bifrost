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
        is_external_http = scenario == "external-http"
        workflow = write_and_register(
            client,
            admin.headers,
            "issue_890_external_http.py" if is_external_http else "issue_890_small.py",
            EXTERNAL_HTTP_SOURCE if is_external_http else WORKFLOW_SOURCE,
            "issue_890_external_http" if is_external_http else "issue_890_small",
            organization_id=org_id,
        )
        profile = client.get("/api/profile", headers=user.headers)
        profile.raise_for_status()
        if profile.json()["organization_id"] != org_id:
            raise RuntimeError("The benchmark user has the wrong organization")
        return user.headers, admin.headers, workflow["id"], org_id


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
                    if scenario == "api-read":
                        response = await client.get(
                            "/api/profile", headers=read_headers
                        )
                        response.raise_for_status()
                        if response.json()["organization_id"] != org_id:
                            raise ValueError("tenant mismatch in profile response")
                    else:
                        input_data = {"value": index}
                        if scenario == "external-http":
                            input_data["delay_ms"] = (20, 50, 100)[index % 3]
                        response = await client.post(
                            "/api/workflows/execute",
                            headers=admin_headers,
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
                        while result.get("status") not in {
                            "Success",
                            "Failed",
                            "Completed",
                        }:
                            if time.monotonic() >= deadline:
                                raise TimeoutError(
                                    "workflow completion exceeded 120 seconds"
                                )
                            await asyncio.sleep(0.1)
                            polled = await client.get(
                                f"/api/executions/{result['execution_id']}",
                                headers=admin_headers,
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
                    latencies.append(time.perf_counter() - started)
                except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as exc:
                    failures += 1
                    if len(examples) < 3:
                        examples.append(type(exc).__name__ + ": " + str(exc)[:160])

        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        client_cpu_started = time.process_time()
        await asyncio.gather(*(one(index) for index in range(operations)))
        elapsed = time.perf_counter() - started
        client_cpu_seconds = time.process_time() - client_cpu_started
        finished_at = datetime.now(UTC).isoformat()
    return {
        "concurrency": concurrency,
        "requested": operations,
        "started_at": started_at,
        "finished_at": finished_at,
        "client_cpu_seconds": round(client_cpu_seconds, 4),
        **summarize(latencies, failures, elapsed),
        "failure_examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=("api-read", "workflow", "external-http"), required=True
    )
    parser.add_argument(
        "--concurrency", type=int, nargs="+", default=[1, 4, 16, 32, 64, 128]
    )
    parser.add_argument("--operations", type=int, default=1000)
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/bifrost/issue-890-load.json")
    )
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 1 if any(level["failed"] for level in result["levels"]) else 0


if __name__ == "__main__":
    sys.exit(main())
