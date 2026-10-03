# Runtime authority sequence: architect decision candidate

Status: **root design candidate, independent review and schema/runtime proof
pending**. This advances the full workflow/agent MVP; it authorizes no C2/C3
implementation, public behavior change, merge or deployment. Source is platform
main `e58db4955ddd30177bd613f1d85b7e203ad7832a`; private credential comparison is
#1024 `6419069da195b053c885ab349f431ff4fae62098`. Guidance package is 2026-10-01.5.
Read with the [freeze audit](rust-core-mvp-runtime-freeze-audit.md) and
[amendment](rust-core-mvp-amendment.md). P0 framing is unchanged.

The decision under review is one ownership and custody sequence, shared by the
selected workflow and autonomous-agent profiles. It preserves distinct logical
jobs, typed attempts, delivery leases, runtime sessions and source obligations.
It is not a replacement job system or a generic delivery port.

## Durable facts and authority

| Fact | Authoritative owner and meaning |
| --- | --- |
| Logical row and typed attempt | Rust for an explicitly admitted Rust-owned item; existing `executions` / `workflow_execution_attempts`, or `agent_runs` / `execution_attempts` with logical type `agent_run`. Python remains authoritative for all other items. |
| Immutable owner assignment | Database-enforced admission decision, fixed for the logical item and its dependent projections. Request-routing configuration cannot change an in-flight owner. |
| Runtime session | One supervisor incarnation, one actual child/process incarnation, one typed attempt. A stored UUID is necessary but insufficient evidence that the session is live. |
| Accepted Start | A durable, unique authorization record for this session and exact Prepare identity, source pin, caller snapshot, selected capabilities, provision slot and predeclared deadlines. |
| Child-observed Start | Validated frame from the exact private process channel; evidence of receipt, never proof of a database commit supplied by the child. |
| Credential provision | Separate trusted custody record, bound to accepted Start/session/attempt. A slot reserved during Prepare contains no bearer material. |
| Report receipt | Durable acceptance of one typed observation and payload digest before its projection; transport sequence alone is not durable idempotency. |
| Runtime stop | Trusted supervisor wait/reap evidence for the actual child and its supervised descendants, independent of `Stopped`, logical terminal state and grant revocation. |
| Source drain | Full source-consumer inventory and trusted close/stop evidence. Logical completion does not settle source obligations. |

These are semantic interfaces, not approved new column/table names. Existing
schemas do not provide all these facts. The next schema package must choose the
smallest additive Alembic records and constraints that realize them, under the
existing migration history. It must not overload a delivery lease or invent an
agent lease token to avoid recording missing custody.

## Admission and Prepare

1. Select ownership from explicit supported-profile configuration **before**
   admission. Unsupported workloads use the unchanged Python path; an already
   accepted Rust item cannot fall through to Python. No dual admission or shadow
   execution. Historical missing original-org provenance remains UNKNOWN, not
   known GLOBAL/null; such queued items remain Python-owned.
2. Acquire the current source-accounting shared admission fence before
   install/execution locks, as `async_executor.py` requires. Resolve authorization,
   original/effective caller, immutable source/dependency closure and limits from
   current authoritative evidence. Capture literal role facts without UUID or
   privilege inference. Ownership registration and accepted source-consumer
   obligations commit with admission; a failed transaction publishes nothing.
3. Claim using the existing typed attempt and delivery contract. Workflow claim
   tokens remain parent-private. Autonomous-agent generic attempt lease tokens
   remain nullable as current source permits. Record the trusted supervisor and
   session binding before launching a child. A child never chooses its attempt,
   caller, target organization, source or accounting organization.
4. Stage verified immutable source bytes and the selected reviewed runtime
   artifact using parent authority. Launch an inert first-party interpreter:
   no tenant site/startup hooks, dependency installation hooks, entrypoint import,
   ambient SDK/provider credential, SQL, Redis or object-store authority. An
   artifact/environment that cannot establish this property fails before Start;
   no claim of equivalent general dependency installation policy is made.
5. P0 framing and its parent-supplied accepted-preparation binding remain
   unchanged; P0 implements neither Prepare nor Prepared. The expanded Prepare
   carries separately frozen parent workload/attempt/caller/limit/capability
   inputs and staged-source metadata. Only its credential component is an opaque
   reserved provision-slot ID. Prepared returns actual byte, namespace and
   artifact observations. Python
   may verify staged bytes and installed artifact metadata; it cannot fetch
   source through the private SDK grant, import tenant modules or run effects.
   Parent verifies Prepared against the accepted closure and actual artifact.

The selected environment's source/module/dependency closure and actual command,
image, UID, mounts, descendants and credential descriptors need executable
positive/negative proof. A digest from the child alone does not establish them.

## Start and provision

1. In a fresh ownership-checked transaction, lock and refresh the selected
   logical row, attempt and session according to the final common lock order.
   Revalidate caller/source eligibility, cancellation, ownership, session and
   exact fence. Persist the unique accepted Start and transition the existing
   attempt/domain to the source-compatible running shape **atomically**. Commit
   before a Start frame or bearer credential can leave trusted custody.
2. Do not issue for a flush-only transaction. For a deployment-v1 workflow,
   CRED-P1-S's issuer runs its own committed precheck, attempt-first lock,
   secondary SHARE NOWAIT refresh and full post-lock revalidation. It preserves
   predeclared initial expiry/deadline and immutable accepted deployment even if
   current pointers change. Its source permissions remain empty.
3. Couple provision eligibility to the actual accepted session registry and
   exact Start. S641 currently binds UUIDs but has no live-session registry;
   this coupling and durable close/revocation must be designed and tested before
   wiring it to a real runtime. The registry is owned by Rust, not a child-
   asserted heartbeat. Private S641 alone is not provision acceptance.
4. Send the single Start over the bound private channel. Release actual reviewed
   credential material only into the separately bound descriptor for this
   attempt/session. The child must validate Start against Prepare **and** receive
   the corresponding actual provision before tenant imports, provider calls or
   workflow effects. Provision may be empty only when the selected workload
   genuinely needs no granted remote operation; empty is not a broad token.
5. Every outbound credential-bearing HTTP client disables automatic redirects.
   Existing Python OAuth recovery behavior is a separate correction gate, not
   silently repaired here. Provider credentials have their own reviewed
   restricted-custody adapter; a workflow SDK grant does not confer model
   authority and an agent runtime does not receive an engine signing key.

A durable Start followed by a lost send, missing ACK, failed provision or
supervisor death means possible execution. Do not create a new Start, retry the
workload or hand it to Python. Stop/revoke the same session and characterize the
source-compatible terminal/recovery decision. This conservative no-replay rule
does not authorize inventing a new public terminal status.

## Close fence and admission linearization

Root chooses one persistent session-close tombstone, never deletion of an
active session or revocation of only whatever grant happens to exist. New grant
creation, token/provision admission, no-timeout renewal admission, child adoption
and close must refresh the same session/accepted-Start/typed-attempt/owner facts
under one shared database serialization fence. Channel validation before the
transaction is necessary but does not replace this refreshed check.

The common order must preserve source-accounting admission fence first when
source is admitted, then the private S-compatible attempt-first lock and
secondary NOWAIT locks. The final schema package must name the remaining session,
owner, logical/source and grant locks and prove the order against every close,
issuer, recovery and adoption transaction. No builder may choose a different
order or introduce a session-first close that deadlocks an attempt-first issuer.

- **Close linearizes at its committed tombstone.** A session closed before a
  grant exists cannot later acquire one. Existing grants are revoked in the same
  close transaction. A transaction that started earlier but loses this fence
  rechecks closed state and aborts without a new admission/child/grant.
- **Grant creation is not token/provision admission.** It retains private S's
  committed-Start requirement and atomic grant-plus-operations insertion, with
  the new live-session gate added explicitly. A grant committed before close
  does not entitle a later issuance read to pass the tombstone.
- **Issuance/renewal/provision admission linearizes at a committed bounded
  admission record under that fence.** A winning record binds exact Start,
  session, grant, unchanged expiry/operation set and one provision slot. Signing
  and private descriptor delivery are distinct later bounded custody actions.
  Close may win after this record: previously admitted signing/delivery can
  complete, but creates no renewed session, new Start or new admission. Such
  credential bytes are not proof of usable post-close SDK authority: every SDK
  operation still freshly rejects committed close/revocation. The trusted
  supervisor stops delivery when it has observed close and must enforce actual
  child stop. No claim of instantaneous invalidation of already disclosed vendor
  material or effects is made.
- **Tool-child adoption linearizes at the committed intent receipt and child
  admission**, with refreshed parent eligibility under the same fence and the
  shared source-accounting lock before child source/row locks. Close before that
  point aborts with no child. Adoption before close retains the same child and its
  independent obligations; it never uses close or rollback to dispatch another.
- **Already-admitted HTTP/provider effects may finish**, preserving request-bound
  authorization semantics. Do not hold database locks across vendor calls or
  promise retroactive effect rollback. Future public H/R integration must enforce
  fresh request eligibility and the exact closed grant; this design is not its
  approval or proof.

This resolves the semantic admission points, not their schema or implementation.
S641 alone implements neither the session tombstone nor these admission records;
its token signing after a fresh read ends is not atomic session provisioning.
Required race tests include close-before-absent-grant, grant-commit-before-close,
close-between-issuance-read-and-sign/delivery, close-versus-renewal, and close
between report validation and concurrent child adoption. Assert admission records,
child/grant rows, token byte custody, usable SDK authority and retained source
obligations separately.

## Reports, agent tools and projections

- Reports enter only through the exact live private channel. Check session,
  accepted Start, workload kind and directional sequence before adopting them.
  No child-selected execution ID, account, source, grant, approval or final status
  is authoritative. Record bounded diagnostic rejection without sensitive
  payloads; malformed input cannot expand authority.
- Receipt identity binds session, accepted Start and message identity/type. Its
  digest covers the complete validated report, including the relevant correlation
  and observation fields. Exact repeated acceptance returns the existing receipt;
  changed payload under that identity fails with no new effect. This durable
  idempotency does not enable reconnect/replay in P0's one-shot pipe protocol.
- For the selected agent's one pending workflow-tool invocation, lock the parent
  binding, recheck actual tool attachment, original caller and current policy, then
  persist an intent receipt and the **one** adopted child execution identity in
  the same admission transaction. The child uses ordinary immutable workflow
  source rules. Dispatch only after commit through existing delivery. Same intent
  and payload reuse the adopted child; conflicting payload cannot allocate one.
  Runtime cannot directly call a Python admission helper or invoke nested SDK
  `agents.run` to bypass this parent decision.
- ToolOutcome carries the real committed child outcome. Retained Python applies
  existing formatting/truncation only to model/step copies. ToolObservation and
  the subsequent real model request prove actual consumption; delivery alone is
  insufficient. Preserve complete child result separately.
- Log, step, usage, result and event projections follow durable accepted receipts.
  Do not publish an event for a rolled-back transaction. Projection watermarks and
  stable event identities must permit retry of publication without launching work
  or billing twice. Real Redis failure/commit failure tests must characterize
  current consumer behavior before ratifying any difference. No general event
  stack migration is included.
- Summary and its metering remain required. A separate field-specific retained
  Python summarizer may execute model/runtime mechanics; it cannot finalize or
  recreate Rust-owned lifecycle state. Whole-table/role exemptions are forbidden.
  Actual allowable summary writes and original accounting org need writer-map
  review, not an assumption that every summary column is harmless.

## Closing, failure and rollback

1. Accept terminal evidence through a durable receipt and current fenced
   transaction. Preserve result serialization/limits, terminal race behavior and
   exact workflow/agent status semantics from the reference. Cancellation's
   autonomous `cancelling` versus consumer `running` discrepancy is unresolved;
   builders must not fix it implicitly.
2. Revoke runtime grants and close future provision/adoption authority under the
   same trusted session fence. A JWT expiry or revoked grant is not evidence that
   code or an external process stopped. No new child tool may be adopted after
   authoritative close; outstanding already-admitted children keep their own
   owners, attempts and source obligations.
3. Wait/reap the actual child and supervised descendant process group. `Stopped`
   is advisory. Report observed process identity/exit and reconcile source
   consumers. Parent death requires a trusted supervisor-recovery mechanism with
   actual process custody; an absent heartbeat cannot certify stop. The exact
   recovery mechanism and timeout/outage policy remain architect freeze gates.
4. Domain result, delivery settlement, grant close, event publication, summary,
   and process/source drain are independent facts. Workflow delivery currently
   completes at child dispatch; agent delivery completes at durable domain
   outcome. Preserve each existing policy rather than keeping workflow delivery
   alive to imitate runtime ownership.
5. Rollback changes ownership for **new admissions only**. It never changes an
   existing row's owner or grants unrestricted Python DML to finish Rust items.
   Keep the Rust coordinator available to drain owned items. If it cannot drain,
   fail closed and produce the reviewed recovery disposition; no data repair or
   automatic second execution is an acceptable rollback mechanism.

## Required implementation gates

| Gate | Evidence before authority-bearing implementation is accepted |
| --- | --- |
| Durable facts/schema | Explicit additive Alembic schema, constraints, FK/identity binding, source-consumer coverage and actual lock order reviewed together; no competing migration history. |
| Writer exclusion | Actual separate DB/PgBouncer identities; ownership-aware guards including child/projection/cascade writes; positive unchanged Python items and negative all incumbent writers on Rust items. |
| Start/provision | Flush-only denial, commit failure, cancellation/lease race, wrong session, stale fence, superseded accepted pin, unavailable authority, failed provision and lost Start ACK tested without replay. |
| Runtime isolation | Earliest interpreter/site/dependency/import hook cannot exercise tenant/SDK/provider effects before validated Start plus provision; child cannot persist/finalize/redispatch lifecycle or reach parent credentials. |
| Durable reports | Same/conflicting receipt, concurrent child adoption, terminal result race, decimal usage exactly once, actual model consumption and summary/event joins. |
| Coexistence/rollback | Mixed Python/Rust writers on one schema, disable/revoke/cancel around Start/result, Redis/DB outages, parent restart, descendants, live in-flight rollback without duplicated execution or ownership reassignment. |

This packet narrows unresolved implementation choices without manufacturing
acceptance. Public SDK actor/external disclosure/renewal ingress, any changed
cancellation/recovery outcome, general dependency or public result-limit policy
still require separate approval when a proposed answer changes current behavior.
