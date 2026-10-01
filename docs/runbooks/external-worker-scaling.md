# Optional external workflow workers

The default runtime is unchanged. `BIFROST_EXTERNAL_WORKER_SCALING_ENABLED=true`
starts a scheduler-owned controller using the native Postgres work-delivery and
execution state. It publishes opaque, permanent Azure Queue wake markers, not
work payloads. Demand is binary: zero after 120 continuous idle seconds, or the
configured maximum (initially two). Failed observations retain the last markers;
the superuser `/api/platform/external-workers` endpoint reports freshness.

Configuration binds one ARM container app resource ID, queue account/name,
managed identity tenant/client/principal, and Defined network/role. The app must
have no ingress, Consumption only, minimum zero, the matching maximum, a native
workflow-only worker, and `BIFROST_SERVICE_CLAIM_ENABLED=false`. The controller
reads the reviewed worker queue from the ARM definition: an isolated
`workflow-executions-...-canary` queue or `workflow-executions`. Production queue
access additionally requires `BIFROST_WORKER_WORKFLOW_QUEUE_SCOPE=production`.
Long-lived services and other consumer classes remain on the core workers.

The enrollment broker verifies signed tenant-fixed Entra application tokens for
the exact worker managed identity, verifies actual ARM replica membership, and
uses the encrypted global Defined Networking integration. Per-replica intent is
committed before the provider request. Lost responses are recovered by exact
name/network/role lookup; restarts re-enroll the same host. One-use startup codes
are encrypted briefly, never logged, and removed on a new boot. Unknown hosts
are never swept by prefix. Recorded hosts must remain absent for 120 seconds
and pass provider ownership verification before deletion and 404 readback.

Observe demand freshness (healthy at <=30 seconds), pending/active counts,
actual replicas and tracked hosts. Zero pending deliveries alone is insufficient:
running/cancelling executions and incomplete attempts retain capacity. While
active, the controller never consumes, hides or deletes wake markers. No new
schema is required; durable bookkeeping uses global SystemConfig rows and a
Postgres transaction advisory lock.

Before changing an ACA revision, fence its intake and prove active work drained.
Stopping the controller retains demand and is not a drain mechanism. Preserve
core worker capacity until real production execution, cancellation, recovery and
platform drain are independently verified. The infra release runbook owns image
pins, managed identities, Bicep preview, deployment and cost evidence.
