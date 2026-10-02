# C1-R committed material-reader interface

The [read-acquisition supplement](rust-core-mvp-agent-read-acquisition.md)
now controls transaction identity and reader-owned acquisition samples.
Root separately releases **C1-R-MATERIAL-ACQ-S** on the SAME two owned files;
the original transaction/result shapes below are extended only by that explicit
supplement. It leaves actual consumer closure binding, source adapters and
semantic acceptance gated. Reader construction never certifies postclosure
freshness or domain success by itself.

Independent source implementation review of the original1813-line reader
`f3258a0346f7d6c01f01313780b2742afccb4cd77c828dfb85fb32637b12f021`
and1038-line units
`0ea55bca6d48ae440d5796fca97095a0918ceb6ae61902edeb87b9d518ba793c`
found no concrete source blocker; report SHA256
`a9b425791fdd3aed5dcbb7a1257d6f5d0040f9bfd758aec3e655bebcfcf8600f`.
AST catalog/SQL/native-alias admission and locked Ruff0.15.12 checks passed.
No tests/DB/runtime/CI ran for those bytes. The acquisition implementation
supplement requires its own different review and supported verification.

Last supported two-file source candidate is reference commit
`e3bed7f8cc2fbfa95eda61a7d93635e8a4fe2e3a`: reader SHA256
`078aaabd5cbbb41e7cd9bfb7d3b73a80875af08c0c744a0bc5e01682466eaeba`,
units `62908bce4bc3e6a6f0bf046ad1fd8545bf0c77f7ae3acdcc60116150561cff77`.
Different implementation review `edf284c87f11d39332ab046968e65cf90e1b9f5361221b3b7a1599ab17bad1a8`
accepted the acquisition implementation. Hosted [run36978794042](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36978794042)
at preceding6a154 FAILED units:11 failed/12,763 passed/3 unrelated skipped/35
deselected. Quality, literal pre-PR and other required runtime lanes passed;
none overrides the unit failure. Pre-PR artifact11214139147 records exact
head/tree/main/image, exit0 and actual empty owned resource inventory.

The e3bed unit-only correction repairs the name-column fixture collision,
injects malformed native values after otherwise-valid SQL charge aliases, and
allows exactly two independent metadata row fetches while forbidding all five
child groups before actual committed Step4 discovery. Reader behavior is unchanged.
Independent repair review SHA256
`6ba9f29a4a59247042c7ef8dda1386fcee85a52fca314c0d3fd4afd034fc9a45`
accepted exact62908 after catching a confounded negative vector. AST and locked
Ruff pass. New supported [run36980564135](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36980564135)
is SUCCESS at exacte3bed:187 selected reader cases passed without skips;
12,774 backend units passed, with3 unrelated skips/35 deselections. All required
quality/pre-PR/API/client/MCP lanes passed. Pre-PR artifact11215402516 records
source tree4b65904f, main01cadfe, exit0 and actual empty owned resource inventory.
This is changed-candidate validation, not a rerun of failed6a154.
No actual PostgreSQL observation, source custody or nominal acceptance follows
from injected connection units. Prior release/source pins below are historical.

## Native-driver correction and actual PostgreSQL characterization

Source inspection of locked asyncpg0.31.0 found its optimized UUID decoder
returns the distinct `asyncpg.pgproto.pgproto.UUID` class. The earlier exact
stdlib-only return-value guard rejects that genuine native result; injected
connection units did not exercise the actual decoder. This is a test-reader
implementation defect, not approval to change the Python public reference.

The narrow correction accepts ONLY exact stdlib UUID or exact driver UUID
classes, using class identity comparisons and retaining the original object.
Foreign subclasses, equality-spoofing metaclasses and serialized surrogates
remain rejected. Input/public IDs, SQL casts, all other native types, isolation,
resource bounds, discovery and uncertainty semantics remain unchanged.
Reader SHA256 `403c3a163c529e9e7dc848550b9ff1a126522035766fe99ed3b02b7f9ad96deb`;
unit SHA256 `9561e9a15c55bb6c3debea31a03230529456154fe1a4eb6b188aa170360a5bc7`.

New sole characterization path
`api/tests/e2e/platform/test_agent_reference_lineage_postgres.py`, SHA256
`9f43dc898cf5bc2ab94c8d9ab40db34fa2b98f07c194e89ad2f73bad9e82aeb2`,
is225 lines below the frozen250-line limit. It uses the existing supported lane's
PostgreSQL16 connection, actual read-only transactions and actual asyncpg Records.
It covers snapshot isolation/acquisition/COMMIT, caller transaction preservation,
metadata drift negatives and all32 fixed material SELECTs with only each native
primary key plus remaining budget. Integer/text keys are not treated as UUIDs;
metadata selectors are not extra material bind arguments. Fixed SELECT positives
exercise real native UUID metadata and material-object retention; these are
projection mechanics, not persisted organization or nominal lineage evidence.

Different implementation review SHA256
`1a0ede47737aefec44cfd4fb44948325ade5aee051e0fcc59cf5394affae9129`
accepts the exact three-file source candidate. Review caught equality-based class
membership in an earlier guard; direct identity and the hostile-metaclass
negative close that gap without widening the frozen interface. AST, exact
Ruff0.15.12 and whitespace checks pass. The clean candidate is pushed as
`5d5dac9f790f522b7eee5a3ad00f58db6d04dba0`, tree
`b750bdf82154a7ad6204f4985cb2bb7ae3e14526`, containing current main01cadfe.
A single supported changed-candidate CI request includes the unchanged pre-PR
gate; runtime verification is pending. No actual PostgreSQL or new unit execution
is yet claimed.

Disposition: root freezes the independently reviewed MATERIAL-ONLY interface
below. Builder package **C1-R-MATERIAL-S** may implement only
`api/tests/e2e/platform/agent_reference_lineage.py` and
`api/tests/unit/test_agent_reference_lineage.py` on reference candidate
`a01437e68948ec08d96c070229e93144f40cde43`. The accountable architect owns
integration and publication; the builder performs only source/AST/locked Ruff
checks and returns review-ready bytes without commit, push or CI dispatch.
No physical-host product/dependency imports, pytest, Docker, credentials,
privileged operations or actual DB connection are authorized.

Exact final reviewed proposal SHA256:
`9eb08293dfd5ee408d310bd7e21acb8f618cdaab4cece2861d4ce0852f6c83ac`.
Independent review SHA256:
`518f3ef65d6f52251242ab684f34e4cc71f59bd8ae250b25524fbc809711be36`.
The proposal below retains its historical pending-release wording; this
preamble supersedes it ONLY for the two-file material reader source package.

The reader returns observed bounded material, never accepted/settled lineage.
The full pure predicate oracle, decryption/canonical/storage/live-witness
adapters, connection/secret custody and nominal consumer remain separately
gated. No source, authority, schema, public API/SDK or product runtime change
is included. Stop/report a mismatch in actual columns, selectors, typed APIs,
resource accounting or transaction semantics instead of guessing a replacement.

After source and independent review, architect-owned supported acceptance uses
`./test.sh tests/unit/test_agent_reference_lineage.py -v`,
`./test.sh quality api` and clean committed `./test.sh pre-pr`.
Real PostgreSQL committed/isolation/public-setup evidence and the full named
agent reference remain later gates; injected connection units cannot prove them.
No C1 nominal, C2/C3 authority, Rust parity, merge or deployment follows.

## Explicit registration membership representation

Root resolved a typed representation gap raised during source construction:
SetupSnapshot has explicit `solution_workflow_rows: tuple[WorkflowRow,...]`
alongside `workflow_rows`. The first retains independently observed complete
by-Solution membership; the latter retains independently observed selected-ID
membership. Both use their fixed N+1 query, admitted actual IDs and identity
rechecks. Never synthesize one membership from expected IDs or the other query.
Within the same observation transaction an identical actual admitted row may
be reused, but both actual membership tuples remain separate. RunSnapshot's
fresh setup_material includes both. This minimal field addition supersedes only
the packet's one-field representation; query/semantic/cardinality/byte/authority
rules remain unchanged. The future oracle must check both sets.

---

# C1-R committed lineage reader — source interface candidate

2026-10-02 revision3. Root corrected transaction termination and same-statement aggregate admission after independent review; revision2 is retained at `/tmp/bifrost-agent-reference-reader-interface-4fb00d45.md` (SHA256 `4fb00d45108e78bacfbe30724d8f84b923a6aef4a363bc8deb37da687eb1f629`). Root review and independent review are required before implementation. Prior revision SHA256 `df5cbda08cff2cb4dc3017f6bb94ff56aeb8df619b1e0c0a8957bac51eb4d67c` is retained unchanged at `/tmp/bifrost-agent-reference-reader-interface-df5cbda0.md`. Root feedback corrects authenticated Agent grants, separates actual setup/run identities and selects MATERIAL READER ONLY as the first package; full predicate oracle/adapters remain separate freezes. Root ratified the selected synthetic reader resource ceilings below, not implementation or public limits. This document owns only this `/tmp` file. No product/dependency imports, module execution, pytest, Docker, CI, network/API/vendor action, repository edit, commit or push occurred. It defines an observation helper for the complete unchanged capacity-agent/tool/summary nominal; it does not reduce the intended Rust workflow/agent lifecycle end state or release C2/C3 authority.

Guidance retained: repository AGENTS/platform project and verification guidance, engineering-flow2026-09-30.1, selected design package2026-10-01.5. Physical-host verification remains source/static only. #1017 is stopped after its authorized extra cycle; nothing here reruns or repairs it.

## Exact pins and interpretation

Requested reference source: `/home/thomas/src/bifrost-agent-capacity-reference` HEAD `a9020248ef370a914eec3ab4271628450e32f44b`, branch `test/agent-capacity-reference`. During inspection it advanced, clean, to `a01437e68948ec08d96c070229e93144f40cde43`. The intervening four files are lane/recipe unit fixtures, Compose and renderer; `git diff a902..a014 -- api/src api/bifrost api/scripts/agent_reference_contract.py` is empty. This proposal retains a902 as its source pin and reports a014 only as observed checkout state. Authoritative platform main and the separately approved AUTH overlay are distinct from this reference branch; this reader neither merges nor activates auth changes.

Controlling document: `/home/thomas/src/bifrost-rust-core-mvp/docs/architecture/rust-core-mvp-agent-lineage.md` at `db55c92104f4e128c2a4cf3d677c72733be80352`, SHA256 `01c646dd97ae7d8a6b3b661c9dabf6cacd645ca2264942b880249a85616425d5`. It incorporates corrected packet `0e500ddd0cd75a1387dbe15c2c081223f431936391056e75a13c9e5bc42c704c`. Q0–Q7 selected nominal cardinalities and source causality are already ratified there; its prelude expressly leaves reader/decryption/hash/deadline ownership and actual construction/custody/closure pending.

`requirements.lock:198` already pins asyncpg0.31.0. `api/tests/conftest.py:114–145` supplies a SQLAlchemy NullPool engine/session, not an independently connected raw asyncpg observation fixture. Do not reuse `db_session` or its rollback-only transaction as committed evidence. Connection acquisition is a separately owned case/coordinator integration gate.

## Proposed two-file package and APIs

Own only future `api/tests/e2e/platform/agent_reference_lineage.py` and `api/tests/unit/test_agent_reference_lineage.py`. No routers, models, migration, security/settings, consumer, SDK, shared observation codec, storage writer or generic job edits. Both files are test-only. A later, separately released case consumer owns public setup, raw connection acquisition, trusted adapters, polling, live witnesses and host closure. The reader creates no socket/connection/pool/engine and discovers no DSN, secret or environment setting.

Use frozen explicit dataclasses, not arbitrary request/context/expected dictionaries. The first package reads material and enforces structural/finite query admission only; it contains no source/result/principal acceptance oracle, decryption/hash adapter, public observation consumer, or partial oracle success fallback:

- `SetupIds(user_id, organization_id, agent_id, solution_id, initial_deployment_id, final_deployment_id, role_id, profile_id, connection_id)` — all actual `uuid.UUID` values from supported completed public setup/readback; selected W/P/B are source-owned constants. Require selected IDs distinct where source requires distinct entities. No fabricated or optional Run ID belongs to SetupIds.
- `RunIds(setup: SetupIds, run_id: UUID)` adds ONLY the actual UUID obtained from the public enqueue202 response. The helper never allocates it. There is deliberately NO input `execution_id` in either type.
- `RunReadStage = poll | final`; `TransactionFacts(isolation, read_only)` retains actual settings readback. `read_setup` uses a fresh short READ COMMITTED READ ONLY transaction; `read_run` uses the stage-specific isolation below.
- Explicit frozen row dataclasses `PrincipalRows, AgentRow, RunRow, StepRow, ExecutionRow, ParentAttemptRow, WorkflowAttemptRow, DeliveryRow, UsageRow, WorkflowRow, SolutionRow, DeploymentRow, ModelSetupRows` follow the actual column catalog below. Collections are tuples; nested parsed JSON is recursively immutable after strict bounded parse. All private material and parent containing it are repr-redacted. No generic table/column/sql selector API or JSON dump method.
- `SetupSnapshot(ids: SetupIds, transaction_facts, principal_rows, agent_row, agent_tools, agent_roles, agent_delegations, agent_mcp_connections, workflow_rows, workflow_roles, solution_row, deployment_rows, deployment_edges, model_setup_rows, solution_execution_metadata, selected_workflow_execution_metadata)` contains only named bounded setup/Q0/Q1/Q7 groups. It reads the two execution exclusion queries as bounded metadata but cannot attribute a Run or fabricate E.
- `RunSnapshot(ids: RunIds, stage, transaction_facts, setup_material, run_row, child_run_metadata, step_rows, parent_attempt_rows, known_generic_rows, agent_delivery_rows, summary_delivery_rows, usage_rows, discovered_execution_id, execution_rows, workflow_attempt_rows, workflow_delivery_rows, deferred_child_groups)` contains a fresh setup-material group from the SAME observation transaction, plus all available run groups. Setup material is not an older snapshot reused as committed rows. Missing rows are `None`/empty tuples; structural E discovery is only from the actual committed selected Step4 as described below. `deferred_child_groups` is a closed enum tuple identifying E-dependent execution/typed-attempt/workflow-delivery/known-ID-generic-exclusion/child-usage queries not yet possible. Never silently mark them checked.
- `ReadResult(kind, code, snapshot)` has `kind=observed|changed|failed`, a closed static reader code and an optional private SetupSnapshot/RunSnapshot. `observed` means one bounded read finished, not predicates passed or work completed. `changed` indicates a READ COMMITTED metadata set changed within the read; it is not a successful final observation. `failed` means input/transaction/schema/type/cardinality/resource/discovery failure. Snapshot repr is suppressed. No `settled`, `accepted`, `verified` or nominal PASS result exists in this package.
- `async read_setup(connection: asyncpg.Connection, ids: SetupIds, *, deadline_ns: int) -> ReadResult` reads setup material before enqueue without a Run ID. It selects Q0, Agent/grant/model groups and Q7 registration/source-parent/exclusion metadata; no AgentRun, Step, attempt, delivery or usage query substitutes a made-up Run. Cardinality/resource admission still applies; domain eligibility/drift belongs to the future oracle.
- `async read_run(connection: asyncpg.Connection, ids: RunIds, *, stage: RunReadStage, deadline_ns: int) -> ReadResult` performs one bounded run observation transaction, including fresh setup material and all available Q0–Q7 groups. It owns neither the supplied connection nor polling. Both APIs require no active transaction on entry and a dedicated observer connection that is not shared concurrently.

The separate future oracle package owns typed `PrincipalBaseline` and `SetupBaseline` from actual public/DB/compiler observations, immutable identity continuity, full Q0–Q7 semantic classification, trusted canonical observations and actual joined witnesses. It must explicitly consume deferred/changed read dispositions and cannot accept an observed material snapshot as lineage verification. Its APIs/files are NOT frozen or released by this two-file material proposal.

## Transaction, deadline and type mechanics

Reject an already-active transaction using `connection.is_in_transaction()` without mutation/termination; a caller's flushed/uncommitted session cannot be reused. Use ONLY the fixed control statements `BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY` for setup/poll, `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY` for final, and `COMMIT`/`ROLLBACK`, each through `connection.execute(timeout=remaining_absolute_budget)`. No nested transaction, SAVEPOINT, generic SQL selector or asyncpg Transaction context manager: its pinned implementation does not pass a per-operation timeout and may await rollback after cancellation. Immediately execute fixed `SELECT current_setting('transaction_isolation') AS isolation, current_setting('transaction_read_only') AS read_only`; require the exact requested isolation (`read committed`/`repeatable read`) and `on`. Readback is actual settings, not the transaction API arguments.

Every poll creates and exits a new short transaction. A long snapshot opened before trigger is forbidden. In READ COMMITTED, material fetches use IDs from a bounded metadata query and recheck that metadata set before transaction exit. A changed admitted identity set produces `changed/snapshot_changed`; excess still fails immediately. The future oracle separately decides whether an observed source transition is pending or contradictory and maintains sticky prior identity observations. The material reader never retries a changing set into success. A READ COMMITTED pass is only a candidate for final observation, never a consistent multi-table acceptance snapshot. Final REPEATABLE READ observes one snapshot and reports its actual absent/deferred groups. The separate oracle classifies incomplete versus contradictory facts within the unchanged deadline. After actual host/API closure, a NEW final transaction rechecks the complete committed set, immutable observed material and exclusions, via surviving DB resources. The future oracle must preserve the previously discovered E and actual PA/WA/DR/DW/DS/step/usage identities; a regenerated/replaced matching-looking cohort is failure, not material-reader acceptance.

The caller supplies the existing absolute case deadline once (`monotonic_ns`, strict positive int, not bool); reader never resets/extends it. Check remaining time before transaction start and every fetch, use bounded cancellation for the entire transaction, and pass remaining bounded timeout to each asyncpg operation. Root-ratified selected synthetic reader ceiling: `MAX_SNAPSHOT_SECONDS=3`, operation/transaction budget `min(3 seconds, remaining case deadline)`. This is an observer resource bound, not an extension of the existing case120 deadline. No internal retries, sleeps, connection recovery or changed isolation fallback. Rollback is attempted only for a non-cancellation failure while the SAME absolute operation/case budget remains; it has the same remaining timeout. Timeout, cancellation, uncertain BEGIN/COMMIT/ROLLBACK, exhausted cleanup budget or failed transaction-exit readback synchronously calls the public `connection.terminate()` on ONLY this exclusively supplied observation connection, and latches a tainted disposition; no connection recovery/reuse, extra cleanup window or async close/rollback await after budget expiry. Cancellation propagates after termination; ordinary uncertain exit returns failed/connection_tainted with no snapshot. No successful server rollback/backend cleanup is asserted after termination. On success, COMMIT must finish inside the same budget and `is_in_transaction()` must be exactly False before any observed result. An already-active caller transaction is never terminated. The caller grants only this narrow emergency termination of its dedicated observer connection, not general pool/engine ownership. Synchronous driver termination is a source-level non-waiting abort, not a bound on OS/server cleanup. [Pinned asyncpg Transaction](https://raw.githubusercontent.com/MagicStack/asyncpg/v0.31.0/asyncpg/transaction.py), [Connection public APIs](https://raw.githubusercontent.com/MagicStack/asyncpg/v0.31.0/asyncpg/connection.py). A timeout never returns observed material as a successful completed read or erases a future-oracle contradiction.

Bind UUID columns with actual `uuid.UUID` instances and `$n::uuid` / `$n::uuid[]`; never stringify UUIDs into SQL or infer generic ledger IDs as text. Usage material primary IDs use strict native int binds, and assignment material primary keys use the fixed source key strings; do not apply a universal UUID-ID assumption to either table. Bind WorkDelivery `message_id` separately as exact `str(R)`/`str(E)` text. JSONB is selected as `column::text AS <column>_json` so caller-installed asyncpg JSON codecs cannot silently change observed types. Strict parse rejects duplicates, nonfinite numbers, bad UTF-8/surrogates, excessive nesting and illegal source-specific shapes; source manifests legitimately contain finite numeric controls, so do not apply the private-wire integer-only rule to all DB JSON. Typed SQL values retain aware datetime, Decimal and bool/int distinctions; no clock rounding, fabricated timestamps, numeric coercion or string/UUID substitution.

## Fixed SQL and cardinality admission

Every collection has a fixed source-owned predicate and metadata-only ordered query with literal `LIMIT expected_count+1`. Reject excess BEFORE selecting content, encrypted envelope or history. Observed absent/partial counts are retained as material, with E-dependent groups explicit; the separate oracle owns absence/source-progress/identity/drift classification. Structural expected-count excess fails in the reader. The selected zero-count collections are fixed negative-query admission guards, not general product eligibility rules. After admission, fetch only the admitted actual primary IDs with fixed column lists and recheck identity/cardinality. No `SELECT *`, dynamic table/where/column bags, newest-success filtering, status-filtered historical rows, ORM identity map, DML, row/advisory locks or schema changes. IDs/flags/length metadata may be read before material; never fetch private history just to discover it is excessive.

| Fixed query | Metadata/identity selection and limit | Selected nominal |
|---|---|---|
| Q0 user/org | `users WHERE id=$1::uuid`; `organizations WHERE id=$1::uuid`, each PK/LIMIT2 | exactly1 each; immutable adjacent/public comparison |
| Q0 grants | `user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=$1::uuid ORDER BY r.id LIMIT2` | exactly1 actual role/name; NOT token role UUID strings |
| Q1 entities | `agent_runs WHERE id=$1::uuid`; `agents WHERE id=$1::uuid`, each LIMIT2 | R/A exactly1 |
| Q1 no delegation | `agent_runs WHERE parent_run_id=$1::uuid ORDER BY id LIMIT1` | zero; a single extra row fails |
| Q1 tools/roles | `agent_tools WHERE agent_id=$1::uuid ORDER BY workflow_id LIMIT3`; `agent_roles WHERE agent_id=$1::uuid ORDER BY role_id LIMIT1` | BOTH W/P tool grants; ZERO AgentRoles for the actual authenticated Agent with role_ids[] |
| Q1 excluded grants | `agent_delegations WHERE parent_agent_id=$1::uuid ORDER BY child_agent_id LIMIT1`; `agent_mcp_connections WHERE agent_id=$1::uuid ORDER BY connection_id LIMIT1` | zero selected autonomous additional tools |
| Q2 steps | `agent_run_steps WHERE run_id=$1::uuid ORDER BY step_number,id LIMIT7` | exactly6 at completion; commit may reveal all6 together; no more than6 |
| Q3 child | `executions WHERE id=$1::uuid LIMIT2` using E discovered from actual Step4 | exactly1 |
| Q4a parent | `execution_attempts WHERE logical_job_type='agent_run' AND logical_job_id=$1::uuid ORDER BY attempt_number,id LIMIT2` | exactly1 PA |
| Q4b child typed | `workflow_execution_attempts WHERE execution_id=$1::uuid ORDER BY attempt_number,id LIMIT2` | exactly1 WA; actual runtime_evidence_hash |
| Q4c all generic types | `execution_attempts WHERE logical_job_id IN ($1::uuid,$2::uuid) ORDER BY logical_job_id,logical_job_type,attempt_number,id LIMIT2` | full set exactly PA; no job-type filter; second row fails BEFORE material fetch |
| Q4c before E exists | same fixed all-types predicate for only R, LIMIT2 | defer E-dependent check until actual discovery; never bind expected child |
| Q5 each delivery | `work_deliveries WHERE queue_name='<fixed queue>' AND message_id=$1::text ORDER BY id LIMIT2` | exactly1 each agent-runs:R, workflow-executions:E, agent-summarization:R; all historical statuses included |
| Q6 usage | `ai_usage WHERE agent_run_id=$1::uuid OR execution_id=$2::uuid ORDER BY id LIMIT4` | exactly3 parent rows and0 child rows; fourth fails; separately reject any returned child-linked row even below4 |
| Q7 registrations | `workflows WHERE id=ANY($1::uuid[]) ORDER BY id LIMIT4` AND independent `workflows WHERE solution_id=$1::uuid ORDER BY id LIMIT4` | exact{W,P,B}, all belong S |
| Q7 role grants | `workflow_roles WHERE workflow_id=ANY($1::uuid[]) ORDER BY workflow_id,role_id LIMIT3` | exact(W,role),(P,role), no B grant |
| Q7 exclusions | `SELECT e.id,e.workflow_id FROM executions e JOIN workflows w ON e.workflow_id=w.id WHERE w.solution_id=$1::uuid ORDER BY e.id LIMIT2`; separately `SELECT id,workflow_id FROM executions WHERE workflow_id=ANY($1::uuid[]) ORDER BY id LIMIT2` | both exact(E,W), covering dissociated preview/bootstrap registrations |
| Q7 source parents | `solutions WHERE id=$1::uuid LIMIT2`; `solution_deployments WHERE id=ANY($1::uuid[]) ORDER BY id LIMIT3` | actual S and D1/D2; no latest/pointer-only substitute |
| Q7 source edges | `solution_deployment_dependencies WHERE deployment_id=ANY($1::uuid[]) ORDER BY deployment_id,dependency_solution_id LIMIT1` | zero for this no-dependency carried recipe; any edge STOP, not ignored |
| Q1 selected model | `ai_model_profiles WHERE id=$1::uuid LIMIT2`; `ai_provider_connections WHERE id=$1::uuid LIMIT2`; `ai_model_assignments ORDER BY assignment_key LIMIT7` | actual selected profile/connection and all6 public assignments, no extra assignment |

Material discovery is narrowly structural: inspect the bounded actual step rows for `step_number=4`; no such row means E=None and explicit deferred child groups. More than one such row, wrong `type` (must be `tool_result`), nonobject content or missing/nonstring/noncanonical UUID `content.execution_id` is `discovery_invalid`. Parse E with UUID and require exact `str(parsed)==stored_string`; never search other steps/exclusion metadata for a fallback ID. The full tool name/is_error/result/content/sequence/authority joins remain future-oracle predicates. A material snapshot revealing a different E in a later call does not silently certify it: the coordinator/future oracle retains the previous actual identity and rejects replacement.

Q7 execution exclusion queries run in setup and every run read, even before E exists. During run polling, one legitimate capacity child metadata row `(actual UUID,W)` can exist before buffered Step4 commits; retain it privately as unattributed metadata and leave child lineage pending in the future oracle. NEVER set E from that row or query the E-dependent groups using its ID. Preview/bootstrap, an extra selected-Solution/workflow row or unsupported workflow attribution are contradictory in the future oracle; the material reader enforces the fixed max-one admission and retains the actual workflow_id. A preenqueue setup snapshot still expects zero executed work in the future setup oracle. Once actual committed Step4 produces E, both exclusion sets must later equal `(E,W)`; an existing capacity row is not labeled unexpected merely because E is absent. Q6 before E discovery may inspect R-only LIMIT4; the final pass MUST perform the full OR predicate. Do not silently omit the deferred queries from a final pass.

## Exact material column catalog

Future SQL uses the following explicit actual names; append no speculative columns. Select `status::text` where a PostgreSQL enum is involved. JSON fields listed below use `::text` aliases. Select raw error text only as `IS NULL AS ..._absent`; no diagnostic string is needed. Claim/lease tokens use null/presence booleans, not raw values. Metadata-only cardinality queries do not select these private fields.

- Q0 users: `id,email,name,organization_id,is_active,is_verified,is_registered,is_system,is_superuser,is_external`; organizations: `id,is_active,is_provider`; grants: `ur.user_id,ur.role_id,r.name`.
- Agent: `id,name,system_prompt,channels,access_level,organization_id,solution_id,owner_user_id,is_active,knowledge_sources,system_tools,llm_profile_id,llm_max_tokens,max_iterations,max_token_budget,max_run_timeout`; selected grant tuples as above. Actual `knowledge_sources/system_tools` are TEXT arrays, channels JSONB; prompt is private.
- Run: `id,agent_id,trigger_type,trigger_source,conversation_id,event_delivery_id,input,output,output_schema,status,org_id,caller_user_id,caller_email,caller_name,iterations_used,tokens_used,budget_max_iterations,budget_max_tokens,duration_ms,llm_model,asked,did,answered,metadata,confidence,confidence_reason,summary_generated_at,summary_status,summary_delivery_id,summary_prompt_version,started_at,completed_at,parent_run_id`, plus `error IS NULL AS error_absent`, `summary_error IS NULL AS summary_error_absent`. SQL `metadata`, NOT ORM `run_metadata`; caller_user_id is VARCHAR255 string, confidence is Float.
- Steps: `id,run_id,step_number,type,content,tokens_used,duration_ms,created_at`. All row IDs are actual UUIDs; do not add a tool_call_id column.
- Child: `id,workflow_id,workflow_name,organization_id,executed_by,executed_by_name,parameters,result,result_type,started_at,completed_at,duration_ms,execution_model,form_id,api_key_id,session_id,solution_deployment_id,runtime_mode,runtime_evidence,runtime_evidence_hash,dispatch_evidence,dispatch_evidence_hash,retry_policy,attempt_tracking_version,execution_context,status::text`, plus `error_message IS NULL AS error_absent`. No variables/log payloads, no nonexistent `executions.solution_id`.
- Generic attempt: `id,logical_job_type,logical_job_id,organization_id,attempt_number,status,policy_identifier,workload_class,admission_policy,mechanism,queue_name,message_id,retry_count,replay_count,worker_id,process_id,started_at,completed_at`, plus `lease_token IS NULL AS lease_absent`, `failure_code IS NULL AS failure_code_absent`, `failure_message IS NULL AS failure_message_absent`. Actual process_id INTEGER; logical_job_id PostgreSQL UUID.
- Typed attempt: `id,execution_id,attempt_number,status,phase,worker_id,worker_incarnation_id,process_id,runtime_mode,runtime_evidence_hash,dispatch_evidence_hash,policy_digest,policy_version,published_at,claimed_at,started_at,heartbeat_at,completed_at`, plus `claim_token IS NOT NULL AS claim_present`, `failure_phase IS NULL AS failure_phase_absent`, `failure_code IS NULL AS failure_code_absent`. Actual process_id VARCHAR255; claim value is neither needed nor exported.
- Delivery: `id,queue_name,message_id,encrypted_envelope,status,available_at,created_at,started_at,settled_at,claim_count`, plus `lease_owner IS NULL AS lease_owner_absent`, `lease_token IS NULL AS lease_token_absent`, `lease_expires_at IS NULL AS lease_expiry_absent`. Ciphertext remains private until trusted decryption; message_id TEXT is not DS UUID.
- Usage: `id,execution_id,conversation_id,agent_run_id,message_id,provider,model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,provider_cost,cost,duration_ms,timestamp,sequence,organization_id,user_id`. Actual IDs INTEGER, monetary values Decimal; all3 sequence values1, not phase ordering.
- Workflow: `id,solution_id,organization_id,path,function_name,name,description,category,tags,type,tool_description,display_name,parameters_schema,execution_mode,timeout_seconds,cache_ttl_seconds,time_saved,value,retry_policy,access_level,endpoint_enabled,public_endpoint,allowed_methods,disable_global_key,is_active,is_orphaned`; compare exactly the compiler-projected definition plus source-expected active/non-orphaned state. Numeric `value` is Decimal; use an explicit finite numeric comparison, never Python bool==1 acceptance. WorkflowRole tuples carry actual role UUIDs.
- Solution: `id,organization_id,status,active_deployment_id,execution_runtime_mode,allow_outbound_access`; active is `status='active'`, NOT a nonexistent is_active column.
- Deployment: `id,organization_id,solution_id,parent_deployment_id,base_deployment_id,state,bundle_hash,compiled_manifest,compiled_manifest_hash,resolution_map,resolution_map_hash,source_artifact_key,runtime_storage_prefix,git_repository,git_ref,git_commit_sha,activated_at,superseded_at`. All3 content digests retain exact `sha256:<64 lowerhex>`71-byte form. D1/D2 source/state expectations come from actual accepted setup observations; never silently require an invented initial parent/base/state.
- Source edges (if any is unexpectedly found, already fail): metadata `deployment_id,dependency_solution_id,dependency_deployment_id`; actual columns also `declared_constraint,resolved_bundle_hash`; no need fetch private/unbounded material for this zero-edge profile.
- Model profile: `id,name,connection_id,model,openai_transport,default_max_tokens,capabilities,enabled_for_chat,failover_profile_id`; connection: `id,name,provider,endpoint`; assignments: `assignment_key,profile_id`. Never select `encrypted_api_key` or price model substitutions. Assignment allowed source keys are primary/summarization/tuning/image_generation/video_generation/chat_default.

Root-ratified selected synthetic reader ceilings: each selected JSON/text/array serialized material ≤65536 UTF-8 bytes; encrypted delivery envelope ≤131072 bytes; total charged fetched private material ≤1MiB per snapshot; strict JSON nesting≤64. Check `octet_length(column::text)` in admitted metadata and use bounded `CASE WHEN octet_length(...)<=limit THEN ... ELSE NULL END` plus explicit length/oversize flag in the material SELECT itself, preventing a READ COMMITTED growth race from fetching oversize text. Per-cell admission alone is insufficient: material fetches are fixed SINGLE-ROW queries by already admitted actual primary ID. In the SAME SELECT, calculate every variable selected cell's UTF-8 octet length (JSON/text/array serialization and ciphertext included) and their aggregate. Before issuing each material SELECT, require remaining_snapshot_bytes >= its known fixed native-scalar+row charge (128 per returned native scalar cell +256 for the one row); otherwise return material_oversize without any private material fetch. Require each cell within its cap AND `row_charge <= remaining_snapshot_bytes`; otherwise every variable material cell is NULL and an explicit oversize flag fails permanently. Never fetch a private multirow batch without a same-statement aggregate SUM gate protecting all returned material. Fixed byte charging is sum(actual admitted variable UTF-8 bytes) +128 bytes for every returned native scalar cell +256 bytes per returned material row; NULL/native UUID/int/bool/aware datetime/finite numeric cells use that fixed scalar charge. Guard metadata aliases/lengths/types, compare returned row_charge against these exact selected-column charges, then decrement the remaining budget before the next query. Material byte charging is deterministic admission accounting, NOT a bound on Python heap, SQL evaluation memory or PostgreSQL/network framing. Metadata queries contain only the fixed bounded identifiers/flags/lengths and bounded schema-defined selector strings, with literal N+1 limits and independent strict scalar/type limits; they must never become a route for private payloads. No caller column/charge selectors. A READ COMMITTED growth can cause failure, never an oversize payload fetch. Never silently truncate. These are new reader resource ceilings for the selected small synthetic profile, not inherited private-wire family caps or public schema limits. Root approval is limited to these reader resource ceilings; it does not approve public limits or consumer construction. Bound private scalar/array values similarly; unknown malformed type or material-size excess is permanent failure. Source archive/runtime objects belong to separately bounded storage observation, not this 1MiB SQL budget.

## Immutable registration and trusted observation ownership

Actual Workflows have no SQL `source_ref,portable_ref,source_hash,runtime_bounds,effects,source_enforced_bounds,source_requested_bounds,parameters_schema_contract` columns. Their immutable ownership is `CompiledDeploymentManifest.workflows[portable_ref]` and `DeploymentResolutionMap.workflows[portable_ref]`, each `RuntimeEntityDefinition(portable_ref,resolved_id,definition,source_ref,source_hash,dependency_solution_id)`; source resolutions live in `resolution_map.sources[source_ref]` as `(object_key,content_hash)`. `api/bifrost/solution_delivery_review.py:328–409` is the existing source compiler. `project_workflow_registrations` at `workflow_revision.py:84–111` removes role_ids to WorkflowRole and removes the five immutable-only definition fields before actual Workflow projection. Thus the future separate oracle must compare full compiler/manifest/resolution registration privately, AND separately compare exact persisted Workflow controls/schema/org/path and actual grants. The first reader only returns bounded actual material for those comparisons. Do not mistake an independently supplied expected digest or recipe dictionary for a verified stored source.

The following remains OUTSIDE the first material-reader package and its unit acceptance. The coordinator owns a fixed trusted adapter module/implementation in its separate case package; no adapter is supplied by fixture HTTP/JSON, no envelope-key argument or arbitrary callback bag appears in reader API. Root must freeze this adapter ownership before consumer release:

1. **Delivery decryption:** invoke existing `src.core.security.decrypt_secret` on only the three selected actual ciphertexts. It derives the existing instance key through settings/HKDF. Do not call decrypt_with_key, accept a caller-provided key, derive a key from expected JSON, mint alternate tokens or duplicate Fernet logic. Root must establish that the isolated observer/coordinator is permitted to use the same existing application secret with narrow custody; the reader cannot discover it. Missing correct custody/decryption is STOP. Parse bounded plaintext, compare exact source-shaped envelopes/headers/context, do not return plaintext in report. Raw exception contexts from crypto/JSON must be discarded before raising a static error (raising `from None` alone does not erase __context__).
2. **Canonical content/dispatch:** use `deployment_manifest.canonical_json`/`sha256_digest`, `validate_runtime_closure` with actual rows/zero actual edges and expected *stored* hashes, followed by independent compiler/source comparisons. These helpers use ensure_ascii=False and allow_nan=False; private-wire canonical bytes are NOT a substitute. For durable dispatch use actual `_validated_pending_dispatch` (or source-reviewed fixed wrapper) over an explicit readonly execution view and independently reconstructed selected request identity. Its Any execution interface needs no ORM/session/write; do not invoke recovery/republish methods. Recompute stored runtime, request, publish and envelope hashes, compare complete exact structures and actual lineage IDs/ordinary authority. Return a private named canonical observation bound to the exact snapshot/material, not a bare trusted=True verdict.
3. **Attempt policy:** existing `_policy_digest` uses runtime_mode plus `snapshot_retry_policy` and the exact workflow-attempt/v1 domain string. Its annotation currently expects ORM Execution although it only reads fields; a readonly adapter view would be an explicit reviewed test-only call boundary, NOT an ORM-created/fabricated entity. Freeze that typed adapter or exact canonical helper exposure before a builder copies the digest algorithm. No product refactor is implicitly approved here.
4. **Source/storage:** root-owned independently configured existing `SolutionDeploymentStorage(S,D)` read_source_artifact/read_compiled_manifest/read_runtime_file use keys derived from actual selected IDs. Compare source_artifact_key/runtime_prefix against exact existing key helpers and actual resolution keys; never read mutable install-wide `SolutionSourceArtifactStorage` as D2 provenance. Read actual archived bytes and manifest/resolution/source map, compare canonical source_archive/source_closure from `bifrost.solution_source_closure` (server reexports `live_handoff_source.py`) and original committed input bytes. Runtime files come from exact accepted source map and complete five-path D2 closure, no wildcard/repo/modules Redis fallback. These read methods for source/runtime are not themselves transport-bounded; the separate storage adapter MUST freeze a bounded read method before consumer release, rather than claim post-fetch length checking avoids resource exposure. The SQL reader does no storage network operation.
5. **Tool content and live joins:** reuse existing closed tool-result validation under `agent_reference_contract` (currently internal `_tool_content`, no public validate_tool_content helper). Root must approve the exact private helper call or separately expose a test-only pure entrypoint; do not manufacture a model_readback record to reach a validator, copy a weaker validator or change product source. Actual K, tool-result string and actual independently observed model tool-role content must agree; actual signed SDK execution E/source/caller projection, genuine event publication rows and fixture Cove/model records remain independent typed joined observations. They are never replaced by an expected constant carrying E.

These adapters may perform imports/use existing private application helpers only in the future authorized supported coordinator. None were executed for this proposal. Their outputs carry actual row/source material identities; pure oracle checks binding before comparison. No adapter can certify process custody, committed visibility, live Redis ACK, signer authority/fresh lease or host closure simply by returning a success boolean.

## Separately gated semantic oracle and final disposition

First-reader static codes are `invalid_input,active_transaction,isolation_mismatch,deadline_expired,snapshot_timeout,snapshot_changed,connection_failure,connection_tainted,schema_mismatch,cardinality_excess,material_oversize,material_invalid,discovery_invalid`. Semantic codes such as principal_drift/source_mismatch/result_mismatch/attempt_failure/delivery_failure/summary_failure/metering_mismatch/witness_missing/adapter_unavailable belong to the separately frozen oracle, not a partially implemented reader. No raw query args, source JSON, ciphertext, tokens, crypto errors, asyncpg exception text or validation errors enter str/repr/log/output. Any exception captured from an input-bearing parser/driver/helper is classified inside catch and raised only after leaving its exception scope. Safe static errors have no raw __context__/__cause__; cancellation is not hidden as a static pass/failure retry.

The future oracle must distinguish the following source-compatible incompleteness from contradictions; these rules are requirements retained from canonical lineage, not reader code release. Pending means source-compatible incomplete commit: absent run before its enqueue commit; absent uncommitted step set, child/attempt/delivery/usage; queued/running parent, Pending/Running child, dispatching/published/claimed/running typed attempt, claimed/running generic parent, queued/claimed delivery, pending/generating summary. A smaller otherwise valid metering set is pending until final summary/transport settles; never require intermediate metering rows that the parent buffers until terminal commit. Unknown statuses STOP; scheduled source state may occur before the actual public enqueue's queued transition but cannot become a new accepted scheduled profile.

Contradiction latches permanently: wrong/extra IDs or grants, more than frozen counts, failed/cancelled/timed-out/interrupted/poison work, any nonnull error/failure indicator, retry/replay/claim_count>1, observed immutable principal/source/pointer/result drift, wrong summary/usage/authority, discovered E changing, or extra generic rows across known R/E. No later successful row can erase it. A canonical final committed completed row missing its same-commit required field is contradiction, not endless pending. Separately committed transport settlement/publication/summary work can still be pending after completed parent/child; DW ACK can already be completed before child success. All3 terminal delivery observations are required independently.

Every actual UUID, datetime and domain output remains genuine. Parent generic message_id and lease_token are selected NULL; do not invent PA↔DR lease custody. SDK signature E/S/delegated fields does not prove a fresh attempt lease. Q0 public reads are cross-request/non-atomic. Public Engine context artifact/is_agent omissions retain source semantics. Reader cannot remedy those limitations by a guessed FK/context flag, new auth claim or private write.

Coordinator PASS requires: independent supported public/setup/source chain; actual3 Redis ACKs and all required bounded raw fanout observations; fixture SDK/model/Cove/collector ledger equations and exact result-consumption joins; a consistent final committed DB observation; frozen finish once; actual unchanged API-pair successful shutdown/host-status/closed ACK proof; and NEW post-closure DB snapshot with no effects/drift. If any is unavailable, outcome remains STOP/pending-to-deadline, not C1 acceptance. The reader's `observed` is deliberately insufficient alone; it makes no settled/verified claim.

## Meaningful tests and release gates

First two-file MATERIAL reader/unit package uses an injected connection double exposing only fixed execute/fetch/is_in_transaction/terminate APIs. Tests inspect exact fixed SQL/UUID binds and metadata-first call order: excess in each collection prevents private material fetch; Q4c preserves every returned logical_job_type for R or E (including a lone wrong type for the future oracle to reject), and two known-ID rows fail the cardinality bound; duplicates/historical attempts/deliveries are never hidden by success filtering; E stays absent until actual Step4 and malformed/ambiguous discovery fails; once E exists every child query runs, while absent E leaves explicit deferred groups even in final stage; no SELECT*, locks/DML/env/DSN calls. Cross-snapshot E replacement is a future-oracle negative, not a claimed reader continuity check. The first package covers Q0–Q7 retrieval/type/cardinality/discovery/resource drift with otherwise-valid material negatives, including zero AgentRoles versus two actual workflow-role grants and an unattributed single capacity row before Step4. Full semantic Q0–Q7 drift negatives belong to the future separate oracle/consumer package; do not claim the first material tests exercise them. Strict returned types, malformed/oversize JSON, private cause/context/repr redaction, early deadline/cancel, stalled BEGIN/COMMIT/ROLLBACK, remaining-budget-only rollback, termination on uncertain exit with no observation/reuse, no termination of preexisting caller transaction, same-statement aggregate growth crossing remaining budget despite individually legal cells, exact byte charging and insufficient-fixed-charge preflight preventing private material fetch, and readback wrong-isolation are red-capable tests. They are unit-injected observations, NOT committed PostgreSQL/isolation/storage/process proof.

Separate later case-consumer scope MUST use actual supported migrated PostgreSQL and real public setup/installation/enqueue, independent connection and existing admitted source/decryption adapters. Include real uncommitted/rolled-back supported setup changes invisible to the observer, commit becoming visible in a fresh poll, old repeatable-read snapshot not seeing later commits (diagnostic negative), correct final settings readback, and failed producer commit not manufacturing successful lineage. Do not seed domain rows/ORM grants, call consumers or bypass immutable-history deletion guards to obtain those positives. Retain owned nominal history within the disposable lane; root owns cleanup and process resources.

After source freeze/review only: supported `./test.sh tests/unit/test_agent_reference_lineage.py -v`, existing locked API quality, and the fully authorized named `./test.sh agent-reference` consumer lane with exact custody. Physical-host source AST/locked Ruff/format/diff can establish only static quality. No CI dispatch or actual commands are authorized by this proposal.

Reader resource ceilings are ratified as recorded. Root and independent review must freeze the MATERIAL reader-only typed APIs/query catalog/discovery semantics before builder release. Separate consumer decisions remain: exact typed trusted adapter outputs and allowed private helper calls; coordinator application-secret custody, independently supplied raw connection acquisition, bounded immutable-storage reads and case consumer ownership. STOP if exact source columns/helpers/chain cannot be retrieved, or any route/write/new auth authority is needed. No schema additions/public endpoint/generic runtime reader/source authority are proposed.

## Supplemental source fingerprints

The canonical lineage document retains the25 governing fingerprints. Additional exact inspected files (unchanged between a902 and observed a014):

| Path | SHA256 |
|---|---|
| api/src/models/orm/workflows.py | 7b74c8581d7292595426b61e99274c086bdbd7360c577a095312224d5677482c |
| api/src/models/orm/workflow_roles.py | 617a7d1036be6c2c02c44224d372b328a536526539a5cbbd2ae4df8a2930a74b |
| api/src/models/orm/agents.py | a71d2a8699f8d5e0d0d138d8663a1d7a326300751a8b74411403d2c181734255 |
| api/src/models/orm/ai_models.py | 9c234fb783092e04e83f3da6389bc08f95b7fbe31feb15af738c421d226dcd4d |
| api/src/models/orm/external_mcp.py | 4641004542723a81579ecff208d18877d4fc4c7acbdfa91d38f188761be0dffc |
| api/src/services/solutions/workflow_revision.py | 92c5940575133546618b8556f1a41f961778dfcbe0637c002017fca45ca0e09b |
| api/src/services/solutions/deployment_manifest.py | 6cd37d90cc52e4b379e2ffbb724a28a3617bf42a870323491c7cf053ded2dd85 |
| api/src/services/solutions/deployment_storage.py | 9f5009c94d79c1ba9dc212bfb000de61802be8255cb87f798ecb639263cae0f3 |
| api/src/core/security.py | 288ef0de137ee71cb5600ec94931229628d9f1d8f56a1b26b073a7e0fdb39d57 |
| api/tests/conftest.py | 8e5eb42729bdcff39a578ffcb32956e9509f83a1e75a2ba62f7c47b24a9f2c7b |

Smallest next step: root and independent reviewer freeze the corrected two-file MATERIAL reader interface, then authorize that bounded reader/unit builder. Separately freeze and release full oracle/adapters/consumer; their unresolved decisions do not become a material-reader bypass. Supported actual nominal and final Rust lifecycle acceptance remain outstanding.
