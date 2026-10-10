# Isolated common owner foundation

The observation transaction reads actual retained owner/session identity under
Thomas's released common lock order. The provisional cancellation operation writes
existing Cancelling status, session close and grant revocation together. Neither
operation admits a workload, issues credentials or spawns processes. The new
provisional Result transaction conditionally projects the existing terminal
outcome with an exact-byte durable receipt; this is not an accepted runtime. Production dispatch is absent.
Matching database custody digests are observations, never proof of a live channel.
The SQL returns no portable launch authority.

`record_release_candidate` adds the isolated common admission lock tail. It
requires an open Running session/attempt, matching retained Start, one exact
provision/grant/delivery and no result receipt. Grant/deadline expiry is checked
at the actual INSERT clock after all locks. It inserts an immutable release only
on a newly observed commit; a matching retained release returns `AlreadyRetained`
without another write or delivery. Conflicts, closure and commit ambiguity deny
fresh authorization. The private probe can exit73 after commit/before reply;
recovery must observe that same row without treating it as a new launch.

`observe_release_candidate` uses the same lock graph but never inserts a release.
It distinguishes an absent release from the exact retained identity, including
after cancellation, without replay, repair or ownership reassignment. The lost
reply test uses this read-only operation rather than calling the writer again.
Absence remains observation only: it does not prove no physical effects or permit
another launch. New read-only recovery cases require their own source-bound proof.

This is transaction evidence only, not an accepted release-to-spawn path. The
synthetic fixtures still supply Start, unsigned grant and provision. Full accepted
bundle/source/input checks, actual observed wire frontier, authenticated issuer
material and live guardian/process/channel custody remain required together.
There is no production caller, actual native adapter launch or credential issuer
in this crate. These new paths require fresh database/race evidence.

The separate crate deliberately imports no Go source or Rust control-plane
implementation. SQLx0.9.0/Tokio1.53.1/toolchain1.98.1 match the existing Rust SQL
characterization source0fa18ddda7ce8ac101df76fe06c7e72b075a8803. This is new source,
not transferred runtime evidence from that characterization or the codec tests.
An isolated dependency bootstrap generates and retains the lockfile before the
locked offline checks. The returned lockfile must be reviewed and committed;
real PostgreSQL pool/race evidence is required before extending this foundation
into admission, Start, cancellation and Result/Receipt transactions.

Observation takes the exact shared workspace fence, typed attempt, NOWAIT owner,
execution, deployment and Solution, then session. Every identity is bound as a
query parameter. Wrong login, stale claim/incarnation, missing association and
secondary contention reject; transaction drop rolls back. A failed commit
observation is explicitly uncertain and cannot trigger automatic authority retry.
No synthetic store or Go-specific public lifecycle state is introduced.

The lockfile is now committed from hosted run38073041614; its sole formatting
failure is retained. Corrected source d3fcb82a4 passed run38073223134 with Rustfmt,
locked offline Clippy and two identity-encoding tests. No database transaction
was executed by that run. Further builds fail if the lockfile is missing and
compare it and source/declarations after the build.

The private `session_observation` example receives only synthetic fixture identity
on bounded stdin and a dedicated fixture DSN in its sanitized environment. It
connects with one pool connection, disabled prepared statement cache and finite
limits, then emits a small credential-free outcome. It does not receive or issue
SDK credentials. Its integration candidate checks each identity mismatch, actual
backend login, retained closure, source fence contention and an execution-first
incumbent versus attempt-first Rust/NOWAIT-root interlock. Test output cannot be
used as a launch permit. The guard fixture grants UPDATE(id) only for PostgreSQL
source-row lock authorization; a custodian-owned trigger rejects every coordinator
source UPDATE while preserving incumbent source writes. These new paths require
fresh hosted database evidence and are not covered by the earlier build proof.


`request_running_cancel` completes the common lock tail after the session and
requires retained Start plus current Running state. It preserves a prior terminal
outcome, rejects inconsistent/winning receipts and reports commit ambiguity without
automatic authority retry. No process termination or source drain is implied by
its return. Its caller is the trusted coordinator; this is no public/SDK endpoint
or substitute for admission authorization and actual channel/process custody.
The private probe classifies mutating-operation timeout as uncertain commit.

The observed connection failures at830682629 were diagnosed with checksum-verified
SQLx source. The correction omits SQLx's default extra_float_digits Startup parameter and leaves the pool's
admitted configuration intact. Run38076305806 at164e6b64d passed the negative default-option rejection control
and the corrected connection path with the unchanged pool policy. The cancellation
fixture uses the existing synthetic storage grant definition without token signing
or source eligibility; this cannot qualify credentials or a runtime writer.

Run38076305806 passed13,722 backend unit,109 baseline and88 guard/Rust cases
with no failures/errors/skips, API quality and empty owned cleanup inventories.
It proves actual Rust observation and provisional cancellation transactions;
synthetic Start/grant facts do not qualify admission, signed credentials or live
process custody. Exact evidence is retained in owner-foundation-source-evidence.json.

The next candidate adds read-only `observe_cancel_decision` under the full common
lock graph. It distinguishes unchanged Running/open from coherently committed
Cancelling/closed/revoked state and rejects partial projections without repair.
An isolated-only probe fault exits73 immediately after actual commit and before
a reply; a fresh Rust process must reconcile the same retained identities without
repeating the write. This is a post-commit/pre-reply crash window, not proof of
uncertain database commit, physical spawn recovery or terminal result settlement.
These new paths need fresh source-bound CI evidence.

`accept_result` imports the independently tested shared codec as a private owner
dependency. No authored Go source or public SDK imports Rust. Its new lock was
resolved in isolated run38078308074, retaining all other registry package records.
The caller still owes authenticated channel/frontier legality, accepted artifact
registration and schema/source admission. Test Start/grant/provision/release facts
are synthetic immutable storage metadata, not physical launch or SDK authority.
A new Result requires retained release, open Running state, exact identity/Start
and current finite grant/deadline under the common locks. It atomically writes
existing execution/attempt outcome, closes/revokes, and retains exact raw Result
bytes with its decision. Duplicate identical bytes return the original receipt
only after coherent retained projection is verified; conflicts fail. A committed
cancellation remains Cancelling with a retained cancel-winner receipt until actual
process/source settlement, without premature Cancelled projection. No send/publication
occurs inside the database transaction. Fresh source-bound CI is required.

Recovery source53a9080bd passed run38078012760 with13,722 backend unit,109
baseline and103 guard/Rust cases, all zero failures/errors/skips, API quality and
empty cleanup inventories. This proves the post-commit/pre-reply cancellation
process-loss window and read-only reconciliation; it does not prove uncertain
database commit, spawn recovery or terminal cancellation settlement.

Success Result values are checked against the retained immutable deployment
output_schema. The separate private validator supports a bounded JSON Schema
2020-12 subset: object/array/string/boolean/null/integer/number, type unions,
properties, required, Boolean additionalProperties and items, with depth64.
It rejects unknown keywords, malformed/unsupported types and unsupported dialects
even inside optional absent properties; it is not a general schema implementation.
Nullable arrays emitted by the static Go extractor are checked as unions, rather
than passed through the wire codec's fixed-document interpreter. The published
wire schema/codec source remains unchanged. Wider schema support must be qualified
before admitting artifacts requiring it. Fresh CI is required for these changes.

`record_start_candidate` is the next isolated common transaction. It validates
actual Prepare/Prepared raw payloads, matching artifact/session/parent evidence,
immutable retained Prepare identity/hash, deployment/Solution/artifact/attempt,
original/effective organization caller snapshot, context and stored execution
input/schema. It requires Pending/claimed/open and an empty Start/admission/grant/
receipt tail under the same ordered locks. A fresh finite database clock gates
Start insertion; existing execution/attempt Running transitions use that exact
clock in the same transaction. The returned common Start body follows observed
commit only. Unknown commit cannot be retried or treated as permission to launch.

This candidate has no production caller or process operation. Live channel,
accepted staged bytes/build/source closure, native registration and admission
remain external prerequisites for the trusted coordinator. Compiler/unit proof
is insufficient: actual SQL, rejection/rollback/race and read-only uncertain-Start
recovery evidence are still required before this path can support runtime
acceptance. Its input/context digests are internal owner bookkeeping, not wire
receipt preimages; exact raw Prepare and Result hashes remain separately retained.
