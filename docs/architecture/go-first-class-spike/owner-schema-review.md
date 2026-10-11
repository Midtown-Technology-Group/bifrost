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
