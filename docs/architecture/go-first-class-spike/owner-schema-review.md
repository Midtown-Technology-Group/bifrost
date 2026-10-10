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
