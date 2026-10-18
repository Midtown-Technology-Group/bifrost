---
type: runtime-architecture
title: Execution and Workflow Runtime
description: Explains workflow admission, RabbitMQ dispatch, Redis acceleration, process isolation, durable logical executions and attempt fencing.
tags: [workflows, execution, workers, attempts, runtime]
sources:
  - id: openwiki-source-bf2182bcb312690f213f7ee8
    resource: repo://api/shared/execution_logs.py
  - id: openwiki-source-6ff2782f53931ed66f7ddcf6
    resource: repo://api/shared/sdk_execution_reads.py
  - id: openwiki-source-831be83c4f4d5fe24e4d9d76
    resource: repo://api/src/jobs/consumers/workflow_execution.py
  - id: openwiki-source-be1538d18358c04a07f6013d
    resource: repo://api/src/jobs/execution_policy.py
  - id: openwiki-source-4454fce9a9f47d443b4dcf67
    resource: repo://api/src/models/orm/execution_attempts.py
  - id: openwiki-source-33752a5fd05810cf391ad12c
    resource: repo://api/src/routers/executions.py
  - id: openwiki-source-6fe582b161bf99b44765f04f
    resource: repo://api/tests/unit/shared/test_execution_logs.py
  - id: openwiki-source-b86f97d98fbf2bd787797f6a
    resource: repo://docs/architecture/workflow-execution-attempts.md
generated: { by: "bifrost-atlas", at: "2026-10-18T09:57:17.518Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T09:57:17.518Z
---

# Execution and Workflow Runtime

A workflow execution is a logical run with a stable user-facing identity; an execution attempt is the durable evidence for one runner claim. The API dispatch path, RabbitMQ consumer, Redis execution context, PostgreSQL state, and isolated process pool cooperate, but they own different pieces of the lifecycle. Recovery protects durable state and rejects stale results; it does not imply that arbitrary workflow code can always be replayed safely. See [agents and chat](agents-and-chat.md), [events and schedules](events-and-schedules.md), and [jobs and schedulers](jobs-and-schedulers.md).

## Workflow discovery and admission

Workflow metadata is discovered and synchronized into the database; API routes query that metadata rather than scanning the filesystem on each request. Organization scope distinguishes global workflows from organization-scoped workflows and filters discovery according to caller authority (`api/src/routers/workflows.py`). Execution requests pass through shared admission and policy boundaries; the workflow queue uses RabbitMQ and is admitted by the process pool. Policy metadata names the durable authority, retry profile, cancellation mode, timeout, memory policy, and worker-loss semantics for each execution surface (`api/src/jobs/execution_policy.py`).

## Dispatch and worker flow

The workflow consumer receives a compact broker message keyed by execution ID and retrieves full pending context from Redis. The API stores pending execution context in Redis and publishes through RabbitMQ; the consumer creates or finds durable PostgreSQL execution state before routing work to the process pool. Pool workers are separate processes and reused for efficiency. Synchronous callers receive a result through Redis; asynchronous callers observe the durable execution record and updates (`api/src/jobs/consumers/workflow_execution.py`).

This division makes PostgreSQL the durable execution authority, RabbitMQ the dispatch transport, and Redis an acceleration/ephemeral-context layer. Loss of Redis context is handled as a result/context failure rather than silently accepting an unfenced result. The process pool owns per-child isolation, timeout/crash reporting, and bounded worker admission (`api/src/jobs/consumers/workflow_execution.py`, `docs/architecture/workflow-execution-attempts.md`).

## Attempts and completion fencing

`ExecutionAttempt` records logical job identity, attempt number/status, policy and admission labels, broker message/queue references, retries/replays, worker/process identity, lease token, failure classification, and timestamps. A unique logical-job/attempt-number constraint orders history and a partial unique active-lease index supports claim fencing (`api/src/models/orm/execution_attempts.py`).

Result callbacks acquire the execution lock and finalize the attempt and logical execution together. Missing attempt fences are rejected when attempt history exists; stale callbacks for a run no longer in a running/cancelling state are discarded. This prevents an old child result from overwriting newer or terminal state. Where source policy allows retry after a defined worker/process failure, the consumer evaluates that policy from the durable attempt history; this is distinct from indiscriminately replaying tenant code (`api/src/jobs/consumers/workflow_execution.py`, `api/src/jobs/execution_policy.py`).

Execution detail routes retain caller authorization and return not-found or forbidden responses as appropriate. Result, logs, variables, cancellation, and cleanup have separate routes and semantics; a listing or detail read should not be treated as a cancellation or replay action (`api/src/routers/executions.py`).

Persisted execution-log reads sort rows by stored sequence and database ID, filter DEBUG/TRACEBACK entries for non-superusers, and then expose zero-based response-local IDs while preserving each stored sequence. In-progress Redis-stream reads use the raw stream ordinal for both `id` and `sequence` before filtering hidden levels, so visible IDs may have gaps. The persisted projection therefore does not describe the separate live-stream path (`api/shared/execution_logs.py`, `api/src/routers/executions.py`, `api/shared/sdk_execution_reads.py`).

## Failure and operational interpretation

Admission rejection, worker loss, timeout, cancellation, result persistence failure, and ordinary workflow failure are separate outcomes. The consumer maps terminal attempt state and failure codes, and downstream notifications/metrics are derived from durable completion. Attempt history provides bounded causal evidence, but it deliberately avoids copying workflow parameters, results, variables, or logs into the attempt record (`api/src/jobs/consumers/workflow_execution.py`, `api/src/models/orm/execution_attempts.py`, `docs/architecture/workflow-execution-attempts.md`).

For an execution that appears stuck, correlate the logical execution row with its current attempt, claim/worker identity, timestamps, and failure code. Distinguish a delayed queue dispatch from an admitted child process and a failed terminal callback. This source-backed architecture page does not assert live queue health or execution outcomes.
