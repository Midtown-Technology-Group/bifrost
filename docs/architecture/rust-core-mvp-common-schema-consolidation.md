# CP-CONSOLIDATE: common schema and credential transformation

Status: review candidate, 2026-10-16. This is a concrete extraction plan, not an
applied migration, approved role grant, frozen runtime profile or acceptance
result. It replaces neither current Alembic history nor the accepted Go packet.
Accountable owner: Rust control-plane workstream. No peer implementation task
is issued by this document.

## Sources and reproducible scope

Platform main was freshly fetched at
`e42999395231ebc536711e5015422edfb75613a7`. CRED-P1 source is
`3918d608a2d497d1ca8424fd4229d191323e2d7a`; common-mechanism extraction baseline
is Go candidate `56d1fb006d220b9dbb0747f1cca4def18677f918`, with accepted evidence
unchanged at `a47586732ef1b2523b9119b5056d83574ccdd779`.

The [static inventory](rust-core-mvp-common-schema-source-inventory.json)
retains exact source hashes, column definitions, constraint expressions and
migration revision/parent identities from pinned Git blobs. It uses Python AST
without importing application code or evaluating ORM/DDL expressions. Starred
constraint generators remain source expressions, not executed expansions. ORM
inventory does not prove installed schema or migration equivalence.

Workspace main advanced to `6689b218cb799b807c81d63ee77de9af27206509`.
The [new boundary audit](rust-core-mvp-workspace-boundary-6689b218.json) parsed
2,451 committed authored Python files using the current gate's roots and
exclusions, plus Solution, google_ops_worker and top-level files. Zero src.* or
SQLAlchemy imports, zero parse errors and an explicitly empty internal-import
allowlist. This is source evidence, not a live workspace deployment claim.

## Migration shape

Current main has one source Alembic head, `20261012_work_delivery_started`.
Neither candidate grant migration is on that head. The common continuation
must have that actual head as parent when prepared; re-resolve if main changes.
Do not combine the two historical candidate chains or silently stamp a database.

| Step | Existing source to extract/adapt | Required resulting change |
| --- | --- | --- |
| C1 | Go runtime_artifacts / runtime_sessions | One artifact association and owner/session family, adapted native links below; existing native tables remain authoritative. |
| C2 | Go runtime_receipts | Typed Start and immutable report receipt identity; no generic polymorphic receipt replacement. |
| C3 | CRED runtime_sdk_grants plus Go runtime_grants/fences | Create the two grant tables once, retaining all 35 CRED fields and adding exactly owner_incarnation_id, committed_start_id, start_message_id. Preserve the eight operation fields. |
| C4 | Go runtime_admissions / runtime_resource_settlements | Exact provision/release identity and physical-resource observation, with no inference from logical terminal status. |
| C5 | Complete reviewed writer catalog | Install restricted writer enforcement and adapter transaction order together before enabling any owner. Prototype fixture roles are not production role names or a provisioning script. |

Prefer a small sequential additive Alembic continuation matching these reviewable
steps, rather than concatenating prototype upgrade functions. Ordering must
create referenced unique keys before dependent FKs. Downgrade with any retained
owner/session/Start/grant/admission/report/resource fact must fail closed; HTTP
routing rollback does not mean dropping evidence tables. An empty-schema
rollback can follow reverse dependency order after its own test.

Preflight must compare the installed revision set, table/column/check/FK/unique
catalog, trigger functions/owners, effective roles and privileges against the
exact approved candidate. Unknown prototype tables or inconsistent history fail
before DDL. Do not use IF NOT EXISTS to adopt an unknown existing shape. If
CRED lands separately first, replace C3 creation with an explicitly reviewed
ALTER/backfill/validation revision; no automatic dual-path migration guessed at
runtime. No such production state is established by this source packet.

## Exact grant constraints and issuer ownership

The inventory provides every field definition, including generated digest/time
checks. Preserve CRED schema, flags, operation names/ordinals, scope/null rules,
text-byte bounds, revocation reasons, digest formats and time bounds. In addition:

| Constraint / binding from Go | Common disposition |
| --- | --- |
| uq_runtime_sdk_grant_attempt, uq_runtime_sdk_grant_session | Retain one grant per typed attempt/session. A new native retry gets a new attempt and session, never a reopened old grant. |
| fk_runtime_sdk_owner_caller | Retain exact execution + owner incarnation + admitted caller digest, independent of nullable current user links. |
| fk_runtime_sdk_start_clock | Retain committed Start + session + message + exact started_at; a separately plausible timestamp is insufficient. |
| fk_runtime_sdk_session_fence and fk_runtime_sdk_session_identity | Retain claim digest, worker, supervisor, owner and typed attempt binding to immutable common session history. Never expose raw claim token in SDK credentials. |
| fk_runtime_sdk_committed_start | Retain exact committed typed Start. Flush-only/native-running-only state cannot satisfy common provisioning. |
| fk_runtime_sdk_attempt_number | Preserve the exact attempt ordinal, replacing the direct permanent native FK with the reviewed live-link/admission enforcement below; do not remove ordinal verification. |
| fk_runtime_sdk_execution_source and fk_runtime_sdk_source_install | Preserve exact accepted workflow/deployment/install association through immutable admitted common identity plus live-link validation, rather than preventing native ancestor unlink forever. |
| uq_runtime_sdk_grant_admission_identity, fk_runtime_admission_exact_grant | Retain initial expiry, operation digest, grant/session/Start identity. Renewal does not rewrite the initial admission or widen its scope. |
| uq_runtime_sdk_grant_install, fk_runtime_sdk_operation_install | Retain grant/install relation when operation install is non-null; keep mapping-get's NULL-install rule. |
| ck_runtime_sdk_finite_access, ck_runtime_admission_finite_budget | Retain finite initial credentials/admissions, issued_at >= Start and expiry > issuance. CRED timeout=0 means bounded renewable access, not an infinite bearer. |
| CRED CASCADE native attempt/source and operation grant links | Do not copy cascades into immutable common receipts. Common-internal relationships retain RESTRICT; native links detach under exact approved operations after obligations drain. |

One policy implementation remains authoritative: CRED SelectedSDKPolicy and its
existing digest/positive/negative semantics. `create_workflow_runtime_sdk_grant`
becomes a trusted adapter over the common committed Start/session, not a second
issuer. `load_runtime_sdk_ingress_authority` remains the existing HTTP admission
symbol; dedicated audience/purpose, live caller/source checks and non-upgrading
renewal remain unchanged. No Go-only credential policy is substituted for Python.
Accepted Go remains workflow + matching organization/Solution + integration-get
only; the table's two-operation enum does not expand its consumer capability.

CRED's current issuer owns a fresh committed transaction, checks native running
phase/start/caller/source, locks the typed attempt, and commits both grant tables
before returning its reference. Preserve the committed-read boundary and
idempotent existing-grant digest check while adding common owner/session/Start
checks. `revoke_workflow_runtime_sdk_grant` currently locks a grant alone; its
common adapter must join the common close order below without losing its first
revocation True / exact replay False behavior. Never call a grant-first helper
from inside a session-closing transaction that then locks parents.

Close with no existing grant is a real case: retain the session's permanent
close fact and serialize any future provision against it. Deleting/revoking an
existing grant alone cannot prevent a late first issuance. Initial issue,
renewal, request admission, close and cancellation must all observe the same
closed session and exact committed grant/Start before any SDK effect is admitted.
Already admitted remote effects do not become undone by later revocation.

## Native links, historical identity and sensitive evidence

Preserve existing common table names and logical identities. Do not introduce
parallel runtime_workflow_* lifecycle tables. Direct native FKs are the part to
adapt; immutable common-to-common composite FKs remain intact.

| Source structure | Proposed transformation to review |
| --- | --- |
| runtime_execution_owners.execution_id/workflow_id/deployment_id | Keep immutable historical IDs and accepted artifact/caller digest. Replace fk_runtime_owner_execution_source's permanent native link with a detachable live association validated on admission. Never change accepted IDs when native executed_by/workflow/CLI/form links clear. |
| runtime_sessions.workflow_attempt_id/execution_id/claim_token/worker | Keep immutable typed attempt identity/fence and one session per typed attempt. Add one common-owned live workflow association keyed by session, with native typed attempt/execution binding. Admission validates exact attempt number, claim and worker under native locks. Native detach cannot change common identity or authorize an old session. |
| runtime_deployment_artifacts deployment/workflow/Solution/reviewed_by FKs | Keep historical source/artifact/reviewer identity; separate nullable live references where native deletion/unlink is permitted. Verify existing relationships at insertion. A missing live reference never grants authority; active source/session obligations still prevent source drain. No permanent RESTRICT to a reviewer who native policy permits deleting. |
| Session, Start, report, grant, provision/release and resource composite FKs | Retain RESTRICT between common historical identities, exact bytes/digests and message bindings. Native link detachment cannot cascade away these facts or make a closed session live. |
| Caller snapshot, email/name, result bytes and resource observations | Inventory separately as sensitive payloads. Minimal identity/digest evidence is not a justification to retain all payloads forever. Payload disposal/redaction policy is unresolved; do not migrate persistent production payloads or claim deletion compliance until approved. |

The proposed live-link representation is concrete enough for the next constraint
review (names are internal; this does not freeze a public profile):

- Keep `runtime_execution_owners.execution_id` as its immutable historical PK.
  Add nullable `live_execution_id`, UNIQUE, native FK to executions.id with
  ON DELETE SET NULL, CHECK NULL or equal to execution_id. Replace the direct
  native composite source FK only when the admission guard validates the exact
  workflow/deployment tuple under locked native rows. The immutable common
  owner/artifact FK still binds accepted historical source and executable.
- Add `runtime_workflow_live_links` as a link table, not a second lifecycle:
  session_id PK; execution_id, owner_incarnation_id, workflow_attempt_id,
  claim_token, worker_incarnation_id NOT NULL. Its common composite FK is
  `(session_id,execution_id,owner_incarnation_id,workflow_attempt_id)` to the
  existing session identity, RESTRICT. Its native composite FK is
  `(workflow_attempt_id,execution_id,claim_token,worker_incarnation_id)` to the
  existing typed attempt identity, ON DELETE CASCADE. Create the referenced
  native composite UNIQUE before it. Validate attempt_number at admission;
  keep the accepted ordinal immutable in the grant. Only the live-link row
  may cascade; no historical session or receipt points directly at a native
  attempt with CASCADE. The guard denies native active-attempt removal before
  trusted close/drain; installed external deletion policy remains unresolved.
- Add nullable `live_deployment_id`, `live_workflow_id`, `live_solution_id` and
  `live_reviewed_by` to the existing artifact association. Each has its native
  scalar FK with ON DELETE SET NULL and CHECK NULL or equal to the matching
  immutable historical ID. Admission validates the original deployment ->
  Solution and workflow -> Solution relationships in one locked transaction.
  The byte/artifact/source/reviewer evidence is not rewritten on unlink.
  Native source retirement is independently gated by undrained sessions;
  NULL live pointers alone do not prove release or allow new admission.
- Common session, Start, grant, report and resource FKs continue to use the
  immutable common IDs. Remove direct native references only in the same
  reviewed change that adds the live binding and exact admission/detach
  enforcement; there is no intermediate unguarded acceptance state.

New link fields are the only mutable part of otherwise immutable association
rows: non-null -> NULL under the narrowly permitted native detach operation;
never NULL -> non-null, replacement, owner transfer or arbitrary field update.
The existing whole-row immutability triggers therefore need exact field-delta
validation, not a disabled trigger or blanket UPDATE exemption. Separate
INSERT validation authenticates the trusted writer and verifies all live rows
and accepted relationships, including mismatched but individually valid IDs.
A valid session UUID alone never authorizes inserting a live-link row.

The earlier preview that partially nulls a composite historical identity is
rejected. This representation still requires independent constraint/concurrency
review before an apply-ready revision. Minimal historical IDs must not be
mistaken for an approved retention policy for caller/result/resource payloads.

Native ancestor positives to retain: inactive-user deletion clears executed_by;
user/CLI cascade clears cli_session_id; form purge clears form_id; workflow and
API-key deletion use existing SET NULL actions. Only exact OLD live parent to
NEW NULL transitions, with all other protected fields unchanged, may use the
ancillary detach exception. No arbitrary incumbent UPDATE of Rust lifecycle,
no reassignment or reattachment. Public errors remain native errors. A full
Execution history delete is not a new API in this packet; installed maintenance
and deletion policy remain a separate catalog gate.

One session per typed attempt is compatible with native retry when the old
attempt closes and a NEW attempt is created. Preserve `create_claimed_attempt`'s
published-attempt reuse and max(attempt_number)+1 behavior. Only proven no-effect
preparation plus trusted reap and actual native retry eligibility may admit a
replacement attempt. Missing Start/ACK/heartbeat is not proof. Current Python
worker_lost retry of RUNNING work is not permission to replay Rust possible-Start
work; writer guards must intercept it while leaving Python-owned policy intact.

## Python artifact support is an extraction prerequisite

Go's `ck_runtime_artifact_descriptor_binding` currently requires
`artifact.kind = native-executable/v1`; this is not a language-neutral common
constraint. Do not weaken it into unrestricted JSON or simply insert Python
source into a native executable descriptor.

The common header must bind runtime/profile, source manifest/resolution,
artifact identity, protocol and build/materialization evidence. Runtime-specific
validation stays a closed discriminator: preserve native-executable/v1 for Go;
derive a separate Python descriptor from actual interpreter/dependency/import
and accepted-deployment behavior before admitting Python. Rust consumes reviewed
registration, never imports Python to discover metadata. Neither this packet
nor Go bounded acceptance freezes a new Python artifact schema. First extraction
may retain Go-only artifact admission while the reviewed Python descriptor is
prepared, but that is not Python MVP completion or authority to change workspace.

## Combined transaction order and remaining lock proof

Proposed order for NEW common mutations: source release shared fence -> native
execution advisory fence -> Execution -> typed workflow attempt -> immutable
common owner -> session -> ordered Start/admission/grant/operation/report/resource
rows. Use stable ID ordering for multirow siblings. Apply this consistently to
Start/provision, close, Result, no-effect recovery and resource settlement; no
transaction may perform vendor/network effects while holding these locks.

This is a proposed adapter order, not a claim all current writers use it:

| Current source | Actual edge / required adaptation |
| --- | --- |
| main execution/poison.py:60-94 | execution advisory -> Execution -> delivery ownership -> typed attempt; preserve delivery ownership gate, intercept wrong-owner writes. |
| main execution/attempts.py:118-175 | caller execution fence -> active typed attempt -> reuse or create next ordinal; common adapter must preserve that counting. |
| CRED runtime_sdk_grants.py:_read_work | attempt FOR UPDATE -> Execution/deployment/Solution SHARE NOWAIT; adapt new common issuer to common order, preserve deny-and-rollback on contention. |
| CRED revoke_workflow_runtime_sdk_grant | grant FOR UPDATE only today; common revoke/close must not acquire parent/session after this lock. |
| Go owner-rs/src/lib.rs:lock_session_state | shared source fence -> attempt FOR UPDATE -> owner/Execution/deployment/Solution UPDATE NOWAIT -> session UPDATE; reuse checks, adapt order in a newly qualified common candidate, leave accepted source fixed. |
| Go fixture isolated_writer_guard | pg_try_advisory_xact_lock on execution before checking OLD/NEW owner; retain fail-closed absent-parent race protection, not an unconditional waiting advisory acquisition inside a later trigger. |

Secondary rows with incompatible incumbent ordering must fail NOWAIT/try-lock
and roll back, rather than waiting while holding a later lock. Statement/lock
timeouts bound failure; they do not prove absence of deadlocks. Include implicit
FK KEY SHARE, index uniqueness, trigger queries, cascade updates, table locks,
source-release exclusive locks and pool transaction boundaries in the final
wait graph. No blanket retry may turn unknown possible effects into replay.

Prototype writer guards grant SELECT on all public tables and DML on a finite
fixture set. They authenticate session_user; Rust additionally checks current_user.
Their broad read grants, fixture ownership column and named logins are not a
production catalog. Ordinary triggers cannot constrain SELECT or all LOCK TABLE
modes. Cross-owner read and ANY-table-lock concession remains an explicit held
authority decision; no production grant is created here. The runtime child gets
no SQL identity. Current native guarded-table candidates are executions, typed
attempts, logs, AI usage, event deliveries, generic lifecycle projections and
their actual ancestors; the installed catalog must prove closure, including
functions, memberships, SECURITY DEFINER owners, search paths and bypass rights.

## Implementable next package and acceptance

CP-C1 owns the common schema extraction, adapters and finite restricted-writer
verification. It does not own Go SDK/consumer code, generic delivery, agent/model
execution, public SDK or Alembic framework migration. Before implementation,
review the proposed live-link constraints, derive the Python descriptor and settle sensitive-payload
policy where required, installed catalog disposition and independent combined
lock/security review. These are concrete remaining design/evidence gates, not
permission for another runtime experiment or a policy waiver.

Prepare supported isolated PostgreSQL tests for:

- All 35+3 grant field bindings, both CRED operations and original/effective
  caller positives/negatives; Go accepted subset remains closed.
- Foreign execution/attempt/claim/worker/supervisor/Start-clock/caller/install
  substitutions fail; operation scope NULL behavior and finite expiry remain.
- Close-before-first-grant, close-vs-issue/renew/ingress, failed commit and lost
  commit ACK: no exposed credential from uncommitted or closed authority.
- Exact native unlink positives, arbitrary reparent negatives, retained common
  identity after live-link detach and denial of late SDK/report traffic.
- Competing incumbent/common Start/Result/cancel/poison/recovery/ancestor writes,
  including trigger/FK/table lock contention; no weaker owner exclusion.
- Proven no-effect NEW-attempt retry with preserved ordinals; possible Start,
  missing teardown or unknown cleanup remains nonreplayable and source pinned.
- Identical Result replay returns the exact prior receipt; conflicting bytes
  deny; native result/log/usage/event projection commits atomically once.
- Runtime session still pins source after native logical terminal; no resource
  settlement is fabricated from missing files, process exit or fixture cleanup.

Use the repository's supported Linux Docker/CI lane and exact clean candidate:
`./test.sh quality api`, affected `./test.sh tests/e2e/platform/...` cases and
`./test.sh pre-pr`, plus actual Rust fmt/clippy/test when Rust changes. No such
application tests or SQL application are claimed by this static packet. W0,
closed campaigns, current #1018 CI and accepted Go proof do not transfer to a
changed common candidate. No merge, deployment or new capability is authorized.
