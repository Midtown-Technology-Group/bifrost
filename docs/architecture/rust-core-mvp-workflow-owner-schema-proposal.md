# Workflow owner schema review packet — structural proposal, HOLD apply

Canonical next-work entrypoint: [common runtime reconciliation](rust-core-mvp-common-runtime-reconciliation.md). This draft is a design input, not authority for parallel schema or lock implementation.

2026-10-16. Source boundary main e42999395231ebc536711e5015422edfb75613a7; inspected through clean AUTH worktree /home/thomas/src/bifrost-agent-caller-authority at 39245315e193e1d04886044677bf7a75bb8c6f1f. CRED #1037 source 3918d608a2d497d1ca8424fd4229d191323e2d7a remains separate/unmerged. Engineering-flow 2026-10-06.1. Read corrected docs/architecture/rust-core-mvp-boundary-contract-proposal.md in verification-disposition worktree and /tmp/bifrost-boundary-contract-independent-review-20261009.md. One bounded source packet only: no product imports, tests, runtime, Docker, CI, GitHub changes, delegation or repository edits. SQL is not migration history and was not executed.

Artifact: [structural SQL](rust-core-mvp-workflow-owner-schema-proposal.sql). It supplies concrete native workflow FKs and nullable-safe receipt keys, append-only evidence, one-session-per-attempt and one Prepared/Start/provision/close/stop receipt per session. Global non-null admission request identity is on owner; committed_start_id is globally unique/non-null exactly for start receipts. No generic polymorphic job model or fake agent attempt is used. It intentionally contains ROLLBACK and does not invent a role grant/guard catalog or CRED FK. Hashes are prefixed SHA256 of validated exact bytes; SQL shape checks do not verify digest/bytes correspondence. Approved trusted admission code must do that before insertion. Profile document bounds/canonical serialization remain a separate decision; no unapproved public result bound is imposed here.

Native sources: api/src/models/orm/executions.py:177-280 has UUID typed workflow attempt -> execution FK with CASCADE, unique execution/attempt_number, claim-token uniqueness and partial active attempt index. New composite unique(id,execution_id) permits session -> exact native pair FK; owner -> executions.id and session -> owner/execution pair prevent mixed-parent UUID substitution. Existing execution_logs at :282-303 has native execution FK but no unique receipt/publication identity; this packet does not claim report deduplication alone prevents duplicate native projection.

## Exact admission and state interface still to implement after approval

One fresh transaction: acquire shared source-accounting fence before native domain/advisory/row locks, authenticate/refresh caller/source and stable request identity, insert native execution+owner+typed attempt+source obligation+admission receipt together. Rust admission request is not caller-selected ownership. On request collision return exact original committed receipt only after matching bytes/digest/binding; conflict denies. Missing owner implies incumbent only on approved legacy/native admission paths, never a new-row race window. The global owner admission_request_id is mandatory even though session-less receipt uniqueness also exists.

All three proposed tables are append-only; immutable evidence avoids clearing close tombstones or retargeting attempt/session identities. Prepared/Start/close are receipt inserts, not mutable nullable state columns. Under the same native attempt FOR UPDATE fence, typed operation checks exact owner, native attempt number/claim/worker, same session/prepare identity, expected frontier and absence of close. Prepared before Start; unique Start records native running/started_at atomically; provision requires committed Start; close is permanent even with absent grant; stop records trusted supervisor reap, not child Stopped. Report/provision metadata references and digest, session/owner/type must be validated by the constrained operation; storing UUIDs in exact_document is NOT a relational association proof. A future exact typed FK for prepared/start receipt references can be added once those wire/profile documents are ratified; no untyped pointer columns are supplied here.

This first-package proposal chooses one session for the entire native attempt, stronger than merely unique open session. It deliberately disallows closed-session replacement even before Start; approve that conservative recovery limit explicitly or replace it with a reviewed native recovery eligibility operation. No automatic replay after possible/uncertain Start. Stale caller/source/cancel/claim or DB uncertainty denies effects. Lost commit ACK queries authoritative receipt via fresh transaction; no blind resend. First report receipt and native result/log/usage/event projection commit together. Exact event/usage publication keys cannot be selected until writer/storage catalog is complete; no full projection implementation is claimed.

## Deletion decision blocks apply

RESTRICT FKs in this structural preview retain evidence and prevent native deletion from cascading it away. This is a proposed safe structural stop, NOT a claim that blocking public deletion preserves parity. The complete native deletion/retention/ancestor SET NULL/CASCADE contracts, remaining active source consumers and required minimal retained nonsecret tombstone fields/retention duration are not established here. Before apply choose separately approved public-data deletion with durable minimal tombstone identity, or approved retention restriction; then adapt native FK detach/tombstone protocol atomically. Do not apply RESTRICT blindly or silently substitute CASCADE. Attempt CASCADE in current ORM alone does not answer this contract.

## Actors, guards and approval boundary

Runtime child: no SQL credentials/role and no lifecycle helpers. Trusted Python/Rust coordinators: distinct authenticated non-owner/non-superuser/non-BYPASSRLS backend identities; immutable assignment and OLD/NEW parent DML guards over the complete approved writer/dependent/ancestor catalog. Native public app read authorization is separate from lifecycle owner. Credential custodian: only reviewed grant operations and exact locked native reads, not blanket lifecycle writes. Migration/guard owner separately managed; no SET ROLE membership, writable GUC authority, search_path/function/table-owner bypass.

Ordinary triggers do not guard SELECT or LOCK TABLE. Recommended minimal decision retains reviewed coordinator public reads and native locking, but cross-owner read and ANY-table-lock concession including ACCESS EXCLUSIVE remains UNAPPROVED. UPDATE/DELETE table grants permit all modes; guards prevent wrong-owner committed DML, not availability interference. Require fail-closed delays/outages and complete lock graph. A no-direct-DML stored-operation rewrite is an alternative only if that concession is rejected; this packet neither mandates nor implements it. Exact roles/PgBouncer generated mapping and installed ACL/catalog are unknown; no new grant statements are supplied. Full trigger installation on guessed tables would be unsafe, so this SQL is deliberately not an apply-ready migration.

## Source lock graph and unknown edges

- main services/workspace_release_projection.py:114-121 shared source advisory fence; execution/async_executor.py:222-227 acquires it before native admission pinning. async_executor.py:236 has event-delivery advisory acquisition; :387-419 locks execution then event delivery. Preserve actual event/delivery interleaving.
- main execution/poison.py:60-94: workflow-execution advisory -> Execution FOR UPDATE -> require_delivery_ownership -> typed WorkflowExecutionAttempt FOR UPDATE. The module imports WorkflowExecutionAttempt as ExecutionAttempt; this local alias must not be mistaken for the generic attempt table.
- CRED3918 services/runtime_sdk_grants.py:119-156: typed WorkflowExecutionAttempt FOR UPDATE -> Execution SHARE NOWAIT -> SolutionDeployment SHARE NOWAIT -> Solution SHARE NOWAIT, populate_existing. Independent committed precheck and locked refresh at :243-280. CRED grant/session integration must preserve that private fence; source advisory if needed must be taken before attempt, never blocking after it.
- Proposed session/receipt inserts follow native attempt fence; append-only tables do not eliminate FK key-share/index conflict locks. Close/grant/renew/SDK operation admission/report all need common fresh gate. Never lock session first then wait on native attempt. No DB locks across vendor effects/signing.
- Full incumbent consumer/cleanup/poison/cancel/event/retention/ancestor FK and installed trigger/advisory catalog is incomplete. NOWAIT on CRED secondary rows does not prove table/advisory/FK waits cannot cycle. Missing edges block rollout and any claim that a global order has been proved.

Current main api/bifrost/_sync.py:30-46 and :78-95 explicitly acknowledges Redis pending changes after the owning database commit. The earlier Redis-before-commit finding is stale for this source. Verify callers obey the current split API; do not cite obsolete behavior as a reason for unrelated repair.

## Smallest later acceptance package

After exact SQL/roles/deletion/profile and catalog approval, supported isolated PostgreSQL/pool tests must prove: native composite binding negatives; null-session/global duplicate admissions and byte conflicts; concurrent one-Start; immutable/tombstoned rows and close-before-grant; close versus issue/renew/SDK/report; stale claim/worker/session; unknown ACK reconciliation; retained native deletion/ancestor positives and late-traffic denial; wrong-owner INSERT/UPDATE/DELETE with OLD/NEW rebinding/cascade and unchanged incumbent positives; role/GUC/SET ROLE/function escape negatives; explicit approved coordinator reads/all table-lock modes and fail-closed contention. Then one genuine workflow lifecycle with atomic result/log/usage/event projection and trusted stop/source drain, plus approved CRED two-operation/renewal regressions. Use exact candidate and current ./test.sh pre-pr/quality/affected CI selection; no old pass transfers. No agent-capability grants are added.

Decision requested for next review: accept or amend these native keys, append-only receipt representation, conservative one-session policy, deletion/tombstone disposition and bounded role/ANY-lock choice. The writer/deletion/installed-catalog gaps prevent apply-ready SQL, not preparation of this concrete structural artifact. Stop after this packet; root owns further approval and implementation.


Root disposition: retain as a **structural review artifact, not builder release**.
The one-session-per-attempt restriction and RESTRICT deletion behavior are
unapproved proposals; neither may silently replace native recovery/deletion
semantics. Receipt documents alone do not establish native projection or
permission checks. The next review must resolve those exact points and the
complete writer/lock catalog before any Alembic revision or role change.

SQL SHA-256: `2dbaffd23da015bdfbd6a301a933a0e8d6acc40508ed3ae2ac498e9ad623b32c`.


The [deletion/recovery follow-up](rust-core-mvp-workflow-deletion-recovery-contract.md)
supersedes the preview's blanket RESTRICT/one-session recommendations and
corrects its typed-attempt alias finding. The SQL remains a historical structural
preview, not an apply-ready or frozen migration.
