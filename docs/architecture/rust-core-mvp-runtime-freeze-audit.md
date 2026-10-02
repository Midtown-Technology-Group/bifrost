# Runtime freeze frontier: architect disposition

This source audit advances the bounded workflow/agent MVP; it does not replace
it with a codec-only experiment. See the [amendment](rust-core-mvp-amendment.md),
[credential foundation](rust-core-mvp-credential-foundation.md), and
[C1-R reference](rust-core-mvp-agent-reference.md). Guidance package is
2026-10-01.5. No C2/C3 builder is released by this packet.

Root decisions from the audit:

- Role facts use bounded literal strings, preserving actual source values rather
  than assuming UUID vocabulary. Any separately verified role IDs must be named
  separately. Snapshot/order/canonical-grant mapping still needs real vectors;
  role text never supplies a grant by itself. P0's already frozen framing is
  unchanged.
- Rust admission must capture original caller facts before effective-target
  projection. Missing historical original organization remains unknown, never
  inferred from the target, transport engine privilege or a mutable User row.
  Unknown historical provenance must have a distinct representation from a
  genuinely known GLOBAL/null organization. AUTH-P1's flag correction is not
  evidence of original-org separation. Existing
  Python run/rerun behavior is not changed by this decision.
- Prepare uses parent-staged immutable bytes/metadata, with independently
  verified accepted source/dependency/namespace custody. It receives no SDK,
  provider, signing, SQL, Redis or object-storage authority. Reserve a provision
  slot only. Dependency hooks and tenant imports cannot run before committed
  Start validated by the bound child and actual credential provision. An inert
  Hello/Prepare child launch remains permitted. The exact mount/descriptor and failed-provision sequence must be frozen
  and tested before extraction; this is a selected-profile design, not a general
  dependency-policy change.
- After durable Start, credentials may be released only to that same bound
  attempt/session. Child workload effects cannot begin until it has validated
  the actual Start and received its bound provision; a lost provision is not permission to replay or manufacture another
  Start. Private S641 offers no source lookup authority and must not be broadened
  to make Prepare work.
- Live session, durable receipt/adoption, row ownership, source-consumer closure,
  field-specific summary authority and rollback decisions remain root-owned
  prerequisites. Neither a pipe acknowledgement nor a UUID snapshot resolves
  them. Complete workflow/agent/tool/summary/event/process-stop behavior remains
  required for the final go/no-go.

These choices preserve the direction already authorized. Any later public
external-disclosure/actor, cancellation/replay, original-org behavior repair,
dependency policy or result-limit change must receive its distinct scope
review. The audit below identifies source evidence and the precise frontier;
it is not runtime acceptance or authority to improvise an implementation.

---

# Runtime v1: final interface freeze audit

Source-only decision packet, 2026-10-01. This is neither interface ratification nor
implementation/merge/deployment authorization. Only this `/tmp` artifact was
written. No product imports, Docker, pytest, CI, vendor calls or agents were used.

Guidance read: repository `AGENTS.md`; applicable platform execution/security and
verification guidance; `mtg-engineering-flow` 2026-09-30.1; `mtg-skill-router`
2026-10-01.1; `mtg-grill-with-docs`, immutable package **2026-10-01.5** at
`2026-10-01.5-334ce0f08a1bacc5`.

## Evidence identities

| Evidence | Exact identity and limit |
| --- | --- |
| Runtime proposal | `/tmp/bifrost-runtime-v1-proposal.md`, source `e77947fab5762e49dd5fdc390bd65ca84bc22c3c`; normative draft, not complete accepted contract |
| Authoritative product source for this audit | platform main `e58db4955ddd30177bd613f1d85b7e203ad7832a`, including #1027 |
| Architecture reading worktree | `/home/thomas/src/bifrost-rust-core-mvp`, HEAD `9e6b7dcb1d03cbc5ca64e10bcf9de02a8312604f`; its `api/` tree matches e58 exactly. Root's later dirty `rust-core-mvp-agent-observation-wire.md` was preserved; no new acceptance inferred from that edit |
| Private credential source | `/home/thomas/src/bifrost-runtime-sdk-credentials`, exact `6419069da195b053c885ab349f431ff4fae62098`; open/private foundation, not accepting HTTP/source/runtime policy |
| AUTH dependency | exact unmerged `d82219f5aada66d879f2da71b386f50675c66d4d`; privilege flags only, existing effective/run org behavior retained |
| Workspace assets | retained `e8605dc8edb6df8a997c171b534b324ac7ebd8ec`; unchanged A/B source identity remains independent of runtime image identity |

Architecture docs report S run `36933342485` and REF99 run `36933538470`, later
REF50 `36936246934`, and REF-e1f4 CI `36938634756`/CodeQL `36938634851` at their
own exact candidates. Those are retained reported evidence, not live checks made
here. REF proves legacy public SDK/result/actor/OAuth behavior; it does not prove
dedicated S-token HTTP admission, session custody, restricted runtime or Rust.
The authorized additional #1017 cycle's passing `36936257108` likewise remains
at its recorded head/merge; this audit changes neither its oracle nor disposition.

The amendment has already ratified **P0 control framing only**: 16 MiB/depth64,
Hello/Start/Heartbeat/Cancel/Stopped and parent-provided prepared binding
(`rust-core-mvp-amendment.md:344`). Do not re-open that builder or silently
replace its paths/types. Expansion to Prepare/workload/result/agent reports
requires the decisions below and independent source vectors.

## Smallest coherent execution boundary

Retain the complete authorized workflow/agent lifecycle. One Rust coordinator
authorizes and admits the selected immutable workflow or autonomous run, commits
its typed attempt/Start, supervises one Python incarnation, accepts evidence,
projects durable results/steps/usage and existing events, and owns cancellation,
recovery and routing/rollback. Python retains source loading and execution,
Pydantic model retry/fallback/budgets/argument validation and existing tool-result
formatting, without lifecycle SQL/HTTP/Redis authority.

For agent tools, one supervised bidirectional channel carries a typed intent;
Rust freshly checks the attachment/caller, adopts one stable workflow execution,
and returns its genuine durable outcome. Python then reports actual formatted
tool observation and next model request/result. Install and grant **both**
unchanged Cove tools/full schemas; the frozen first B invocation remains only
capacity, yielding six actual ordered steps. Preview execution is a later case,
not a silently removed grant or replacement asset.

Successful logical completion is followed by separately owned summary delivery
and summary metering/fields, actual child stop and source-consumer closure.
A codec-only pass, a Python queue/auth callback mesh, or Rust forwarding to the
existing consumer is insufficient. Unsupported ownership capabilities stay on
their configured Python route **before admission**; no public rejection or
redispatch after possible Start may be added merely to simplify extraction.

## Remaining decisions and source STOP gates

`R` means root can freeze a compatible internal mapping/implementation choice.
`A` means root must explicitly ratify an authority/ownership design with its
security proof before a builder. `U` means present a concrete user decision if
the proposed answer changes authorized scope, public behavior, privilege or
recovery semantics. A is not automatically a new permission request; existing
narrow approval covers its stated direction only.

| # / decision | Concrete source/interface | Freeze required / owner | STOP |
| --- | --- | --- | --- |
| 1. Caller vocabulary and provenance | Draft §4 `CallerFacts.role_ids:[UUID]`; main `core/security.py:307` has `EmbedUser`, `routers/auth.py:1311,2061` has `authenticated`; `core/auth.py:430` separately hydrates role IDs/names. S `core/runtime_sdk_credentials.py:105` takes bounded literal role strings. `agent_run_service.py:169` puts execution org in queued caller; AUTH rerun `agent_runs.py:690` retains original run org | **R:** preserve exact bounded role strings, with separately named verified IDs only where actual source requires them; roles never manufacture grants. **A:** freeze the admission-owned original snapshot plus separate effective scope. Historical queued org is not original provenance. **U:** any repair that changes existing run/rerun organization behavior or expands AUTH's privilege-only approval | Missing original org/flags cannot be reconstructed from target org, transport admin or a later mutable User row. Such an item is not fully evidenced Rust-owned work. Do not relabel AUTH as original-org separation |
| 2. Workload, context and observation mapping | Draft §§4–6; `workflow_execution.py:1495` context projection; `engine.py:354` does not set `is_agent`/artifact workspace in normal context; `worker.py:434`; `autonomous_agent_executor.py:212,253,298` resolves caller/model/tools after claim | **R:** exact adapter table for actual fields, omission/null, opaque artifact workspace IDs, provider chain order and claim-time multi-read configuration observation. Root must retain source-observed context values; observation ID is not an atomic cross-table version. Closed provider `extra_params` extensions need actual capture (`llm/base.py:124`). **U:** enabling fields or changing prompt/provider selection/config atomicity as a product guarantee | Generic context bags, dropping nonempty extras, regenerated prompt/schema, guessed flags or admission-time agent pin asserted over claim-time behavior |
| 3. Prepare/source/credential sequencing | Draft §8 reserves credential provision, §9 allows source fetch before Start. S `runtime_sdk_grants.py:159–188,206` requires committed started attempt; `core/runtime_sdk_credentials.py:356` hashes **zero** source lookups | **R/A:** reserve descriptor/provision ID at Prepare with no SDK/provider bytes. Choose exact pre-Start staged immutable closure/metadata custody; freeze post-committed-Start credential delivery/wait before tenant imports, including failed/lost provision. One runtime/session must bind the same durable Start. Source HTTP grants are a separate reviewed extension, not implied by S | Early engine token, early Start to fetch source, package/tenant hook in Prepare, or a credentials-bearing pre-Start source fallback. S cannot service workspace-release source/runtime grants unchanged |
| 4. SDK public H/R policy | S operations are only integration-get/mapping-get with null entity/OAuth selectors; `_check_policy:86` uses separate original/effective org and conservative eligibility. Draft opaque SDK slot lacks accepting-handler semantics. `cli` get/mapping legacy engine and direct external results differ; proposal `/tmp/bifrost-cred-p1-public-integration-proposal.md` retains exact measured matrix | **A:** freeze accepting routes, exact bound values/null/defaults/installation, ingress denial, operation actor and finite/no-timeout renewal mapping. Keep legacy engine and direct external observations distinct. **U:** external result/OAuth disclosure, altered actor/audit attribution, selector widening, new generic ingress or a changed public response. A normal same-org slice must not be presented as all external/foreign-org authority accepted | S helper success is not public policy approval. Ordinary app/MCP/WebSocket/refresh decoders must not accept or upgrade dedicated purpose. No new SDK field/method/route or authored accommodation |
| 5. Live session and expiry authority | S `_load:380–412` checks immutable grant + attempt fence; supervisor/session IDs are snapshots, not a queried live supervisor registry. Explicit revoke is `:506`; initial expiry is `:235–245`, finite timeout+300 maximum and no-timeout 600-second window; only timeout-zero renews `:459–502` | **A:** choose durable live-supervisor/session/committed-Start validation and who atomically closes/revokes it. Bind provisioning, renewal, terminal result and actual process stop; distinguish vendor effects already in flight. Preserve private S digest/JWT/time rules unless separately reviewed. **U:** finite renewal/extension, changed expiry/public refresh semantics or broader grants | Treating a runtime UUID/heartbeat as lease authority, orphan renewable grants, replacing session without revocation, or claiming vendor-account revocation from SDK grant revocation |
| 6. Complete source/crypto/artifact identity | Draft §§5,7; `deployment_runtime.py:73,143` omission-sensitive evidence, `workspace_release_runtime.py` release identifiers; current module loader `module_cache_sync.py:435,476,707` uses module reads/resolve and a modules-index call without a matching router | **R:** freeze actual A/B evidence vectors, existing prefixed/bare digests, omitted optional keys, full source/dependency/resource closure and virtual namespace/package semantics. Parent must verify source authority; child reports bytes only. Choose exact launch artifact/interpreter/SDK/package observation schema. **A/U:** required new source authority or replacing mutable dependency/update/fallback semantics; prebuilt selected-profile experiment is not a general package policy | Source hash substituted for interpreter/dependency immutability, uncharacterized dynamic import, fabricated modules-index endpoint, general Redis/S3 source credential, or silently discarded declared bound |
| 7. Results, bounds and metering | Draft §§6,10–11; `draft_limits.py` measures `json.dumps(default=str,ensure_ascii=False)` bytes; `process_pool.py:211` workspace hard-duration minimum differs from deployment; worker result serialization uses default=str. `autonomous_agent_executor.py:1449` bills Agent.organization_id; `agent_run.py:506–563` persists result, flushes race-surviving steps/usage and queues summary | **R:** capture exact result type/serialization/byte accounting, status/error/null/omission/log mapping, actual provider decimal cost and accounting org. Preserve whole child result separately from model/step truncation. Null limits are not zero. Root must resolve draft Positive-duration versus source timeout-zero support before expanding that profile. **A:** exact durable report/usage receipt semantics. **U:** new public size limits, billing attribution or status normalization | Whole results exceed frame and are truncated; floats replace decimal cost; arbitrary child accounting org accepted; child/context outcome used as authority; terminal-race usage or summary metering disappears |
| 8. Intent/report receipts and event custody | Draft §11; `AgentRunStep` has only run-ID index (`models/orm/agent_runs.py:132–150`), no ordinal uniqueness. `autonomous_agent_executor.py:1482` emits Redis-first steps and later DB buffers. Workflow attempts use claim token; agent generic attempt lease is optional | **A:** freeze one stable child adoption key, exact same/conflicting-payload receipt hash, accepted Start/report receipts, projection watermarks and Rust-owned event publisher. Additive Alembic schema remains shared history, no parallel job model. **R:** correlation IDs/codec vectors once this meaning is ratified | Duplicate intent launches twice, per-pipe sequence mistaken for durable idempotency, direct runtime Redis publication/helper DML, runtime-supplied result ID adopted, or ToolOutcome delivery mistaken for real model consumption |
| 9. Writer exclusion and ancillary fields | Amendment §5 mechanical gate. Incumbent consumers, cleanup, poison/recovery, CLI/HTTP completion and cancellation are global writers. `run_summarizer.py:263,446–471` writes delivery/status, asked/did/answered/confidence/metadata and separate usage | **A:** exact immutable row ownership, guarded dependent projections, actual separate DB roles/PgBouncer identities and field-specific retained summary/annotation/delete authority. Choose lock order before implementation. **R:** internal guard naming consistent with that approved design. **U:** public deletion/cancellation/annotation contract changes or disabling required summary behavior | Shared unrestricted coordinator role, queue split without row protection, whole summarizer/table exemption, guard owner confused with caller, or denied lifecycle writes obtained by breaking unchanged SDK positives |
| 10. Cancellation/recovery/routing | Draft §12; `execution_policy.py:193` workflow delivery ends at child dispatch, agent at durable domain outcome. `agent_runs.py:764` sets cancelling; autonomous consumer `agent_run.py:506` updates only running, while chat `:1012` accepts cancelling. Heartbeat DB outage in current pool is not confirmed revocation | **A:** transition table covering committed Start, missing ACK, result-commit/process-exit races, cancellation, unavailable authority, supervisor death and in-flight rollback. Preserve delivery/domain/session distinctions. Root can routinely require conservative no replay after possible effects. **U:** silently fixing cancelling behavior or granting a new replay/automatic fallback recovery policy | Delivery settlement used as result fence; missing ACK/pipe loss interpreted as no execution; runtime reports worker-lost; ownership rollback redispatches started work or repairs domain rows |
| 11. Source-accounting drain and #1027 lineage | Main `async_executor.py:227` acquires shared admission fence before pin/row locks; `workspace_release_projection.py:104–121` uses matching exclusive/shared global lock. `solution_source_accountability.py:339–348` inventories accepted Execution and **generic** workflow attempts, not dedicated attempts, grants or runtime sessions. #1027 `workspace_promotions.py:2328`, `live_handoff_readback.py:54,168–240`, activation `:1093` certify omitted loose bindings | **A:** Rust admission takes the same shared lock before install/execution locks through durable commit. Freeze complete active-source consumer inventory and trusted close/stop proof, including dedicated workflow attempt and still-live session after logical terminal. **R:** preserve #1027 reviewed active/dependency/origin readback and omitted-registration set exactly. **U:** allowing loose reclaim, early retirement or manufactured source settlement | `completion_evidence`/terminal row/grant revoke taken as runtime drain; new Source obligations hidden; old deployment pointer replaced for an accepted pin. #1027 keeps governed source files and does not settle Source debt or Live retirement |

## Concrete interaction disposition

- **S641 is reusable as private foundation, not an entire runtime credential.** Its
  migration `20261001_runtime_sdk_grants` follows `20261001_solution_src_account`;
  #1027 adds no migration. Preserve that ancestry. `_read_work` deliberately allows
  a superseded accepted deployment (`allow_superseded=True`), so a pointer refresh
  must not switch a running grant to current source. Reconcile actual active install
  state without rewriting the accepted pin.
- **#1023 fence is compulsory in Rust admission.** Its current inventory is not a
  runtime-session drain oracle. Choose genuine retained consumer evidence before
  terminal projection can hide an alive child; extend accounting under the shared
  lock as required, rather than mark source obligations settled in tests.
- **#1027 is certified omission, not source deletion/retirement or new source
  authority.** It re-proves exact UUID, namespace/scope/exposure, immutable bytes,
  active dependencies and reviewed lineage under native locks; loose snapshots keep
  governed files. C3 must consume this evidence without duplicating its ownership
  semantics or treating current registrations as the source of an accepted run.
- **Provider transport remains a real gate.** Main `oauth_provider.py:327` posts
  credential-bearing recovery data without `allow_redirects=False`; retained
  aiohttp source establishes default following, including body replay on 307/308.
  REF normal recovery is not redirect-negative proof. Narrow transport correction
  and actual synthetic 301/302/303/307/308 target non-delivery remain separately
  reviewed; this packet authorizes no production correction.

## Actionable freeze sequence

1. Root publishes the source-compatible type/mapping correction: literal roles,
   distinct original/effective provenance, context omission/null table, and the
   exact scope of P0 versus unfrozen expansion. Keep the independent P0 builder.
2. Name one authoritative Start/session/credential/close transaction sequence,
   receipt ownership and mechanical DB writer design. Include source-consumer
   accounting and summary fields in that same lifecycle boundary.
3. Ratify the staged-byte versus reviewed source-HTTP strategy and artifact/hook
   policy. Reconcile private S641 under exact e58 without broadening its source
   grant or changing source-accounting/handoff semantics.
4. Present concrete unresolved **U** choices together: original-org behavior
   repair if needed, external SDK result/actor policy, any dependency/public-limit
   difference, and cancellation/replay difference. Ask only if a proposed answer
   actually changes existing approved behavior; document unchanged behavior where
   source/reference already settles it.
5. Complete A/B reference vectors through the supported isolated lane; ratify
   expanded codec schema and then C2 extraction. Require unchanged SDK positives,
   actual model/tool/summary/events plus same-provision authority negatives before
   C3. Static review, codec vectors and test-only HTTP observer custody cannot
   replace those proofs.

No control-plane builder may choose unresolved authority, public behavior,
ownership or recovery values. The immediate unblocked work is root's exact
mapping and authority-sequence decision packet, not another infrastructure lane
or a reduced workflow-only/codec-only substitute for the authorized goal.
