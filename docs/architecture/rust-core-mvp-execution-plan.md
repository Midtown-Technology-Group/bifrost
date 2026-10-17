# Rust control-plane MVP: execution plan

Refined 2026-10-17. This is the current sequencing and acceptance plan;
[common reconciliation](rust-core-mvp-common-runtime-reconciliation.md) owns
shared decisions, and original source-bound receipts retain their evidence.
This plan does not approve held schemas, roles, capabilities or new campaigns.

## Outcome and current evidence

Prove or falsify Rust ownership of workflow and then agent control-plane
lifecycles while Python executes unchanged authored code. A green service,
codec, row planner, reference fixture or Go spike is insufficient: Rust must
commit real owner/attempt/Start/cancel/result decisions and native projections.
A complete MVP still includes safe coexistence, unchanged consumers, security,
reversible routing, observability and an evidence-based CONTINUE or STOP.

Fresh fetch: platform `e42999395231ebc536711e5015422edfb75613a7`, workspace
`8a2a441afcee3fc8a1bb3f74deb07440da5a86d0`.
The [latest boundary audit](rust-core-mvp-workspace-boundary-8a2a441a.json)
records 2,459 authored Python files, zero forbidden imports/parse errors and an
empty allowlist.
Both prerequisite merges remain ancestors of these exact heads. Recheck these
invariants when either baseline changes.

REST readback: #1011 is open at
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e`; architecture remains provisional.
AUTH-P1 #1018 is open/unmerged at
`39245315e193e1d04886044677bf7a75bb8c6f1f`, with exact-head source review and
applicable CI evidence complete in its validation receipt. CRED-P1 #1037 remains
open/unmerged at `3918d608a2d497d1ca8424fd4229d191323e2d7a`.
Passing #1018 does not establish CRED integration or Rust parity. Do not require
merging prerequisites to test an explicitly composed, pinned isolated candidate;
never treat that candidate as deployed or main. GraphQL was rate-limited during
this refresh; REST supplied the metadata above, not refreshed review/check lists.

Existing Rust foundation/domain work and common Go mechanisms are reusable
inputs. Accepted Go remains frozen and independently owned. Sopdet stays a
separate blocked compatibility track. Neither is a substitute for Python or
agent acceptance. Preserve every stopped/exhausted campaign and its failures.

## Changes to execution strategy

1. Use one thin end-to-end workflow as the next product experiment. A successful
   isolated Rust transaction is its first checkpoint, not a redefinition of MVP.
2. Reuse common mechanisms and existing repository lanes. No new supervisor,
   test conductor, generic runtime framework or parallel lifecycle schema unless
   the current implementation demonstrably cannot meet a specific contract.
3. Close the exact blocking decisions in one packet. Do not wait for a universal
   plugin/package/security architecture before testing the selected workflow.
4. Identify the selected environment reproducibly, not every possible Python
   environment. Preserve existing Python-owned dependency behavior; no global
   pip redesign or Rust-specific workspace accommodation.
5. Keep current installed-schema proof in the isolated supported environment
   separate from production rollout proof. Production catalog/role assumptions
   may not support production claims; missing production access alone is not a
   reason to stop independently authorized source or isolated work.
6. Integrate observability and parity in each product package. Avoid a final
   unbounded testing phase and avoid accumulating more unexecuted case rosters.

## Work packages and dependency gates

| Package | Owned scope | Depends on | Observable exit |
| --- | --- | --- | --- |
| R0 — resolve the finite seam | Exact common migration/live-link constraints, effective restricted-principal proposal, combined lock graph, payload disposition for the selected slice, interpreted-artifact environment evidence and negotiated execution profile | Existing consolidation/Python-artifact inputs | Independently reviewed concrete diff; held authority decisions bundled for Thomas; every gate identifies its exact affected package. No speculative role grants. |
| R1 — first Rust-owned transaction | Extract the minimum common owner/session/Start/report persistence into existing core-rs crates; one Alembic continuation and its guarded native bindings; tests on real PostgreSQL | R0 decisions needed for this operation and permission for its isolated DDL/roles | One Rust-owned execution commits its typed attempt/Start and fenced result; incumbent wrong-owner writes fail; Python-owned writes still pass. No fake runtime or whole lifecycle acceptance claimed. |
| R2 — unchanged Python workflow | Narrow Python runtime adapter and selected artifact observations; unchanged workflow/SDK; existing CRED signer/ingress as the one closed authority implementation; Rust orchestration and native projections | R1 plus artifact/profile/credential gates | Real submit -> Rust admission/Start -> post-Start provision -> Python import/execution -> Rust committed result/log/usage/event -> trusted reap/resource/source closure. No Python terminalizer standing in for Rust. |
| R3 — failure, coexistence and rollback | Extend the same vertical slice with concurrency/fault cases and existing routing/deployment configuration | R2; separately authorized execution where campaign limits require it | Same-database mixed writers, close/issue, cancel/result, duplicate/conflict, lost ACK, no-effect NEW-attempt retry and possible-spawn ambiguity pass; new admissions switch back to Python while existing Rust owners drain safely. |
| R4 — actual agent ownership | Typed agent lifecycle/attempt extension, unchanged selected Cove agent/tools and genuine Python agent/model runtime, idempotent child-workflow adoption and usage/summary/event projection | Workflow R2/R3 plus exact agent authority/profile decisions | Rust owns actual agent state, tool intent/one child outcome and terminal/cancel; unchanged full tool schemas, retained ordered steps and real permitted model-response consumption. No broadened workflow grant or fabricated terminal result. |
| R5 — decision | Acceptance audit, fair resource/cost comparison, known differences and rollback runbook | R1–R4 acceptance evidence | Concrete CONTINUE or STOP against every requirement; unresolved critical security/correctness/custody gate means no successful MVP. |

Owned implementation paths are the existing `core-rs` domain/db/core/contracts
surfaces, additive `api/alembic/versions` revisions, narrow Python execution and
CRED adapters, and scoped parity/integration tests. R4 separately owns its typed
agent tables/adapters. Keep package PRs narrow; do not place a global event,
Redis, scheduler or generic durable-delivery rewrite inside them. Enumerate exact
files and interfaces before each implementation package; low-overlap delegation
is allowed only under applicable authorization, with one accountable architect.

Current acceptance status: R0 is a concrete but unapproved design input; R1–R4
are unproved as integrated MVP behavior; R5 has no final decision. Current Rust
cancellation source is `6d07e94fcb5f8028ed7d17f3725b282e488cf093`: an internal
cancellation/session-close/grant-revocation transaction with execution-first
locks, strict principal checks, exact native live binding and trusted SQL name
resolution. Independent Sol source review accepted it. The new live-db test
covers temporary-shadow resistance in transaction preparation, not full
cancellation/authority SQL. The [R1 checkpoint](rust-core-mvp-common-session-checkpoint.json)
retains earlier source-bound results; the current composed source qualification
is in the [writer checkpoint](rust-core-mvp-common-writer-checkpoint.json).
Common-schema/live-db lifecycle checks remain unexecuted. No Start/result
ownership, public endpoint/event execution or
complete first-issuance race proof is claimed. R1 needs the R0 decisions relevant
to its transaction; Python packaging does not block independent approved
persistence work.

The next cancellation dependency test is now executable Rust test source at
`3c66dc70a58a8be1758a535c6b47c1147df71f6e`,
`core-rs/crates/bifrost-db/tests/common_session_sql.rs`; the operation module
itself remains byte-identical to `6d07e94f`. It requires actual core/incumbent
logins and committed synthetic Rust/Python fixtures on one database. It tests
the real cancellation function, wrong actor/binding, direct wrong-owner DML,
an incumbent-owned write positive, advisory contention, fresh committed
readback and internal reconciliation. Incumbent must observe the same Running
to Cancelling transition. It creates no schema, roles, grants or fixture and
is behind the explicit `common-session-sql` test feature. Independent source
review accepted the repaired assertions. Compilation now passes; actual common
SQL remains unproved. The [isolated packet draft](rust-core-mvp-r1-cancel-sql-packet.md)
now composes current-main guards, reviewed two-stage fixture production and
the Rust test on one source candidate, `560e3decd`. The producer preloads native
deployment history before source guards, then uses genuine core for common
writes. Its immutable private intent survives final-publication failure.
Independent source review accepted the producer, finite inactive role SQL and
explicit driver. Supported checks exposed and corrected checkout setup errors
and Rust formatting defects without weakening checks. Pinned fmt, strict
all-feature Clippy and 36 default Rust tests passed on VM101 at source
13744851d. Its exact Rust tree and all 41 inputs remain unchanged at
560e3decd; the opt-in common SQL test was compiled but not executed. Earlier
a6b selected154/pre-PR passes and original failures remain source-bound.
Final source inspection found one unqualified fixture attempt INSERT under the
core trusted search path. On native PostgreSQL, the original361 source fails
both missing-public and temp-shadow cases; reviewed560 explicitly names the
native public table without broadening the path. The same two cases now pass,
as do all 154 selected tests, with zero skips. This is native fixture evidence,
not common cancellation/role qualification. Final literal pre-PR also passed
after a preserved, diagnosed GitHub action-pin API rate-limit failure; one unchanged-source recovery followed a successful
exact endpoint probe and available quota. No credentials or gates changed.
The trusted comprehensive fallback retains broader suites as required CI,
not local passes.
Normal CI38996089958 and Rust Core38996091192 were dispatched once for560 and
remain unqualified; obsoletea6b runs are terminal cancelled, not green.
The exact eight-file activation recipe and read-only catalog capture are
independently source-accepted. A native-only PostgreSQL 16.15 catalog smoke
passed with no common relations or proposed roles installed. The complete independently source-reviewed
[operator procedure](evidence/r1-cancel-sql-proposal/operator-runbook.md) now
settles private inputs, exact activation, restricted authentication and bounded
cleanup. Explicit isolated authority approval and actual installed-catalog
reconciliation still gate SQL/login execution. This dependency does not complete R1's
Start/result exit or qualify unchanged Python/agent execution.

The [storage checkpoint](rust-core-mvp-common-storage-checkpoint.json) now binds
independently source-reviewed inactive C1-C4 candidates. C3 at
`b7922c9b640692afca5ce4a2ec48f8cdc4f71429` preserves closed CRED grant and
operation policy through exact common identity. C4 follows at
`f973411fee9cfc65995f9729f91eaf5c4d6701a1`, with provision/release and separate
immutable physical-resource observation storage. Its original checkpoint
recorded static checks only; current composed offline storage tests now pass.
Actual candidate SQL remains unexecuted. Earlier qualification of C1/C2 confirmed
current-main schema and absent candidate tables, then external VM shutdown
interrupted the first offline test. No collected test is counted as passed.
That task's resources were removed and VM101 returned to stopped. Do not repeat the
same uncoordinated VM qualification blindly or treat its legacy RabbitMQ
configuration as PostgreSQL delivery evidence. Historical run 38983420259 and
its parent runs cannot qualify current composed source. The previous a6b VM101 validation stack was removed after evidence collection,
and the guest was read back stopped. This cycle restarted the same tagged test
VM for native-only361/560 namespace and final source qualification, with task
ownership of project `bifrost-test-0290298a` and eventual cleanup/shutdown.
No common schema, SQL role/login or runtime was activated. After final checks,
this task project was removed and independent readback found zero containers,
networks and volumes. A fresh other-activity check was empty; VM101 was then
gracefully shut down and read back stopped. Source/private evidence and pinned
Python61a/Rust0dc tool images remain owned cache.

C5-A/B1 now implement native writer exclusion, exact ancestor/sanitation
exceptions and close/late-grant serialization. Source-reviewed B2/B3 add all
common writers, open live Start/admission fences, historical source accounting
and destructive-drain checks. The
[writer checkpoint](rust-core-mvp-common-writer-checkpoint.json) identifies
exact `51dd6c3e4` source acceptance and current composed candidate560e3decd.
Superseded CI38988901890 was cancelled without tests; it supplies no pass.
All new PostgreSQL behavior remains unexecuted; source review does not qualify
physical custody, implicit lock/cascade races, public errors or the post-commit
S3 sweep. Existing CRED remains the one policy/issuer owner.

C5-C adds the finite, review-only isolated SQL principal profile. Core receives
no user/source privileges; a separate fixed NOLOGIN artifact guard preserves
the original parent NOWAIT locks. The existing lifecycle custodian stays
read-only. Independent review caught missing CONNECT/schema USAGE, repaired
before source acceptance. No roles, grants or schema were applied; effective
installed ACLs, genuine direct/pool logins and read/lock-authority approval still
gate a SQL experiment. This is not a full Python backend credential profile.

CP-C5-D source review rejected reusing the core SQL factory for the trusted
Python credential issuer: it would confer native lifecycle write authority.
The existing one-issuer direction remains valid, but its least-authority SQL
principal, source row-lock privileges and exact ORM projections require a
concrete separate disposition. No new role is approved or implemented.

With final560 pre-PR passed, the next finite action is the bounded
isolated authority decision for the R1 no-grant fixture/qualification packet;
the CRED authority gap independently gates R2. Keep R1's selected persistence experiment separate
from R2's interpreter/physical-resource acceptance; do not make production
catalog access a prerequisite for an approved disposable SQL test.
CRED's common adapter and report/projection enforcement
remain required for R2; no-issuer R1 tests cannot qualify them. No candidate DDL,
role grant, credential or runtime enablement is authorized by source acceptance.
No-session owners stay pinned until an explicit pre-session resource/proof
boundary is established; general partial-cleanup and payload disposition remain
unresolved. Logical terminal/close, missing files and bare resource-row presence
cannot substitute for the original qualified physical observer.

## First falsification checkpoint

R1 should answer a small hard question: can the common Rust owner and incumbent
Python paths safely share current native tables and schema under distinct actual
principals? Use real constraints/transactions and public ancestor-unlink cases,
not fixtures that isolate the writers into different ownership databases.

R2 then answers the runtime question: can one unchanged Python reference use
closed attempt/session credentials and preserve its observed behavior without
privileged backend imports or ambiguous re-execution? The artifact must identify
the actual interpreter, adapter, SDK, source and dependency environment. Current
source closure and installed-package counts cannot supply that proof.

A failure here is valuable evidence. Do not respond by expanding authority,
changing workspace imports, relaxing fences, replaying unknown effects or
inventing a parallel schema. Diagnose the specific failure, propose the smallest
in-scope repair, or record a falsification requiring a new decision.

## Verification contract

Use the same executable scenario against pinned Python and candidate Rust.
Compare HTTP/error/auth/org behavior, committed native/common rows, attempts and
fences, log/usage/event effects and cancellation/resource/source disposition.
Normalize only named nondeterminism. Prove meaningful response, DB and committed
event drift detection before using the harness to qualify a product milestone.
A synthetic observation comparator alone cannot close those service-level gates.

Use existing Linux Docker/CI lanes; no product tests/imports on the PVE host.
For changed Rust run native fmt, strict all-target/all-feature Clippy and workspace
tests in the supported image. For Python run repository quality and scoped
unit/integration tests. Final clean candidates must satisfy literal pre-PR and
all applicable normal CI/review/provenance gates. New source needs new evidence.
Do not rerun unchanged failures until green, revise expectations for parity,
weaken CI or create an alternative workflow to dodge a stopped campaign.

Record exact source/reference/tree, environment/image, command/scenario,
producer/run/attempt/digest, observed result and cleanup owner/disposition.
Retain failure evidence. Unknown custody or physical cleanup is not success;
logical terminal, process exit or missing files do not settle resources.

## Progress and communication rules

Maintain one current acceptance table rather than another chronological ledger.
Every work cycle selects the earliest unproved product dependency and produces
one of: integrated implementation, a test that demonstrates/catches the relevant
behavior, an evidence-backed interface decision, or a diagnosis that changes the
next action. Documents support those outcomes; document count is not progress.

If two successive cycles revisit the same blocker without new evidence, change
the approach or present the concrete decision needed; do not invent another
framework or rename a stopped experiment. This is a diagnosis trigger, not a
waiver, automatic pause, budget reset or permission to abandon the workstream.

After a meaningful supported run or bounded repair, assess expected versus
observed benefit before spending another run. Respect current retry limits and
existing human controls. Continue independent source work while a gate is
blocked. A verified wait names a live handle and cannot replace other available
work. Stop completed PR watches; no multi-day idle stewardship.

The Go owner works independently. Contact only for a concrete blocking interface
or dependency; no routine acknowledgements, status relays, duplicate evidence
runs or edits to the accepted Go packet. No production, merge, release, live
vendor use, broader SDK capability or held schema/role authority is added here.

## Final decision criteria

CONTINUE requires the unchanged Python workflow and selected agent to work with
actual Rust lifecycle ownership, meaningful parity/drift evidence, safe mixed
writers, no weaker auth/tenancy/fences, correct ambiguity and physical closure,
reversible routing, supported CI and diagnosable operation. Compare startup,
idle/load resources and latency fairly, and include build/review/maintenance
cost. Do not use speed to compensate for semantic drift.

STOP or recommend a narrower Rust role when isolation requires competing
schemas, unchanged consumers need accommodation, required authority cannot be
bounded, possible effects replay, native behavior cannot coexist, rollback needs
data repair, evidence cannot distinguish drift, or integration/harness complexity
outweighs demonstrated ownership benefit. Identify what to keep or retire and
where Python should remain. A sound stopped experiment is a valid outcome.
