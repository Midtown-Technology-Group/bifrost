# Selected agent Prepare/Prepared facts — mechanical contract 0.2.0

Status: the accountable architect ratifies this private mechanical data contract after independent source review. It preserves the existing control-only profile; it does not release runtime orchestration, provider credentials, database authority or Stage1. The selected model loop and summary computation remain Python.

Reviewed proposal SHA256 `256eb9d96a7a3675261ff2c9a6b9ff51894a1e07b070c090d517e0f163ed34e6`; independent review `406b47e639e903c52287f38196262d81c392e9021df7c8b05cbb1bd9e927a905`. The earlier outline omitted measured readbacks, full closure, caller/limit bindings, provision slots and codec semantics; this version supplies them. Actual selected UUIDs, catalog/configuration, closure and image measurements remain unobserved.

Source: platform main `2f5b77a7db3bcc6b9c4c11d624b533f239676e49`, P0 `9f35278f90ba3757318ac9f07895bda9957330de`, controlling architecture `09b8339b48808e3f542904eebbd5a090794ba2a7`. Workspace source audit at `8aea9d1fc3c70ffaf74557b5f384ad200f150d16` preserves all ten selected reference assets and the public-boundary invariant; it is source evidence, not runtime proof. Exact inspected platform/P0 hashes follow below. [Authority sequencing](rust-core-mvp-runtime-authority-sequence.md), [freeze audit](rust-core-mvp-runtime-freeze-audit.md) and [dependency gate](rust-core-mvp-dependency-profile.md) remain authoritative for their separate concerns.

## Codec and negotiation (closed proposed extension, not new protocol version)

Reuse P0 `bifrost.runtime/v1` frame: protocol/message_id/sequence/session_id/correlation_id/body;
control.py:172–182,306–443 supplies binary length framing, strict UTF8,16MiB complete-frame cap,
depth64, safe integer maximum9007199254740991, canonical lowercase hyphenated control UUIDs,
positive sequences/attempt numbers/durations, duplicate-object-key rejection, finite JSON numbers.
Proposed negotiated capability `agent_prepare_profile/v1` adds Prepare/Prepared bodies; current
control_profile/v1 remains control-only and MUST reject unsupported frames. No implicit P0 acceptance.
The private capability name is ratified here; negotiated codec implementation requires separate review and tests, not adapter-owned globals.
Every structural DTO has exact required keys plus explicitly listed optional keys; unknown keys reject.
Business input/schema/tool parameter JSON maps preserve legal arbitrary schema keys; these are not open authority bags.
New control/identity/count integer fields reject bool/fraction/negative lexical zero and use P0 UInt/Positive
as specified below. Existing business/evidence signed integers retain source DTO semantics within safe range;
no source signed value is silently converted to control UInt.
Finite floats allowed only existing JSON/business fields; no float canonicalization, Decimal-to-float or normalization.
Strings reject surrogate codepoints. String/array/object sizes share WHOLE16MiB frame/depth64 bounds;
no additional invented per-field public caps/truncation. Preserve any existing DTO field limits at actual admission.
Unsupported/oversized/non-JSON selected private profile routes incumbent BEFORE admission/Start;
never fallback/redispatch after possible effect. Public payload/dependency behavior unchanged.
`prepare_payload_sha256` means lowercase bare SHA256 of COMPLETE transmitted Prepare frame UTF8 JSON
payload bytes (excludes four-byte binary length prefix). Parent retains exact bytes; child hashes same received bytes.
No reserialization/hash of normalized floats/objects, no invented prepared_facts digest over unspecified data.
Prepared echoes this digest; parent binds its observed Prepared raw bytes separately for custody.
New measurement/raw-byte digest fields use bare64lowerhex. Existing source evidence keeps prefixed/bare digest
syntax and omitted optional keys EXACTLY as source admits, never blanket converts to P0 digest syntax.
All failure diagnostics static/nonsecret; no raw frames/secret/prompt/customer payload logging.

## Prepare body — typed field matrix

R=required present; N=nullable when explicitly stated; O=optional absent retained. Other values cannot be null.
All listed objects closed; maps/arrays below use whole-frame bounds. No ORM/client/callable/pickle crosses pipe.

| Field | Presence/type/meaning |
|---|---|
| type | R string exact Prepare; proposal within existing body discriminator |
| binding | R object: supervisor_incarnation_id,session_id,prepare_message_id canonicalUUID; process_identity nonemptystring; logical_job R{kind:agent_run,run_id:UUID}; attempt R{kind:agent_execution_attempt,attempt_id:UUID,attempt_number:Positive}; expected_artifact_id nonemptystring; expected_image_digest R string-or-null. Mirrors P0 session.rs:8–38; expected labels only |
| source_baseline | R object: reference_source_main GitSHA40; authored_workspace_revision GitSHA40; selected_manifest_sha256,adapter_source_sha256,mechanical_contract_sha256 bareSHA256. Parent provenance labels, not deployment readback |
| staged_closure | R closed object described below; complete parent-admitted entrypoint/source/dependency/resource/namespace descriptor, not only hash labels |
| admission | R object: admission_snapshot_id UUID; original_caller R caller-object-or-null; effective_context R closed object; original_caller_provenance_sha256 bareSHA256. Null original caller only genuinely callerless admission; missing provenance cannot be reconstructed |
| limits | R object: configured_max_iterations Positive; configured_max_token_budget Positive; llm_max_tokens R Positive-or-null; parent_duration_seconds Positive; parent_deadline_monotonic_ms UInt. Parent deadline in supervisor's clock domain; child cannot assert shared clock origin |
| agent | R object: id UUID,name string,is_active bool,organization_id R UUID-or-null. is_active only snapshot used for existing paused computation, not renewed permission; organization usage association is not effective authorization org |
| prompt | R object: system_prompt string,input_data R JSON-object-or-null,output_schema R JSON-object-or-null. Raw falsey values retain source prompt behavior; no new redaction/rewriting |
| tools | R ordered array{ name nonemptystring,description string,parameters JSON-object,workflow_id UUID }; exact admitted ordered definitions+mapping for both capacity and preview. No entitlement booleans |
| model_chain | R nonempty ordered array{slot Positive,profile_id UUID,provider closed openai/anthropic/google,model nonemptystring,provider_connection_id R UUID-or-null,default_max_tokens R Positive-or-null,anthropic_prompt_cache_supported R bool-or-null,openai_transport R responses/chat_completions/null}. Consecutive1..N slots, unique profile IDs, exact source primary→fallback order; unsupported actual config selects incumbent pre-admission |
| provision_slot_id | R canonicalUUID opaque reservation, unique immutable within exact admission/session/typed attempt/Prepare. No credential or source lookup authority |
| loop_profile | R nonemptystring exact parent-admitted selected implementation source identity. Not configurable retry/policy flags; root RunUsage initially zero |

Original caller object REQUIRED fields: user_id UUID; email/name strings; organization_id UUID-or-null;
is_superuser,is_platform_admin,is_external,is_provider_org bool; roles array of literal strings;
verified_role_ids O arrayUUID only if actual source requires/evidences those separately. Preserve literal flags/roles
without privilege or UUID inference; no implicit authenticated role. Actual selected snapshot provenance required.
Effective_context REQUIRED: organization_id UUID-or-null,solution_id UUID-or-null,solution_install_id UUID-or-null,
agent_id UUID,agent_run_id UUID,caller_user_id UUID-or-null,caller_email/name string-or-null,
accounting_org_id UUID-or-null,trigger_type string,trigger_source string-or-null,event_delivery_id UUID-or-null,
artifact_workspace_id string-or-null. Values from actual admitted context, no arbitrary extension bag.
This table names separate original/effective/accounting roles; it does not enable currently absent execution fields.
Unsupported/missing original flags/context evidence must select incumbent before admission; do not guess from User/targetorg.
Parent duration is agent.max_run_timeout or1800 (consumer:44,418), separate from configured loop budgets.
StartAuthorization uses same duration (P0 session.rs:40–45); trusted parent enforces deadline/process close.
Child elapsed observations remain advisory; exact hard-deadline scheduling/clock semantics held for supervisor interface.

## Complete staged closure descriptor, evidence preservation and parent-only source

staged_closure REQUIRED: entrypoint{kind:autonomous_agent,adapter_module:string,adapter_function:string,
registered_agent_id:UUID}; execution_evidence{kind:solution_deployment|workspace_release|parent_admitted_agent,
data:closed-kind-object}; entries array; namespace array; runtime_expected object.
Agent loop executes adapter, not authored workflow tenant entrypoint before Start. Capacity/preview source stays
PARENT-owned child-workflow closure, summary stays separately parent-triggered. Neither is imported by agent Prepare.
entries contains ALL admitted child-consumed staged files/resources and dependency artifact files, ordered by
unique entry_id:string, each R{entry_id,path:string,kind:adapter_source|dependency_file|resource_file|manifest_file,
expected_byte_length:UInt,expected_digest:string}. Digest syntax matches particular source evidence unchanged.
Zero-byte file is legal; directories/implicit packages described in namespace, not fake zero-byte file hash.
namespace ordered R records{module_name:string,kind:module|package|implicit_namespace,
staged_paths:arraystring,entry_ids:arraystring,expected_origins:arraystring}. Complete admitted namespace/package
mapping includes dependencies/resources; no full-closure inference from just entrypoint hash or selected4files.
runtime_expected uses exact P0 RuntimeArtifact fields: artifact_id,image_digest:string|null,
interpreter{implementation,version},sdk{distribution:string|null,version:string|null},
requirements_lock_sha256:string|null,runtime_protocol exact bifrost.runtime/v1 (control.py:101–117).
Expected runtime data cannot certify actual installed byte/path/hook closure.

Existing evidence is tagged, exact-kind closed; the following REQUIRED sets use actual names without shorthand.
Solution deployment_runtime.py:73–100:
- UUID: solution_id,solution_deployment_id.
- string digests unchanged: bundle_hash,compiled_manifest_hash,workflow_source_hash.
- string|null: git_commit_sha,workflow_organization_id.
- strings: runtime_storage_prefix,workflow_portable_ref,workflow_name,workflow_function_name,workflow_path,
  workflow_execution_mode,workflow_type.
- source integers within safe range, existing DTO constraints retained: workflow_timeout_seconds,
  workflow_time_saved,workflow_cache_ttl_seconds; finite source number: workflow_value.
- bool: solution_global_repo_access; map path→existingdigest: deployment_source_hashes.
- OPTIONAL only: workflow_runtime_bounds integer-map,workflow_parameters_schema JSON-object.
Workspace workspace_release_runtime.py:408–449:
- fixed schema_version=bifrost.workspace-release-runtime/v1.
- UUID: workspace_release_row_id,workspace_release_artifact_id,workflow_id.
- prefixed digest strings: workspace_release_id,workspace_release_effective_manifest_id,
  workspace_release_governed_manifest_id,workspace_release_registration_manifest_id.
- strings: workspace_release_runtime_storage_prefix,workspace_release_source_commit_sha,
  workspace_release_source_tree_sha,workspace_release_registration_state_fingerprint,workflow_name,
  workflow_function_name,workflow_path,workflow_source_hash,workflow_execution_mode,workflow_type.
- map path→existingdigest: workspace_release_source_hashes; required integer-map: workflow_runtime_bounds.
- source safe integers/existing DTO constraints: workflow_timeout_seconds,workflow_time_saved,
  workflow_cache_ttl_seconds; finite source number: workflow_value; string|null: workflow_organization_id.
- no optional extra key invented by this adapter. Any source-admitted optional variants require separately named
  exact variant before admission; no generic arbitrary evidence object.
Solution optional bounds/schema absent stays absent, never manufactured null/zero/default.
Runtime bounds retain source-positive required max_duration_seconds,max_external_calls,max_records_read,
max_output_bytes and exact source-admitted extension keys; this data map does not confer new enforcing authority.
Parent_admitted_agent evidence has R admission_snapshot_id UUID,agent_id UUID,authored_agent_manifest_sha256
bareSHA256,source_observation_id UUID; it is a proposed observation association for actual agent row/config,
NOT an existing deployment pin or atomic multi-table version; no new source authority conferred.
Root must freeze actual selected evidence-kind and full closure from existing source custody; absence stays STOP.

## Prepared body — measured observations, never expected-label echo as proof

R type=Prepared,session_id UUID,prepare_message_id UUID,prepare_payload_sha256 bareSHA256,
provision_slot_id UUID,hello_message_id UUID,hello_payload_sha256 bareSHA256; parent binds exact prior Hello bytes.
R observations object contains these required members (empty only actual empty admitted class):
- entries: array{entry_id:string,observed_path:string,observed_byte_length:UInt,observed_sha256:bareSHA256};
  one per staged admitted FILE, actual fd/path/byte measurements, not expected bytes copied into observation.
- namespace: array{module_name:string,kind:module|package|implicit_namespace,
  observed_paths:arraystring,origin_entry_ids:arraystring,shadowing_origins:arraystring}; measured inert path/package
  resolution without tenant import/site hooks. Unexpected/unresolved/missing/shadowed outcomes retained for parent comparison;
  no runtime true/false authority acceptance flag or silently dropped unexpected origin.
- artifact: exact observed P0 RuntimeArtifact fields plus launch_artifact_files array
  {path:string,byte_length:UInt,sha256:bareSHA256}, interpreter_executable{path:string,byte_length:UInt,sha256:bareSHA256},
  sdk_files array same file record,packages array{distribution:string,version:string,installed_path:string,
  files:array file-record}; actual installed bytes/path observations, NOT expected artifact digest echo.
- startup: {observed_sys_path:arraystring,site_hook_files:array file-record,
  package_hook_files:array file-record}; actual observed candidate hook inventory. Observation does not execute hooks.
Parent compares whole observed entries/namespaces/artifact/package/hooks with admitted closure, detects extra/missing
origins and byte drift; expected labels remain in Prepare, actual measurements in Prepared. Child cannot validate
source entitlement or prove all hostile interpreter hooks inert merely by saying it inspected them.
If complete measurement exceeds16MiB, no silent truncated Prepared/closure; unsupported profile before admission.
Measurement mechanism/earliest inert launch/import prevention and installed-image custody remain separately held;
no fabricated observations or self-signed immutable-artifact proof. Prepared is NOT SDK/provider readiness or Start.

## Reserved provision interface — post-Start only, exact binding

Credential component in Prepare/Prepared is ONLY opaque provision_slot_id. Proposed later provision envelope
binds R provision_slot_id,session_id,supervisor_incarnation_id,prepare_message_id,prepare_payload_sha256,
start_message_id,committed_start_id canonicalUUID,typed attempt{kind:agent_execution_attempt,attempt_id,attempt_number},
model_slots ordered exact1..N{slot,profile_id,provider_connection_id,secret_material} matching prepared chain.
Parent verifies accepted same-session/typed-attempt Start and reserved slot before provisioning; runtime validates
matching binding before provider clients or selected entrypoint effects. Unknown/duplicate/missing slots reject.
secret_material retains api_key,endpoint:string|null,extra_params JSONobject and matching model/provider/settings;
whole envelope secret; no copied material in Prepare/Prepared/report/diagnostics. Empty provision only genuinely
credential-free admitted computation; selected model loop is NOT credential-free. No public mint/source lookup.
This reserves exact semantics, NOT transport/decryption/provider egress/revocation/lost-delivery implementation approval.
Root must freeze bounds/custody and finite failure/no-replay behavior; no early Start to fetch credentials/source.

## Actual parent resolution/locks and retained mechanical behavior

Consumer:740–781 current advisory agent-run lock→AgentRun FOR UPDATE→conditional active-delivery ownership→
generic execution_attempt creation/commit already marks running BEFORE executor model/tool resolution.
That incumbent order is NOT future inertPrepare→committedStart. Executor:218–325 resolves externalactor/User/
model config then access/catalog; plain config/catalog reads have no explicit locks. Parent-owned snapshot/revalidation
must be separately frozen with source-accounting admission first/private attempt compatible ordering and all actual writers;
this proposal supplies neither completed Start transaction nor fake live/session oracle/second registry.
Parent resolves AIModelAssignment/Profile/Connection chain (ai_model_service:219–396), retains graph validation,
current caller/tool attachment/calleraccess (agent_helpers:120–127,158–190,244–350), and exact catalog IDs.
Before EACH tool dispatch parent rechecks current Workflow/AgentTool/caller/active/tags (executor:632–734;
agent_workflow_tools:48–117), canonical-adopts child, commits before dispatch; Prepare tools never durable entitlement.
get_llm_configs currently decrypts during resolution; splitting metadata/preparation from postStart secrets remains
held resolver/provision interface. No dummy session factory/redis=None substitute for credential isolation.
Selected inactive snapshot preserves existing caller/config resolution BEFORE paused return (:218–268).
System prompt uses exact autonomous suffix (agent_helpers:226–242); falsey input/schema behavior unchanged.
Executor:270–295,433–478 retains zero RunUsage, configured limits, own fallback defaults, primaryagent override,
retries1/exhaustive/empty-output billedattempts; ObservedModel:185–218 retains retry observation surface.
Toolset:40–73 modelcopy32000 bound and executor:729–750 falsey/error/default=str formatting retained separately
from full real tool outcome and step20000 copy. No child SQL/Redis/pubsub/flush/admission/role credentials.
Summary separate parent-triggered facts/computation; run_summarizer:211–294 current domain lock→conditionaldelivery,
generating/commit then model/snapshot; parent owns completed eligibility/delivery/metadata merge/usage, not agentPrepared.

## Remaining held interfaces / acceptance

Actual selected source/closure/config/caller literal custody; dependency policy/inert installed artifact proof;
metadata-versus-decryption split/provider secret transport/revocation; cancellation/outcome-wait and existing terminal race;
complete Start lock/owner/source transaction; durable report/tool/summary receipts and event watermarks;
restricted principals/mechanical mixed-writer/schema authority; separate summary principal. None ratified by DTOs.
Mechanical vectors: duplicates/unknown/missing/null/optional/digest syntax/depth64/16MiB/safeintegers, exact rawbyte
hash sensitivity including whitespace/floatlexeme, full closure+unexpectedshadowing, measured-vs-expected mismatch,
wrong provision/session/typedattempt/Start/slot, no preStart imports/provider effects, zero childSQLRedis,
paused ordering/exact prompt/catalog/failover/budget/null/result format. All unsupported selected before admission.
Actual A readiness+B unchanged agent→capacity→genuine answer→streaming summary, providerusage, event/custody,
owner/fences/rollback remain mandatory supported execution gates. No product edits/CI/runtime/source mutation here.

## Exact inspected source SHA256s
- `api/src/services/execution/autonomous_agent_executor.py`: `912940c9704da9650bf9797010d18b793fb53ff90a091bdb853c1300f8a18029`
- `api/src/services/agent_executor.py`: `badaf61845c1e4561b281fcc91ab1a467e536b2953470efd0810a2a8198cae5c`
- `api/src/jobs/consumers/agent_run.py`: `302c36679d6dd45ab2e0d300ad5d009289d17829f0a88d234dc5033e5869ac2f`
- `api/src/services/execution/agent_workflow_tools.py`: `80ff9ba5edbea42de4a9de1fc42ca453319a31da34190962a8fa6b47f1941972`
- `api/src/services/execution/run_summarizer.py`: `64beba2771dc0a963f99477561637d1d9b06a993721c28fb4063484dc0460f3e`
- `api/src/services/agent_runtime/observed_model.py`: `b52112159b4242e88d892a88225d5b10533c3532ebe272a4b325b4798bc21d79`
- `api/src/services/agent_runtime/toolset.py`: `bcb732b3df8ec2395b1247d0eb57d916c5abbe5709dc6de943e908b3daeafc99`
- `api/src/services/llm/base.py`: `863168ff4dea6b4174654ac8965bfc8c9d3d17ba805c040ce563420bcb23d9d4`
- `core-rs/crates/bifrost-contracts/src/runtime/control.rs`: `33ee2987bdb5ff507afbd4a1a0b95681cd9b322be965a530145f68b788497243`
- `core-rs/crates/bifrost-contracts/src/runtime/session.rs`: `53965be75561bd5ed76ca366d6ca1a826411e66559d212d2cc4c53dfe9406a8e`
- `api/src/runtime_protocol/control.py`: `77a54873539c5116a8645e6db0f74ed403529626b842b1654ef52bc13b5f2a30`
- `api/src/runtime_protocol/session.py`: `a3f116706976d88c65a334377bf11f388c766b607108787bdb9a37191eeb7885`
- `api/src/services/execution/agent_helpers.py`: `638e00048d4d0631cec79f96a78f12e5a8069078da628f21d43333738d707a3e`
- `api/src/services/llm/factory.py`: `bdcd0494145636e37ebb9a138cb57ea039ef3d0aabbd6c9744875aa6e4a77933`
- `api/src/services/ai_model_service.py`: `6b9467db070ca74be4c056fcef954f4aa1552e5ec79f5c7f581ab94b7d37afd2`
- `api/src/services/tool_registry.py`: `bc7efabb66ac4f304e6e687c7a3565ad39be554380a29d4cc0790473afadbad0`
- `api/src/services/solutions/deployment_runtime.py`: `5282fb57793eab13861193590e3f47bcfca6901c52b38fb9b12dfc14c73cc331`
- `api/src/services/workspace_release_runtime.py`: `6a659b13875da2e954c4ac8af340472f9be90c4104a3f47452e412d9c7675a41`
