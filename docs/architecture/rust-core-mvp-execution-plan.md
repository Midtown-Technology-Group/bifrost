# Rust control-plane MVP: execution plan

Refined 2026-10-16. This is the current sequencing and acceptance plan;
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
`f10b80e806bebc232247e4568f389de0857ed733`.
The [latest boundary audit](rust-core-mvp-workspace-boundary-f10b80e8.json)
records authored source, forbidden-import/parse results and empty allowlist.
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
are unproved as integrated MVP behavior; R5 has no final decision. Existing
unit/source/Go and prerequisite CI results remain separately scoped. Do not
turn these milestone labels into new experiment allowances. R1 needs only the
R0 decisions relevant to its transaction; Python packaging does not block
independent approved persistence work.

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
