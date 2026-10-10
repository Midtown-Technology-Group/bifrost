# Actual-schema writer guard experiment

This is an isolated implementation candidate under Thomas's interface release at
b8b44cbc0 (SDK0.0.0-spike.2), not an accepted lifecycle writer or production
migration. The existing Alembic graph and actual platform tables are installed
first. A fixture adds temporary ownership bindings and triggers; no application
DSN, Python execution code, production principal or dispatch is changed.

The candidate uses actual authenticated `wex_core`/`wex_incumbent` backend logins
through a separately pinned transaction pool with no forced backend user. Its
non-owner, non-superuser, non-BYPASSRLS principals have no membership, schema CREATE,
TRUNCATE or trigger/function ownership. The read-only NOLOGIN guard owner has a
fixed search path and no callable grant to either actor. These are synthetic
fixture identities, not new operational credentials.

A coordinator execution must be born in the same committed transaction as its
retained `runtime_execution_owners` record. An initially deferred reverse FK and
an immediate class-bound forward FK enforce that association; immutable root
identity/class prohibits taking over an existing incumbent execution. The root
record continues to use the existing execution domain. No runtime-specific
lifecycle/status or synthetic result store is introduced.

Typed attempts, execution logs and AI usage retain a class bound by composite
FK to the real execution. Their original FK delete action is preserved. Conditional
coordinator FKs cover generic workflow attempts, normalized lifecycle events and
event deliveries, while existing incumbent links before an execution exists remain
legal. Generic lifecycle events also bind their actual attempt, job type and job
ID together. UPDATE cannot detach or reparent a dependent record. Entire rows are
protected against a foreign actor; there is no summary/maintenance exemption.

For absent-parent non-FK links, guard operations use a non-blocking acquisition
of the existing poison advisory namespace
`hashtext('bifrost:workflow-execution:' || execution_id)`. Contention aborts with
55P03 rather than waiting behind execution-first poison while an owner holds an
attempt or execution lock. Root creation rejects already retained incumbent
pre-execution links. This is an additional guard fence, not a replacement for the
released common source→typed-attempt→NOWAIT owner/execution/deployment/Solution→
session→Start/admission→grant→receipt order. Real issuer, ingress, cancellation,
recovery and poison races under that complete order remain required.

Hosted verification runs the normal repository test lane against the actual
schema and this pool. Cases cover each guarded table's UPDATE/DELETE/conflict
attempt, correct-owner writes, reverse exclusion, owner reassignment, missing
retained owner rollback, non-FK pre-execution links and their live transaction
race, reparenting, ancestor cascade rollback, privilege bypasses, backend identity
and function custody. Original storage/attempt/history checks and full backend
unit tests run before the fixture, against the new exact source. The fixture is
removed only by verified teardown of the disposable task database/stack.

Acceptance remains outstanding: new Alembic/role configuration review; all
incumbent entry points and external Redis/broker/event effects; source/deployment
ancestor accounting and legitimate retention/drain; real common lock-order races;
restricted issuer/ingress; Rust owner transactions; same unchanged Go binary;
API durable readback; actual process/descendant custody and crash recovery. The
current privileged application backend is not qualified by this separate-pool
experiment. A SQL guard test cannot accept that application configuration or
stand in for runtime evidence.

The first queued run38068612875 at ebe12873a was cancelled before any job steps
executed. Source review found client timeout startup parameters inconsistent with
the pinned pool's admitted configuration. The correction retains the same finite
limits as synthetic server-role defaults and uses the primer's disabled client
statement cache. No runtime failure was waived and no timeout/retry was enlarged.
A fresh source run is required; the cancelled candidate is not test evidence.

The reviewed follow-up closes the actor mapping to the two synthetic principals
and the existing incumbent bootstrap login; unknown identities receive no owner
class. Coordinator birth requires common `deployment-v1` source identity, and
caller/source/input/retry facts remain immutable after birth. Added cases also
exercise the actual Python poison-finalization entry point over the incumbent
pool login: foreign SQL rollback must precede cleanup/publication callbacks, while
an incumbent-owned poison still terminalizes. The callback spies test invocation
ordering, not real Redis/broker behavior; those acceptance proofs remain required.

Run38068697598 at6fa983aab remained queued with no runner or executed steps while
that review completed. It was cancelled as superseded before publishing the
closed-actor/immutable-facts/real-poison follow-up. Neither queued cancellation
counts as passed runtime evidence or a diagnosed test failure.


Executed source d690e378e (run38069000716) passed13,722 full backend unit tests
and109 storage/attempt/history tests, all with zero skips/errors/failures, then
failed all51 guard cases at setup. The module-scoped async installer requested
pytest-asyncio's default function-scoped runner; no guard SQL or assertions were
executed. Explicit module loop scope repairs that mismatch without changing the
repository default. Original failure logs and exact suite XML are retained in
[the source evidence](writer-guard-source-evidence.json); cleanup proved empty
container/volume/network inventories. API quality was unrun after failure. The
old workflow copied guard XML only on success; cleanup now independently retains
the final JUnit on failures. This is a source correction requiring fresh hosted
proof, not an accepted writer or an unchanged rerun.


Run38070069167 at a04bfb973 repaired module loop scope and installed the actual
schema guards. It passed13,722 unit and109 baseline tests (zero skips/errors/
failures), then all51 cases failed while constructing a claimed workflow attempt
without its required publication/claim timestamps. The existing state-shape
constraint correctly rejected it before case bodies. The fixture now supplies
those timestamps; no constraint was relaxed. Failed JUnit is retained and
teardown again proves empty inventories. API quality was unrun. Source-bound
logs/XML and corrections remain in the evidence JSON; fresh proof is required.


The corrected candidate `9e2d247676eec16444ea8855ba210bc10b303f1b` passed
hosted run38071353079:13,722 backend unit tests (410.207s),109 existing
storage/attempt/history tests (14.224s), and all51 actual-schema guard cases
(14.368s), with zero errors/failures/skips. API Pyright/Ruff passed. Retained
source/tree and all six source hashes match Git, and final failed/success JUnit
capture agrees. Independent final inventories contain no task containers, volumes
or networks. The longest guard duration is one-time schema installation setup
(5.75s); no case call exceeds two seconds or launches a deployment/build job.

This proves the synthetic principal fixture's tested SQL exclusion, immutable
bindings, non-FK races and actual poison callback ordering against platform main
968ac4693c99cc52f156f5aacb63a40a8fcd9437. It does not qualify existing privileged
application connections, real Redis/broker side effects, full common lock order,
Rust owner transactions, native registration or runtime acceptance. The earlier
two diagnosed fixture failures remain retained alongside the successful result.

## Next Rust transaction candidate

The separate Rust owner foundation at d3fcb82a4 passed its locked offline checks
and two identity-encoding tests in run38073223134. That run executed no database
transaction. The next candidate builds a private observation probe before starting
the application test stack, retains its exact binary hash, then runs all70 actual-schema cases together:51 guard cases and19 Rust
observation/lock/custody cases under one installation. The unchanged accepted Go workload is not rebuilt or launched
by this observation lane.

PostgreSQL16 requires UPDATE privilege on a column even for SELECT row locking.
The fixture therefore grants UPDATE(id) on Solution/deployment only, paired with
a NOLOGIN-custodian-owned trigger rejecting all coordinator source updates.
Incumbent source writes remain legal. Exact custody, lock-versus-write negatives
and incumbent positives are included; no operational principal or application DSN
changes. [PostgreSQL locking privilege requirement](https://www.postgresql.org/docs/16/sql-select.html).
This exercises the released source/attempt/NOWAIT secondary/session prefix of the
common order. Start/admission/grant/receipt races, live custody and lifecycle writes
remain outstanding. A successful observation grants no authority to spawn.


Run38073887223 at fca8ac08d passed13,722 backend unit,109 baseline and51 guard
cases, then all19 Rust cases failed during setup: PostgreSQL roles survived the
per-command database reset, so a second installer collided with the retained
guard role. No Rust database case body executed; API quality was unrun and actual
teardown inventories were empty. The failed XML/logs and exact hashes are retained
in the owner foundation evidence. All70 cases now share one module/install and one
normal repository test command. No role is silently reused or recredentialed; all
original assertions remain and fresh source proof is required.

Run38074807470 at830682629 collected all70 cases under one installation.56
passed and14 real Rust cases failed with `database_failure`; there were no setup
errors or skips. The original probe did not retain free-form database errors.
Checksum-verified SQLx0.9.0 source shows its default Startup packet includes
`extra_float_digits=2`, which the unchanged pinned pool does not admit. The next
candidate omits that client option and adds a red-capable control using the exact
rejected default; this diagnosis still needs the fresh runtime result. Pool policy,
roles, deadlines and assertions remain unchanged. Original logs/XML/source hashes
and empty teardown inventories are retained in the owner foundation evidence.

## Provisional Rust cancellation write

The next isolated implementation uses the same common prefix and locks retained
Start, ordered admissions, grants and receipts after session serialization. It
requires a matching active attempt, retained Start and Running execution before
committing existing Cancelling status, permanent session close and same-session
grant revocation together. A repeated matching request preserves clocks and
returns the existing decision; an accepted/inconsistent Result receipt rejects.
There is no pending/scheduled cancellation shortcut, early Cancelled projection,
process stop, finalization, admission, grant signing or production endpoint.

Actual-schema cases cover stale identities, incumbent rejection, missing Start,
late-tail contention, preserved terminal success, inconsistent winning receipt,
duplicate cancellation and an actual PostgreSQL revoke-trigger failure that must
roll back the preceding root/session writes. Fixture Start/grant/receipt metadata
remain synthetic storage facts, not accepted admission, signed credentials,
validated wire Result or live process evidence. No new lifecycle writer is accepted
by adding this code or by its conformance tests. The full schema/identity/immutability,
mechanical writer exclusion and common source/attempt/secondary/session/Start/
admission/grant/receipt graph remain subject to the released acceptance gates.

## Verified cancellation boundary and next recovery candidate

Run38076305806 at164e6b64d passed13,722 backend unit tests (418.169s),109
baseline cases (14.767s) and88 guard/Rust cases (22.064s), all zero errors,
failures and skips. API Pyright/Ruff passed; actual cleanup inventories were empty.
The unchanged pool rejected SQLx default Startup options in the negative control
and admitted the corrected client. Actual Rust cancellation atomically projected
Cancelling, closed the session and revoked grants, preserving duplicate clocks
and rolling back preceding writes on a late PostgreSQL revoke failure.
Source objects and retained files are hashed in owner-foundation-source-evidence.json.
No runtime writer or full vertical slice is accepted by this evidence.

The next candidate reads back a cancellation decision under the same complete
lock graph without lifecycle writes, repair, replay or reassignment. Its probe
fault exits after actual database commit and before replying; a fresh process
reads the retained same-session decision. Cases cover unchanged pre-commit state,
post-commit state, identity/backend denial, inconsistent partial close and tail
contention. This deliberately bounded crash window does not prove commit transport
ambiguity, spawn/lost-launch-ACK recovery, source drain or terminal finalization.
