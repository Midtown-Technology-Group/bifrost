# Isolated owner storage review

Reviewed candidate: `e02337989c931143b848f143f7801af947691388`.
Platform main: `b8e3e5a4fac0a0a7c70cd6b2a3b9394e0cbbac01`.
Interface release: Thomas's approval of `b8b44cbc01b1fb6be86f4fba050e93eb0da55924`.
SDK: `0.0.0-spike.2`; MTG package: `2026-10-09.1`.

This is an accountable source review of the concrete storage graph and its
remaining authority gates, not acceptance of a lifecycle writer. Hosted
run38020522305 passed13,657 backend unit tests and110 scoped tests, all with zero
skips/errors/failures, API quality and independent empty task-resource inventories.
[Storage evidence and diagnosed predecessor failures](owner-session-schema-proof.json)
remain tied to their exact sources. No production routing or role grant is added.

| Record | Mechanical identity and retention | Remaining owner responsibility |
| --- | --- | --- |
| Artifact association | Actual workflow/Solution deployment composite identity; descriptor/artifact/build digests; immutable UPDATE/DELETE guard | Verify the accepted descriptor, build evidence, source closure and actual staged bytes; synthetic descriptors are not acceptance |
| Owner | Actual execution workflow/deployment and accepted artifact; one immutable owner incarnation and caller snapshot | Admission authorization, literal original/effective caller facts and source consumers in the same transaction |
| Session | Actual owner and typed attempt/claim/worker; one session per attempt; immutable binding/Prepare/custody digests; permanent close tombstone | Actual live process/channel registry; hashes or UUIDs alone cannot establish custody |
| Start | One per session, exact attempt/owner/session and message; immutable input/context hashes and clock | Fresh eligibility and atomic existing execution/attempt Running projection before any outbound Start/material |
| Grant and operations | Actual session, claim digest, worker/supervisor, Start/message/clock, attempt ordinal, owner caller digest, workflow/deployment/Solution; closed operation rows; immutable facts and one-way revocation | Trusted issuer, real manifest/resolution/dependency verification, exact operation selection/digest, finite access-only signing, fresh open-session ingress checks; no renewal |
| Provision/release admission | One per session/purpose; exact grant/Start/operations/expiry; release references the preceding provision and exact delivery; immutable frontier/evidence | Recompute eligibility after the common locks; authenticated private material, observed Start, observed release commit and live guardian serialization before spawn |
| Result receipt | Exact session/Start/message; SHA256 of retained raw payload, immutable disposition/winner/decision | Validate the common frame/binding; serialize Result/Cancel and commit exactly one existing domain projection with its receipt before ACK |

The generated session claim digest uses private CRED-P1's exact netstring
preimage. Its tombstone guard now runs AFTER generation and retains the whole-row
comparison, including the derived digest. Raising from the guard rolls back the
statement; no mutable field exemption was added. The regression checks a legal
close, repeated identical close, retained digest and the original forbidden
identity/close mutations. PostgreSQL16's generated-column rules explain why the
original BEFORE guard failed; the failed run is retained, not waived.

## Common transaction order and unresolved writer gate

The source-admission fence must use the existing exact expression:
`pg_advisory_xact_lock_shared(hashtext('bifrost:workspace-release'))`.
Do not substitute a different hash function or lock namespace. Follow with the
actual workflow attempt lock, then owner/execution/deployment/Solution secondary
NOWAIT refresh, session, Start/admission, grant and receipt. Secondary contention
aborts the whole transaction. The session is the common serialization point for
close versus an absent grant; every issuer, ingress, close and recovery operation
must follow this graph. Constraint/FK locks and incumbent poison's execution-first
order must be included in real races, not merely documented as compatible.

Current storage does not mechanically exclude incumbent lifecycle writers.
PUBLIC revocation and row immutability do not protect execution result/status,
logs, usage, source accounting or external effects from the existing privileged
application identity. The configured test pool forces `DB_USER=bifrost` and its
bootstrap login owns the schema. Separate frontend names or SET ROLE cannot prove
separate authenticated backend authority.

Before accepting any writer, install and test a separately reviewed isolated
principal/pool/guard candidate. Prove distinct `session_user` identities,
non-owner/non-superuser/non-BYPASSRLS authority and no membership, schema,
function, trigger, TRUNCATE or configuration escape. Cover actual execution,
typed-attempt and dependent OLD/NEW parents, conflict updates, cascades/SET NULL,
retention and source locks; unchanged Python-owned positives and actual consumer
negatives remain required. Add no blanket maintenance or summarizer exemption.
RESTRICT retention edges must be checked against legitimate deletion/drain paths.

Admission metadata does not imply an atomic database/OS launch. Only the trusted
guardian's observed matching release commit may permit a physical launch. An
uncertain commit, spawn or ACK keeps possible-effect evidence, closes/revokes the
same session and requires real process/descendant/source settlement; it never
replays the workflow, reopens the tombstone or reassigns its owner. Stored Result
or Stopped alone does not prove that settlement.

No Rust owner transaction, restricted token or runtime endpoint is enabled by
this candidate. The real unchanged-artifact SDK/cancel/Result/Receipt/API/recovery
slice remains the next required outcome, not a claim inferred from this review.

## Authenticated pool prerequisite readback (2026-10-10)

[Source-bound hosted proof](owner-pool-primer-proof.json) at `a92d07d2e` now proves
separate backend `session_user` identities through the dedicated transaction pool,
SELECT-only non-owner/non-superuser/non-BYPASSRLS principals with no membership,
12 privilege escape denials, six connection denials and actual task cleanup.
All133 public relations, including the eight runtime relations, remain owned by
the unchanged bootstrap owner. The API image is identical before and after.
The application's existing shared/privileged login is not replaced or qualified
by this experiment. No lifecycle DML or new writer privilege was granted.

The concrete guard audit must explicitly include non-FK associations:
`api/src/models/orm/events.py` declares `event_deliveries.execution_id` without a
foreign key, and generic attempts/lifecycle events also retain logical job links.
Guarding only typed attempts and declared FK children would leave those paths
uncovered. OLD and NEW references and ancestor effects remain required. Existing
poison uses an execution-first lock and commits before Redis cleanup; existing
workflow-consumer cancellation/failure paths publish updates and mutate Redis.
Their actual entry/commit/effect order must be exercised alongside SQL guards;
a rejected database mutation alone is not evidence that external effects were
excluded. This review does not accept a guard or change Python behavior.

## Restricted integration admission candidate

The isolated Rust `authorize_integration_get_candidate` now uses the same shared
workspace-release → attempt → NOWAIT owner/root/deployment/Solution → session →
Start → ordered admissions → ordered grants prefix. After confirming the request
references this session's single grant, it locks that grant's immutable operations
in ordinal order, then the session's ordered receipts. An issuer/ingress using
operation rows must keep this order; it must not lock an unrelated request-supplied
grant or reverse the receipt/operation tail. No existing writer is changed.

This candidate requires Running/open state, matching Start, retained provision
and release, the exact grant digest and one organization-scoped integration-get
for the requested name and installation. It checks the grant and non-null Start
deadlines using the actual clock after the locks and denies uncertain read commit.
It performs no lifecycle writes, signing, renewal or integration fetch. Source/
caller entitlement, the full CRED-P1 signature/claim/digest verification, trusted
ingress custody and real restricted SDK HTTP behavior remain mandatory gates.
Its earlier tests used real Rust Start/release with an explicitly unsigned
synthetic grant. The current candidate replaces SDK fixture insertion with real
Rust owner birth, Start, canonical finite grant/provision and release transactions.
Artifact/source and channel custody remain synthetic in these database tests;
passing them cannot qualify ingress or the whole runtime.

SDK admission now reuses owner birth's current user/organization/role/workflow
eligibility predicate, binds caller identity only from the locked grant and
requires the current complete caller snapshot to equal the immutable owner
snapshot. It rechecks active deployment/Solution and runtime mode, exact current
source manifest/resolution, same-org scope and coherent grant caller literals
before its final actual-clock read and commit. Current checks add no row locks,
new writer, operation or credential and preserve the common lock order. Dedicated
tests change caller activity/email/name/admin state, roles, workflow activity and
Solution runtime after grant birth, require read-only denial and restore shared
caller fixtures. Hosted results for this new candidate remain required. Full
accepted source/dependency closure, signature/claim/preimage validation, fresh
live guardian custody and the real restricted HTTP gateway remain separate gates.

## Finite grant/provision writer candidate

`record_provision_candidate` follows the same shared source fence, attempt and
NOWAIT owner/root/deployment/Solution locks, session, Start, ordered admissions,
ordered grants and ordered receipts. An absent grant is serialized by that same
session row: cancellation closes it before any later birth can be accepted.
The existing composite keys, one grant per attempt/session, immutable operation
rows, one provision per session/purpose and permanent tombstone remain intact.
No new schema, role, Python-owned writer or production dispatcher is introduced.

The closed private snapshot has the unchanged 32 CRED-P1 fields; owner and Start
identity are derived from locked parents. The candidate checks actual parent
identity, caller literals, deployment manifest/resolution, finite Start deadline,
claim/attempt clock and freshly usable expiry. It recomputes the ordered grant,
source and single integration-get operation preimages independently in SQL.
Grant, ordinal-zero operation and provision are one transaction. A late admission
failure rolls back all three; an uncertain commit is never retried as a birth.
There is no conflict update, renewal or delivery/spawn permission in its return.

The trusted issuer still must establish current authorization, complete accepted
source/dependency/operation selection and live custody before entering this
private transaction. Retained owner/caller digests do not establish those facts.
The tests retain synthetic source and channel prerequisites while using real Rust
Start and grant/provision/release transactions, finite signing, application-token
rejection and Rust SDK admission. Full hosted checks and the actual unchanged Go
artifact/HTTP/guardian/recovery/cleanup connection remain acceptance requirements.

## Existing-domain admission birth candidate

The private `record_admit_candidate` creates an existing execution root, common
owner, first typed workflow attempt and session in one transaction. It allocates
no alternate logical job or Go lifecycle state. Existing rows are rejected; no
clone, takeover, conflict update or uncertain-commit retry is provided. The
isolated owner-class/deferred owner FK and parent composite identities apply.

Birth takes the exact shared workspace-release fence, checks absent attempt and
owner/root identities, then locks deployment and Solution NOWAIT. Its source
selection requires the currently active org-scoped deployment, active Solution
and workflow, and the immutable native artifact association. It reads actual
active user, organization and roles, derives the external claim with existing
admin/provider semantics, and enforces workflow access-level/role entitlement.
This isolated slice accepts an authenticated caller in the same organization as
its effective scope; cross-org/provider expansion remains outside approval.
It reads authorization again at root insertion and requires the identical caller
snapshot. The unchanged CRED-P1 caller digest uses byte-length netstrings and
ordered role names. Input must match the retained schema, the common Prepare
binding/context must match, and its finite deadline must remain usable at birth.

A new identity has no attempt row to lock. Competing births cannot both win:
source NOWAIT locking and root/attempt/session uniqueness retain the original
identity, with whole-transaction rollback on a conflicting insertion. The trusted
caller must still prove accepted build/artifact/source closure and actual channel
custody before invoking the primitive; strings and active fixture rows are not
that proof. Source retention uses existing deployment FK/immutable association;
physical descendant settlement and source drain remain unresolved acceptance
requirements. No production route, dispatcher, credential or role grant changes.

## Common resource/source settlement candidate (2026-10-11)

This isolated implementation extends the released Stop/drain interface. It does
not accept the new writer or runtime. The unchanged Go workload and common wire
profile receive no new lifecycle API. Production dispatch remains disabled.

`20261011_runtime_resources` follows the existing Alembic head; ORM metadata
registers the same `runtime_resource_settlements` table. Its primary key permits
one fact per session. A RESTRICT composite FK binds session, execution, immutable
owner incarnation and typed workflow attempt. The bounded 1–65536-byte raw
observation is retained with a database-checked exact SHA256 and database clock.
UPDATE/DELETE/conflict-update mutation is rejected by the existing immutable
report-evidence trigger. Insertion requires the exact parent to be closed; the
closed-session tombstone receives no mutable-field exception. Downgrade refuses
retained facts. PUBLIC receives no privileges or callable guard function.

The existing isolated fixture extends its same `wex_core` metadata guard to this
relation. Incumbent/ordinary SDK writers remain excluded; no new login, token
purpose, SDK operation, source mutation, production role grant or dispatch is
introduced. UPDATE privilege is used only for the common row-lock path; the
immutable trigger rejects changed observations. Storage tests explicitly label
synthetic observations as schema-only, never process or finalization acceptance.

The Rust `settle_retired_session` entrypoint consumes an opaque `RetiredSession`.
The verified original guardian's `drain_session` and `retire_resources` path
constructs it: actual kernel/descendant/container/channel drain, original material FIFO and
SDK gate retirement, no-follow/UID/mode/single-link reads of all six accepted
source files and the pinned binding index, exact source digests and removal of
those complete directories. A public JSON report, Stopped, bare recovered stop-only
object or copied fence cannot construct this value. The proposed closed-Result
recovery constructor below requires separate SQL and original-custody proof. Any incomplete cleanup
produces no settlement token. The source archive digest is rechecked against the
locked immutable owner association before the SQL write.

Transaction order is the unchanged shared workspace-release fence → exact typed
attempt → NOWAIT owner/execution/deployment/Solution → exact session → Start →
ordered admissions → ordered grants → ordered receipts → settlement row. Session
serialization fences an absent settlement. FK/closed-parent trigger locks revisit
that already-held parent, never acquire a new unrelated session. Source locks
carry the original read/lock-only guard. Competing Result/Cancel/issuer/ingress
paths share the same prefix and cannot reopen the closed session or revoke the
winner. No operations rows are mutated or independently locked by settlement.

The writer requires a closed session, committed Start, provision/release, one
revoked same-session grant and consistent existing domain/receipt state. Normal
Success/Failed and their accepted Result receipt are preserved. A Cancelling
root with its uncompleted running attempt and retained cancellation tombstone
can become the existing Cancelled/terminal-cancelled projection only in the same
transaction as the actual resource/source fact. Existing accepted Result receipts
cannot be overwritten. The new path is INSERT-only: no retry, conflict update,
owner reassignment, grant renewal, channel reconstruction or spawn follows an
uncertain commit. The consumed physical proof cannot be reclaimed for replay.

Required evidence is fresh exact-source native/owner/guard/storage validation,
actual three-execution unchanged-binary API/receipt/settlement readback, identity
and byte-drift/open-parent/immutability/rollback/role negatives, and further actual
race/crash/uncertain-commit/lost-ACK evidence. Source review and green conformance
alone cannot accept this writer, the runtime, or completion of the goal.

Source accounting now includes a common session whose immutable settlement is
absent, even when Result already made its existing root and attempt terminal.
Admission's atomic retained session therefore creates a durable source-consumer
obligation. `solution_source_accountability` keeps that deployment/dependency pin
until settlement; the predicate does not depend on runtime language. Roots with
no common session retain their existing Python behavior. The live lane pauses
after original physical/source retirement and checks the actual common source
predicate and absent settlement before releasing the fixed writer; it then
checks the pin is discharged only after committed settlement. This private
first-party observation seam is not a workload message, admission, replay or
new control-plane authorization.

Refreshed platform main remains `08255ad355a3ab63f0c04a214c3c230432552dfe`.
Workspace main is `3e8de3d1b19501fc2be07c5591d3c47482488a40`; no workspace
content is changed. Relevant #1011, #1024 and #1132 heads remain respectively
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e`,
`6419069da195b053c885ab349f431ff4fae62098` and
`35daf020d5e1286f82bdfed6bb3fc537bf3d78f6`.

## Actual lost Result COMMIT acknowledgement candidate

A fourth unchanged-binary execution uses a dedicated first-party byte-forwarding
fixture in front of the existing synthetic `wex_core` pool. Its only fault is to
consume one server `CommandComplete(COMMIT)` after the trusted SDK response-hold
seam arms it, then close that connection before forwarding the acknowledgement.
The runtime/SDK fixture never receives the core credential. The proxy receives
no credential environment, records no authentication/SQL/payload bytes, binds
only host loopback and forwards only to the already selected private pool. This
fault case explicitly uses that isolated pool's existing plain TCP transport;
production TLS, roles, SDK operations and dispatch are unchanged. Packet bounds,
wrong-arm identity and exact one-shot/no-repeat behavior have hosted unit gates.

The original Rust actor must receive `ObserveError::UncertainCommit` from the
actual Result commit. Its sole report attempt stays consumed. It sends no runtime
ResultReceipt, repeats neither Result acceptance nor SDK work, and drains the
same original guardian/source. Readback then requires the exact retained closed
session/owner/attempt/Start, matching terminal root/attempt outcome and the sole
accepted immutable receipt with the original decision ID. That read-only
reconciliation does not construct/send a protocol receipt or restore the runtime
channel. The existing resource-settlement writer repeats its complete locks and
preserves that already committed durable result. Crossed Success/failed or
Failed/succeeded projections are rejected.

The live case must retain the server-observed/client-unforwarded commit marker,
Rust's uncertain observation, explicitly unsent runtime receipt, one actual SDK
HTTP200, one immutable Result receipt, existing authenticated API result, source
pin before/after settlement and complete original-process/source/proxy/fixture
cleanup. The public receipt row is coordinator evidence; the terminated runtime
never learned its receipt. This candidate proves no runtime replay on lost SQL
ACK only after actual execution. It does not prove owner-crash recovery, uncertain
spawn/release, or runtime acceptance; those remain separate required gates.

## Original custody retention prerequisite

Before SQL admission, the original guardian now exclusively writes and fsyncs
`original-custody.json` and its private journal directory. The bounded0600 file
contains the original observation and its exact digest, session/nonce, and the
original owner PID/start ticks. It contains no credential or claim token and is
not uploaded. No new runtime wire field or SQL lifecycle writer is introduced.
Readers reject symlinks, multiple hard links, wrong owner and permissions.
Duplicate creation is rejected; uncertain persistence cannot trigger another
observation or admission.

The existing physical crash probe kills and waits for the original guardian,
then checks that this retained observation matches the original independently
observed custody and process identity. Recovered guardians remain stop-only:
no attached channel, Start, material delivery or SDK authority is recreated.
This is a prerequisite for future closed-session recovery, not proof of durable
crash finalization. That next writer must match the retained SQL fence, prove
original owner/CLI/kernel and ingress/source retirement, use the common lock
order, and reconcile uncertain commits without replay or ownership reassignment.
The new prerequisite still requires fresh hosted tests and live proof.

## Closed-Result owner-crash recovery candidate

The fifth unchanged-binary case extends isolated implementation only. The same
one-shot proxy must cause the original Rust actor to observe actual lost Result
COMMIT acknowledgement. Before any physical/source cleanup, that actor persists
its uncertainty observation, emits a public coordination event and accepts no
more commands. The launcher first tries read-only recovery while it is alive;
that must fail before a cleanup intent or OS effect. It then SIGKILLs and waits
for the original actor. The authenticated fixture proves an accepted immutable
Result receipt and Success already exist, source is still held, and no resource
settlement exists. Only then may a new first-party Rust process close resources.

No logical owner, attempt, worker, session, deployment or claim is reassigned.
`ClosedResultRecovery` is an opaque readback value: the existing common lock
prefix must observe the exact retained closed session, matching terminal root
and attempt, original accepted receipt decision and immutable accepted artifact.
Its commit is read-only; any uncertainty denies physical recovery. This value
grants no channel, Start, SDK, credential provision, release or renewal permit.

Original owner PID/start ticks and the exact retirement-journal digest now form
part of the original observation whose digest SQL admission retains. This binds
bundle, material directory/FIFO and gate directory/socket device/inode/type/mode
identities to the retained session; editing a private cleanup journal alone cannot
qualify a different owner or substituted resource. All journal reads are bounded,
no-follow, single-link, UID/mode checked. The source index binds the entire neutral
binding and artifact; every accepted source file is hashed before and after drain.

The new process remains a stop-only guardian. It requires the old owner
incarnation gone and a new exclusive, fsynced closing intent before physical
action. It drains the retained original container/kernel, separately observes
the original Docker CLI gone, and removes only matching original resources. It
correctly reports `attached_cli_waited:false`: it has no original child handle.
The separate `original_cli_reaped_observed:true` must come from kernel
incarnation checks, never from copying a report. Complete source retirement can
then construct the opaque proof consumed by the existing settlement writer.

That writer repeats the full source→attempt→NOWAIT secondary→session→Start→
ordered admission/grant/receipt→settlement order and preserves the already
committed Result outcome. There is no Result acceptance replay or runtime
ResultReceipt resend. The fixture must prove one SDK HTTP200, exactly the original
admissions, unchanged owner identity, durable fact hash/identities, source pin
before/after, authenticated API readback and physical/fixture cleanup. Normal and
Cancelled paths retain their own original-handle semantics and required checks.

This candidate changes no SQL schema, role grants, protocol messages, SDK
operations, production dispatcher or Python-owned execution. It remains subject
to human review of these concrete constraints and common lock order before
writer/runtime acceptance. It does not prove pre-Result owner recovery, uncertain
spawn/release, partial cleanup recovery, or ambiguous settlement commit. Failure
after exclusive closing intent remains inspect-only; no retry or new owner is
authorized. All fresh hosted security/race/source and live gates remain required.

## Uncertain release abort candidate

The sixth same-artifact case arms the existing one-shot response-loss proxy
only after the original issuer returned actual finite material and immediately
before the release transaction. The server must observe COMMIT while Rust
receives UncertainCommit. No Provision or FIFO write can follow that error.
The common read-only release reconciliation must find the exact retained row;
AlreadyRetained remains observation, never a new delivery permit.

The authenticated fixture observes that release fact, zero SDK requests and
zero Result receipts, then requests abort of this same original execution.
`LiveStart::abandon_failed_release` consumes further report service, requires
original live custody and an attempted but unobserved release, and uses the
existing common running→Cancelling/session-close/grant-revoke transaction. It
sends no forged Cancel/Stopped/ResultReceipt frame. Original actual physical
and source retirement then qualifies the existing resource-settlement writer
to project the common Cancelled outcome; a stopped process alone cannot do so.

There is no second release, issuance, material delivery, SDK call, report, owner
assignment or credential renewal. A second ambiguity fails closed and requires
inspection rather than retry. The live fixture must prove exact release identity,
revoked finite grant, no Result, source pin before/after resource commit, durable
Cancelled root/attempt and existing authenticated API readback with cleanup.
This adds no SQL schema, role privileges or Go-specific state. Fresh hosted
evidence is required; uncertain spawn and other crash phases remain separate.

## Closed cancellation before durable resource settlement

The next isolated candidate shares an opaque `ClosedSessionRecovery` between
closed Result and closed cancellation recovery. This is an implementation-private
readback value, not wire data, owner reassignment or a Start/SDK permit. The
cancellation observer uses the same source/attempt/NOWAIT owner/root/deployment/
Solution/session prefix, requires `cancel_requested`, Root Cancelling and the
unfinished Running typed attempt, exactly one Start, no Result receipts and the
single session grant already revoked for `session_closed`. Its transaction is
read-only; uncertain read commit denies recovery. Immutable Start/receipt and
closed-session facts are read under the session serialization point. The later
resource writer repeats the complete common lock tail before any final projection.

The seventh live case holds an actual admitted SDK response, lets the original
Rust owner commit cancellation and validate common Stopped, then parks that actor
before physical/source retirement. A recovery attempt while it is alive must
deny before an exclusive cleanup intent or OS effect. The parent SIGKILLs and
waits for that exact original actor, proves no Result receipt/resource fact exists
and the existing authenticated API still reads Cancelling, then permits a fresh
stop-only closing actor. It must match the unchanged owner/attempt/session and
original SQL-bound custody/resource/source journals, prove the old incarnation
gone, drain the original namespace/CLI, retire the original paths and use the
existing resource-fact writer to finalize Cancelled. No workload, SDK, Result,
Cancel or Stopped is replayed. Stored Stopped/counts are observational evidence,
not cleanup authority. No new schema, principal, lifecycle transition or wire
message is added; the coordination events belong only to the trusted fixture.

Fresh hosted evidence is required. This does not cover owner death while the
session remains open, interrupted cleanup or ambiguous resource-fact commits.
Those paths retain obligations and fail closed; they do not authorize replay.
