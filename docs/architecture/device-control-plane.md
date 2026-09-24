# Device control plane

## Status and purpose

This document freezes the Scope A **device control plane** protocol and
architecture for epic [#818][epic]: ad-hoc PowerShell on estate devices via a
long-lived, WebSocket-connected agent (sopdet serve mode), replacing the slow
Ninja callback path for agent-driven data-plane execution.

It is the M0 review gate (#819, tasks #827/#828). It resolves all six review
feedback items on the epic; see [Review-gate answers](#review-gate-answers).
The JSON wire shapes it references live beside this file in
[`device-control-plane/`](./device-control-plane/) (task #828). M1+ pull
requests may not merge before operator/peer review of this freeze.

NinjaOne remains the **distribution and enrollment** channel (M5 bootstrap)
and the **monitoring/alerting** path. The direct device path only replaces the
ad-hoc script data path.

## Related documents

| Document | Relevance |
| --- | --- |
| [platform-jobs.md](./platform-jobs.md) | Why `device_jobs` is deliberately **not** a PlatformJob |
| [execution-operations.md](./execution-operations.md) | Existing execution systems inventory; `device_jobs` is a new sibling domain record, not a fourth execution system |
| Workspace `AGENTS.md` (`MTG-Thomas/bifrost-workspace`) | Transport contracts, effect declarations for device execution (M4) |
| sopdet `AGENTS.md` / `docs/signing.md` / `docs/appcontrol.md` (M3 update) | Agent trust boundary, signing and App Control tiers |

Building blocks already in tree that this design reuses: `worker_control_commands`
(durable fenced desired state + claim tokens), `workflow_keys` /
`workflows.api_key_hash` (key generate/hash/verify and partial index pattern),
`/api/endpoints` + `X-Bifrost-Key` (header-auth + CSRF exemption pattern),
Redis pubsub -> `/ws/connect` fanout (lossy hints), and the CLI local-runner
poll/heartbeat/log/result protocol shape.

## Domain record, not PlatformJob (feedback #3)

**Decision:** `device_jobs` is a **domain execution/transport record** — a
sibling of workflow `executions` and workspace `script_jobs` — not a
PlatformJob, PlatformJob child, or PlatformJob projection.

[platform-jobs.md](./platform-jobs.md) requires new *durable platform
operations* (publishes, deploys, refreshes, backfills…) to extend PlatformJob
and forbids parallel job frameworks for platform-owned work. Device jobs are a
different class: the **execution authority is a remote estate device**, not a
platform replica. The platform never claims or runs these jobs; it only
records, fences, and observes a protocol with an external runner. Forcing them
through PlatformJob would invent platform retries, leases, cancellation, and
UI contracts the remote protocol cannot honor (for example, PlatformJob may
requeue on runner loss; this protocol must never requeue after side effects
may have started — feedback #1). This is therefore an explicit architecture
decision of the kind platform-jobs.md asks for, not an accidental second job
authority: PlatformJob remains the only authority for platform-executed work,
and `device_jobs` remains the only authority for device-side executions.

Ownership of the four contested concerns:

| Concern | Owner | Rule |
| --- | --- | --- |
| Status | `device_jobs` service (server-authoritative) | Agent inputs are fenced protocol submissions only (`claim`, `mark_running`, logs, terminal result). Only the server writes `lost`, reclaims stale `pending`/`claimed`, and performs watchdog transitions. |
| Cancellation | Org user with `can_execute_devices` | **Cooperative flag only** — no process-kill guarantee by the platform. `pending`/`claimed` cancel immediately to `cancelled`; `running` sets `cancel_requested_at`, observed by the agent via heartbeat hint; the agent does a best-effort kill and posts `cancelled`, or the natural terminal result wins. An unobserved running cancel whose agent disappears becomes `lost`, never a silent retry. |
| Retention | Maintenance sweeper (leader-owned recurring cleanup, same class as existing retention callbacks) | Executes the frozen scrub/prune policy in [Data handling](#data-handling-and-retention-feedback-6); no per-feature worker. |
| Observation | REST + WebSocket fanout under create-level authz | `GET /api/devices/{id}/jobs[/{job_id}[/logs]]` and user channel `device_job:{id}`. No PlatformJob status endpoint, no platform-job progress events. An optional PlatformJob parent may be added later **only** if a platform-job UI/cancel surface is explicitly required (feedback #3). |

## Device identity and enrollment

Table `devices` (M1 #829) — columns: `id`, `organization_id`, `display_name`,
`external_ref` (nullable, e.g. Ninja device id), `status`, `agent_version`,
`os`, `hostname`, `api_key_hash`, `api_key_enabled`, `enrollment_token_hash`,
`enrollment_expires_at`, `last_seen_at`, timestamps. Partial index on
`api_key_hash` mirrors `workflows.api_key_hash`.

Device status state machine:

```text
            create (operator)                enroll (single-use token)
  (none) ─────────────────────► pending_enrolled ─────────────────────► active
                                                                    ▲     │
                                                    enable (operator)     │ disable (operator)
                                                                    │     ▼
                                                                    └──── disabled
```

- `pending_enrolled`: row exists, no device key yet; only an enrollment token
  can advance it.
- `active`: enrolled; may heartbeat, claim, stream logs, post results, and
  hold a WebSocket device principal.
- `disabled`: every agent-authenticated route rejects with `device_disabled`;
  existing WebSocket principals are closed; job creation targeting the device
  is rejected. `enable` returns it to `active` with the same device key.
- v1 has no hard delete; `disable` plus M5 uninstall is the retirement path.

Enrollment:

- Operator creates the device and receives a **one-time enrollment token**
  (raw returned exactly once). Format `bfen_<device_uuid>_<secret>` with
  `secret = token_urlsafe(32)`; stored as a bcrypt hash in
  `enrollment_token_hash` with `enrollment_expires_at` (default TTL
  **15 minutes**).
- `POST /api/devices/enroll` (CSRF-exempt exact path, no user session) with
  the token: single-use, hashed at rest, TTL-enforced. Success ->
  `active`, enrollment hash cleared, **device key issued exactly once** in
  the response ([enrollment.schema.json](./device-control-plane/enrollment.schema.json)).
  Expired / consumed / unknown tokens and `disabled` devices are rejected
  with the shared error envelope; a reused token never yields a second key.
- Re-running enrollment with a fresh token (re-enroll / rotate flow) follows
  the same single-use rule; old tokens and old device keys stop verifying.

Key material (all three formats; secrets are `token_urlsafe(32)`; **only
bcrypt hashes are stored; raw values are returned once and never logged**):

| Key | Format | Stored in | Presented as |
| --- | --- | --- | --- |
| Device key (agent) | `bfdk_<device_uuid>_<secret>` | `devices.api_key_hash` | `X-Bifrost-Key` on `/api/device/*`; `Authorization: Bearer` on `/ws/connect` |
| Control key (job create) | `bfck_<key_uuid>_<secret>` | `device_control_keys.key_hash` | `X-Bifrost-Control-Key` on user/job routes |
| Enrollment token | `bfen_<device_uuid>_<secret>` | `devices.enrollment_token_hash` | Request body of `POST /api/devices/enroll` |

The embedded UUID routes verification to one row (constant-time bcrypt compare
of the full raw value), so auth stays O(1) while matching the `workflow_keys`
hash discipline. Verification always re-checks `enabled`, expiry, device
status, and org scope. Keys have rotate/revoke; rotation invalidates the old
hash immediately. Audit rows store the key **id**, never the secret.

Device-scoped **control keys** are a first-class table (feedback #2; M1 picks
this option): `device_control_keys` — `id`, `organization_id`, `name`,
`key_hash`, `enabled`, `expires_at`, `last_used_at`, `device_ids` (non-empty
allow-list), `created_by`, timestamps. A control key with an empty allow-list
is rejected at creation: **no code path mints a key that implicitly covers
every device in an org**. Tag-based scope is deferred (non-goal for M1); the
allow-list is the v1 scope vocabulary. `revoke` sets `enabled = false`;
`rotate` replaces the secret/hash on the same row (audit continuity).

## Authorization matrix (feedback #2)

User permissions are the existing JSONB role permissions resolved the way
`_user_has_permission` resolves them today. M0 names two keys:

- **`can_execute_devices`** — create/cancel/read device jobs (create-level).
- **`can_manage_config`** — device registry and control-key CRUD (org admin
  surface). Platform superusers bypass all checks.

| Action | User JWT | Control key | Device key | Notes |
| --- | --- | --- | --- | --- |
| Create device, issue enrollment token, rotate device key, disable/enable | org member + `can_manage_config` | no | no | Raw material once |
| `POST /api/devices/enroll` | no session; enrollment token only | no | no | CSRF-exempt exact path |
| Control-key create/list/rotate/revoke | org member + `can_manage_config` | no | no | Scope = non-empty `device_ids` |
| **Create job** `POST /api/devices/{id}/jobs` | org member + `can_execute_devices` | yes — device must be in `device_ids`, key enabled, unexpired, device `active` | no | Attribution always recorded (below) |
| Cancel job | org member + `can_execute_devices` | no | no | Cooperative flag only |
| Read job list/detail/logs (incl. script body) | org member + `can_execute_devices` | only jobs **it created** | **no** | Script body is create-level readable; the device principal's only script-body path is the fenced claim response for the job it holds |
| Read device freshness (`id`, `status`, `last_seen_at` only) | full registry read above (`can_manage_config`) | **yes — allow-listed devices only, reduced view** | **no** | Pre-accept freshness input for transport fail-closed. Approved epic delta: #818 comment 5815371680 |
| Heartbeat / claim / logs / result | no | no | yes, device `active` | `X-Bifrost-Key`, `/api/device/` prefix |
| WebSocket connect + `device:{id}` | no (`device:*` denied) | no | yes, `Authorization: Bearer` only | Query credentials rejected |

Attribution on create (feedback #2): record `requested_by_user_id` for JWT
creates, `requested_by_api_key_id` for control-key creates, plus optional
caller-supplied `workflow_id` / `execution_id` stored as
`requested_by_workflow_id` / `requested_by_execution_id` (validated for org
scope when resolvable; audit stores ids, never secrets).

HTTP status / error-code mapping lives in
[error.schema.json](./device-control-plane/error.schema.json). Notably:
**busy = HTTP 409 `device_busy`** (structured envelope the workspace turns
into `UserError`), fence/terminal violations = 409, scope/permission failures
= 403, key failures = 401.

## Job lifecycle (feedback #1)

Table `device_jobs` (M2 #833) — columns: `id`, `organization_id`,
`device_id`, `status`, `script_name`, `script_content`, `params`,
`timeout_seconds` (default 120, max 900), `max_output_bytes` (default 1 MiB,
min 1 KiB, max 16 MiB), `requested_by_user_id`, `requested_by_api_key_id`,
`requested_by_workflow_id`, `requested_by_execution_id`, `claim_token`,
`claimed_at`, `agent_session_id`, `last_agent_activity_at`,
`cancel_requested_at`, `exit_code`, `result`, `error`, `log_sequence`,
timestamps. Script body hard cap **256 KiB**; params serialized cap 32 KiB.

Statuses: `pending` -> `claimed` -> `running` -> `succeeded` | `failed` |
`timeout` | `cancelled` | **`lost`**.

Create request body for `POST /api/devices/{id}/jobs` (prose-frozen; the
schemas in `device-control-plane/` cover the agent-facing shapes):

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `script_name` | string, 1–256 chars | yes | Audit/display label |
| `script_content` | string, ≤ 256 KiB | yes | PowerShell body |
| `params` | JSON object | no (default `{}`) | ≤ 32 KiB serialized |
| `timeout_seconds` | int, 1–900 | no (default 120) | Agent enforces locally |
| `max_output_bytes` | int, 1 KiB–16 MiB | no (default 1 MiB) | Truncation cap |
| `workflow_id` | uuid | no | Attribution; org-validated when present |
| `execution_id` | uuid | no | Attribution; caller-asserted, org-scoped |

Success: `201` with the public job row (id, `status=pending`, attribution,
caps, timestamps — no hash fields). Busy: `409` error envelope with the
active `job_id`. The response never echoes the control key or any secret.

```text
create ─► pending ──claim(FOR UPDATE SKIP LOCKED, mints claim_token)──► claimed
                                                                     │ mark_running
                                                                     │  (only after real spawn)
                                                                     ▼
                                                                 running ──fenced result──► succeeded
                                                                     │                      failed
     claim lease (60s) expiry: claimed stays claimable in place       │                      timeout
     with a fresh token; pending rows are always claimable            │                      cancelled
                                                                      │
                              server: fenced-activity silence > 90s,  │
                              or deadline backstop                    ▼
                                                                   lost   [TERMINAL]
                              (lost is written ONLY from running; never re-queued;
                               explicit retry creates a NEW job id)
```

Transitions and rules:

- **Busy (feedback #5):** partial unique index — one row with status in
  (`pending`, `claimed`, `running`) per `device_id`. A second create fails
  with **409 `device_busy`** (include the active `job_id`); it is never
  queued, never silently coalesced, and never handed to Ninja.
- **Claim:** `FOR UPDATE SKIP LOCKED` (modeled on `worker_control_commands`);
  exactly one concurrent winner. Claimable: `pending`, or `claimed` whose
  `claimed_at` is older than the **60 s claim lease**. Each claim mints a new
  `claim_token` and `agent_session_id`; stale tokens fail with
  `fence_violation`.
- **`running` is only reported by the agent after a real process spawn**
  (feedback #1) — that is what makes pre-spawn reclaim safe and post-spawn
  ambiguity `lost`.
- **Reclaim:** only from `pending` or `claimed`. **`running` (or any
  ambiguous mid-side-effect state) is never re-queued.** The server writes
  terminal `lost` when fenced activity for the active job goes silent for
  **90 s** (agent heartbeats every 30 s and only the owning
  `agent_session_id` renews `last_agent_activity_at`), or as a backstop when
  a `running` job passes `timeout_seconds + 60 s` with no terminal report —
  a healthy agent always posts `timeout` itself first, so the backstop only
  fires when the agent is gone or wedged. `lost` is terminal; **explicit
  retry creates a new job id** (audited like any create). No automatic
  re-execution, ever.
- **Fencing:** logs and results require the current `claim_token` (sent as a
  required request-body property), are idempotent on `(job_id, seq)`, and are
  rejected with `fence_violation` / `job_terminal` once the row is terminal —
  including after `lost`. A late
  agent result never resurrects a job. On fence rejection the agent stops its
  local execution best-effort, drops that job's spool, and never re-runs.
- **Cancellation:** cooperative per the ownership table above. The agent
  observes `cancel_requested` on its heartbeat response for the job it owns.
- **Offline before accept:** `transport=direct` (and v1 `auto`) fails closed
  when the device is not healthy — `active` **and** `last_seen_at` within the
  **90 s** freshness window (3 × 30 s heartbeat). See
  [Transport freeze](#transport-freeze-feedback-5).

## HTTP routes

User routes (user JWT or control key; org-scoped; registered like existing
routers, contracts in Pydantic, hashes never exposed):

| Route | Purpose (M1/M2 issues) |
| --- | --- |
| `POST /api/devices` | Create device + one-time enrollment token (#831) |
| `GET /api/devices`, `GET /api/devices/{id}` | List/detail (#831) |
| `POST /api/devices/{id}/rotate-key` / `disable` / `enable` | Device lifecycle (#831) |
| `POST /api/devices/enroll` | Single-use token -> active + device key once (#831); CSRF-exempt exact path |
| `POST/GET /api/device-control-keys`, rotate, revoke | Control-key CRUD (#832) |
| `POST /api/devices/{id}/jobs` | Create job: JWT permission **or** `X-Bifrost-Control-Key` in scope; busy 409 (#836) |
| `GET /api/devices/{id}/jobs`, `.../jobs/{job_id}`, `.../jobs/{job_id}/logs` | Observation, create-level read (#836) |
| `POST /api/devices/{id}/jobs/{job_id}/cancel` | Cooperative cancel (#836) |

Agent routes (device key; CSRF-exempt prefix **`/api/device/`** — this prefix
is reserved for the agent protocol; note `/auth/device/*` remains the unrelated
OAuth device grant):

| Route | Contract |
| --- | --- |
| `POST /api/device/heartbeat` | Body `{agent_session_id}`; updates `last_seen_at`, renews the active job's activity iff the session owns it; returns `server_time`, `last_seen_at`, `poll_interval_seconds` (5 when a pending job exists, else 10), `cancel_requested` |
| `POST /api/device/jobs/claim` | 204 (no work) or [claim-response.schema.json](./device-control-plane/claim-response.schema.json) |
| `POST /api/device/jobs/{id}/logs` | [log-batch.schema.json](./device-control-plane/log-batch.schema.json); fenced + idempotent on `(job, seq)`; fanout on `device_job:{id}` |
| `POST /api/device/jobs/{id}/result` | [job-result.schema.json](./device-control-plane/job-result.schema.json); fenced; late/lost -> 409 |

Key formats and auth headers are frozen in
[Device identity and enrollment](#device-identity-and-enrollment). CSRF
exemptions are exactly: prefix `/api/device/` and path `/api/devices/enroll`
— cookie-less header auth already bypasses CSRF enforcement, so no broader
exemption is needed.

## WebSocket contract (feedback #6)

- `/ws/connect` authenticates a device principal **only** via
  `Authorization: Bearer <device_key>`. Query credentials are excluded the
  same way the existing endpoint excludes them; presence of `?device_key=`
  (or any device credential in the query string) closes the socket with
  **4001** before any subscription. `?device_key=` never exists in client or
  server code.
- Channel: the device may hold **only** `device:{device_id}` (auto-subscribed
  on connect). User JWT principals are denied on `device:*`. The user-side
  log fanout channel `device_job:{job_id}` is for user principals with
  read authz; devices never subscribe to it.
- Envelopes ([ws-envelopes.schema.json](./device-control-plane/ws-envelopes.schema.json)):
  connect ack (`connected`), `subscribed`, `error` (denied subscribe answers
  with `error`, it does not close), and **`device_job_available`** published
  on `device:{id}` after a create commits.
- The WS layer is a **lossy hint only**: HTTP claim/log/result is the
  authority; the agent falls back to timed claim polling whenever WS is down
  (`poll_interval_seconds`), with reconnect exponential backoff + jitter
  (M3).

## Transport freeze (feedback #5)

Non-negotiable table for the workspace extension (M4 #823). Double-execution
invariant: **at most one transport attempts a given logical request after the
platform accepts it** (accept = create returned 2xx with a job id in
`pending`/`claimed`/`running`/`lost`).

| Case | Behavior |
| --- | --- |
| `run_as=system` on direct | Allowed; the platform job payload carries **no `run_as` at all** — execution is the agent service identity (LocalSystem). Workspace validates before create. |
| `run_as=<user>` on direct | **Reject** with `UserError` before create (service identity fixed in v1; never silently ignored, never impersonation) |
| `allow_concurrent=true` on direct | **Reject** with `UserError` before create (concurrency is hard 1; busy is 409, never a queue). `false`/unset OK |
| Device busy (active job exists) | Platform returns structured **409 `device_busy`** -> workspace surfaces `UserError`. Never silent queue, never Ninja fallback |
| Direct job **accepted** | **Never** fall through to Ninja for the same logical request, even when the outcome becomes uncertain — uncertain outcome is `lost`, not a Ninja retry |
| Device offline before accept + `transport=direct` | **Fail closed** (device outside the 90 s freshness window counts offline) |
| Device offline before accept + `transport=ninja` | Existing Ninja path, byte-compatible with today |
| `transport=auto` | v1 (M4–M6): behaves as `direct` with the same fail-closed offline rule — it never implies Ninja. M7 may introduce a documented offline policy only after M6 sign-off (#825/#826); **after accept, never Ninja** regardless of transport |

M4 ships with default **`transport=ninja`**; flipping the default to `auto`
is M7, gated on M6. Device <-> Ninja mapping uses `devices.external_ref`.
Device-execution effect declarations are added in the workspace per its
AGENTS.md (M4).

## Agent privilege (feedback #4)

Product decision, not an implementation accident (M0/M3; M5 executes it):

- **Service identity:** the sopdet serve agent runs as **LocalSystem** by
  default on Windows. v1 has no alternate identity and no impersonation
  (`run_as` non-system rejected above). Estate-wide SYSTEM script execution
  is the explicit Scope A trust class: same class as today's Ninja callback
  runner, with explicit size/timeout/output caps, audit, and device scoping.
- **Install / update / uninstall:** installed under `C:\Program Files\Sopdet\`
  with config + spool ACL'd to SYSTEM/Administrators; delivered and updated
  only via the M5 Ninja bootstrap (pinned, checksum-verified binary — re-run
  to update); no in-agent auto-update in v1. Uninstall removes
  service/binary/config/spool and leaves no key material (M5.2), with device
  disable as the operator-side revocation.
- **Signing / App Control:** two tiers — (1) **private-trust signed** builds,
  required for any managed/non-canary target and for hardened/WDAC endpoints;
  (2) **unsigned + SHA manifest** builds, **canary only**. A binary and a
  manifest from the same source are not authenticity proof; WDAC policy
  guidance stays in sopdet `docs/appcontrol.md` + `docs/signing.md`.
- **Trust boundary:** sopdet's current "read-only collection, no elevation"
  promise changes for serve mode: it becomes a resident arbitrary-PowerShell
  runner. README/AGENTS.md trust-boundary updates land with M3 (#843);
  inventory mode is unchanged.

## Data handling and retention (feedback #6)

| Data | At rest | Lifetime |
| --- | --- | --- |
| Device / control keys | bcrypt hash only; raw returned once; never in URLs, logs, audit, or error messages | Until rotate/revoke; revoked rows keep ids for audit with `enabled=false` |
| Enrollment token | bcrypt hash + expiry | Single-use; hash cleared on successful enroll |
| Job `script_content` + `params` | Plaintext in DB (required for claim) | **Scrubbed (NULLed) 30 days after terminal** |
| Job logs | Plaintext rows | **Content deleted 30 days after terminal** |
| Job metadata row (status, ids, timings, exit code) | — | **Pruned 365 days after terminal** |
| Agent spool (unsent logs/results) | Files `0600`/DACL = SYSTEM+Administrators; no keys inside | Cleared after successful post; startup sweep removes files older than 7 days; uninstall wipes the directory |
| Audit rows | Key id / user id only | Existing audit retention |

Access: script bodies and logs require create-level authz (matrix above);
the device principal reads its held script only from its own claim response.
The retention sweeper is leader-owned maintenance, not a new worker. All
participants use the repo's `log_safety` discipline: raw keys and full script
bodies never enter logs.

## Offline posture (epic open point #2)

Confirmed **fail-closed**: until M7 sign-off, a `transport=direct` (or v1
`auto`) request whose device is not `active` + fresh within 90 s fails before
accept; it does not degrade to Ninja. Ninja runs only when `transport=ninja`
is explicit. After any direct accept, uncertainty resolves to `lost`, never
to a second transport.

Freshness read for control keys is an **approved epic delta**
(#818 comment 5815371680): `GET /api/devices/{id}` with
`X-Bifrost-Control-Key` returns only `id`/`status`/`last_seen_at`, and only
for devices on the key's allow-list — the pre-accept input the workspace
needs to observe offline state. Full registry detail remains user-only.

## Protocol schemas (freeze rule)

| Shape | File |
| --- | --- |
| Claim response (+ `claim_token`, lease) | [`device-control-plane/claim-response.schema.json`](./device-control-plane/claim-response.schema.json) |
| Log batch (seq idempotency) | [`device-control-plane/log-batch.schema.json`](./device-control-plane/log-batch.schema.json) |
| Result payload (agent statuses; `lost` handling) | [`device-control-plane/job-result.schema.json`](./device-control-plane/job-result.schema.json) |
| Busy + structured errors | [`device-control-plane/error.schema.json`](./device-control-plane/error.schema.json) |
| WS envelopes | [`device-control-plane/ws-envelopes.schema.json`](./device-control-plane/ws-envelopes.schema.json) |
| Enrollment request/response | [`device-control-plane/enrollment.schema.json`](./device-control-plane/enrollment.schema.json) |

These files are frozen for Scope A. Schemas are intentionally closed
(`additionalProperties: false`): **additive** changes (new error codes, new
optional fields) are non-breaking but must be recorded by editing the
affected schema file — validators reject undeclared fields on purpose.
**Breaking changes** (removing or repurposing a field, changing a status
enum, weakening fencing/auth/busy contracts) require an update to epic #818
and this document **before** any implementation change. Implementation issues
(#829–#854) must not reinterpret these contracts; if reality conflicts, stop
and raise it on #818.

## Review-gate answers

Direct answers to the six review comments on #818:

1. **No auto re-execute after side effects** — reclaim only `pending`/`claimed`;
   `running`/ambiguous -> terminal `lost` + explicit new job id. Fenced late
   reports rejected; drills required in M2/M3/M6. → [Job lifecycle](#job-lifecycle-feedback-1)
2. **Constrained SYSTEM submission authority** — create = org membership +
   `can_execute_devices` **or** a control key with a non-empty device
   allow-list; full attribution; rotate/revoke/expiry/`last_used_at`; script
   body = create-level read; audit stores key ids. →
   [Authorization matrix](#authorization-matrix-feedback-2)
3. **`device_jobs` is a domain execution/transport record, not PlatformJob** —
   explicit divergence from platform-jobs.md with named status/cancel/
   retention/observation owners. → [Domain record](#domain-record-not-platformjob-feedback-3)
4. **Agent privilege is a product decision** — LocalSystem default, bootstrap
   delivery, uninstall hygiene, signed private-trust vs unsigned-canary tiers,
   sopdet trust-boundary doc update. →
   [Agent privilege](#agent-privilege-feedback-4)
5. **Transport contract frozen** — no `run_as` impersonation on direct,
   `allow_concurrent=true` rejected on direct, busy = 409 never queue, never
   Ninja after direct accept, at-most-one-transport invariant. →
   [Transport freeze](#transport-freeze-feedback-5)
6. **WS auth + data handling** — `Authorization: Bearer` only (query device
   key closes 4001), `device:{id}` only, `device_job_available` is a lossy
   hint; keys hashed, spool 0600/DACL + cleared, script/log retention frozen.
   → [WebSocket contract](#websocket-contract-feedback-6) and
   [Data handling](#data-handling-and-retention-feedback-6)

M6 measures p50/p95 dispatch->claim / first output / terminal against the
Ninja baseline on the same device+script, plus disconnect, busy, restart, and
ambiguous-result drills; **M7 stays held until those pass** (#825/#826).

## Non-goals

- Scope B: routing Bifrost workflow *steps* to devices
- mTLS / client certificates (per-device API key over WSS in v1)
- Multi-job concurrency per device (concurrency = 1; busy = 409)
- Replacing Ninja for monitoring/alerts or for distribution (bootstrap only)
- Tag-based control-key scope (deferred; allow-list is v1)
- In-agent auto-update; alternate service identities; PlatformJob UI binding

## Risk note

Free-form SYSTEM scripts are accepted for Scope A with the caps, scoping,
attribution, and audit above. **This is not a sandbox.** It aims to match or
beat the status-quo trust of the Ninja callback runner while cutting latency.
Hardened/WDAC endpoints require the signed private-trust agent tier. The
highest-consequence failure modes — double execution and silent requeue — are
addressed by the fencing/`lost` rules and the at-most-one-transport
invariant, and are proven by drills before any default flip (M6 gate).

## Canonical implementation map (planned)

| Concern | Planned location | Issue |
| --- | --- | --- |
| `devices` migration + ORM | `api/alembic/versions/*`, `api/src/models/orm/devices.py` | #829 |
| Device + control-key services | `api/src/services/device_keys.py`, `device_control_keys` model | #830 |
| Device CRUD + enrollment routes | `api/src/routers/devices.py` | #831 |
| Control-key CRUD routes | `api/src/routers/device_control_keys.py` | #832 |
| `device_jobs` migration + claim/lost service | `api/src/models/orm/device_jobs.py`, `api/src/services/device_jobs.py` | #833 |
| Agent HTTP protocol routes | `api/src/routers/device_protocol.py` (prefix `/api/device`) | #834 |
| WS device principal | `api/src/core/auth.py`, `api/src/routers/websocket.py` | #835 |
| Job create + observation routes | `api/src/routers/devices.py` | #836 |
| Fencing / lost / busy failure suite | `api/tests/` (unit + e2e) | #837 |
| sopdet serve mode | `Midtown-Technology-Group/sopdet` `internal/agent/` | #838–#843 |
| Workspace extension + transport matrix | `MTG-Thomas/bifrost-workspace` `modules/extensions/bifrost_device.py` | #844–#847 |

[epic]: https://github.com/Midtown-Technology-Group/bifrost/issues/818
