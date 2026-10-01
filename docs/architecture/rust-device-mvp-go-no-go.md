# Rust device MVP: architect STOP disposition

Date: 2026-10-01. Decision: **STOP before W1/W2**. Do not claim the five-route MVP
successful. This is a protocol prerequisite finding, not evidence that Rust is
unsuitable for a control plane. Sol independently reviewed the source chain.
The operating procedure package is `2026-09-30.2`.

## Scope and authority

The [execution plan](rust-device-mvp-plan.md) records packages and Python ownership.
Platform main is `d39aa0adf15ba12dfde5f2f39628cac88c2e98a0` (#1001 present).
Workspace main is `749197f6425a54a899bc596c9c33cdb2e6f9d2e2` (#1112 ancestor).
The workspace audit found zero internal-import findings across 2,095 authored
Python files with an empty allowlist. No workspace changes were made.
[RFC #1005](https://github.com/Midtown-Technology-Group/bifrost/pull/1005) remains
provisional at `2a70092eb773750d6bd082659c2a46b428748654`.
Sopdet authority is `Midtown-Technology-Group/sopdet` main
`8120838b113d9980d533af69ada385f7fa405138`, unchanged.

## Current-main reconciliation

The clean-candidate gate fetched platform main
`c527ca1e4c0e1dc338b42542799290f6eba52ede` (#1004) during execution.
Candidates include this head. Its four changed files concern pinned workflow
module imports and related tests; device/auth/schema/instructions are unchanged.
The pinned F-01 source citations remain applicable. Workspace main is unchanged.
A later gate fetched `e77947fab5762e49dd5fdc390bd65ca84bc22c3c` (#960);
candidates include it. Its three changed files fix user-role ORM delete cascades
and tests. Device protocol, device schema, database dependency and instructions
are unchanged; this does not assert that all authentication behavior is unchanged.

## Finding F-01: claimed does not establish absence of execution

The user requires both current Python behavioral parity and no automatic replay
once execution may have spawned. Current Python plus Sopdet cannot satisfy both:

1. Sopdet calls `cmd.Start` before reporting running. An unconstrained child can
   already have effects. Every running-report callback failure kills/reaps the
   child and returns `ErrRunningRejected`, including transport uncertainty.
   [runner.go, pinned lines 288–302](https://github.com/Midtown-Technology-Group/sopdet/blob/8120838b113d9980d533af69ada385f7fa405138/internal/agent/runner.go#L288).
2. Its loop drops the terminal report on that error and resumes claiming. This
   assumes the server owns a terminal/lost verdict even when the server never
   received running.
   [loop.go, pinned lines 234–240](https://github.com/Midtown-Technology-Group/sopdet/blob/8120838b113d9980d533af69ada385f7fa405138/internal/agent/loop.go#L234),
   [claim loop, lines 148–163](https://github.com/Midtown-Technology-Group/sopdet/blob/8120838b113d9980d533af69ada385f7fa405138/internal/agent/loop.go#L148).
3. If no running transition committed, Python still sees claimed. After 60 seconds
   from `claimed_at`, `claim_next` reclaims it with a new token. The predicate
   ignores session/activity, and heartbeat updates activity without renewing
   `claimed_at`. The sweeper deliberately leaves stale claimed jobs reclaimable.
   [claim and fresh fence](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/services/device_jobs.py#L190),
   [heartbeat](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/services/device_jobs.py#L474),
   [sweeper](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/services/device_jobs.py#L385).

```mermaid
sequenceDiagram
    participant S as Sopdet
    participant P as Python control plane
    participant X as External process
    S->>P: claim
    P-->>S: claimed, token A
    S->>X: spawn (effects now possible)
    Note over S,P: running reports never commit
    S->>X: kill (cannot undo completed effects)
    Note over P: still claimed; claimed_at ages past 60 seconds
    S->>P: claim again
    P-->>S: same job, new token B
    Note over S,X: automatic execution is permitted again
```

This is a reachable source path, not evidence that a live device has replayed.
Retries, process termination and stale-token fencing do not eliminate it: fencing
protects old reports, but a new claim authorizes another spawn. A crash between
spawn and running introduces the same absent durable information.
The existing [device invariant](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/docs/architecture/device-control-plane.md#L225)
also prohibits replay after ambiguous effects.

**Committed running with a lost acknowledgement is distinct.** Same-token running
replay is idempotent; running is excluded from claims and the watchdog eventually
marks it lost. Do not mischaracterize that safe branch as the defect.
[Running replay](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/services/device_jobs.py#L271),
[watchdog](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/services/device_jobs.py#L374).

No parity expectation was altered, and no Rust/Sopdet accommodation or protocol
fix is authorized by this disposition. A separate defect review must address
durable possible-execution knowledge before effects, honest spawn reporting,
crash/restart, pre-spawn recovery, cancellation and rollback. Merely increasing
the lease or changing transient-error handling does not close the crash window.

## Evidence and acceptance matrix

Characterization also exposed F-02, a source-derived log-batch quirk awaiting
runtime confirmation: `append_logs` can stage a new lower sequence before a later
conflicting existing sequence raises an error. The route converts that exception
to a normal 409 response; `get_db` commits on normal dependency return. Those
staged rows can therefore persist without the successful-path broadcast, while
activity/sequence updates after the loop are not reached. Do not assume atomic
rollback or silently correct the reference during porting.
[Staging and conflict](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/services/device_jobs.py#L564),
[handled response](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/routers/device_protocol.py#L249),
[dependency commit](https://github.com/Midtown-Technology-Group/bifrost/blob/d39aa0adf15ba12dfde5f2f39628cac88c2e98a0/api/src/core/database.py#L149).
The harness must wait for the owned transaction to release its row lock before
capturing committed state; a post-response SELECT alone is insufficient evidence
of dependency teardown completion. Actual response/cleanup ordering must be
characterized in the supported pinned FastAPI environment.

| Acceptance area | Evidence | Disposition |
|---|---|---|
| Current prerequisites and workspace boundary | Exact fetched heads and authored audit | Verified at stated heads |
| Rust W0 foundation | Bounded candidate workstream; separate validation ledger | Unaccepted pending gates |
| Response/DB/event drift detection | Harness candidate; real reference required | Unaccepted pending supported execution |
| Five route parity | No Rust device routes implemented | Not attempted after F-01 |
| Unchanged Sopdet | Exact source pinned; existing Go agent tests pass | Backend compatibility unproved |
| Mixed writers, cancellation/fencing and rollback | Requires W1/W2 | Not attempted |
| No automatic replay after possible spawn | Independent source review F-01 | Failed prerequisite |
| Production/process delivery readback | Azure configured PostgreSQL; status probe failed | Partial evidence |
| Normal CI/CodSpeed | RFC analysis remains blocked by #1003 | No waiver or threshold change |

A fault reproducer was not executed: the agent preparing it was rejected by an
automated cybersecurity review. Static review and existing Go tests do not replace
real-client fault proof. The rejected action was not retried through another agent.

## Environment disposition E-01

Supported host/daemon: `pve-t340`; kernel `7.0.14-19-pve`, Docker
`26.1.5+dfsg1`, default AppArmor/seccomp. New PostgreSQL/RabbitMQ and Rust
containers fail Unix socket creation. Read-only kernel evidence records
`apparmor=DENIED`, `operation=create`, `family=unix`, `sock_type=stream`,
`profile=docker-default`, `info=failed protocol match`, `error=-13`.
Rustup's `UnixStream::pair` and Cargo/libcurl fail along the same path.
These are environment failures, not passed service/schema/parity tests.

An earlier independent Compose prerequisite was corrected with the official
user-local Buildx `v0.37.2`, verified against release checksum
`982ca20490b45ed1ec8d99795974d3d874a358f75938c9c237305010e6b7e548`.
The system plugin and daemon security policies were not changed. No unconfined
profile, host pytest substitution, timeout inflation or repeated benchmark run
was used to mask E-01. Supported CI or a separately authorized host repair is
needed for the outstanding W0 operational evidence.

## Sanitized deployment configuration readback

Read-only Azure observations on 2026-10-01: production site
`app-mtg-bifrost-production`, resource group `rg-mtg-bifrost-poc-core-centralus`.
App settings: `BIFROST_WORK_DELIVERY_BACKEND=postgres`,
`BIFROST_ENVIRONMENT=production`, `BIFROST_ADMISSIONS_PAUSED=False`.
API/worker/scheduler sitecontainer records each map those named settings.

| Container | Configured image digest |
|---|---|
| api and scheduler | `bifrost-api@sha256:d8799b90609b9c3fca20eeec00eadf6ab9f3f9aa60483dea39fccd5fbb8732ae` |
| worker | `bifrost-worker@sha256:a4474735be0e418aa551bf4162faced9f54c136b3c272f0fe2edb757b93b9869` |
| client | `bifrost-client@sha256:9edcdc6dc63d025ef4d8d237b3ef0cdd5cf0993a216770ef30f4cd7efeaf6f8e` |

Registry: `ghcr.io/midtown-technology-group`. `az webapp sitecontainers status`
failed parsing its response. These observations establish configured identities,
not running-image/process evidence. No production traffic or configuration changed.
No observed configuration requires Rust RabbitMQ support. Generic worker cutover
is outside this MVP regardless of this readback.

## Ownership, rollback and measurement

All five routes remain Python-owned. Python retains administration, control keys,
cancellation, watchdog, WebSocket authorization, workflow/agent execution and every
non-goal in the execution plan. Alembic remains authoritative. W0 Rust code applies
no migrations and owns no jobs. No request routing was changed, so this stopped
experiment requires no data repair or production rollback. Do not expose the
candidate readiness/health service as a substitute device endpoint.

No Python/Rust performance comparison is valid: there is no route candidate and
the supported local environment fails before operational testing. No throughput,
memory or latency benefit is claimed. The RFC's later same-environment workload
requirements remain pending; CodSpeed's cross-environment blocking issue
[#1003](https://github.com/Midtown-Technology-Group/bifrost/issues/1003) remains open.

## Recommendation and smallest next step

**STOP this device MVP at the W0 gate.** Retain reviewable foundations as explicitly
unaccepted candidates; do not merge/deploy or begin device SQL/HTTP ports. Retire
none of the Python/Sopdet implementation. No evidence yet supports continuing the
broader control-plane migration, nor does F-01 establish that Rust would cause it.

The next bounded work is a separately reviewed correction/specification for F-01
in Python/Sopdet, followed by executable crash/uncertainty characterization against
a new approved corrected baseline, with Sopdet unchanged throughout the resumed
MVP comparison. Resolve E-01 or use the supported
CI lane for W0 proof. Resume only after the reference protocol satisfies the
non-negotiable invariant and both foundations pass independent acceptance.
