# C1-R source-reviewed lineage contract

Root and independent source review froze the corrected predicates below after
closing the two Q4 findings (runtime_evidence_hash and known-ID generic ledger
exclusion). Reviewed packet SHA256
`0e500ddd0cd75a1387dbe15c2c081223f431936391056e75a13c9e5bc42c704c`;
independent review SHA256
`6cd48c17d52cf516611a54dd8215dee8f60b587a7bbc49bb331ef6104efffedd`.
Root ratifies the selected fresh nominal's explicit cardinalities and source
causality, with the recorded NULL/missing-context limitations. This freezes the
behavioral oracle, not a reader/consumer implementation release: exact private
query/decryption/hash/deadline ownership and supported nominal construction,
process custody and closure remain gates. No new authority or C2/C3 acceptance.
The historical pins below stay exact; later recipe/mount source does not modify
the25 selected product fingerprints.

## Reviewed source packet

Source-only proposal for root review. Scope is the one unchanged capacity nominal, both installed/granted tools, actual autonomous parent/child execution and real summary settlement. No repository edit, product import, compiler execution, runtime/test/Docker/CI/API/vendor action, commit or push occurred. No C1 nominal, Rust, C2/C3, fresh-lease or public CRED policy acceptance follows from this packet.

## Pins and controlling corrections

Reference worktree `/home/thomas/src/bifrost-agent-capacity-reference`, branch `test/agent-capacity-reference`, inspected clean HEAD `bb3d3fd6720e897badcc6e3f703bea1b3af28422`; platform main is separately `01cadfe09710d293a40da14d6cf4056165289e31`. Docs inspected at `/home/thomas/src/bifrost-rust-core-mvp` HEAD `767223f9774c08bc22598da2a2f334ff93f09483`.

Root-review correction2026-10-02: retained prior packet SHA `52d8f0e4aed819cdefa275fb1d0e4eef5d841c05bdb3ba981f19faf8daad9ed1` as history. This revision removes its unsupported second enqueue-admission observation, specifies Q0's implementable non-atomic principal/DB characterization and exact Q7 exclusion joins, and updates parent-reported broader CI disposition. During this reread, parent advanced reference HEAD to `da9dac5c9d4cf8b26401bd01ea20d5f9914a9a75`; that diff contains only the two root-owned event modules, no selected product source change. This correction is source self-check for root review, not independent approval.

| Input | SHA256 |
|---|---|
| prior `/tmp/bifrost-agent-reference-case-setup-proposal.md` | `3d35a9e5e06b6295e2d646f20784894149ccc2148c75f12ec0659924e8479398` |
| independent `/tmp/bifrost-agent-reference-case-setup-independent-review.md` | `349c01d34140b8eaff11c084bbf7b2805b718f82d5d558314c6bf627b8dca748` |
| docs `rust-core-mvp-agent-reference.md` | `8185b92273ddc7917f10209be44aecb3fd245f9378783f64b8523037ead1299f` |
| docs `rust-core-mvp-agent-model-oracle.md` | `7b3579f6bdc134086fafda88c1898269c084b2bd25e592c01c4c001d117634cb` |
| docs `rust-core-mvp-agent-reference-integration.md` | `fa3c1c41c7bacef60b885dea6da40a0dc879cc9ff0c330b18a98670862c09eb7` |
| docs `rust-core-mvp-agent-observation-wire.md` | `6986ac7220acd086b37cc25dd2c37d81c71a14e0f7a89943f5996119d5c10f54` |

Guidance: repository AGENTS/applicable platform instructions; engineering flow2026-09-30.1 and selected design package2026-10-01.5. Parent reports all prior17 unit failures resolved and broader bb3d supported CI completed SUCCESS; this source task does not independently re-execute or promote that evidence to nominal acceptance. Historical broader-CI pending disposition is superseded only for that parent-reported bb3d run. No named C1 nominal has executed or been accepted; #1017/Sopdet remain distinct.

The independent setup review governs three retained corrections, without editing the earlier proposal: public `/auth/status` needs_setup=true and actual first-human eligibility (allow inventoried migration/system state; forbid default-user/stale human/business setup); fresh **GET** `/api/config?scope=global` after the accepted synthetic SECRET PUT (PUT can contain encrypted material and lacks full mapping); Redis SAME pre/post-bind budget64 received publications, each≤65536bytes, no dedup/drop/eviction, parent-proposed4MiB raw-payload aggregate ceiling. ACK control handling remains a separate bounded root-owned interface. This packet closes the missing DB/lineage reader design, not those collector implementation gates.

## 1. Immutable observed inputs and ID vocabulary

Use values obtained from real public setup/readback or admitted observations, never generated expected child IDs:

- `U,O,A,R`: actual normal caller UUID, actual provider organization UUID, actual generated Agent UUID, actual public enqueue run UUID. Preserve independently observed ordinary principal/DB facts (§Q0), the actual admitted DR caller snapshot, and effective run org separately. This nominal has original=effective provider O; no original-org separation claim or invented same-request enqueue observer.
- `S,D1,D2`: actual public Solution UUID and supported caller-selected deployment path UUIDs, with independently observed active pointer D2, full source/manifest/resolution digests and exact carried bytes. D1 contains the retained harmless bootstrap, D2 all3 selected registrations/full closure. Source commit is the actual clean committed carried-assets candidate, distinct from origin workspacee860.
- `W=0760d416-be3a-4a33-b540-5b6b0658075d`: capacity; `P=93b7f115-de11-444d-8309-7aee9bac2bec`: preview; `B=81c8961e-cedb-4d51-831b-d29a75e99cb9`: bootstrap.
- `T=cove_data_protection_recovery_steward_inspect_capacity`: exact normalized model tool name. Persisted `Execution.workflow_name` is the authored registered `recovery_steward_inspect_capacity`, a DIFFERENT field/name. Do not alias one into the other.
- `E`: discovered only from the actual committed selected tool-result step. `PA`: actual generic parent attempt ID; `WA`: actual workflow attempt ID. `DR,DW,DS`: actual WorkDelivery IDs selected independently below. No direct AgentRun→Execution or generic-attempt→delivery FK exists in this path.
- `F`: exact model-oracle final text; `K`: privately parsed actual capacity result; `C`: independently allocated lowerhex32 case identity. SDK role/nonce/seq identify witness custody, not domain identity.

Bind every observation to actual lane/project/source/API/runner custody first. Arm/bind fixture input, receipt signatures and source hashes alone do not establish a committed entity. Never export encrypted/decrypted delivery envelopes, lease/claim tokens, raw JWTs, credentials or unrestricted execution variables/context. Only approved safe synthetic projections may enter evidence.

## 2. Mechanical join graph and exact committed predicates

### Q0: Implementable independent original-facts characterization

Use the actual normal caller's newly public-issued login/MFA access bearer from setup, then authenticated **GET `/auth/me` with that SAME bearer immediately before enqueue**. Require actual returned id=U,email/name equal the fresh explicit setup values,organization_id=O,is_superuser=false,is_active/is_verified=true and literal roles. `/auth/me` uses the same ordinary CurrentActiveUser/get_current_user principal-resolution path as enqueue (`routers/auth.py:1026-1052`, `core/auth.py:145-299,303-355`). It DOES NOT expose provider/external flags, and the current observer captures only the later SDK request. No existing enqueue admission capture is asserted.

In the adjacent independently connected read-only pre-trigger snapshot, read explicit selected columns from users WHERE id=U (id,email,name,organization_id,is_active,is_verified,is_registered,is_system,is_superuser,is_external); organizations WHERE id=O (id,is_active,is_provider); user_roles JOIN roles ON user_roles.role_id=roles.id WHERE user_roles.user_id=U (actual role_id,role.name). Require the exact selected active registered verified non-system/non-super/non-external user in active provider org, and exactly the one public-generated role association/name. Preserve that entire observed projection and compare it unchanged in both pre-close and post-closure final Q0 snapshots. No grant/membership/flag mutation is allowed during this selected case; any actual observation drift is STOP, not a later-row reconstruction or new bearer workaround.

Token mint paths call `get_user_roles` and prepend literal authenticated (`routers/auth.py:663-675`, plus login/verify equivalents). That helper selects **Role.name**, not UUID (`services/user_provisioning.py:163-185`). For this exactly-one-role case, require `/auth/me.roles` EXACT `["authenticated", <actual selected Role.name>]`, retaining order and exact strings; compare DR.context.caller.roles to that actual `/me` array. Role UUID remains the independent UserRole/workflow grant identity, never a replacement string in the token-role array. Multiple/unexpected literal roles or role-name/UUID vocabulary mismatch requires STOP/source review rather than UUID coercion or sorting. This also corrects any earlier proposal's implication that ordinary login token roles necessarily contain Role UUIDs.

Expected provider/external facts are source-derived **from this selected stable DB projection**, not nonexistent `/me` fields: shared `resolve_provider_org_claim` uses actual Organization.is_provider for the non-system user, yielding true; `resolve_external_claim` returns false immediately for this User.is_external=false (`api/shared/external_access.py:25-91`). Preserve these boolean expectations and compare the actual admitted DR snapshot and actual signed delegated SDK flags to them. Actual bearer was obtained publicly after the configured role/user state; no JWT decoding/minting, private token replacement, ORM writes or forced claims are needed.

These are independently observed cross-request ordinary principal facts plus adjacent stable DB readbacks, **not atomic capture of the resolved enqueue request**. Current auth resolves ordinary flags/roles from the validated token; stable pre/post rows alone cannot prove no unobserved transient change or certify the exact token-mint/admission transaction. DR's encrypted caller context is the actual admitted snapshot itself, not an independent second recording of that request. This nominal compares the actual admitted snapshot against independently source-backed ordinary facts and stable observed state; true same-request resolved-authority capture and fresh lease/new-purpose validation remain separate C2/runtime prerequisites. If these exact flags or role vocabulary cannot be unambiguously reconstructed for the selected supported public issuance path despite stable DB, STOP; do not weaken original authority or invent an observer seam.

### Q1: Parent run, actual Agent and admitted caller

Read `agent_runs` WHERE id=R, and `agents` WHERE id=A. Exactly one each. Require:

- run.agent_id=A; org_id=O; caller_user_id=str(U), caller_email/name equal Q0's actual authenticated `/me` projection AND actual DR admitted snapshot; trigger_type exactly the public route's `api`, trigger_source=NULL because this route omits it; input exact frozen one-key task object, output_schema=NULL; conversation_id/event_delivery_id/parent_run_id=NULL; no child AgentRuns WHERE parent_run_id=R.
- status=`completed`, error=NULL; started_at/completed_at/duration_ms present; iterations_used=2,tokens_used=62,llm_model=`gpt-5.4-mini`; budgets16/32000. Output EXACT `{"text":F}`. F is the canonical frozen final completion string, not the whole executor response or summary output. Preserve actual timestamps/duration; no fixture epoch substitution or rounding.
- Agent actual name/prompt/profile/token budgets and both tool grants equal the independent public/DB setup projection; active=true. Agent is not falsely labeled Solution-managed. No changes to profile/grants/source/pointer during this one case.

Public route facts: `routers/agent_runs.py:1026`; publisher `services/execution/agent_run_service.py:144-226`. Consumer re-applies admitted context then terminalizes/flushes/enqueues summary in one transaction at `jobs/consumers/agent_run.py:375-413,494-563`; output wrapper508-511. The encrypted DR envelope's original `body.context.caller` is the actual retained admission snapshot (§Q5); Q0's adjacent independent observations characterize its source-compatible values without pretending a later User row reconstructs admission.

### Q2: Six actual steps, unique actual child discovery

Read `agent_run_steps` WHERE run_id=R ORDER BY step_number,id. Exactly6, numbers1..6 without duplicates, types:

`llm_request, llm_response, tool_call, tool_result, llm_request, llm_response`.

Each step has its own actual UUID id, run_id R and retained content; match every corresponding genuine detail `agent_run_step.step` publication by this actual id plus run_id/step_number/type/content/tokens_used/duration_ms. `created_at` is generated later by DB flush and is not in the broadcast step object; do not compare it to the event timestamp or Redis Stream timestamp.

- Requests1/5: content.model selected model, tools_count=2. Preserve actual framework messages_count/context_breakdown and emitted values; do not compare framework ModelMessage count to HTTP wire-message count or invent serialized-byte/hash metrics. Independent fixture verifies exact HTTP prompt/catalog/history.
- Response2: one tool_calls entry `{name:T,arguments:{}}`; selected actual content/finish_reason/usage. Response6: no tool_calls, content=F; usage17/5/22 then29/11/40, cache tokens0; response step tokens_used22/40. No model error/retry/delegation/system/preview/approval step.
- Call3: content exactly emitted `{tool_name:T,arguments:{}}`.
- Result4: content.tool_name=T; is_error EXACT false; execution_id present canonical UUID string; no error/child_run_id. This value alone establishes E for the child query; reject ambiguous/multiple/missing/empty execution IDs. content.result is the real ≤20000-character result string emitted by `str(event.result)`, not an injected parsed object. Parse as JSON and require K; compare byte-for-byte with actual model tool-role message content under the separately frozen mapper. No truncation is admissible for this small selected result.

Source `autonomous_agent_executor.py:324-395,402-438,747-759,591-624,1499-1559`. Execute helper passes agent_run_id only into its separate approval branch; ordinary execute_tool has NO parent-run FK (`agent_workflow_tools.py:68,99-116`). The model's tool_call_id joins actual first/final HTTP history inside the fixture; it is NOT stored in these ordinary step contents. Do not invent a DB tool_call_id join.

### Q3: Selected child Execution and immutable pin

Read `executions` WHERE id=E. Exactly one; require workflow_id=W, workflow_name=`recovery_steward_inspect_capacity`, organization_id=O, executed_by=U, executed_by_name Q0's independently observed name and actual admitted DR name; parameters={} and form_id/api_key_id/session_id=NULL. status EXACT enum/string `Success` (not lower-case `completed`/`succeeded`), error_message=NULL,result_type=`json`; result EXACT K. started_at/completed_at/duration_ms actual and present; execution_model=`process` from the real selected consumer. No normal child Execution is an AgentRun.

Require solution_deployment_id=D2,runtime_mode=`deployment-v1`,attempt_tracking_version=`v1`. Independently Q7 joins Workflow.solution_id=S, active Solution pointerD2 and actual SolutionDeployment D2/organizationS/compiled manifest/source bytes; no repo-v1, legacy, workspace-release or mutable storage fallback.

Runtime_evidence is the exact `PinnedWorkflowRuntime.queue_evidence()` object, source `services/solutions/deployment_runtime.py:73-102`. Require its solution_id=S,solution_deployment_id=D2,bundle_hash,compiled_manifest_hash,git_commit_sha,runtime_storage_prefix equal independent deployment evidence; workflow_portable_ref exact carried `features/cove/workflows/recovery_steward.py::recovery_steward_inspect_capacity`; authored workflow_name/function/path/source hash; full deployment_source_hashes exactly the5-path D2 closure; workflow_type tool,workflow_organization_id O,solution_global_repo_access=false; timeout/control/runtime_bounds/parameters_schema exactly the canonical reviewed registration. Recompute runtime_evidence_hash with the EXISTING canonical_json/sha256_digest algorithm, compare digest; do not copy a permissive hash normalizer or treat a supplied digest as truth. Optional runtime-bound/schema evidence keys follow the actual canonical pin; all expected selected values must be independently present.

Dispatch_evidence is exact schema `bifrost.workflow-pending-dispatch/v1` with keys schema_version/request/request_hash/publish/publish_hash. Recompute all three hashes using existing canonical implementation; correlate request and publish to actual E/W/parameters/O/U/name/email,sync=true,form/api-key NULL, original is_platform_admin=false,is_provider_org=true,is_external=false,artifact_workspace_id=str(R). Publish excludes request-only schema_version/caller_solution_deployment_id then adds exact solution_deployment_id D2,runtime_evidence,runtime_mode,execution_record_exists=true. Compare full safe structure privately to source's `_validated_pending_dispatch`, not a guessed bag or ignored unknown keys (`async_executor.py:47-175,285-357`). Source currently does not put roles or is_agent into this durable dispatch identity; their absence must not be repaired by invented values.

Persisted execution_context, where emitted, independently retains actual U/email/name/execution_id/auth flags/provider organization/parameters. Worker→engine source (`worker.py:426-455`, `engine.py:354-374`) does **not** supply is_agent/artifact_workspace_id to the normal engine context constructor; baseline serialized is_agent=false/artifact_workspace_id=NULL is not proof of a different caller/run. `bifrost/_execution_context.py:235-263` also omits Solution/deployment from its public snapshot. Use dispatch/runtime evidence for those joins, not nonexistent context fields. No product repair is authorized by this observation.

### Q4: Two different attempt ledgers, no guessed aliases

Generic parent query: `execution_attempts WHERE logical_job_type='agent_run' AND logical_job_id=R ORDER BY attempt_number,id`. Exactly one PA for this fresh, no-retry nominal: number1,status succeeded, organization_id O,policy_identifier agent-runs,workload_class interactive_agent,admission_policy consumer_qos,mechanism postgres_lease,queue_name agent-runs; retry_count/replay_count0; nonempty actual worker_id,positive process_id,actual started_at/completed_at; failure_code/message NULL.

Parent **message_id=NULL and lease_token=NULL** are baseline selected source: consumer188-191 passes body.get(message_id), actual public body has no message_id, BaseConsumer531-551 keeps transport ID separate without injection; `_claim_durable_run`735-781 does not pass current-delivery token to start_execution_attempt. Do not join these NULLs to DR or demand PA.message_id=R/PA.lease_token=delivery token. Join by actual logical identity and independently verify DR. This is source characterization, not an exact PA↔DR claim-capability proof. Source policy changes mechanism to postgres_lease at `jobs/execution_policy.py:288-299`.

Workflow query: `workflow_execution_attempts WHERE execution_id=E ORDER BY attempt_number,id`. Exactly one WA:number1,status succeeded,phase terminal,policy_version workflow-attempt/v1; claim_token nonnull, published_at/claimed_at/started_at/heartbeat_at/completed_at present; actual worker_id/worker_incarnation_id/process_id present; failure_phase/code NULL; runtime_mode/runtime_evidence_hash/dispatch_evidence_hash equal Q3; policy_digest independently recomputed existing `_policy_digest` for runtime mode and exact disabled retry snapshot. Do not export claim_token. `services/execution/attempts.py` imports `WorkflowExecutionAttempt AS ExecutionAttempt` at12: this alias is NOT generic `execution_attempts`. Ensure dispatch attempt63-89, claim128-147, running188-209 and accepted result finalization `workflow_execution.py:440-496,544` establish this source path. Durable child result and WA finalization share one commit before the private synchronous result wakes the agent.

Independent known-ID generic exclusion: select explicit generic attempt columns FROM execution_attempts WHERE logical_job_id IN (:run_id,:child_execution_id). Bind actual R/E as UUID-typed values: the actual column is logical_job_id, non-null PostgreSQL UUID(as_uuid=True), not logical_id or a text ID. Require the full selected set to be exactly the already selected PA row with logical_job_type='agent_run' AND logical_job_id=R; no other row/type for R and no row for E. No guessed child/summary type alias is required. The reader must bound cardinality before loading history (a second returned row already proves failure), rather than filtering away a non-parent row.

No selected source creates a generic child workflow or summary ExecutionAttempt. A fake generic workflow/summary row cannot stand in for WA/DS. If such selected logical rows or extra WA/PA arise in this exclusive fresh case, STOP and inspect actual cause; do not relabel retry/recovery as nominal or select only the newest success. WA token remains private and its equality to an SDK token is NOT in the safe observer schema (§6).

### Q5: Three independently selected durable transport rows

Query **ALL** WorkDelivery rows, not only active/newest, for pairs `(agent-runs,str(R))`, `(workflow-executions,str(E))`, `(agent-summarization,str(R))`. Require exactly one each and capture actual IDs DR,DW,DS. Partial active uniqueness permits another row after settlement, so arbitrary `.first()`/latest/created_at selection is unsafe. All3 final rows: status completed,settled_at/started_at present,claim_count1,lease_owner/lease_token/lease_expires_at ALL NULL. Fail queued,claimed,interrupted,poison,missing,duplicate,redelivered or unsettled. Settlement fields use DB clock; no fabricated lease values.

Privately read encrypted_envelope and decode with the existing pure decrypt/JSON path (no writes), validate emitted envelope shape/body/headers rather than export it. `jobs/rabbitmq.py:983-1000,1012-1036,1382-1408` generates stable message IDs from run/execution and encrypted body+headers. Require actual headers x-origin-queue/x-idempotency-key/x-original-message-id correspond to selected queue/id; x-schema-version="1",retry/replayed counts0; retain actual x-enqueued-at privately without normalizing. Unexpected selected envelope extensions require source review, not acceptance by an open dict.

- DR.body exact admitted run_id R,agent_id A,trigger_type api,sync=false and context. Context exact run/agent/input/output_schema/org/trigger source/caller snapshot, event_delivery_id/conversation_id NULL,cancelled false. Its actual admitted caller user/email/name/org/roles must equal Q0's authenticated `/me` projection; flags must match Q0's exact source-derived expectations from stable independent User/Organization readbacks. Preserve that admitted snapshot separately from engine admin. There is NO second public POST-context observer: DR IS the actual persisted enqueue snapshot, compared to Q0's non-atomic cross-request source/runtime characterization. Generic PA is linked to R, not via an invented delivery ID.
- DW.body execution_id E,workflow_id W,sync=true,execution_record_exists=true,solution_deployment_id D2,actual source file_path if emitted, and pending_context. pending_context exact E/W/{}/O/U/name/email/adminfalse/providertrue/externalfalse/sync true,pin/runtime/source evidence and artifact_workspace_id R; compare to Q3 publish semantic fields including source-defined form_inputs/embed defaults. The pending context includes actual created_at,cancelled=false; its whole dict is NOT identical to the dispatch publish dict. No false raw-object equality across the two different source serializers. Source `async_executor.py:501-556`, `core/redis_client.py:123-212`; consumer1068+ prefers body.pending_context under POSTGRES. Do not rely on expiring Redis pending/context keys as the durable oracle.
- DS.body exact `{run_id:str(R)}` (no backfill_job_id/tuning mutation). `agent_runs.summary_delivery_id` must equal actual DS.id; message_id=R is not DS.id. Enqueue occurs in parent terminal commit; real summarizer takes DS ownership, changes summary_delivery_id/status and later persists fields+usage under repeated delivery fence. No generic summary attempt is fabricated.

All completed lease fields are NULL by contract (`work_delivery_store.py:226-256`). Historical exact lease owner/token is not retained after settlement; claim_count1/actual IDs/source fences characterize one selected claim, not a new fresh-lease admission oracle. Workflow policy completion boundary is child_dispatched (`execution_policy.py:193-207`), so DW may settle before WA/Execution success. Parent transport ACK is after handler's durable parent result; DS transport ACK is after actual summary handler; both are separate transactions after domain commit (`rabbitmq.py:447-471`, `postgres_delivery.py:73-96`). Require all independent terminal states, never infer settlement from a completed domain/event.

### Q6: Actual summary fields and metering, not a token aggregate shortcut

Same AgentRun R: summary_status completed,summary_error=NULL,summary_delivery_id DS,summary_generated_at actual nonnull,summary_prompt_version="v4". Exact final fields:

asked=`Inspect capacity evidence.`; did=`Inspected capacity; no recovery agents or restore rows were configured.`; answered=`HUMAN_REVIEW_REQUIRED`; confidence numeric0.4; confidence_reason=`Authoritative target mapping was not supplied.`; raw SQL column metadata EXACT `{fixture_case:C}` (ORM attribute run_metadata). No failover_path/unrequested metadata. The source truncation/string conversion/merge rules are independently applied to selected fixture values, not normalized arbitrary outputs (`run_summarizer.py:438-487`). A successful summary event lacks answered/confidence_reason/full metadata/version; do not invent them in event evidence.

Read `ai_usage WHERE agent_run_id=R ORDER BY id`. Exactly3 rows with actual distinct IDs, all execution_id/conversation_id/message_id NULL,user_id NULL,organization_id O,provider openai,model gpt-5.4-mini,cache_read/write0,provider_cost NULL. Compare multiset of input/output counts EXACT `(17,5),(29,11),(37,13)`; first2 are agent metering62, third summary50. All selected sequence values are source default1, **not a phase/order key**; there is no persisted summary category flag. Distinct token tuples plus actual step/model/summary transcript distinguish this selected fixture; no general phase classifier is asserted. Actual agent `run.tokens_used` stays62; public AI totals input83/output29/call_count3 combined112. Do not make run.tokens_used112 or count transport-probe usage as metering. Child E has no AIUsage rows; empty capacity tool does not directly use a model.

`ai_usage_service.py:98-116,142-177` canonicalizes provider/display model and may swallow metering failures; missing records are a failed oracle even if run/summary completed. It receives no api_key for these calls. In the exclusively fresh reference Redis model registry, absent mapping falls back to unchanged gpt-5.4-mini (`model_registry.py:31-79,313-335`); verify no preexisting mapping drift and never populate a cache privately to force expected model. Capture actual cost/duration/timestamp without invented pricing or zero-cost/time assertions; costs are nullable/reference pricing behavior. Parent flush records organization from actual Agent; summary records run.org_id; both equal O in this nominal but are not interchangeable provenance in general.

Public normal caller GET `/api/agent-runs/<R>` before finish must independently match supported public fields/output/steps/metering totals/child_runs empty. DB-only delivery IDs, prompt version and immutable evidence are not claimed public DTO fields (`routers/agent_runs.py:80-114,473-547`). Preserve observed public representations; no timestamp normalization to manufacture equality.

## 3. SDK association and independent source/actor joins

Exactly one successful selected SDK-kind receipt, full closed ledger/status equations and no sticky/late/unbound observation, as controlling wire requires. Verify safe projected original signed claims, not caller-supplied expected headers:

`verification.outcome=verified,reason=NULL`; claims.sub fixed engine `00000000-0000-0000-0000-000000000001`,engine=true,is_superuser=true; engine_execution_id=E,engine_solution_id=S,org_id=O; delegated_user_id=U,delegated_is_superuser=false,delegated_is_provider_org=true,delegated_is_external=false;engine_global_repo_access=false. Request complete,reasonNULL,value exact `{name:"Cove Data Protection",scope:"global",solution:str(S)}`; response actual status200/complete,disconnectfalse/app_exceptionNULL and bounded bytes. Compare request path/body and selected safe receipt to actual integration/public mapping; do not add another case header or let the fixture arm pick E.

Actual engine signature binds E/S/delegated identity; Q2/Q3 bind E to R/W/D2/result; Q7 binds source bytes. No engine_solution_deployment_id or original role/original-org claim exists in the safe projection. engine_attempt_token is deliberately excluded; signer-source/token fencing alone does not prove a fresh active lease at the observed SDK endpoint. Existing broad CurrentUser internal engine route is not C2/CRED new-runtime admission. Preserve original provider/non-super flags separately from engine transport admin. Source `core/security.py:519-575`, consumer mint1479-1492, observer245-293/contract270-320.

Q7 selected read-only source joins: workflows WHERE id IN(W,P,B), exact3 belonging to S and canonical registration/grants; ALSO workflows WHERE solution_id=S must return exactly{W,P,B}, forbidding hidden extra registrations. Solution WHERE id=S active/deployment-v1/pointer D2; solution_deployments WHERE id IN(D1,D2) actual S/org/base/state/digest/source fields; actual D2 resolution/compiled manifest/source archive/runtime bytes exactly independent setup evidence. E.runtime evidence and WA hashes must match them. The concrete exclusion query is `SELECT e.id,e.workflow_id FROM executions AS e JOIN workflows AS w ON e.workflow_id=w.id WHERE w.solution_id=:S ORDER BY e.id`; require EXACT one row `(E,W)`. This covers ALL actual workflows associated with this selected Solution; no `Execution.solution_id` FK exists. Separately `SELECT id,workflow_id FROM executions WHERE workflow_id IN(:W,:P,:B)` must return EXACT `(E,W)`, including no preview/bootstrap rows even if an unexpected mutation removed their Solution association. Any Workflow.solution_id drift already fails registration Q7. Do not infer attribution for workflow_id=NULL from a nonexistent parent FK; this selected registered-tool profile rejects such a substitute child at Q2/Q3. Both tools remain actually granted/catalog-visible; preview uncalled is transcript exclusivity, not grant removal.

Actual returned K has EXACT outer keys success/observed_at/capacity, success=true and genuine UTC observed_at string. Capacity has EXACT keys success/read_only/agent_count/restore_row_count/active_restore_count/active_restore_count_by_agent/agents/active_restores/recent_restores: booleans true, all3 counts0, empty map and all3 empty lists. This is authored `recovery_testing.py:259-306` output and existing closed codec `_tool_content` at872-891, not a copied weaker schema. Compare parsed child result, exact tool-result string, model tool-role message, frozen finite Cove successful Login/two GET outcomes. Preserve datetime string exactly across these materialized results; validate UTC shape/parseability through existing contract rather than replacing it with fixture created epoch or DB times. HTTP model call IDs join first/final tool history, not DB call ID fields. AI/body fixture success alone is never proof of actual child/result consumption.

## 4. Read-only query and snapshot contract

Use independently connected supported actual PostgreSQL read-only observation. No writes, ORM fixture registration, direct consumer invocation, `FOR UPDATE`, advisory lock, schema change or attempt/delivery settlement helper. Select explicit needed columns; full private JSON/envelope is retained only within bounded comparison and never dumped. One parent,one child,six steps,two attempts,three deliveries,three usage rows andthree registrations are the selected finite cardinality gates; do not fetch/filter unbounded history to make a latest match.

| Query | WHERE / identity | Purpose |
|---|---|---|
|Q0| users.id=U; organizations.id=O; user_roles JOIN roles WHERE user_id=U; authenticated `/auth/me` before close | independent ordinary facts and exact stable pre/post DB projection; DR remains admitted snapshot |
|Q1| agent_runs.id=R; agents.id=A; agent_runs.parent_run_id=R | actual parent/output/summary/Agent; forbid delegation |
|Q2| agent_run_steps.run_id=R ORDER BY step_number,id | six steps; discover actual E from result4; join live event UUIDs |
|Q3| executions.id=E | exact result/actor/deployment/source/dispatch |
|Q4a| execution_attempts.logical_job_type='agent_run' AND logical_job_id=R | actual generic parent PA |
|Q4b| workflow_execution_attempts.execution_id=E | actual child WA; never generic alias substitution |
|Q4c| execution_attempts.logical_job_id IN UUID-typed(R,E), all types | exact selected set PA only; explicit generic child/summary/extra-parent exclusion |
|Q5| work_deliveries (queue_name,message_id) IN three exact pairs | ALL-row uniqueness/final settlement/private body; actual DS.id equality |
|Q6| ai_usage.agent_run_id=R OR ai_usage.execution_id=E | exactly3 parent-linked records and0 child-linked records |
|Q7| workflows.id IN(W,P,B) AND independent workflows.solution_id=S exact set; executions JOIN workflows ON execution.workflow_id=workflow.id WHERE workflow.solution_id=S exact(E,W); separate executions.workflow_id IN(W,P,B) exact(E,W); actual Solution/deployment UUIDs | actual immutable/source/grant chain and precise wrong/extra child exclusions; no Execution.solution_id assumed |

Polling uses short fresh read-only READ COMMITTED transactions, no cached SQLAlchemy identity-map objects across iterations. Incomplete but valid running/queued/generating evidence is pending until existing absolute case deadline; contradictory IDs/hash/result/error/failure/duplicate or extra effects latch permanent failure immediately. No retry/reseed/timeout extension converts a contradiction to pending.

Once all candidate states and required events/transcript/quiescence are present, take one **short REPEATABLE READ READ ONLY** final observation transaction for all Q0–Q7 (or a single source-equivalent joined statement snapshot under READ COMMITTED). Explicitly read back actual transaction_isolation/read_only; complete and close it promptly. This observer-only snapshot is separate from unimplemented writer-authority guards; it changes no product isolation/ownership. Beginning one long repeatable-read transaction before trigger would hide later commits and is forbidden. Sequential READ COMMITTED SELECTs alone are individually committed snapshots, not automatically one consistent multi-table snapshot. Compare fresh pre-close `/auth/me` and run public responses with this committed set and Q0 pre-trigger observations; capture incongruence and refresh only incomplete observation within the unchanged deadline, never substitute public output for private IDs or treat stable cross-request views as same-request admission capture.

After all SDK ACK/quiescence+domain/attempt/delivery/metering/event predicates succeed, request frozen finish once and remain alive for host actual API-pair stop and closed/status/ledger/host proof. Then take a NEW short read-only final snapshot via surviving PostgreSQL resources, recheck the entire selected committed set and no extra effects, and only then return pytest PASS. No public API request after close or deleted expiring Redis-key dependency. Host/API closure does not settle missing transport rows or create missing events. Root retains coordinator/cleanup ownership.

## 5. Event and causality contract

Preserve every received selected-channel publication including fanout copies, bounded by controlling wire64 total and65536 bytes/event. Before-trigger real subscription ACK and bounded prebind correlation to R remain mandatory. Detail step publication has actual run/step id; terminal/summary fanout channels have actual run/agent/org/status/summary fields. Compare source-shaped safe fields; do not deduplicate into artificial exactly-once events.

Source causal order: workflow accepted result+WA terminal+Execution result commit precedes private sync result consumed by the tool; tool result step publication follows that response; all steps/agent metering+parent result+PA terminal+summary enqueue commit occur later together. Parent terminal publisher follows this commit. Summary generation and final summary/usage commit precede its final publisher; DS ACK/settlement occurs afterward in another transaction. DW settlement has no required order after child success; policy intentionally permits dispatch-only ACK. DR/DS settled_at is DB clock and domain completed_at is application clock: no global timestamp ordering/equality guarantee across processes/clocks.

Step events publish BEFORE buffered steps persist, and publish exceptions can be swallowed. Subscriber receive time may follow commit despite publication being earlier; do not demand row absence when receiving. Event timestamps, DB step.created_at, actual observed_at and fixture created epoch are different clocks/semantics. No guessed aliases, timestamp round/truncate or synthesized absent event fields. Require terminal completed event and successful summary completed event plus actual step events and final independent rows. Missing required live event cannot be repaired by streams/DB polling or calling a publisher.

## 6. Negative predicates and remaining STOP gates

Meaningful future negatives must independently fail if: E absent/wrong/in another run step; signed SDK E/S/U/org/privilege projection drifts; child workflow/pin/source/schema/hash/result mismatches; preview/bootstrap executes or extra child/AgentRun exists; generic/workflow attempt alias substituted, only newest success selected, claim/retry/failed attempt hidden; parent message_id/token invented; any delivery missing/duplicate/claimed/poison/unsettled/extra claim, wrong DS.id or wrong encrypted body/context; summary complete without exact fields/version/usage; agent tokens normalized to combined112; detectorusage inserted; missing/swallowed metering or required publication; prebind/closure/host proof missing. Instrumentation negatives characterize the oracle, not live vendor/model or new authority compatibility.

Routine root freeze before builder: exact read-only helper/canonical-hash invocation ownership and private-envelope safe comparison; snapshot implementation and finite deadline/poll cadence using existing case120 timeout; exact raw event projection/ACK bounds; exact emitted request/step shape validation rather than invented metrics; unchanged canonical result field shape and installed process observation. Parent may ratify source-compatible nominal no-retry cardinalities1 PA/1 WA/1 claim per delivery. This packet does not ratify its own choices.

STOP if actual source cannot supply the admitted DR caller snapshot and unambiguous Q0 ordinary-facts comparison, full immutable bytes/dispatch pin, unique committed E/result step, exact terminal ledger/delivery/usage/event relationships or final closure. Historical lease/token absence is explicitly characterized, not fabricated; if the desired acceptance requires atomic same-request resolved authority capture, exact PA↔DR token custody or fresh SDK lease validation, that is a DIFFERENT mechanical/product credential prerequisite requiring root scope/authority review, not a test fallback. Stable pre/post DB/public reads do not prove an absent transient change or provide that atomic capture. Persisted context is_agent/artifact omissions likewise do not authorize production repair. No auth policy, broad generic delivery port, Rust facade or smaller lifecycle substitute is proposed.

## Selected source fingerprints

| Path | SHA256 |
|---|---|
| `api/src/services/execution/autonomous_agent_executor.py` | `0a89f7119a238c27fda445a5de7bfee47b37a07af3a53d17007b6965465b2425` |
| `api/src/services/execution/agent_workflow_tools.py` | `838aaa0b2ba2d451c1d076fa65ece71681d75594aee72a10a4b9e8f5eb3008af` |
| `api/src/services/execution/agent_run_service.py` | `b8f761a9527e8985d6a8eff9734d41ae9c6d03d4ec1be67d6c5083efa6265559` |
| `api/src/services/execution/async_executor.py` | `546989f2a9859795fb44b684e8dd1b31035ff7edefdc66f80d5812a948c6e1bf` |
| `api/src/services/execution/attempts.py` | `82ed8abc28a1a51d17ddcdaeeabf9473468ef8474c172bb01e7bc5b66b067afa` |
| `api/src/jobs/consumers/agent_run.py` | `302c36679d6dd45ab2e0d300ad5d009289d17829f0a88d234dc5033e5869ac2f` |
| `api/src/jobs/consumers/workflow_execution.py` | `c285f2d315b36bcdfc81dc0b8e23705f22a7dbb6f740311233612068e10cf218` |
| `api/src/services/execution/run_summarizer.py` | `64beba2771dc0a963f99477561637d1d9b06a993721c28fb4063484dc0460f3e` |
| `api/src/jobs/rabbitmq.py` | `5690a12319ff24c4ab7d7b8f96c8628ad50410194343c3e86405ecd70b2dc7de` |
| `api/src/jobs/postgres_delivery.py` | `da25373a5df851cae7b83d7f60e367af88aa75df4f9f06187b00c7a3241088ff` |
| `api/src/services/work_delivery_store.py` | `a208dbeb2c16fd50b6062b07362533278be00e1fe0c66aad152ab3c3c6bd4a6c` |
| `api/src/services/ai_usage_service.py` | `83fa683afdd8feb4cb6361488fc35f9af77b4fd448af95faeb7c19da50897efc` |
| `api/src/services/solutions/deployment_runtime.py` | `5282fb57793eab13861193590e3f47bcfca6901c52b38fb9b12dfc14c73cc331` |
| `api/src/models/orm/agent_runs.py` | `4c6e24ec96d5000228ce20ea4b16e8f0e87a2f4f0246417ca2dbcb8e99e9ea1a` |
| `api/src/models/orm/executions.py` | `9d398b075384ff402631b98d53a1df0cadd962baa3bccb20f3144770b6c2ce7f` |
| `api/src/models/orm/execution_attempts.py` | `3e45d487aa81839deb027eea503d08e3a9e666865d5b8106fcb7fb15d7b232a0` |
| `api/src/models/orm/work_deliveries.py` | `90a9def314c7b2c981b7d86c0624c9e83d261499ae2703ea1d5d71fba99717af` |
| `api/src/models/orm/ai_usage.py` | `f9b29c0d6feefcf0baeb3a34be0381d7b8d67f3f5f60d66710302e2d75d07386` |
| `api/scripts/agent_reference_contract.py` | `0152ff30f04ad1419f330132ceac7146b013f31931b6176ccac0212a37fc835b` |
| `api/src/routers/auth.py` | `ad664e6e4c71732fe37bf3a7273a444253d9af912305729c1b20051bb5dc9437` |
| `api/src/core/auth.py` | `5df923f2a6ff98df3cfeba25bacdbd36b42d12b51d16b9366b8678ab5ace5771` |
| `api/shared/external_access.py` | `555e8090726950aab5b65960587c9b657149810b9c172ecbc42baa71ce96810f` |
| `api/src/services/user_provisioning.py` | `4fb289c854822d82c2c952458ec7f194dbabc835d800d94d77c8401886d5cb12` |
| `api/src/models/orm/users.py` | `06f4b57bd1e1b6d6c4b8da01afaef57e918d11d6adf7ee61c416f488ed9bdff7` |
| `api/src/models/orm/organizations.py` | `dd9679e9b44988096cffaf216f5bb8eba9665ecfe311cdda5c1c007225b773aa` |

Only `/tmp/bifrost-agent-reference-lineage-interface.md` is owned/written. Smallest next step: root reviews/corrects and freezes exact query/cardinality/private observation/snapshot/event interfaces, then releases the bounded case consumer. Supported nominal remains unexecuted and all independent custody/closure/source/summary gates remain required.

Root correction2026-10-02 after independent review of9ab9eb7d: actual runtime_evidence_hash field and explicit Q4c UUID-typed known-ID generic exclusion added. Review readback remains required; no nominal/runtime acceptance.
