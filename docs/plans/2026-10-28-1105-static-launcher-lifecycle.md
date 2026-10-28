# #1105: static launcher version, opt-in rotation, idle and max-runs exit

Sample design for [gobifrost/bifrost#1105](https://github.com/gobifrost/bifrost/issues/1105).
The issue's file pointers are the starting point; this is the shape that
fits the current remote-launcher split.

## Problem

A remote static launcher (Compose, Azure Container Apps, any process that
connects with the worker-control WebSocket) is always-on. The API registers
its row and stamps `execution_workers.version` with the API's own
`get_version()`, so Diagnostics and the upgrade check describe the API, not
the container. After an upgrade the old container keeps claiming work. It
also cannot scale to zero: `PoolWorker._claim_loop` skips idle and lifetime
drains when `static` is set, and `begin_idle_drain` refuses to drain while
ready standard Jobs are at or below Warm Workers. A static row is not one of
those Jobs, so calling the idle drain for it would still be refused on an
install with no spare Job.

Kubernetes Job rotation (#1052) does not apply. Bifrost did not start the
static process and must not pretend to replace it.

## Rules

1. **The launcher reports its build.** `Hello` gains optional `version`,
   `drain_when_outdated`, `exit_when_idle_seconds` and `max_runs`. Each is
   omitted from the signed canonical bytes when unset, so a launcher from
   before this report still verifies. A set field is part of the HMAC.
   `register_static_worker` stores `version` when the hello sent one and
   otherwise keeps today's API stamp. `admit_worker` does the same for a
   Job hello, which had the same stamp. A reconnect refreshes the static
   row only when the hello includes a version, so an old reconnect cannot
   clear an opt-in.

2. **Rotation is opt-in and does not launch a replacement.**
   `BIFROST_STATIC_DRAIN_WHEN_OUTDATED` on the launcher process sets
   `drain_when_outdated` on the row. `LiveControllers.outdated` is true for
   a ready or draining static worker only when that flag is set and
   `version` is not among live controller versions. There is no
   `launched_by` check: no controller started it. `replace_outdated` marks
   those ready rows `draining` / `rotated` immediately. It does not wait
   for a warm Job, and it does not create one. The existing heartbeat sees
   a non-ready state, the claim loop stops, `_drain` lets in-flight work
   finish, and `_retire` sends `retired`. The process exits 0. The host
   restart policy or scaler starts the new image.

   Default is off. A static worker that omits the flag, including every
   current warm-minimum install, is not outdated.

3. **Idle and max-runs are also opt-in, on the launcher.**
   `BIFROST_STATIC_EXIT_WHEN_IDLE_SECONDS` (unset, or a non-negative
   integer; `0` means the next idle observation) and
   `BIFROST_STATIC_MAX_RUNS` (unset, or a positive integer) are sent on the
   hello and applied by the API-side `PoolWorker` for that session. They
   are not columns: a handoff reads them from the next hello. Job workers
   ignore the variables and keep Settings idle time and
   `pool_max_lifetime_seconds`.

   The claim loop asks to drain only when `busy()` is false. `busy()` is
   workflow children, agent runs, services and platform jobs, so a claimed
   run is not abandoned to exit. Max-runs counts `runs_handled` (a workflow
   or agent dispatch whose consumer returned). Services and platform jobs
   block the exit and do not increment it. Idle exit calls
   `begin_idle_drain(..., hold_for_warm_minimum=False)`. The default call,
   used by Jobs, is unchanged. Static rows already are not in the warm
   count; the flag stops that count from pinning a launcher that asked to
   leave. While the row is `ready` it still counts as free capacity for
   placing work.

## What this does not do

- No Kubernetes Job controller changes beyond accepting a reported version
  on admit. The 300s SIGTERM grace is untouched. A rotated, idle, or
  max-runs drain still waits for in-flight work with no deadline of its own.
- No new worker is launched to replace a static launcher. An install that
  also has Warm Workers still launches Kubernetes Jobs for that minimum;
  the static process does not satisfy it.
- A permanently mixed controller fleet (an old API replica that keeps
  publishing its version) does not drain an opted-in static launcher, same
  as Jobs. That is deliberate: draining during the rollout, while the old
  build is still live, would flap.

## Rollout

Upgrade the API first. A launcher that sends `version` will not
authenticate to an API that does not know the field, because the proof
covers it. Launchers that omit it keep working against the new API.
