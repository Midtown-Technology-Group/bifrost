# C1-R public setup: bounded source-derived interface

This packet incorporates independent review of proposal SHA256
`3d35a9e5e06b6295e2d646f20784894149ccc2148c75f12ec0659924e8479398`
and root-selected event/recipe corrections. Source references below retain
their historical exact hashes and are unchanged through `da9dac5c9d4cf8b26401bd01ea20d5f9914a9a75`. Current
platform main `01cadfe09710d293a40da14d6cf4056165289e31` and workspace main
`e33f2b30c5318a9a6b21220c7c351202cb5b0cbd` are audited separately.
This releases recipe/setup direction, not the full nominal consumer: exact
committed lineage predicates, real Redis adapter, installed process custody,
privileged host interfaces and nominal closure must be frozen and verified
before execution. Physical pve-t340 runtime remains forbidden. No product
auth redesign, #1017 repair, vendor call, C2/C3 acceptance, merge or deployment.

## C1-R-CASE-SETUP evidence and release boundary

The source review and corrections select the setup direction and recipe metadata. The complete nominal consumer remains unreleased until the remaining interfaces below are frozen and independently reviewed; this is not runtime or public credential-policy acceptance.

## 1. Source custody and selected boundary

Requested reference HEAD is `65a1a5b3ed0bef317b83e68b9435ad8d39db10e3` on `/home/thomas/src/bifrost-agent-capacity-reference`, branch `test/agent-capacity-reference`. During parent-owned commits the observed HEAD advanced to `ba4f6a758a0d010b8a690a80142422494b37f50e`; the intervening diff contains fixture/material preparation and the two previously reviewed observer/server test repairs, **no selected public setup/compiler/product source change**. Platform main remains separately pinned `01cadfe09710d293a40da14d6cf4056165289e31`. Neither pin relabels old AUTH, CRED, #1017, Sopdet or runtime evidence. #1017/Sopdet acceptance remains independently gated.

The original proposal inspected canonical docs at historical worktree HEAD `17fc6d510add8bddb7a7f29bd33c0d4dab3208a9`:

| Path under `/home/thomas/src/bifrost-rust-core-mvp/docs/architecture/` | SHA256 |
|---|---|
| `rust-core-mvp-agent-reference.md` | `8185b92273ddc7917f10209be44aecb3fd245f9378783f64b8523037ead1299f` |
| `rust-core-mvp-agent-model-oracle.md` | `7b3579f6bdc134086fafda88c1898269c084b2bd25e592c01c4c001d117634cb` |
| `rust-core-mvp-agent-reference-integration.md` | `fa3c1c41c7bacef60b885dea6da40a0dc879cc9ff0c330b18a98670862c09eb7` |

Guidance read/reused: repository AGENTS and applicable platform rules; mtg-engineering-flow `2026-09-30.1`, router `2026-10-01.1`, selected design package `2026-10-01.5`. This task's explicit source-only constraint leaves supported execution gates open.

Nominal remains the full selected autonomous/model/workflow-tool/summary path: BOTH unchanged tools are installed, granted and present in the actual model catalog; only capacity is called once. Preview and inert bootstrap never execute. No chat/delegation/MCP/knowledge/approval migration, auth repair, source extraction, direct ORM registration or private entity seed. Fixture signatures associate observations; independent committed DB lineage proves execution relationships.

## 2. Data and identity rules

Root's real nominal runner obtains fixed RO assets `/app/reference-assets`, the independently verified private source binding and existing fixed model-input file. It verifies provenance and every asset byte before compiling. The source commit supplied to installation is the **actual clean committed platform candidate containing these carried assets**, obtained from the host-pinned binding; it is not the original workspace SHA, an invented SHA or an assumed `.git` checkout inside the runner. Retain declared-to-actual identity mappings privately and report only approved sanitized evidence.

Solution, user, role, AI connection, profile, Agent and run IDs are actual public response IDs, independently read back. The two deployment IDs are fresh caller-selected UUIDs in supported public deployment paths. The two authored tool UUIDs and bootstrap UUID must match their decorators exactly. The retained YAML Agent UUID is original asset identity, not an argument accepted by AgentCreate and not the actual generated Agent UUID. Never normalize a returned wrong identity into the expected one.

No raw bearer, MFA secret/invite URL, model key, Cove password/visa or authorized integration-config response enters assertions, logs, receipts or artifacts. Failures emit static bounded classifications. Setup HTTP clients use fixed actual API origin, `trust_env=False` and redirects disabled. Synthetic model/Cove HTTP is only the separately frozen internal reference transport; this is not general credentialed-HTTP/ambient-environment compliance proof for unchanged authored Cove code.

## 3. Public principal and role setup

Source: `api/src/routers/auth.py:1230`, `users.py:270`, `roles.py:186,444`; `models/contracts/users.py:90,229,286`; `services/user_provisioning.py`/the provider migration; existing public-only login/MFA pattern `api/tests/e2e/fixtures/setup.py:52-96`.

1. Require the owned fresh stack to contain only independently inventoried migration/startup system state: actual public `/auth/status` must report needs_setup=true; no qualifying human, default-user bootstrap or selected preexisting business/AI/deployment/tool/run state is permitted. Do not DELETE/seed state to obtain eligibility. The fixed System/provider rows are permitted migration state. Register the first synthetic admin with public `POST /auth/register` `{email,password,name}` (201). First-user provisioning makes it a real provider-org platform admin. Authenticate via `POST /auth/login` form `username/password`, then actual public `/auth/mfa/setup` and `/auth/mfa/verify` using the returned MFA token and a real TOTP if required by actual settings. Do not disable MFA, create JWTs or invent nominal claims. Do not copy helper failure formatting that exposes raw responses.
2. Public admin `GET /api/organizations` verifies the actual provider row: UUID `00000000-0000-0000-0000-000000000002`, `is_provider=true`. This is source migration identity, not permission to create a substitute provider. Capture public `/auth/me` and actual read-only user/org flags.
3. Public `POST /api/users`: unique synthetic email/name, explicit provider UUID, `is_active=true,is_superuser=false,is_external=false,invite=false,trigger_automation=false`. Retain returned actual user ID. Current source ignores the invite flag and creates a real invite record regardless; keep its URL private and do not claim no invitation row. The password field on this admin route does not establish a password.
4. Require independently observed testing/development mode before completing this pre-created user's registration through public `POST /auth/register` with the same email and fresh password. Require returned identity/org/non-super flags to match. The registration response's `roles=["authenticated"]` is not evidence of assigned Role IDs.
5. Public `POST /api/roles` `{name:<unique>,description:<fixture>,permissions:{}}` returns the actual globally defined Role ID. `POST /api/roles/<id>/users` `{user_ids:[<actual user ID string>]}` returns204. GET role/users and user/roles verify the exact association; it conveys no platform-admin privilege.
6. Authenticate the normal user **after** assignment through actual login/MFA. Capture original user ID/email/name/org/non-super/provider/non-external facts and literal role values from real resolved source/public readbacks. For this one-role case require exactly `["authenticated", <actual Role.name>]` in that order: `get_user_roles` selects Role.name, not Role UUID. Keep the actual Role UUID for workflow grants and UserRole joins. Do not coerce or sort token role strings; other source flows have additional literals. Public `/auth/me` plus independent selected user/org/role reads establish provenance; unverified JWT decoding is not authentication evidence.

Nominal uses this normal provider caller in its own provider organization. Original and effective organization are equal in this case; it does not prove historical original-org separation or foreign-effective authorization. Existing AUTH privilege propagation is preserved, including the distinction between original provider/non-admin flags and the engine transport's administrator claim. Later authorized foreign-effective tests are separate, not silent setup expansion.

## 4. Exact unchanged source set and two public immutable installs

All runtime/repository path mappings use real `.py` filenames, not `.py.source`. `recipe.files[path]` is `test-fixtures/agent-reference/` plus the same runtime path. Read exact bytes; no template substitution, fake initializer, retag or private checkout fallback.

| Runtime path | Bytes | SHA256 |
|---|---:|---|
| `reference/bootstrap.py` |219| `d4e12e1ab615015aa9c3dad267eb7f99096e25a69c96eac9183554d47b2117bc` |
| `features/cove/__init__.py` |23| `0498ef9c35aad1478ab89f334894b7bd3bb418b6901356530ad3e9384fb595a0` |
| `features/cove/workflows/recovery_steward.py` |17642| `71bc320261ab6f6da2abb3b4f1bc3335fc9925ae28ac9e922a1f260e378529eb` |
| `features/cove/workflows/recovery_testing.py` |32957| `cf0876ecf4fd793f709e6070771e5c0f84dce34128581cf68049a1cce844879a` |
| `modules/cove.py` |60566| `778ec86607025eabf60efb768cf7f05b641a13532ea1686c19709211f5f52133` |

The four authored workspace files originate at `e8605dc8edb6df8a997c171b534b324ac7ebd8ec`. The fixture-only219-byte bootstrap imports exact unchanged `features.cove` before declaring `@workflow(id="81c8961e-cedb-4d51-831b-d29a75e99cb9",name="c1_r_reference_bootstrap")`; it returns `{"fixture_only":True}` if called, but the selected Agent has no bootstrap grant and it must remain uncalled. Stage1 expected compiler closure is the first two paths (242bytes); stage2 is all five (111407bytes). These are source-derived expected gates, not executed compiler closure proof.

Source interfaces: SDK `api/bifrost/solution_delivery_review.py:107-177,328,524`; server DTOs `models/contracts/solution_deployments.py:179-260`; public routes `routers/solutions.py:690` and `solution_deployments.py:658-778`; services `solutions/initial_workflow_install.py:335` and `workflow_revision.py:137-170`.

### Recipe and compilation

Only later, inside the supported actual runner, call the canonical **static AST** compiler/reviewer with actual bytes and selected recipe; never import authored modules or execute their function bodies during setup. `schema_version="bifrost.solution-workflow-delivery/v1"`, actual solution_id, exact files map, selected workflows, and empty shared_tables/resources/root_file_bindings. Require review's exact source closure and registrations, including previous-recipe compatibility. `live_state_verified=false/runtime_verified=false` are truthful static review flags; public activation and independent reads provide separate evidence.

Registrations contain explicit authored UUID/path/function_name, actual provider organization_id, root-reviewed runtime_bounds and controls. Preserve all canonical names/descriptions/categories/tags/types/tool_description, root and parameter JSON schemas, defaults, source_ref/source_hash/portable_ref and UUID. Do not discover/register other decorated functions merely because they exist in the closure.

Stage1 selects only bootstrap. Stage2 retains bootstrap with exactly unchanged protected recipe fields and adds BOTH authored tools:

| UUID | Function/name | Schema |
|---|---|---|
| `0760d416-be3a-4a33-b540-5b6b0658075d` | `recovery_steward_inspect_capacity` | no arguments; canonical object/properties{} and additionalProperties=false |
| `93b7f115-de11-444d-8309-7aee9bac2bec` | `recovery_steward_preview_restore` | required device_id:int,recovery_agent_id:int,vm_name:str,halo_ticket_id:int; defaults cpu_count2,ram_size_mb4096,vhd_path `E:\` |

Both category `Cove Data Protection`, unchanged read-only authored descriptions/tags; `type=tool`. The full canonical root schema includes draft2020-12 `$schema`; the actual AI tool conversion removes that root marker and emits names `cove_data_protection_recovery_steward_inspect_capacity` and `cove_data_protection_recovery_steward_preview_restore`. Compare these distinct projections; never hand-normalize a mismatch.

Root-selected reviewed fixture metadata controls (not authored enforcement or production policy): bootstrap bounds duration60/external_calls1/records_read1/output_bytes4096; both tools duration60/external_calls16/records_read200/output_bytes4096. All controls display_name=null,execution_mode=async,timeout_seconds60,cache_ttl_seconds0,time_saved0,value0,endpoint_enabled=false,public_endpoint=false,allowed_methods[POST],disable_global_key=false,access_level=role_based; bootstrap role_ids[], tools role_ids[actual Role ID]. Retry exact default `{version:"execution-retry/v1",enabled:false,max_attempts:2,retry_on:[]}`. Optional bounds max_records_written/max_output_rows/max_pages remain null. Compiler-derived effects/source_enforced_bounds/source_requested_bounds must remain their actual canonical values, not invented enforcement. These reviewed metadata bounds are not a claim of authored source enforcement or preview execution bounds. These exact literals are selected for the bounded nominal capacity fixture only. Unexpected preview invocation must fail the frozen model transcript; no preview execution-bound proof is inferred.

### Public operations, required response checks

| Order | Public request | Actual response/readback |
|---|---|---|
|1| POST `/api/solutions` `{slug:<unique>,name:<fixture>,organization_id:<provider>,allow_outbound_access:false,allow_inbound_access:true,git_connected:false}`; omit/null git selectors | actual Solution ID; initial repo-v1 and null active pointer; GET actual solution |
|2| POST `/api/solutions/<sid>/deployments/<d1>/initial-workflow/candidate` `{source_commit_sha:<actual candidate>,reviewed_recipe:R1,files:[{path,content_base64}],resources:[]}` |200 InitialWorkflowInstallInspectResponse, exact sid/d1/org/source/workflow_ids/source_hashes/evidence_id, state ready |
|3| POST same d1 `/initial-workflow/preflight` `{reviewed_recipe:R1}` | exact independently repeated identities/hash set/evidence ready |
|4| POST same d1 `/initial-workflow/activate` `{reviewed_recipe:R1,expected_evidence_id:<actual>}` | state active; GET `/deployments/active` actual pointer d1/deployment-v1; GET `/deployments/<d1>` actual compiled_manifest_hash and immutable keys |
|5| POST d2 `/workflow-revision/candidate` `{expected_active_deployment_id:d1,expected_active_manifest_hash:<actual GET hash>,source_commit_sha:<actual candidate>,reviewed_recipe:R2,files:<all5>,resources:[]}` |200 SolutionSourceRevisionInspectResponse, exact sid/d2/d1/source, all3 workflow IDs/source hashes/evidence_id, no subscriptions, state ready |
|6| POST d2 `/workflow-revision/preflight` `{expected_active_deployment_id:d1,expected_active_manifest_hash:<actual>,reviewed_recipe:R2}` | exact identities/source/hashes/evidence ready |
|7| POST d2 `/workflow-revision/activate` same preflight body plus expected_evidence_id | actual active d2; GET active and d2 again; actual parent/base/hash readback |

Public stage1 explicitly rejects tools, so direct initial installation of the unchanged two tools is unsupported. Revision supports the exact retained-bootstrap/full-tool recipe. Do not use mutable zip or loosen registration to bypass this boundary. The activation's real reviewed service writes/projects rows and CASes the active pointer under solution write lock; the test does not replicate those writes.

Any unsupported closure/registration, tool collision, stale pointer/evidence, conflict422/409 or lost lock503 stops this owned attempt. No alternate route, reimport, retry/rebase onto unexpected deployment or protected-field repair to make it pass.

### Independent immutable observation, distinct from public setup

Parent explicitly permits selected actual read-only DB/storage observation, consistent with `tests/e2e/platform/test_initial_workflow_install.py:284` public-stage/storage-read precedent. Public deployment GET exposes hashes/keys, **not** the complete manifest/resolution map/source bytes. Public workflow list `/api/workflows?scope=<provider>&type=tool` exposes WorkflowMetadata/parameter projections, **not** the entire stored canonical root JSON schema.

After each activation independently read actual SolutionDeployment row/storage using actual sid/dN: exact archive path set/bytes; individual runtime bytes; canonical compiled manifest bytes/hash, resolution map/hash, source_artifact_key/runtime_storage_prefix, source commit, registered Workflow source_ref/portable_ref and parameters_schema, role joins and solution active pointer. Correlate actual immutable digest chain to the canonical independently compiled output. Require exactly the selected3 registrations in this Solution; bootstrap ungranted; tools solution-managed and role-granted. Existing E2E in-memory services/mockS3 are not the reference path. Never write these observations back or present DB/storage fields as if returned publicly. If read-only real storage/DB observation cannot retrieve the full chain, stop rather than source-introspect the live process or invent an API.

## 5. Public AI setup and actual Agent creation

Source: `routers/ai_models.py:34,131,261`, DTO `contracts/ai_models.py:24,59`, `services/ai_model_service.py:665-695`; Agent DTO `contracts/agents.py:56`, route `routers/agents.py:416` (uuid4 actual ID), tool/read routes596/675/1102.

1. Require empty public AI connections/profiles/assignments before creating anything. `POST /api/admin/ai/connections` `{name:<unique>,provider:"openai_compatible",api_key:<private synthetic model_key>,endpoint:"http://scheduler-fixtures:8080/__agent-reference/<case_id>/model/v1"}` ->201 actual Connection ID; verify provider/endpoint/api_key_set=true by public GET, never key echo.
2. `POST /api/admin/ai/profiles` `{name:<unique>,connection_id:<actual>,model:"gpt-5.4-mini",capabilities:null,enabled_for_chat:false,default_max_tokens:null,failover_profile_id:null}` ->201 actual Profile ID. First-profile source forces enabled_for_chat=true and adds ALL six assignments automatically. Public GET connections/profiles/assignments must show exactly one selected profile/connection and `primary,summarization,tuning,image_generation,video_generation,chat_default` all mapped to that actual profile. This is observed source behavior, not a request to broaden executed modalities. Do not create a replacement profile, run verify/model-list/probe endpoints or preseed detected transport; those add transcript effects.
3. Verify retained `declaration.yaml`1718bytes SHA `b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914` before parsing its values. `POST /api/agents` exact declared name `Cove Recovery Steward Escalation Analyst`, description and prompt scalar including final LF, channels[chat],access_level authenticated, explicit provider organization_id, tool_ids BOTH exact authored UUIDs, delegated_agent_ids/role_ids/knowledge_sources/system_tools/mcp_connection_ids empty, llm_profile_id actual, llm_max_tokens8192,max_iterations16,max_token_budget32000. Do not send ignored YAML id/llm_model/is_active or modify the retained YAML. Public create actually supplies is_active=true and generates a new Agent UUID; require these actual values on GET.
4. Public admin tool list, actual Agent GET and `/api/agents/<actualid>/tools`, plus normal caller `/api/agents/accessible-tools` verify full catalog/grants and absence of bootstrap, extra tools/system/delegation. Independently compare stored canonical schemas to the compiler and actual model-wire tool arrays. No Solution-managed Agent claim: this public AgentCreate does not attach it to a Solution.

The exact preview catalog/declaration remains accessible as authored. A named preview call in this nominal case fails as unexpected transcript; it does not revoke its grant or claim preview behavior is proved.

## 6. Public global synthetic Cove integration and real secret semantics

Source: `routers/integrations.py:817,1191,1244`; DTO `contracts/integrations.py:76`; `repositories/integrations.py:436`; ORM `models/orm/config.py:38`; public config DTO51/router126/repository333.

1. Public admin POST `/api/integrations` name exactly `Cove Data Protection`, fixture description, four required config_schema entries: partner_name/string,username/string,password/secret,base_url/string. No OAuth, organization mapping, PSA aliases or unrelated config. Record actual generated Integration ID and actual public schema identifiers.
2. PUT `/api/integrations/<id>/config` `{config:{partner_name:"C1R Synthetic Partner",username:"c1r-reference@example.invalid",password:<fresh synthetic password>,base_url:"http://scheduler-fixtures:8080/__agent-reference/<case_id>/cove"}}` creates global defaults (organization_id null). Unchanged module normalizes base_url by appending `/jsonapi`, preserving the selected fixture prefix.
3. **Baseline semantic gap:** integration schema type SECRET does not itself set Config.config_type or encrypt; `_save_config` stores raw `{value}` and Config defaults STRING. This case must not accommodate password as STRING or infer encryption from the schema label.
4. Root explicitly accepted the existing public admin remedy for ONLY this fresh synthetic password: `GET /api/config?scope=global`, select exactly one actual row with integration_id=<actual>,key=password,scope GLOBAL,org_id null; `PUT /api/config/<actual observed rowid>` `{type:"secret",value:<same fresh password>}`. DTO/service support this exact update; repository encrypts supplied nonempty value with effective SECRET, sets ConfigType SECRET, preserves omitted key/org and integration/schema mapping. No product/auth policy change.
5. After PUT success issue a fresh `GET /api/config?scope=global`; the PUT response contains stored encrypted material and must remain private. Select the same actual row ID; public GET readback requires same rowid/mapping/org, actual type secret and masked `[SECRET]`; independent read-only row proves exact integration/schema relation and stored type. Authorized integration GET compares returned decrypted value **privately** to the fresh input if used; later actual SDK integration result and Cove Login password equality provide separate transport evidence. A mask/type alone is not fabricated decryption proof. Config update touches existing generic cache behavior; this selected exercise is not proof of arbitrary integration/config policy safety.

Never use a raw-string password fallback, seed encrypted bytes directly, substitute a mocked `integrations.get`, create a live vendor OAuth link, or send that password/model key outside the fixed internal fixture. Parent's approval here does not approve CRED public H/R policy.

## 7. Before trigger, real subscriptions and actual worker enqueue

Source: `routers/agent_runs.py:1026`, DTO `contracts/agent_runs.py:103`; `services/execution/agent_run_service.py` real durable enqueue; `core/pubsub.py:211,393,453`; selected real consumer, autonomous executor and summarizer. Queue/delivery authority remains the existing Python path; no direct consumer/service/tool invocation.

All setup, independent source/pointer/catalog/caller reads and fixture arm happen before the one public trigger. Existing fixture arm associates actual identifiers/digests after independent observation, never manufactures truth. Root lane must have already independently verified effective POSTGRES backend on all six product processes and real workflow/agent-run/summarize consumers, custody/gates, model input/secret ownership and readiness. API status files cannot substitute for actual foreground process identity.

Subscribe to actual Redis BEFORE enqueue: PSUBSCRIBE `bifrost:agent-run:*` (actual run UUID not yet known), SUBSCRIBE `bifrost:agent-runs:all` and `bifrost:agent-runs:org:<actual provider UUID>`. Wait for actual subscribe/psubscribe ACK with acknowledgement messages visible; do not infer readiness from calling subscribe. Maintain bounded prebind publications, then bind to the actual returned run UUID/Agent/org. Use the [bounded collector interface](rust-core-mvp-agent-event-collector.md): retain every selected-channel publication within one combined64 pre/post-bind budget, at most65536 raw bytes each and4194304 payload bytes aggregate. Overflow/malformed/foreign identity permanently fails; no deduplication or discarded quiet overflow. The three genuine subscription ACKs are separately bounded and must complete within the selected3-second monotonic readiness deadline before arm/enqueue. This is not a Redis allocator/decoded-object memory guarantee. Pattern and exact channels may duplicate the same genuine publication; retain channel identity and compare counts/content accordingly rather than inventing deduplication or exactly-once delivery.

Normal caller `POST /api/agent-runs/enqueue`:

```
{"agent_name":"Cove Recovery Steward Escalation Analyst",
 "input":{"task":"Independently inspect capacity evidence only. No authoritative device or recovery-agent mapping is supplied; do not preview a restore."},
 "output_schema":null}
```

Require202 `{run_id:<actual>,status:"queued"}`; paused200 or any alternate response stops. Do not replace this with `/execute`, direct tool execution, TestModel, consumer mocks, hand-built child results or synthetic events. Actual queue payload captures authenticated/resolved caller flags/roles and org; preserve that original snapshot, actual run org/context and child SDK transport claims separately. A later mutable User row is not reconstruction of original admission facts.

Real transcript: bounded Responses404 -> Chat detection pair(s) under existing oracle; two nonstream agent calls, exactly one capacity child, SDK integration lookup, actual Cove Login and two read-only capacity GETs, actual returned capacity/datetime result into the second model request, final output and real separate streaming summary. No preview, bootstrap, verify/profile probing or extra effect. Capacity's dynamic observed_at is actual result data, not a hardcoded fixture timestamp.

Require genuine agent_run_step and terminal/summary agent_run_update publications. Step broadcast may precede step persistence (`autonomous_agent_executor.py` reference ordering); these are live events, not committed event rows or universal commit-before-broadcast proof. Independently compare final committed AgentRun/AgentRunStep/AIUsage, child Execution/WorkflowExecutionAttempt, generic execution attempt, selected WorkDelivery/summary_delivery_id/ownership-fence-state, exact asked/did/answered/confidence/confidence_reason/metadata/summary version and actual public response. Transport delivery settlement is distinct from workflow result completion. Missing events cannot be repaired by DB polling alone.

After actual domain/child/summary settlement and acknowledged SDK quiescence, follow the already frozen fixture finish -> host exact API-pair stop -> actual closed ledger/status/host-disposition protocol **before pytest returns**. No API restart, post-close public requests, fake receipt, independent fallback shutdown or result returned before closure. Existing120-second pytest timeout remains unchanged; host240 does not extend it. Owned lane teardown/empty-resource proof is a separate required host gate, not case-created success.

## 8. Freeze/stop checklist and proof plan

| Remaining interface | Root may choose routinely / exact proposed next choice | STOP or escalation |
|---|---|---|
| Recipe control/bound literals | Use root-selected table in §4 and complete R1/R2 dumps including disabled retry; static source-compatible fixture controls | Do not claim authored enforcement; source review must show revision accepts both unchanged tools/full closure; otherwise genuine supported-install prerequisite, not retag/seed |
| Redis collection | Implement the linked collector contract and independently review the concrete real Redis adapter/deadline/closure before nominal execution | Unknown/unbounded buffer or missing genuine publication is not acceptance; no synthetic events |
| Independent DB/storage reader | Use existing selected read-only models/storage and actual immutable keys; complete digest/schema/grant/lineage predicates | Any missing full-byte chain or required ORM WRITE/source introspection is out of scope and STOP |
| Principal setup | Public testing-mode registration/login/MFA and normal provider role as §3 | Unexpected production registration gate, first-user state, provider row/claim or privilege drift STOP; no auth-policy fix |
| Global password | Parent-approved exact public row-ID SECRET update §6 | Wrong mapping/type/readback, inaccessible decryption/actual Login evidence STOP; no raw STRING accommodation |
| Existing host integration | Consume frozen real custody/readiness/finish interfaces only | Missing provision/init/reset/host coordination or observation does not become accepted through case construction; root owns this remaining implementation/runtime gate |

Next release prerequisite: freeze exact committed reader and host-custody interfaces, then independently review the concrete real subscription and root-owned case consumer. Supported future verification is selected units through `./test.sh`, `./test.sh agent-reference`, `./test.sh quality api`, and clean literal pre-pr. All remain future supported execution, not performed here. Tests must go red for wrong returned IDs/source commit/archive byte/schema/grant/pointer, missing bootstrap retention or extra grant, wrong original flags, wrong secret row/type, stale/extra AI assignment, missing subscription ACK/event, fake child/summary lineage, unexpected preview call or absent real closure. No new public API, auth semantics or narrowed agent lifecycle is needed merely to implement the proposed setup; any discovered actual requirement for one returns to root rather than silent repair.

## 9. Selected source fingerprints

SHA256 read directly from the selected reference source (unchanged between65a1 and observed ba4f):

| Path | SHA256 |
|---|---|
| `api/bifrost/solution_delivery_review.py` | `e2c354e433f9eda1dc638bbecd0e1e6b3b1d775ee1876cf99db42dede2636979` |
| `api/src/services/solutions/initial_workflow_install.py` | `ff3ab2147e7db63ad0a70f5fc142e001dca620da3379f54237be48ef57f0b5c6` |
| `api/src/services/solutions/workflow_revision.py` | `92c5940575133546618b8556f1a41f961778dfcbe0637c002017fca45ca0e09b` |
| `api/src/routers/solution_deployments.py` | `c5a7c964ba251ce0918bdc8547564712fdbb5e37d6e07ffcf5aebc1a14c8cce0` |
| `api/src/routers/agents.py` | `10940cbcbd239b7cb82d1eb35067f53978fee497dfbd64dffd89728805de514c` |
| `api/src/routers/agent_runs.py` | `e89a1eb5050e96c4184380bfd30f9bb6761d12d4e4587c0c227431d752f77c86` |
| `api/src/routers/ai_models.py` | `40bfc37c99decbcf4fd1562e56d7677d9a45f3e713e66efe17124cef40036429` |
| `api/src/routers/integrations.py` | `2bcc129743bba168f287e13f92305e96ad3651209128ad6c51b615f1eef882fc` |
| `api/src/repositories/integrations.py` | `8650e8b34ba09a87af47eb54aba9cb174fcf82712f6cf5516a89d92c28baae9e` |
| `api/src/routers/config.py` | `e0d40622214e8bc6b4c94509c6551e425bda3b767a79ecba6f9465b8f6d56119` |
| `api/src/repositories/config.py` | `0a1b24c3716ae91ae51f946f8005e20cb0c74d0898ec1eb9f7457722792560e8` |
| `api/src/core/pubsub.py` | `14435ac03fcc684194ed2ce4ab2deff757a34d2efd3f82da4ac18d654c92296d` |

This packet contains reviewed source direction and selected metadata, not independently reviewed implementation acceptance. Parent remains architecture/scope authority; existing narrow private CRED foundation and current C1 codec results do not approve new public credential policy or Rust runtime authority.
