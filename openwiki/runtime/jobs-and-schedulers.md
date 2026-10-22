---
type: runtime-architecture
title: Durable Jobs, Queues, and Schedulers
description: Explains shared PlatformJob durability, RabbitMQ and PostgreSQL delivery mechanisms, scheduler replica roles, leases, retry, and operator status.
tags: [platform-jobs, queues, scheduler, leases, durability]
sources:
  - id: openwiki-source-dab3d8eb8901f371bb5978a9
    resource: repo://api/src/jobs/platform/runner.py
  - id: openwiki-source-19771b0e151793a19d428dba
    resource: repo://api/src/jobs/postgres_delivery.py
  - id: openwiki-source-9e935d6aa02da5dc18ced706
    resource: repo://api/src/jobs/rabbitmq.py
  - id: openwiki-source-f84e79c76b4307c69dfc366f
    resource: repo://api/src/routers/platform_jobs.py
  - id: openwiki-source-246812ad788094feb1dbb9cd
    resource: repo://api/src/scheduler/leadership.py
  - id: openwiki-source-9cf1f741d4027775051fd105
    resource: repo://api/src/worker/app.py
  - id: openwiki-source-fee556be92c51c9a45831bd0
    resource: repo://api/src/worker/main.py
  - id: openwiki-source-319b1ec69184b5f0bba933eb
    resource: repo://api/tests/unit/worker/test_app.py
  - id: openwiki-source-36c18eac42a2147e8af8371c
    resource: repo://docs/architecture/platform-jobs.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:51:00.673Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Durable Jobs, Queues, and Schedulers

Bifrost uses separate durable authorities for three classes of work: workflow and agent executions have their domain execution records; non-workflow platform operations use the shared `PlatformJob` model; RabbitMQ or optional PostgreSQL work-delivery rows transport consumer messages. Scheduler replicas host platform jobs and compete for a database lease that elects one trigger leader. See [execution and workflows](execution-and-workflows.md), [data and contracts](../architecture/data-and-contracts.md), and [observability and operations](../operations/observability-and-operations.md).

## PlatformJob authority

`PlatformJob` is the canonical durable path for system work that outlives an HTTP request and needs persisted progress/outcome, deduplication, bounded retry, timeout, cancellation, or resource protection. Existing migrated families include publishing, OAuth refresh, webhook renewal, Solution lifecycle, App builds, embedding reindex, workspace reimport/Git operations, and agent-summary backfills. New platform work should extend the shared registry and lifecycle rather than add a feature-specific job table or poller (`docs/architecture/platform-jobs.md`).

PostgreSQL owns each row's payload version, requester/scope, progress, result or structured error, retry policy, timestamps, and lease state. Each scheduler replica runs a small fixed number of claim loops: recover expired leases, claim rows with `FOR UPDATE SKIP LOCKED`, fence the claim with a lease token, run the registered handler in a fresh child process/group, heartbeat while enforcing timeout/cancellation/memory rules, then record terminal state or retry an eligible lost attempt. Handler payload schema mismatch and unknown job type fail closed. Retry behavior is policy-controlled (`docs/architecture/platform-jobs.md`, `api/src/jobs/platform/runner.py`).

An operation can defer the parent job while tracking child work, allowing a scheduler slot to return before the child finishes. For opt-in Kubernetes App builds, PostgreSQL remains authority; the scheduler records backend and Kubernetes identity, while a controller launches a fenced Job. Kubernetes is execution capacity, not an alternate source of job status or retry truth (`docs/architecture/platform-jobs.md`).

## Queue delivery mechanisms

RabbitMQ consumers share delivery policy for retryable infrastructure errors, permanent malformed messages, duplicate messages, and domain failures that have already been recorded. Queues declare durable poison/dead-letter topology. Delivery context tracks retry/replay counts, idempotency metadata, and broker identity (`api/src/jobs/rabbitmq.py`).

The optional PostgreSQL delivery backend adapts leased `WorkDelivery` rows to the shared consumer outcome contract; it does not emulate AMQP topology or connections. Its poller claims rows under lease ownership, settles completed/queued/poison outcomes, renews before shared poison handling, and interrupts work on shutdown. Lease ownership loss prevents a stale consumer from settling another owner's row (`api/src/jobs/postgres_delivery.py`).

## Scheduler ownership and leader election

Every scheduler replica can claim durable platform jobs, but only one holds the `scheduler-triggers` lease and runs APScheduler trigger callbacks. The lease is acquired/renewed with database time and a fresh generation token; renewal and release require the current owner/token so an expired leader cannot release a newer generation (`api/src/scheduler/leadership.py`). The elected callback should remain short; long-running recurring work should enqueue a deduplicated PlatformJob. Workflow cron sources are processed by this trigger lane; see [events and schedules](events-and-schedules.md).

## Worker process and shutdown lifecycle

The RabbitMQ/worker process is distinct from scheduler-trigger leadership: `src.worker.main` installs an early SIGTERM handler before importing the application, while the application registers asynchronous SIGINT/SIGTERM shutdown handlers once the event loop is running ([thin entrypoint](../../api/src/worker/main.py#L1-L24), [signal registration](../../api/src/worker/app.py#L494-L507)). The worker validates its CA bundle, initializes the database and execution schema, then starts selected consumers. By default the consumer set includes workflow, package-install, agent-run, summarize, summarize-backfill, and tune-chat; `BIFROST_WORKER_CONSUMERS` can explicitly narrow it, rejects empty/unknown names, and workflow-only consumers require an isolated `-canary` queue or an explicitly enabled production queue ([consumer names and workflow queue guard](../../api/src/worker/app.py#L57-L124), [selection validation](../../api/src/worker/app.py#L127-L141), [startup sequence](../../api/src/worker/app.py#L170-L219)). With PostgreSQL delivery, package-install is excluded as a separate consumer and requires the workflow worker's control poller ([PostgreSQL startup rule](../../api/src/worker/app.py#L257-L272)).

On shutdown, the worker stops claiming service work, drains consumers concurrently up to `BIFROST_DRAIN_DEADLINE_SECONDS` (default 300 seconds), retries failed consumer stop a bounded number of times while shared database resources remain open, then closes broker/HTTP/database resources ([drain policy](../../api/src/worker/app.py#L367-L413), [bounded stop retries and close](../../api/src/worker/app.py#L417-L474)). The Kubernetes worker manifest allocates 360 seconds for termination, which is manifest intent with cleanup margin and should remain greater than the configured drain budget ([worker termination budget](../../k8s/worker/deployment.yaml#L84-L99)). Focused worker tests cover consumer selection, lifecycle/drain and stop retries ([worker startup and consumer tests](../../api/tests/unit/worker/test_app.py#L193-L245), [drain and stop tests](../../api/tests/unit/worker/test_app.py#L407-L528)). Source and tests describe intended lifecycle; they do not prove a running worker has drained successfully.

## Operator visibility

Users can list and query their own platform jobs; platform administrators can inspect across users. Status includes bounded result/progress and cancellation acceptance. Scheduler diagnostics expose trigger leader health, replicas, active claims, queue age, memory waits/utilization, and last scheduled-task outcomes (`api/src/routers/platform_jobs.py`, `api/src/routers/scheduler_diagnostics.py`). These are point-in-time views. Source and policy definitions do not establish current queue or cluster health.
