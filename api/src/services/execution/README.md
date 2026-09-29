# Execution Engine

Distributed execution system for running workflows, scripts, and data providers
in fresh isolated processes. PostgreSQL is authoritative for durable workflow
identity and status **and** for production work delivery: with
`BIFROST_WORK_DELIVERY_BACKEND=postgres`, dispatchers enqueue into the
`work_deliveries` table and workers claim deliveries under a 90-second
ownership lease. The checked-in default is still `rabbitmq`
(`api/src/config.py:100`); PostgreSQL is selected by promoted production
configuration (runbook: `bifrost-infra`), whose deployed value is not verified
from this repository. Redis carries
bounded ephemeral context, live logs, cancellation signals, and synchronous
results. RabbitMQ remains only as a legacy/compatibility transport for the
same consumer contract.

## Architecture Overview

```text
                          API Request
                               |
                               v
+---------------------------+  |  +---------------------------+
|        service.py         |  |  |      async_executor.py    |
|  - Workflow lookup        |<-+->|  - Pin PostgreSQL run     |
|  - Metadata resolution    |     |  - Store Redis context    |
|  - Sync/async dispatch    |     |  - Enqueue delivery       |
+---------------------------+     +---------------------------+
                                              |
                         execution-ID advisory transaction fence
                                              |
                                              v
                                     +------------------+
                                     |   PostgreSQL     |
                                     | work_deliveries  |
                                     | (RabbitMQ when   |
                                     |  legacy mode)    |
                                     +------------------+
                                              |
                       poll + 90s lease claim (prod) /
                       broker delivery (legacy)
                                              v
+------------------------------------------------------------------+
|                  workflow_execution.py (Consumer)                 |
|  - Read pending execution from Redis                              |
|  - Claim delivery lease + PostgreSQL execution + durable attempt  |
|  - Pre-warm SDK cache                                             |
|  - Route to ProcessPoolManager                                    |
+------------------------------------------------------------------+
                                              |
                                              v
+------------------------------------------------------------------+
|                    process_pool.py (ProcessPoolManager)           |
|  - Fork one-shot children on demand                               |
|  - Cap concurrent children at max_workers                         |
|  - Monitor timeouts and crashes                                   |
|  - Heartbeat publishing for UI visibility                         |
+------------------------------------------------------------------+
                                              |
                        +---------------------+---------------------+
                        |                     |                     |
                        v                     v                     v
                 +-----------+         +-----------+         +-----------+
                 |  Worker   |         |  Worker   |         |  Worker   |
                 | Process 1 |         | Process 2 |         | Process N |
                 +-----------+         +-----------+         +-----------+
                        |
                        v
+------------------------------------------------------------------+
|                     simple_worker.py                              |
|  - Isolated subprocess for user code                              |
|  - Receive context from the parent's private pipe                 |
|  - Clear workspace modules (pick up code changes)                 |
|  - Execute via engine.py                                          |
|  - Return one result, then exit                                   |
+------------------------------------------------------------------+
                        |
                        v
+------------------------------------------------------------------+
|                        engine.py                                  |
|  - Unified execution for workflows, scripts, data providers       |
|  - Set up SDK context (bifrost._context)                          |
|  - Variable capture via sys.settrace()                            |
|  - Real-time log streaming to Redis                               |
|  - Type coercion for parameters                                   |
+------------------------------------------------------------------+
                        |
                        v
              Result via private pipe
                        |
                        v
+------------------------------------------------------------------+
|               workflow_execution.py (Result Handler)              |
|  - Update PostgreSQL with result                                  |
|  - Flush SDK writes (Redis -> Postgres)                           |
|  - Flush logs (Redis Stream -> Postgres)                          |
|  - Publish WebSocket updates                                      |
|  - Push sync result to Redis (for BLPOP)                          |
|  - Cleanup Redis keys                                             |
+------------------------------------------------------------------+
```

## Key Files

| File | Responsibility |
|------|----------------|
| `service.py` | High-level orchestration. Workflow lookup by ID, metadata caching, sync/async dispatch routing, and PostgreSQL fallback when a synchronous Redis wait expires. Entry point for `run_workflow()` and `run_code()`. |
| `engine.py` | Unified execution engine. Handles workflows, inline scripts, and data providers. Sets up SDK context, captures variables via `sys.settrace()`, streams logs to Redis, handles data provider caching. |
| `async_executor.py` | Dispatch management. Pins immutable execution/runtime evidence in PostgreSQL, stores ephemeral context in Redis, enqueues the minimal dispatch message via `publish_message` (PostgreSQL `work_deliveries` in production mode, RabbitMQ in legacy mode), and returns the execution ID. |
| `process_pool.py` | One-shot child lifecycle management. Forks on demand up to `max_workers`, handles timeouts (SIGTERM -> SIGKILL), detects crashes, and publishes heartbeats. |
| `simple_worker.py` | Isolated subprocess entry point. Receives one parent-assembled context over a private pipe, clears workspace modules, delegates to `engine.py`, returns one result, and exits. |
| `workflow_execution.py` | Work consumer (both transports). Claims a delivery — a PostgreSQL lease in production mode, a broker delivery in legacy RabbitMQ mode — then claims a durable attempt, resolves pinned metadata, routes to the process pool, token-fences results, flushes data, and publishes updates. |

## Execution States

```text
SCHEDULED   Durable execution exists; delivery enqueue is unconfirmed or deferred
    |
    v
PENDING     Delivery enqueue confirmed (PostgreSQL `work_deliveries` row in
            production mode, broker publication in legacy RabbitMQ mode)
    |
    v
RUNNING     Consumer claimed the run (attempt records claim/start separately)
    |
    +---> SUCCESS              Completed successfully
    |
    +---> FAILED               Execution error (exception thrown)
    |
    +---> COMPLETED_WITH_ERRORS  Returned {success: false}
    |
    +---> TIMEOUT              Exceeded timeout_seconds
    |
    +---> CANCELLED            User cancelled via API
```

An authorized cancellation may project `CANCELLING` between `RUNNING` and
`CANCELLED`. Historical executions created before attempt tracking return
`legacy_unavailable` coverage rather than a fabricated attempt.

## Data Flow

### Async Execution (Default)

1. API calls `run_workflow()` or `run_code()`
2. `async_executor.py` creates a PostgreSQL `SCHEDULED` row and `dispatching`
   attempt with immutable dispatch/runtime evidence under the execution lock
3. Ephemeral context is stored in Redis and a minimal dispatch message is
   enqueued — a `work_deliveries` row in production PostgreSQL mode, a broker
   publication with confirmation in legacy RabbitMQ mode
4. In PostgreSQL mode the enqueue happens in the same fenced transaction that
   advances the logical row to `PENDING` and the attempt to `published`, so a
   consumer can never race an uncommitted row. In legacy RabbitMQ mode the
   broker publication happens before the DB commit, so that atomicity
   guarantee does not apply
5. Consumer claims the delivery — a `SELECT ... FOR UPDATE SKIP LOCKED` lease
   claim on `work_deliveries` in production mode — assigning the durable
   attempt an internal capability token and worker incarnation
6. Consumer resolves pinned metadata and routes a fresh child through
   `ProcessPoolManager`
7. The delivery is settled after durable claim and successful child routing,
   before tenant code completes. In production mode this releases the
   PostgreSQL lease (`completed`); recovery after that boundary comes from the
   durable attempt lease and heartbeat, not transport redelivery. In legacy
   mode the broker acknowledges the message.
8. The pool copies the token into real and synthetic timeout/cancel/crash
   results returned over the private result pipe
9. Consumer accepts only the active token, terminalizing attempt and logical
   execution before publishing updates
10. Client treats WebSocket updates as live hints and reloads durable detail

Delayed retries apply only to failures before child execution, such as
admission pressure: production mode requeues the delivery with
`available_at` delayed, legacy mode uses Rabbit delayed-retry queues.
Exhausted or malformed deliveries go to poison handling. Tenant workflow code
is never automatically replayed.

## Production delivery: leases, heartbeats, recovery, settlement

In PostgreSQL mode (`BIFROST_WORK_DELIVERY_BACKEND=postgres`, the production
transport), every delivery is a row in `work_deliveries`
(`api/src/services/work_delivery_store.py`):

- **Lease claim.** `claim_deliveries` assigns each claimed row an owner, a
  unique lease token, and a 90-second expiry (`LEASE_SECONDS`). The same
  consumer outcome contract (retry, poison, domain finalization) runs on both
  transports, but the transports differ beyond claim mechanics: PostgreSQL
  mode adds lease heartbeats with ownership fencing, PostgreSQL-backed
  settlement, and interrupted-delivery recovery, while legacy mode relies on
  broker delivery and acknowledgement.
- **Heartbeat / ownership.** The worker renews the lease while the delivery
  is in flight. `require_delivery_ownership` fences domain admission inside
  its own transaction: if the row is no longer `claimed` under the same token
  with a live expiry, it raises `DeliveryOwnershipLost` and the handler must
  not admit or settle domain work.
- **Interrupted-delivery recovery.** A maintenance sweep
  (`interrupt_expired_deliveries`) marks leases whose expiry passed as
  `interrupted`. `recover_interrupted_delivery` then recovers each one under
  the domain lock: derived LLM queues retry within the existing retry-header
  budget, an exhausted summary is durably failed, and agent execution loss is
  terminalized as `worker_lost` without replaying tools.
- **Settlement / retry boundary.** `settle_delivery` atomically resolves a
  delivery to `completed`, `poison`, or `queued` (delayed retry via
  `delay_seconds`) — but only while the caller still owns the lease; a lost
  lease returns `False` and the caller must not claim settlement. Retry
  budgets bound recovery signals; they do not guarantee an interrupted domain
  operation can resume automatically.
- **What stays Redis-backed.** Pending execution context, the sync-result
  `BLPOP` list, log streams, cancellation pub/sub, and worker
  registration/heartbeats remain ephemeral in Redis and are not durable
  delivery state.

Persisted inline-code execution retains a legacy Redis-first creation path and
therefore reports unavailable attempt coverage until that path is reconciled.
Transient editor execution is intentionally not durable.

### Sync Execution (Tool Calls, `sync=True`)

1. Steps 1-8 same as async
2. Consumer pushes result to Redis list: `bifrost:result:{execution_id}`
3. API waits on `BLPOP` for result (up to timeout)
4. If that Redis wait expires, API checks the authoritative PostgreSQL execution
   projection for a terminal result
5. API returns the durable terminal result when present, otherwise the polling
   timeout response

```python
# Sync execution with BLPOP
result = await redis_client.wait_for_result(execution_id, timeout_seconds=1800)
```

## SDK Context Injection

The execution engine injects context for the Bifrost SDK:

```python
# engine.py sets up context before execution
from bifrost._context import set_execution_context

context = ExecutionContext(
    user_id=request.caller.user_id,
    email=request.caller.email,
    organization=request.organization,
    execution_id=request.execution_id,
    _config=request.config,      # Integration credentials
    startup=request.startup,     # Launch workflow results
    roi=roi,                     # ROI tracking
)
set_execution_context(context)
```

Workflows access via:
```python
from bifrost import context

# Available in @workflow functions
context.user_id
context.organization.name
context.startup["launch_workflow_result"]
```

## Error Handling

### Timeouts

Process pool monitors execution duration:

```python
# process_pool.py
if elapsed > exec_info.timeout_seconds:
    # 1. Send SIGTERM for graceful shutdown
    os.kill(pid, signal.SIGTERM)

    # 2. Wait grace period
    await asyncio.sleep(graceful_shutdown_seconds)

    # 3. Force kill if still running
    os.kill(pid, signal.SIGKILL)

    # 4. Report timeout via callback
    await on_result({
        "execution_id": exec_info.execution_id,
        "success": False,
        "error_type": "TimeoutError",
    })
```

### Process Crashes

Pool detects crashed processes and reports any interrupted execution. The
next request forks a fresh one-shot child:

```python
# process_pool.py
if not handle.is_alive and handle.state != ProcessState.KILLED:
    # Report crash if execution was in progress
    if handle.current_execution:
        await _report_crash(handle.current_execution)

    # The next route_execution call forks on demand
```

### Cancellation

Cancellation requests via Redis pub/sub:

```python
# Client publishes to bifrost:cancel channel
await redis.publish("bifrost:cancel", {"execution_id": "..."})

# Pool listens and kills the process
await _handle_cancel_request(execution_id)
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `max_workers` | 10 | Maximum worker processes |
| `execution_timeout_seconds` | 300 | Process-pool fallback; registered workflows carry pinned timeout policy |
| `graceful_shutdown_seconds` | 5 | Time between SIGTERM and SIGKILL |
| `worker_heartbeat_interval_seconds` | 10 | Heartbeat publish interval |
| `worker_registration_ttl_seconds` | 30 | Redis registration TTL |

Environment variables:
```bash
BIFROST_MAX_WORKERS=10
BIFROST_EXECUTION_TIMEOUT_SECONDS=300
```

## Template Recycling

Execution children are never reused. After a package install or manual recycle
request, the pool lets active one-shot children drain and restarts the
long-lived import template. The next execution forks from the new template.

## Redis Keys

| Key Pattern | Purpose | TTL |
|-------------|---------|-----|
| `bifrost:pending:{execution_id}` | Pending execution context | 1 hour |
| `bifrost:exec:{execution_id}:context` | Worker process context | 1 hour |
| `bifrost:result:{execution_id}` | Sync execution result (BLPOP) | 1 hour |
| `bifrost:pool:{worker_id}` | Worker registration/heartbeat | 30 seconds |
| `bifrost:logs:{execution_id}` | Real-time log stream | Until flush |
| `bifrost:workflow:metadata:{workflow_id}` | Cached workflow metadata | 5 minutes |
