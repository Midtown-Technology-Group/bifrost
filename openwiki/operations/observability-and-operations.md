---
type: operations-guide
title: Observability, Diagnostics, and Operational Controls
description: Documents health probes, optional OpenTelemetry export, scheduler diagnostics, metrics aggregation, and safe log handling.
tags: [observability, health, telemetry, diagnostics, operations]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-45d5b7e334187baf0faea713
    resource: repo://api/src/core/log_safety.py
  - id: openwiki-source-9a84dea256bc20d38a09a5a8
    resource: repo://api/src/core/metrics.py
  - id: openwiki-source-29edc162380b81f8d36ae787
    resource: repo://api/src/core/telemetry.py
  - id: openwiki-source-8495f85969371b705777318c
    resource: repo://api/src/routers/health.py
  - id: openwiki-source-f6085cd92335800e66a4e8b8
    resource: repo://api/src/routers/scheduler_diagnostics.py
  - id: openwiki-source-b1c5fdf0255a90709ec21f41
    resource: repo://docs/runbooks/otel-platform-observability.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# Observability, Diagnostics, and Operational Controls

Operational evidence comes from several layers: health probes assess process or dependency readiness, scheduler diagnostics explain leases and queued capacity, execution metrics maintain daily aggregates, and optional OpenTelemetry exports traces and runtime measurements. These answer different questions and should not be substituted for one another. See [development and verification](development-and-verification.md), [security and audit](security-and-audit.md), and [jobs and schedulers](../runtime/jobs-and-schedulers.md).

## Health and readiness

`/health` is a basic process response, and `/health/live` reports whether the API process can respond. `/health/ready` and `/health/detailed` probe the configured database, Redis, selected work-delivery backend, and object storage. Checks run with per-probe timeouts; an unhealthy component makes readiness return HTTP 503. If Postgres is the selected work-delivery backend, it is probed instead of RabbitMQ, while the response retains a stable RabbitMQ key marked not configured. Storage may likewise report not configured when no provider configuration exists (`api/src/routers/health.py`).

These endpoints report the state observed at request time; a source definition or prior successful response is not evidence of current readiness. Liveness is intentionally a narrower signal than readiness and should not be used to infer dependency health.

## Scheduler and queue diagnostics

The platform scheduler diagnostics endpoint is restricted to superusers. It reports whether the trigger lease is currently healthy, replica heartbeats and online status, available job slots, active platform jobs per replica, scheduled-task configuration and last run, and queue depth/age. It also exposes jobs waiting for scheduler memory and the maximum reported memory utilization among online replicas with usable limits (`api/src/routers/scheduler_diagnostics.py`).

For an execution or schedule issue, correlate the durable execution/job identifier and scheduler task/run record first. The diagnostics endpoint is an aggregate current-state view, not a complete event history; task-run history is provided by a separate route. See [durable jobs and schedulers](../runtime/jobs-and-schedulers.md) for execution state ownership.

## Metrics and traces

Execution completion updates daily metrics through atomic upserts. Organization IDs are parsed from supported wire forms; malformed IDs fall back to the global metric row, while valid organizations are aggregated both at organization and global levels. Failures updating metrics are logged but do not fail the completed execution. ROI time-saved and value fields count only successful executions (`api/src/core/metrics.py`).

OpenTelemetry trace and metric export is optional and enabled only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Missing SDK/exporter dependencies result in a warning and skip configuration; flush failures are also logged as warnings. Export includes service identity, event-loop-lag buckets, and optionally container CPU and memory observations. The runbook documents the intended trace join key and privacy rules, but also marks collector/backend/retention/dashboard adoption as a maturity gate; it is an operating plan, not proof that a backend is deployed (`api/src/core/telemetry.py`, `docs/runbooks/otel-platform-observability.md`).

## Log hygiene

User-controlled values interpolated into plain-text logs should pass through `log_safe`: it escapes carriage returns/newlines, removes control and ANSI sequences, and truncates long values. This reduces log forgery and terminal-control risk; it does not replace structured redaction or secret handling. Avoid putting request bodies, secrets, or raw workflow results into log or telemetry attributes (`api/src/core/log_safety.py`, `docs/runbooks/otel-platform-observability.md`).

No live diagnostics were queried for this page. Consult the authorized environment for current health, queue depth, scheduler leadership, telemetry delivery, or log data.
