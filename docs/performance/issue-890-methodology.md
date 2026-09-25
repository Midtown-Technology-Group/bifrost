# Issue 890 performance study

This is the execution plan for [issue #890](https://github.com/Midtown-Technology-Group/bifrost/issues/890). The issue remains the scope and decision authority. Keep measured results in dated files beside this plan, with the exact source revision and commands used.

## Measurement order

1. Describe a reduced, anonymous production workload: payload and queue-item sizes, workflow nodes, integration/tool calls, agent loop lengths, and observed concurrency. Record how each shape was obtained. Never copy tenant identifiers, secrets, or customer content into fixtures.
2. Measure the current production runtime and the best reasonably supported Python configuration on the **same** workload and compute budget. Record Python version, architecture, CPU, native library versions, dependency-lock revision, process and worker counts, async settings, and benchmark mode. Test cheap changes such as process topology, blocking calls in async paths, serialization, and avoidable model conversion before comparing languages.
3. Attribute request and execution **wall time** to Python CPU, native-library CPU, PostgreSQL wait, external API/model wait, queue wait, and other time. Use sampling profiles plus DB, HTTP, and queue telemetry. Coarse endpoint, feature, integration, workflow type, and runtime-role labels are useful; customer identifiers are not. Do not label aggregate host CPU as Python CPU.
4. Ramp concurrency through 1, 4, 16, 32, 64, and 128, extending only if the knee is not visible. Measure throughput/core, latency percentiles, failures, CPU, RSS, event-loop lag, DB pool pressure, queue depth, and external wait. Continue briefly beyond the knee, return to baseline load, and record whether latency, memory, event-loop lag, DB pools, and throughput recover without restart.
5. Reproduce the production B3 shape with API, scheduler, and four workers. Attribute CPU and memory separately to API, scheduler, worker classes/processes, and renderer work where present. Compare one Python process with high async concurrency against multiple processes under the same total CPU and memory limit. Record useful executions/B3-hour and the first saturated resource.
6. Only if profiling finds a concentrated runtime-bound subsystem, implement one narrow Go equivalent. Use the same fixtures and dependencies. Prove parity for success, validation, errors, retries, timeouts, idempotency, persistence, and claim/ordering semantics before comparing speed. Restore realistic DB/network/API latency for the final comparison. Rust follows only if the Go result and a Rust-specific requirement justify it.

## Benchmark layers

| Layer | Purpose | Initial implementation |
| --- | --- | --- |
| CodSpeed simulation | Stable regression checks for pure CPU helpers | Retain `api/benchmarks/test_benchmarks.py`; organize only when adding related cases. |
| CodSpeed walltime | Representative operation latency and differential profiles | Add authenticated API/DB read, small workflow, queue round trip, nested serialization, agent loop, and mocked integration cases incrementally. |
| CodSpeed memory | Allocation and density changes for those same operations | Reuse macro fixtures and record peak/retained memory where supported. |
| External load harness | Whole-system saturation, overload, and recovery | Fixed production-shaped fixture, bounded concurrency ramp, B3 resource limits, per-role measurements. |
| Production telemetry | Check whether the fixture resembles real workload shape | Compare anonymized distributions and role resource use; do not replay customer data. |

CodSpeed's current Python 3.12 simulation workflow is useful for small helpers but cannot establish B3 capacity or cost. Measure CI variance before considering dedicated runners. Use the current supported CodSpeed walltime/memory interface when those cases are implemented; do not assume the illustrative commands in the issue are supported. Record tool versions and modes with results.

## Decision record

For every candidate, report the Python baseline, the first saturated resource, the measured end-to-end gain at realistic I/O latency, p95/p99 and recovery behavior, memory per role, and cost per useful execution. A synthetic CPU speedup alone does not pass the issue's decision gates. Keep the current Python product surface unless a bounded extraction demonstrably changes real capacity, latency, density, or operations enough to justify its migration cost.

The first committed slice emits `bifrost.event_loop.lag` from the API under the `bifrost-api` OpenTelemetry service. Worker and scheduler lag, CPU, and memory must be captured separately during the production-shaped experiment; this API series alone cannot identify host saturation.

## Production telemetry inventory (2026-09-24, read only)

Azure resource `app-mtg-bifrost-production` is running on one B3 App Service plan with `api`, `client`, `renderer`, `scheduler`, and `worker` site containers. Its live settings specify four workers, concurrency four, database pool size two, and overflow three. `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, and `OTEL_LOGS_EXPORTER` are all `none`; `OTEL_EXPORTER_OTLP_ENDPOINT` is absent, and the app has no Azure diagnostic settings. The code's OTel provider also returns immediately without that endpoint. Consequently the existing workflow spans and new event-loop metric are not being exported from this production deployment.

Azure Monitor's App Service metrics remain available. In the 24-hour window queried on September 24/25 UTC, they reported 10,631 requests and 230 HTTP 5xx responses. Hourly mean working set averaged about 1.70 GB, with a maximum sample of 3.00 GB. These are app-level aggregates; they cannot identify endpoint mix, API versus worker CPU, queue wait, Python CPU, or execution semantics. The request count is not a replay fixture by itself, and this short observation must not be treated as a capacity limit.

Production instrumentation is justified before a language decision. First use a bounded, sampled export through the existing OTel hooks into an approved collector with retention and cardinality limits. Collect coarse route/workflow category, API/worker/scheduler role, queue wait, DB and outbound wait, event-loop lag, and per-role CPU/RSS. Keep customer content and identifiers out of benchmark fixtures and metric labels. Compare those distributions with the isolated B3 lab; run overload tests only in the lab. Confirm collector reachability and export overhead before enabling a production-wide stream.

The first September 25 lab sweep used the repository's test Compose stack and found that its API and scheduler run under `coverage`. Its latency and throughput are harness smoke data, not a production-runtime baseline. The issue-specific Compose override removes coverage from those roles and sets the worker concurrency and process cap to the live value of four. Future reported capacity runs must use that override and record its configuration.

The next sweep exposed two measurement hazards: a 99 Hz `py-spy` attach fell behind and disturbed request latency, and the HTTP load client initially retained only its default 20 keepalive connections even at concurrency 128. Exclude the disturbed run from capacity conclusions. The harness now retains as many connections as its requested concurrency; rerun the curve without a profiler before identifying a server-side knee. Profiling should be a separate, lower-rate experiment with its own overhead check.
