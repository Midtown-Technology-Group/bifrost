# Rust device MVP: architect STOP disposition

Date: 2026-10-01. Decision: **STOP before W1/W2**. Do not claim the five-route MVP
successful. This is a protocol prerequisite finding, not evidence that Rust is
unsuitable for a control plane. Sol independently reviewed the source chain.
The operating procedure package is `2026-09-30.2`.

## Scope and authority

The [execution plan](rust-device-mvp-plan.md) records packages and Python ownership.
Latest reconciled platform main is
`e77947fab5762e49dd5fdc390bd65ca84bc22c3c` (#1001 ancestor).
Workspace initial main was `749197f6425a54a899bc596c9c33cdb2e6f9d2e2`;
latest main is `af6d73a40cb602a1f82f089dbac27233ccddd5fb` (#1112 ancestor).
The latest workspace audit found zero internal-import findings across 2,095
boundary Python files (1,973 standard authored files plus Solution/top-level
authoring locations), with an empty allowlist. No workspace changes were made.
[RFC #1005](https://github.com/Midtown-Technology-Group/bifrost/pull/1005) remains
provisional at `2a70092eb773750d6bd082659c2a46b428748654`.
Sopdet authority is `Midtown-Technology-Group/sopdet` main
`8120838b113d9980d533af69ada385f7fa405138`, unchanged.

## Current-main reconciliation

The clean-candidate gate fetched platform main
`c527ca1e4c0e1dc338b42542799290f6eba52ede` (#1004) during execution.
Candidates include this head. Its four changed files concern pinned workflow
module imports and related tests; device/auth/schema/instructions are unchanged.
The pinned F-01 source citations remain applicable.
A later gate fetched `e77947fab5762e49dd5fdc390bd65ca84bc22c3c` (#960);
candidates include it. Its three changed files fix user-role ORM delete cascades
and tests. Device protocol, device schema, database dependency and instructions
are unchanged; this does not assert that all authentication behavior is unchanged.
Latest workspace main `af6d73a40cb602a1f82f089dbac27233ccddd5fb` (#1116)
changes only CIPP delivery recipes and handoff documentation. A fresh exact-head
audit again found zero internal imports across 2,095 boundary files and the
allowlist empty; #1112 remains an ancestor.

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

Characterization also exposed F-02, a log-batch quirk confirmed by the supported
Python reference run at `0f519eeb`: `append_logs` can stage a new lower sequence before a later
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
of dependency teardown completion. The pinned FastAPI reference run confirmed the committed partial batch, unchanged
activity/sequence and absent successful-path event. See
[reference run 36826144370](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36826144370).

| Acceptance area | Evidence | Disposition |
|---|---|---|
| Current prerequisites and workspace boundary | Exact fetched heads and authored audit | Verified at stated heads |
| Rust W0 foundation | Bounded candidate workstream; separate validation ledger | Unaccepted pending gates |
| Response/DB/event drift detection | 90 supported reference/mutant cases passed at `b392ebca` | Core drift gate verified; normal pre-PR gate still failed |
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
was used to mask E-01. Supported CI has provided cold Rust/image and Python
operational evidence without altering daemon policy. E-01 still prevents equivalent supported local checks.

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

Reviewable W0 candidates are [W0-A #1009](https://github.com/Midtown-Technology-Group/bifrost/pull/1009)
and [W0-B #1008](https://github.com/Midtown-Technology-Group/bifrost/pull/1008).
Neither is architect-accepted or merged. Rust package version is `0.1.0`.
W0-A at `3c65f47204fcd2622e2cff192e54d8d6a880f1dc` passed supported CI
formatting, all-feature Clippy/tests (six tests), cold production-image build,
and security/license policy. Its preceding `48ab9` candidate also passed the
migrated Alembic schema/PgBouncer and real production-image smoke job: healthy and
ready against the existing schema, live but unready against a schema without the
device tables, sanitized request logs, and SIGTERM exit 0. These are actual
container/service checks, not host substitutes. The final TLS-test candidate is
`5ad5010400bf1ade5d1a96cda22684a3f4d6cf35`;
[run 36828498345](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36828498345)
passed its cold image, security/license, formatting/Clippy/tests and actual HTTPS
OTLP positive/negative tests. Trusted TLS delivered a request span and duration
histogram; an untrusted chain delivered neither signal. These tests use ephemeral
CA/server keys and process-local trust only, with no committed private keys or
host/runtime trust changes. Final migrated-schema/PgBouncer/service smoke readback
remains pending. Independent source review found no additional security blocker. SDK 0.33 trace shutdown
acknowledgement does not guarantee final export delivery: that SDK discards the
last trace-export result. Collector receipt must be verified separately, as the
positive TLS test does; a successful shutdown log alone is not delivery evidence.

W0-B's supported Python reference run at `0f519eeb` executed 87 cases:
69 passed and 18 failed because the tests incorrectly expected FastAPI's default
422 envelope. BiFrost's global handler instead returns the flat
`error="validation_error"`, joined `message`, and `details=null`. The fixtures were
corrected from source and actual supported-container responses; Python behavior
was not changed. Passing cases included full lifecycle, stale-claim reclaim,
committed-running ACK-loss handling, F-02 partial persistence, independent
reference comparisons and misrouted-event drift detection. The corrected candidate
`b392ebcac796f4cc5cf1cc2a21948025a93b1725` also tests actual HTTP-response and
committed SQL-state mutation boundaries, not just mutations of copied observations.
The [corrected reference run 36827831696](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36827831696)
passed all **90 cases in 116.69 seconds**. It ran PR head `b392ebca` through
GitHub's synthetic merge `3d939da790e438d116a7e853d9c3dfec4407f20b`.
The sanitized twelve-step lifecycle artifact contains symbolic token labels,
not credential values; downloaded JSON SHA-256 is
`dcd0d7d17f9e44473d20c4f464929a1c37666957f21218d67851d07f186f7aa7`.
The harness cannot attribute a scoped event if both its job/device identity and
channel scope disappear; this limitation is explicit, not normalized away.
Existing response-copy mutants are supplementary to the actual HTTP, committed
SQL and misrouted Redis publication mutants.

Local foundation pre-PR gates report thousands of dependency-resolution/type
errors in API quality; their cause is not established by the AppArmor diagnosis.
W0-A's authenticated clean-candidate gate at `5ad50104` completed with exit 1,
3,944 API type errors and 89 warnings. W0-B
at `b392ebca` reported 3,954 API type errors and 89 warnings in its normal gate.
Each PR records its exact candidate and gate outcome. Supported scoped CI passes
do not waive a failed normal pre-PR gate.
The two W0-B CodeQL ineffective-statement findings were corrected and their review
threads answered/resolved after source readback. No benchmark was rerun to evade
[#1003](https://github.com/Midtown-Technology-Group/bifrost/issues/1003).

All five routes remain Python-owned. Python retains administration, control keys,
cancellation, watchdog, WebSocket authorization, workflow/agent execution and every
non-goal in the execution plan. Alembic remains authoritative. W0 Rust code applies
no migrations and owns no jobs. No request routing was changed, so this stopped
experiment requires no data repair or production rollback. Do not expose the
candidate readiness/health service as a substitute device endpoint.

No Python/Rust performance comparison is valid: there is no route candidate and
local service testing is blocked; successful CI bootstrap checks do not constitute
a comparable device workload. No throughput,
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
