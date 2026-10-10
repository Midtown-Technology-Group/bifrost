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
