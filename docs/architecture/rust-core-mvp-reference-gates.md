# Rust core MVP: selected reference gates

Status: source-derived decision record; nominal Scenario B is blocked. This
record does not freeze the complete runtime contract or authorize an authority
change. Procedure: mtg-engineering-flow 2026-09-30.1. No merge or production
deployment is authorized.

## Source and evidence

Current reconciliation (2026-10-02): platform main
`01cadfe09710d293a40da14d6cf4056165289e31`; workspace main
`b3cf568378e43359e1149e88a65bea139b4b01c9`. Both prerequisites remain
ancestors. Workspace's latest fourteen-file onboarding/Cisco Solution, release
and test/CI/doc delta changes no selected Cove fixture source or boundary
checker. A fresh stdlib AST audit applying the repository's exact authored/boundary
roots and exclusions passes2,112 boundary files (1,989 standard authored), zero
forbidden imports and empty allowlist.
Historical source/test/deployed-image evidence below keeps its original pins.

On 2026-10-01, fresh fetches returned platform main
`ba783472b770291e612433ad7ca9564fe671dade` and workspace main
`970f3030ef66d20abe83fd7ef0e400cd114da80d`. Platform #1001 and workspace #1112
remain ancestors. The workspace's own AST boundary audit found zero forbidden
imports across 2,100 boundary Python files (1,978 standard authored files),
with an empty `PLATFORM_INTERNAL_IMPORT_ALLOWLIST`. Selected workflow and agent
source bytes are unchanged from the retained
`83c1cb034dbbcfa29eb1723506735b13b4788536` provenance pin.

A subsequent fresh reconciliation returned platform main
`bd98a10a647c6dde934e2f9b48328798298e53ef` and workspace main
`19ea0fbb97fcb1701931067e20c279b30e1663db`. Both prerequisites remain
ancestors. The workspace AST boundary audit passed 2,107 boundary files
(1,984 standard authored files), with zero forbidden imports and an empty
allowlist. Selected authored workflow/agent bytes and platform business source
used by these references are unchanged. Platform #1019 changes debug-resource
ownership/cleanup instructions; the candidates retain those current rules.

The next pre-PR refresh advanced platform main again to
`86caddecd8bbcf1feea9b0f5ed7865c0ea724d7b` (#1013, external-worker
control plane). This architecture candidate includes that merge. Earlier
characterization results retain their exact source pins; they are not evidence
for the newly landed scheduler/worker configuration and scaling behavior.
Reconcile those changes before freezing extraction or granting lifecycle
authority. The prerequisite correction and selected reference gates are not
relaxed by this advancement.

Latest source reconciliation fetched platform main
`f10da7c27568e65ee36d5699b72ef3a44ea7a3d8` (#1007, global Solution
execution-scoped root tables) and workspace main
`e8605dc8edb6df8a997c171b534b324ac7ebd8ec`. Both prerequisite commits remain
ancestors; the fresh workspace AST boundary audit passes 2,107 boundary files
(1,984 authored), zero forbidden imports and an empty allowlist. This candidate
includes platform main. The inspected production delta introduces explicit
per-installation organization policy and reviewed multi-scope root-table grants;
legacy single-table recipe serialization is preserved. Runtime grants must
retain exact signed execution organization and reviewed scope selection, rather
than treating a global Solution as unrestricted organization authority. Pinned
reference/compiler and credential interfaces must reconcile this delta before
freeze; earlier CI results remain evidence only for their recorded commits.

A subsequent pre-PR refresh fetched platform main
`fcfbedfe189e8efab0546890828806c91cec358d` (#1021, immutable declaration
replay). Its six-file delta adds retained declaration identity and advances
Alembic to `20261001_ws_declaration_digest`; it changes no auth/SDK/attempt or
deployment helper. This candidate includes that main. CRED-P1 uses the new
migration parent and still excludes workspace-release grants. Earlier evidence
retains its recorded source pins.

An earlier documentation reconciliation fetched platform main
`f770094eb28d8315a414fe8cb306f510da752d89` (#1025, CI capacity guards)
and unchanged workspace main `e8605dc8edb6df8a997c171b534b324ac7ebd8ec`.
This documentation candidate includes that main. Its delta from the prior
`658283e8c` baseline changes workflow concurrency/benchmark job time bounds and
CI-sharding assertions; selected production source is unchanged. Reference
runs below retain their actual earlier source and checkout pins.

### C1-R1: the single authorized additional cycle

The five-cycle stop at #1017 candidate `abf468d70` remains historical:
run `36928399496` failed two comparisons and passed 371 tests. The user then
explicitly authorized exactly one additional diagnosis/repair cycle, with
independent review and unchanged identity, causal-order and drift expectations.

At head `75c84583f33a0a6ddf17b77b3ceee18d99e4a2b3`,
[supported run 36936257108](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36936257108)
passed all 385 tests in 225.68 seconds. Actual checkout was synthetic merge
`9e340dacd4a010750df87ba31597d31bf901a17d`, combining that head with stacked
base `00c3a1075fe09dad8e9c7cd42cee387db5d0ca5a`; the head contains main
`658283e8c`. Artifact `11198098727` retains both repo-v1 and reviewed-Solution
readiness observations. Readback verified PostgreSQL delivery in API, replica,
worker, scheduler and test runner. The job's owned-stack teardown succeeded;
its log shows removal of the owned stack/network/volumes, but no independent
post-teardown empty-resource inventory is claimed.

The focused repair validates actual installation Solution identity before
aliasing its three request/publish/durable-envelope metadata paths. Coherent
and independent-plane negatives passed. Pure vectors materialize the producer's
JSON wire envelope to avoid shallow dictionary aliases. Independent review
caught that alias before publication. Clock canonicalization and equality,
ordering/window comparisons are unchanged; only bounded failure diagnostics
were added. A passing run does not establish the earlier clock failure's cause
or long-term stability. No sixth unprompted or repeat execution occurred:
queued run `36935881851` was cancelled with no executed steps before final lint.
Current-head literal pre-PR proof and ordinary CI remain separate incomplete
gates. This reference pass does not accept runtime extraction, Rust lifecycle
ownership, C2/C3, merge or deployment.

The findings below are source evidence, not an observed agent run. Codec passes,
SDK direct calls, and a rehydrated organization do not prove tool execution.
The pinned readiness workflow reference remains independent and can continue.
Device F01 remains a device-specific STOP.

## Selected Cove agent: original caller authority is lost

The unchanged Escalation Analyst asset is
`agents/387064f6-af8d-4b88-97b4-adbf0207a2fa.agent.yaml`, SHA-256
`b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914`.
Its capacity tool calls `cove.get_client(scope="global")`. The public Python
SDK checks the execution context's original-caller `is_platform_admin` or
`is_provider_org` before sending `integrations.get`; organization metadata alone
is insufficient (`api/bifrost/_context.py:156–188`).

The current agent paths cannot supply that authority faithfully:

- `api/src/routers/agent_runs.py` enqueue, execute and rerun omit caller authority
  flags when calling `api/src/services/execution/agent_run_service.py`, which
  stores false/empty defaults.
- Durable chat preserves those flags at admission, but both
  `api/src/services/agent_executor.py` and
  `api/src/services/execution/autonomous_agent_executor.py` construct
  `AgentWorkflowCaller` without provider/external fields.
- `api/src/services/execution/agent_workflow_tools.py` passes only admin authority
  to `execute_tool`. `api/src/services/execution/service.py` constructs the
  workflow context with the provider flag defaulting false; async dispatch
  freezes it.
- `api/src/jobs/consumers/workflow_execution.py` rehydrates the organization's
  provider metadata but separately forwards the false caller flag. Worker,
  engine and the unchanged SDK preserve that explicit false value.

Thus source predicts local `PermissionError` before SDK HTTP or Cove requests,
even for an authenticated non-superuser provider-organization member. Alternate
chat admission does not repair the tool boundary. An error-valued tool result
may be characterized as a negative case; it must not replace the proposed
successful model/tool/SDK reference acceptance. Rust must not silently repair
this behavior or inherit the engine transport credential's superuser authority.

No complete alternative authored binding was verified: the main Cove agent also
requires delegation; the Halo reporting agent's authored tool UUIDs do not match
current source declarations. Do not replace tools or drop capabilities to obtain
a passing test.

## Authorized Python prerequisite; nominal B remains gated

The user authorized a separate, narrowly reviewed Python prerequisite PR on
2026-10-01, without merge or deployment. Candidate
`31dd41f519b5c510c77a7ac6f70b9400c94a65e3`, based on the platform main above,
carries trusted original caller admin/provider/external/role attributes
through public agent admission, `AgentWorkflowCaller`, `execute_tool` and pending
dispatch. Ordinary admin authority is refreshed from the database at tool
dispatch, including revocation during the model wait; external actors retain
their existing re-resolution. Callerless defaults remain unprivileged. No
authority may be inferred from transport admin or destination organization.

The approved prerequisite is published separately as
[#1018](https://github.com/Midtown-Technology-Group/bifrost/pull/1018).
Its current main-reconciled candidate is
`b6b20a43dafed1fdd446d7026471193a30873ee4`; the original behavior candidate
above passed supported hosted CI, including 11,533 unit tests, all four backend
E2E shards, and the real-service delivery failure matrix
([run 36895595796](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36895595796)).
Independent Sol review found no concrete regression; automated review of the
original candidate reported no actionable comments. The reconciled candidate
has a separate CI run. These checks do not establish nominal Cove-agent
acceptance. Local application proof remains unavailable because the task's
PostgreSQL startup failed at Unix-socket permissions; all three confirmed
task-created failed debug projects were cleaned up, with successful empty
container/network/volume readback. CodSpeed cross-environment failures remain
blocking under #1003; no thresholds, retries or waivers are introduced.

Automated security review separately retained a missing-caller concern despite
having no line-threaded requested changes: chat can convert a supplied
authenticated user that no longer resolves into callerless authorization while
retaining its captured provider claim. Source review confirmed that transition.
AUTH-P1 must reject the unresolved authenticated caller before workflow dispatch
and prove it cannot invoke the execution sink; truly callerless invocation must
remain distinct. The correction is under narrow implementation/review and is not
yet passing evidence. Provider/external snapshot behavior otherwise follows the
existing contract, not a new global revocation policy.

Current AUTH-P1 status: separate #1018 remains open and unmerged at
`d82219f5aada66d879f2da71b386f50675c66d4d`. The missing-authenticated-caller
correction above is included in that candidate;
[supported CI 36903534996](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36903534996)
passed at that exact SHA. The earlier candidate and under-implementation
dispositions are historical, not the current source status. No unchanged
Cove model/tool/SDK nominal result, merge or deployment follows from CI.

This correction deliberately preserves existing run/rerun execution-org
expressions. Their dual-use caller/target organization is **not** repaired by
privilege propagation. Separate original-caller and effective execution scope
remain a full runtime-protocol gate; this candidate must not be represented as
having completed that separation or broader privilege-revocation policy.

Required tests include provider non-superuser, ordinary tenant, superuser and
external principals; enqueue/execute/rerun/chat; foreign-org and revoked-grant
negatives; exact signed delegated caller/fence; unchanged capacity tool with
actual SDK and strictly synthetic Cove transport; cancellation; and credential
non-disclosure. Scope approval is not passing evidence: independent review,
supported candidate checks and the real unchanged agent/tool/SDK reference are
still required. Current main remains the uncorrected reference; an unmerged
corrected candidate must be named explicitly in any comparison. Nominal Cove B
remains blocked until those gates pass, and C2/C3 cannot invent flags.

## Preserve real model bootstrap and summarization

`api/src/services/ai_model_service.py` automatically enables the first profile
for chat and creates every model assignment, including summarization. The
original proposed empty-assignment fixture was false. A dedicated disposable
reference installation must observe this actual bootstrap behavior, without
customer settings or global assignment reuse. Public `AgentCreate` can project
all unchanged declaration fields with explicit installed-ID/profile binding;
this is not a claim that the legacy YAML was imported byte for byte.

Completed agent runs atomically enqueue Python's existing summary delivery.
Exercise the real `api/src/services/execution/run_summarizer.py` model/parser,
summary fields, metadata merge, usage and event commit through a distinct
synthetic transcript. Summary intentionally omits the agent's max-token setting.
Public deletion cannot remove required primary/chat assignments, so retain the
owned bootstrap until the supported isolated installation is disposed of; do
not privately erase assignments to make cleanup pass. Model emulators prove
transport/runtime behavior, not live provider quality or compatibility.

Isolated reference environments do not establish safe mixed Python/Rust writers.
That later gate still requires both implementations against one schema/database,
mechanical exclusion, concurrency, and in-flight rollback without data repair.

## Deployed delivery configuration: partial read-only evidence

On 2026-10-01, the documented Azure operating lane was read without mutation:
`app-mtg-bifrost-production`, resource group
`rg-mtg-bifrost-poc-core-centralus`, subscription
`a1d63b24-1202-4bfa-9086-cf32d1d352fc`. API, worker and scheduler site-container
configuration references app settings. Selected settings read back
`BIFROST_WORK_DELIVERY_BACKEND=postgres`, production environment and concurrency
values of four. These are configured references/values, not effective process
readback for every role or instance.

At 16:51:32 UTC, an unauthenticated, redirect-disabled GET to the deployed
`/health/ready` returned 200: database and Redis healthy, work delivery healthy
with type `postgresql` and provider `postgres`, RabbitMQ `not_configured`.
This is one actual API response, not worker/scheduler runtime evidence.
Deployed immutable image identities were:

- API and scheduler: `ghcr.io/midtown-technology-group/bifrost-api@sha256:fe4cfca0ec804f763de04649a373580c0520f6529fba1198970ace344ba62996`.
- Worker: `ghcr.io/midtown-technology-group/bifrost-worker@sha256:81b15c59388a70179fb9fe42adb27f2efb3fdd02d549723684c020057c89b58c`.
- Frontend: `ghcr.io/midtown-technology-group/bifrost-client@sha256:f7189555a9821d0b3cd591f5186898789afbe0ea53503ef4bd8fb6481f3d321c`.

The source/deployment gate remains incomplete for effective worker/scheduler
settings. Do not infer their backend from checked-in defaults or treat these
image digests as proof that current main is deployed. No RabbitMQ Rust support
is authorized; any discovered relevant RabbitMQ runtime is a separate future
worker-cutover prerequisite.

## C1-Q2 sampled byte experiment: supported evidence

[#1020](https://github.com/Midtown-Technology-Group/bifrost/pull/1020) candidate
`9ca29b1cfca4df6de18266c6d987938937eaf7ff` passed the supported
[control/oracle run 36922225572](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36922225572).
The actual synthetic merge checkout was
`e768f6444a5792389048c2b834957c75c6e2d553`, with Python image
`sha256:c381dcf4b11d70a59fbf68f745e61a0f5995ab9c9f16cc9060946fd5afaf348c`
and Rust checks image
`sha256:22bca4693bc7dc2b9474a9444b726baaf87c3c3dc9f338a51a5be344e606c9f8`.
The peer ran CPython 3.14.7; the Rust contracts package remains 0.1.0.

The independent peer verified actual Rust-emitted bytes/hashes for all 64
historical source witnesses (61 encoded, three rejected), retaining artifact
SHA-256 `1be1929bca57e1204a33bbad9ff2d15338adf9f4862ce26b13280917c19d514a`.
The generated campaign comprised 40,940 exponent cases, 82,142 decimal-power
neighbors, four explicit nonfinite cases, one million deterministic raw-bit
patterns (seed `0000b1f2057c1a02`), and 14 string cases. It matched 1,122,627
finite byte outcomes and 473 nonfinite rejections. Its framed matched-byte
digest was `67a2930a94b44a12f6aa619188fdfe534c5cca7b865bff368277a346f68b74be`.
A coherent deliberately altered UTF-8/hex pair failed at the expected exact-byte
drift, proving the oracle did not merely compare candidates to themselves.

Candidate `30131dab0bd16ceccd57a1ebf6964d47a2e2de15` had failed before the
oracle at Clippy; the next candidate corrected two source lints without changing
thresholds, dependencies or fixture bytes. This was a changed-source repair,
not an unchanged rerun. The pass covers sampled numeric/string compatibility
and historical fixture projections, not a production canonical serializer.
Named repr regressions, targeted ties, bounded-tree/permutation fuzzing, broad
Unicode coverage, transport depth limits and full model validation remain the
documented gates in #1020. No C2/C3 authority, new-main source capture, runtime
extraction, vendor use, merge or deployment follows from this result. Other CI
and normal local/live proof gates retain their own dispositions.

## Latest unchanged-input reconciliation

The latest documentation pre-PR fetch advanced platform main to
`e58db4955ddd30177bd613f1d85b7e203ad7832a` (#1027) after initially fetching
`f770094e`; the gate correctly rejected that now-stale candidate. Source review
of the nine-file handoff delta and a clean branch refresh preceded the final
passing docs gate. Workspace main remains
`c1856d2fbc7530c65c67adfd28e4896ec402ccbf`. The latter changes only
`features/ninjaone/scripts/Dell.Idrac.EnterpriseLicenseMonitor.ps1`. Selected
Cove authored bytes and the boundary checker remain identical to retained
`e8605dc8`. The workspace-owned static AST audit again passes 2,107 boundary
files (1,984 standard authored), zero forbidden imports and an empty allowlist;
#1112 remains an ancestor. No authored source was imported or executed.
The [C1-R packet](rust-core-mvp-agent-reference.md) distinguishes current main,
retained source blobs and the approved unmerged AUTH reference overlay.
Current #1027 handoff verification separates historical byte custody from
active/dependency contracts and repeats omission proof under release/Solution
locks; it does not establish runtime/session or Source-obligation drain.
C1-R foundation `7b24f9d` still names its retained `f770094e` base and approved
unmerged AUTH overlay; no old runtime pass is transferred to newer main.
This reconciliation does not restart the exhausted #1017 repair cycle.


The subsequent C1-R codec candidate `ef752000299b299d804e8f945195e5eb9e20168d`
contains e58 and passed 249 selected codec unit cases plus ordinary API quality
in its own [supported CI](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36950604053).
The [C1-R evidence packet](rust-core-mvp-agent-reference.md#pure-codec-supported-execution-evidence)
records exact checkout, job IDs, broader suite skips and remaining nominal gates.
This is new-candidate codec proof, not transfer of the historical foundation's
source review into model/tool/summary or mixed-writer acceptance.

## Current workspace reconciliation and reference integration

The preceding reconciliation used platform main `e58db4955ddd30177bd613f1d85b7e203ad7832a`; workspace main advanced to `9941427941587d253540942b714c5e2b7abfc410` (#1129). Its two-file delta from c185 changes Ninja alert reconciliation and its unit tests. Selected Cove sources and the static boundary checker are unchanged. The exact detached 9941427 checkout passes the workspace-owned AST audit: 2,107 boundary files, 1,984 standard authored files, zero forbidden imports and an empty allowlist. Workspace #1112 remains an ancestor; no authored code was imported or executed. The earlier c185 snapshot above remains historical evidence.

The [reference integration specification](rust-core-mvp-agent-reference-integration.md) now records private observer TLS, verified-reader mode0600 capability ownership, role-isolated status mounts, the dedicated host-status directory, named-only staged reset bypass and retained transient init custody. These are bounded reference-lane construction decisions, not public authorization changes, C2/C3 acceptance or a new #1017 repair cycle. No actual nominal model/tool/summary run, live vendor call, merge or deployment follows from this source review.

## Latest main and oracle source reconciliation

The next literal documentation pre-PR refresh advanced platform main to
`01cadfe09710d293a40da14d6cf4056165289e31` (#1028). The stale docs candidate
correctly failed before checks; root inspected the six-file delta, then merged
current main locally. #1028 retires the CodSpeed workflow/dependency by maintainer
direction, updates its workflow test and performance methodology, and changes
only a comment in Compose. Selected product, SDK, agent, auth and schema behavior
is unchanged. #1003 is closed as not planned; historical cross-environment
measurements remain unsuitable decision evidence, not a current required CI
check. No Rust comparison is inferred and no threshold was weakened here.
Workspace main remains the exact audited9941427 above.

The [model oracle](rust-core-mvp-agent-model-oracle.md) closes the selected full
model/Cove transcript and adds only bounded private construction/readback
families. Its shared codec and meaningful negatives passed independent SOURCE
review; fixture/observer loader and retained-witness defects were repaired in
uncommitted source. Actual tests, installed image/custody, real public worker
trigger, DB/event/usage/result/summary joins and closure remain required.
#1017 stays STOPPED after its one extra cycle; neither new docs nor source
review transfers its385 passes into a healthy ordinary CI gate. No merge,
deployment, vendor use, H/R or C2/C3 acceptance is authorized by these updates.


## Current foundation execution disposition

C1-R construction candidate `bb74d2b72c4a7f0a4f9eab4a045e7aa2e64ccdae`
contains platform main01cadfe and the unchanged AUTH-P1 source at
`fe69d963dffc8057b07a9438cb547a6e4b906f9a`. The latter PR#1018 now has
passing current-head ordinary checks; it remains unmerged. Its ten owned source
and test files are byte-identical to the previously reviewed AUTH candidate.

The foundation's own [supported run36959618248](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36959618248)
is **FAILED**: quality reports five Pyright test-double errors, the full unit
job reports12,144 passed/15 failed/3 skipped/35 deselected, and the literal
pre-PR job fails on those type errors. Its uploaded artifact11206864753 is
failure evidence, not a passing gate. Relevant unit defects involve resource
preflight test prerequisites, preserved configured entrypoints, TLS test
connection routing and a CPython3.14.7 decoder assumption. Repairs require
independent source review and a new supported candidate run; no expectations,
authorization boundaries or deliberately detectable drift may be waived. The
unchanged observer already converts invalid body projections into retained
failed signed witnesses; source inspection corrected a preliminary root
suspicion that this path was absent. The real finite nested-body invariant
remains mandatory, while lowering Python frame recursion limits is not proof
of a C JSON decoder exception on the selected runtime.

The full model/Cove fixture is a separate uncommitted source candidate. It has
not run and is not represented by this foundation run. Private materials,
actual installed-image/capability custody, public worker trigger, independent
DB/event/result/usage/summary joins and consumer closure are still incomplete.
No nominal agent reference, Rust parity, mixed-writer safety or MVP acceptance
follows from these checks. #1017's exhausted cycle remains STOPPED.

## Reviewed fixture repairs and second execution disposition

The independently reviewed materials generator and full model/Cove fixture are
now committed on the bounded C1-R reference branch. Candidate
`ba4f6a758a0d010b8a690a80142422494b37f50e` includes current platform main01cadfe;
its [supported run36962193811](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36962193811)
passed lint/type checking and the literal clean-candidate `./test.sh pre-pr`.
Root inspected artifact11207854885: candidate tree
`ca3b4116ee5fa214d1f9f277bdf89e8d686eeb6e`, main01cadfe, exit0 and actual empty
owned container/volume/network queries after teardown. The supported image
identity is `sha256:d47d3993d624783ffbac6da68c76ee91dd06e485ee1842c3b4bb6022811fd181`.
This pre-PR planner does not substitute for backend unit or nominal-case proof.

The same run is **FAILED** overall: unit tests report12,379 passed/17 failed/
3 skipped/35 deselected. Eight failures patch OpenAI's nullable public `_client`
singleton instead of its implementation module; nine reach actual TLS but reject
the unit fixture's ambiguous same-subject CA/leaf chain. Reviewed candidate
`bb3d3fd6720e897badcc6e3f703bea1b3af28422` changes only those two unit fixtures:
explicit module import; distinct CA/leaf subjects and proper certificate
extensions. AST comparison retains all header vectors and assertions, and all
observer code outside certificate construction is unchanged. Actual TLS/HTTP,
certificate and hostname verification, fixed SNI, absent-trust/SAN/expiry
negatives, redirect rejection and ambient-environment attacks remain mandatory.
Locked Ruff0.15.12 and format checks pass. Its own
[supported run36963527229](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36963527229)
has passing lint/type checks and literal pre-PR job110702387902. Root inspected
artifact11209062488: exact bb3d3fd tree
`5de2091717211c0ad6c38e363b612125561bc0e2`, current main01cadfe, exit0 and
empty owned container/volume/network queries after teardown. Unit job110702387913 subsequently passed12,396 cases/3 skipped/35
deselected in331.96 seconds on CPython3.14.7; the overall supported run, including broader E2E, subsequently completed
SUCCESS. The prior failed runs retain their actual dispositions.

The nominal public case, verified reader/image/interpreter custody, privileged
host closure interfaces and complete DB/event/usage/result/summary joins remain
unimplemented or unexecuted gates. No Rust parity, mixed-writer acceptance,
merge, deployment, vendor use or C2/C3 acceptance follows. These C1-R repairs
are independent of #1017; its exhausted extra cycle remains STOPPED.

## Public agent setup review disposition

Independent source review of the bounded public case proposal against unchanged
product source through bb3d3fd found three required corrections. Public
registration/MFA/provider-role setup, both immutable workflow installs, actual
Agent creation and the first AI profile's six assignments are source-supported
in direction; no executable nominal case is yet accepted.

The selected collector must retain every actual selected-channel publication,
including genuine detail/all/org fanout duplicates: maximum64 business
publications across pre- and post-binding together, each raw payload at most
65536 bytes, aggregate payload at most4194304 bytes. Prebinding consumes the same
budget. Overflow permanently fails the case; no eviction, deduplication,
silent discard or synthetic ordering is allowed. This is a bounded collector
contract, not a Redis client allocator or decoded-object memory guarantee.
Subscription acknowledgements require separately bounded handling and cannot
provide additional business-event capacity. Exact ACK deadlines, raw-byte
projection and joined reader predicates remain frozen-interface prerequisites.

After the already selected public synthetic-password SECRET update, perform a
fresh `GET /api/config?scope=global` and verify the same actual row/key/integration/
organization, secret type and `[SECRET]` mask. The PUT response contains stored
encrypted material and is private; it is not the masked readback. Actual
private equality/SDK/Cove Login remains separate decryption evidence.

Bootstrap eligibility must permit independently inventoried migration/startup
system state while requiring actual public `/auth/status` needs_setup=true,
no qualifying human, no default-user bootstrap and no selected preexisting
business/AI/deployment/tool/run state. Do not DELETE or seed state to pass.
Subsequent public registration requires actual testing/development mode; do
not bypass production registration or MFA restrictions. Exact original admitted
caller context and child/source/attempt/delivery/summary joins, recipe bounds,
privileged host interfaces and nominal closure remain open release gates.
These source-backed corrections do not change product authorization or approve
public CRED H/R, C2/C3, Rust parity, merge or deployment.

## Repaired construction unit evidence

Run36963527229 at exact bb3d3fd passes all725 selected construction units,
with zero failures/skips in these files: fixture180, observer65, materials46,
shared contract249, model contract61, server36, lane88. These include real
unit TLS certificate/redirect negatives and the mandatory materials-tool
exercise. Root read actual completed job110702387913 logs; SHA256
`acc376537171c1403b1e465277a0052a0792e75b09d195e29590e572d75efae8`.
This closes the previously recorded17 unit failures without expectation waivers.
Supported quality and literal pre-PR also pass against this candidate; the complete
supported run subsequently finished SUCCESS. Unit construction evidence does
not execute the public nominal worker case, prove installed process custody,
or satisfy DB/event/result/summary/closure joins, Rust parity or C2/C3.

## Current main and bounded collector advancement

Platform main remains01cadfe09710d293a40da14d6cf4056165289e31; workspace
main advanced to `e33f2b30c5318a9a6b21220c7c351202cb5b0cbd` (#1130). The
12-file delta adds reviewed production Source-delivery inputs/dependency review
and advances the workspace SDK lock to platform e58/image digest c5924a79.
Selected Cove authored sources remain unchanged. The exact detached e33 checkout
passes the workspace-owned AST audit:2,109 boundary files,1,986 standard authored
files, zero forbidden imports and empty allowlist. #1112 remains an ancestor;
platform #1001 remains an ancestor of current main. This is source-boundary
proof, not deployed SDK or authored-runtime execution proof. Historical retained
Cove fixture provenance and supported API image identities remain their actual
pins; neither is silently relabeled as the current workspace SDK installation.

The [bounded event collector interface](rust-core-mvp-agent-event-collector.md)
releases only a two-file test evidence package. Genuine selected publications,
channel fanout duplicates, ACK gating and prebind/postbind budget are explicit.
It cannot itself certify a real Redis subscription, public nominal run or
committed domain result. Exact lineage predicates, real subscription transport,
privileged host custody and nominal consumer remain separate integration gates.
No generic Redis/WebSocket port, #1017 restart, vendor call, public CRED authority
or C2/C3/Rust acceptance is authorized.

## Event evidence implementation candidate

Two-file test-only candidate `da9dac5c9` implements the selected bounded
collector plus characterization units. Independent review found and closed
three source findings before supported execution: parser failure raised outside
exception scope to avoid retaining decoder payload exceptions, a type-compatible
frozen-record mutation test, and decisive original raw-byte preservation rather
than merely parsed-payload equality. Exact module SHA256
`16302c29b324a3ebd2524888e14c4d230dc1a278aa644d80d24254a0986ca537`;
unit SHA256 `5ab934b6158e29f4fec9518703c549dd7eb1f4bb66d8c37769f63be305fb8bfb`.
AST and locked Ruff0.15.12 check/format pass. Supported run
[36966147194](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36966147194)
against exact `da9dac5c9d4cf8b26401bd01ea20d5f9914a9a75` completed FAILURE.
Unit job110710414527 passed12,486 tests with3 unrelated skips and35 deselections,
including all90 new event collector cases; API and browser E2E jobs passed.
Pyright in both quality and literal pre-PR rejected the JSON traversal at
`agent_reference_events.py:80:60` (Never is not iterable). This is a blocking
new-source typing defect, with a bounded typing-only repair under review;
no suppressions, narrowed JSON behavior, or test expectation waiver is allowed.
It adds no Redis connection/adapter or nominal run and does not transfer the
prior bb3d supported success into new-source acceptance.

The [public case setup packet](rust-core-mvp-agent-case-setup.md) records the
selected reviewed recipe metadata and corrected public-state/SECRET/collector
readbacks. Full consumer release still requires exact lineage and host-custody
interfaces and actual supported construction/closure proof. Current source
derivation distinguishes parent generic attempts from child workflow attempts,
and retains baseline NULL parent transport-token linkage. No fabricated
parent/child foreign key, same-request authority observer or fresh SDK lease
validation is permitted merely to make a test pass.


The root-reviewed repair `6a96f206337216d43e60bf921b770f0d162d0fa3`
adds only `list[tuple[Any, int]]` to the heterogeneous JSON traversal worklist.
Annotation-normalized executable AST is unchanged; all90 event test bodies and
unit-file digest remain unchanged. Locked Ruff0.15.12 check/format pass.
Repaired module SHA256
`d6cebce252dfea114d5976db8162291ced1c915b69d110d2d8b0e70b56a0bbde`.
New supported [run36967563219](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36967563219)
is pending; the prior run's unit success does not certify repaired-source
quality or literal pre-PR. No #1017 rerun or repair is included.


## Latest workspace boundary readback

A subsequent fresh fetch advanced workspace main to
`e1d3ba3880c62c20dd66ec237dc93c062db885c4` (#1131). The13-file delta from
retained e33 adds reviewed Cove/AutoTask delivery inputs and adopted-contract
closure/release checks. The four selected authored Cove fixture sources and
workspace boundary checker remain unchanged. Exact detached current-head
stdlib AST audit passed:2,111 boundary files,1,988 standard authored files,
zero forbidden platform imports, zero SQLAlchemy imports and explicitly empty
platform-internal-import allowlist. #1112 remains an ancestor; platform main
and #1001 ancestry remain unchanged. This is source evidence, with no product
import or deployed/runtime proof. Historical source/image/test pins stay exact.

C1-R-RECIPE is separately released for source construction: a two-file test
helper/unit package builds the exact bootstrap-only and retained-bootstrap plus
both-tool recipes through the existing public SDK AST compiler/reviewer.
Actual public-generated Solution and Role UUIDs remain caller inputs. Recipe
controls/bounds/source paths and byte digests are frozen in the setup packet;
no arbitrary authored import, HTTP/ORM registration, source mutation, metadata
relaxation, nominal case or private authority accompanies this release. Root
and independent review plus supported compiler/unit/quality gates are still
required before the nominal consumer may use it. Privileged helper source is
not released by this independent package.


## Privileged evidence complexity hold

The proposed protected host helper is still source-design-only. Root holds its
implementation release: a custom Unix-socket HTTP/header parser, privileged
bootstrap and cross-invocation registry must justify their complexity against
the explicitly trusted isolated coordinator/Docker-root boundary. Extra archive
path-stat/timestamp claims are not public device/workflow contracts and are not
reason to create a large security-critical subsystem for reference evidence.
The selected simplification for review is fixed bounded Docker CLI archive
reads from the exact stopped normal-init ID, single-member tar and pinned
source/config/image/state checks, with no original inode/nlink claim. Protected
source snapshot and the minimum repeated-publication/cleanup ownership evidence
remain required; no resumed/adopted session is accepted. This is a design hold,
not accepted runtime custody or a replacement for real process/reader proof.

Recipe units need the actual carried bytes in ordinary supported CI. Root owns
a separate test-only integration to mount that exact asset directory read-only
in test-runner and keep exactly one identical mount in the named lane. Missing
assets must fail rather than skip, embed substitutes or alter authored code.
The two-file recipe builder does not own Compose/renderer changes and cannot
certify this unimplemented mount gate through source-only unit construction.


## Repaired collector supported acceptance

[Run36967563219](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36967563219)
completed SUCCESS against exact
`6a96f206337216d43e60bf921b770f0d162d0fa3`, tree
`588f0efd78e3a15c859cdb5e4965bc99b22c1639`, platform main01cadfe.
Quality, units, literal pre-PR and API/browser E2E passed. Actual unit
job110714647059 records12,486 passed,3 unrelated skipped,35 deselected;
all815 selected construction/evidence cases passed with zero selected skips:
fixture180, observer65, materials46, shared contract249, model contract61,
server36, lane88 and events90. Actual unit-log SHA256
`19c034dd566f9d7898fad19ef0bc180e914414393058b9db5c51934f98b19b8e`.

Root read pre-PR artifact11209974788: workflow/head/tree/main matched;
exit0, cleanup_status0 and actual containers/volumes/networks all empty.
API image ID is
`sha256:d47d3993d624783ffbac6da68c76ee91dd06e485ee1842c3b4bb6022811fd181`,
reported digest
`ghcr.io/midtown-technology-group/bifrost-ci-api-dev@sha256:91986f5895c07d9b5dbde7f263de8125f4999e30ab99123a3c89778312689f8b`.
This accepts the bounded collector/construction source only; actual Redis,
public nominal, process custody, committed lineage and closure remain gates.
It does not accept later uncommitted recipe/mount changes.

The same artifact reports Docker server28.0.4 and Compose2.38.2. Independent
primary-source review confirms server28.0.4's source default API1.48, so the
held privileged packet's fixed API1.51 is not a supported-platform assumption.
Live daemon maximum was not read. The selected fixed CLI archive simplification
avoids that custom API parser. Compose2.38.2 source supports the selected
normal-init flags; installed CLI/config/init/runtime proof remains required.
No host Docker/runtime command was executed to obtain these hosted artifacts.


## Reviewed lineage and public recipe candidates

The [lineage contract](rust-core-mvp-agent-lineage.md) freezes source-correct
Q0–Q7 predicates after independent review closed a wrong workflow-attempt field
name and a missing generic known-ID exclusion query. Q4c binds actual UUID R/E
to execution_attempts.logical_job_id, requires PA only across all types, and
rejects a second row before unbounded loading. No fake parent/child FK, NULL
transport token or atomic cross-request authority proof is introduced.

Separately, root reviewed and committed test-only asset integration e9aa290ea
and public-SDK recipe helper/unit candidate
`a9020248ef370a914eec3ab4271628450e32f44b`. Independent mount review SHA256
`d89be149db62304f99f2a550c4614d5123c65dd421be206a4eecec3ad3867df0`;
independent recipe review SHA256
`47b947317ade461147089f73201a832a4efecd756a16cfbc75467c4fabc70afe`.
Module SHA256
`dc0c99869005f19a2a1c71ba7a868cd39c3beebd97bffa47f15a25d9cd872f98`;
unit SHA256
`fa42d02c80e40abf92688707e21dbaad5da8d766418169aeb95bcb092f4b6656`.
AST and locked Ruff0.15.12 check/format pass. Supported
[run36968919042](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36968919042)
finished FAILURE: quality and units passed (12,584 passed, 3 unrelated skips,
35 deselected; all 93 recipe and 93 lane cases passed), but literal pre-PR
failed because ordinary `/app/reference-assets` exposed intentionally imported
bootstrap fixture source to backend Ruff. Artifact11210927112 records failure,
cleanup_status0 and actual empty containers/volumes/networks; cleanup does not
turn failure into acceptance. Prior 6a96 acceptance does not transfer. Public models preserve their existing
shallow frozen semantics: future consumers must verify actual submitted recipe
bytes, not treat mutable nested containers as deep immutable authority.
No authored import/registration, product API change, host helper release or
nominal C1/Rust acceptance is included.


## Asset-layout repair and dedicated host source release

Root-reviewed candidate `a01437e68948ec08d96c070229e93144f40cde43`
changes only Compose/renderer and their lane/recipe units. Ordinary assets are
read-only at `/repo/reference-assets`, outside backend Ruff's `/app` root,
with the explicit private asset-dir environment. The strictly validated named
renderer remaps exactly one asset bind and that environment to the existing
`/app/reference-assets`. No fixture source, helper behavior, lint rule,
exclusion, suppression or SDK/product bytes were changed. Wrong/missing env,
wrong role and extra/writable mounts fail closed. Independent repair review
SHA256 `231e1823c24b0cb3b985db26be6b371250fd09d733bd64c07fc1a9d6911162b0`.

Supported [run36969881943](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36969881943)
finished SUCCESS at that exact head: quality, literal pre-PR, all API E2E
shards, MCP conformance and client suites passed. Units: 12,587 passed,
3 unrelated skips, 35 deselected, including all 93 recipe and 96 lane cases.
Actual unit-log SHA256
`2a440c520f696329ffda3fbd14408cfbf3655d0fb0566bce80032a43d1a5f57b`.
Manual pre-PR artifact11211766041 records matching workflow/head a014,
tree `3a40b3d60589355f0e4dffd47210812e377e42a0`, main
`01cadfe09710d293a40da14d6cf4056165289e31`, exit0, cleanup_status0 and actual
empty containers/volumes/networks. This is foundation/compiler/closure proof,
not nominal agent execution, installed privilege custody or Rust parity.

The [dedicated host interface](rust-core-mvp-agent-host-interface.md) now
releases only C1-R-HOST-S's two source files after final independent proposal
review. It supersedes generic-renderer elevation and pathname chown; fixed
isolated bootstrap, protected sources and descriptor operations remain required.
Actual helper bytes/record/bootstrap require independent source review and
supported tests before runtime. No privileged command was run on pve-t340.

The public setup contract also corrects the observation split: immutable
registration metadata lives in manifest/resolution, while actual Workflow
columns provide the persisted projection. No invented columns/schema change.
The material-reader contract, its independent oracle/credential/storage
adapters and nominal consumer remain gated; green units do not satisfy C1.


## Material-reader source interface disposition

Independent review found two mechanical gaps in the original proposal:
per-cell caps did not enforce aggregate fetched material, and asyncpg
Transaction context exit could await rollback beyond the observer deadline.
The [corrected material reader](rust-core-mvp-agent-material-reader.md) freezes
single-row same-statement per-cell/remaining-total admission, exact charging
and fixed-charge preflight; fixed timed transaction control and narrow emergency
termination affect only the dedicated observer connection. Uncertain exit has
no snapshot, recovery/reuse or server-cleanup success claim. Primary source is
[pinned asyncpg0.31 Transaction](https://raw.githubusercontent.com/MagicStack/asyncpg/v0.31.0/asyncpg/transaction.py)
and [Connection](https://raw.githubusercontent.com/MagicStack/asyncpg/v0.31.0/asyncpg/connection.py).

Final packet9eb08293 and independent review518f3ef6 release only
C1-R-MATERIAL-S's two source paths. Pre-enqueue SetupIds never fabricates R;
RunIds uses actual public202 R; E remains unknown until committed Step4.
Authenticated Agent has zero role joins, separate from the two workflow grants.
A legitimate pre-Step4 capacity row stays unattributed, not an E discovery.
The reader has no partial oracle acceptance. Full semantic oracle, private
adapters, raw connection/secret custody, live Redis evidence and nominal closure
remain outstanding. Instrumented units will not constitute committed DB proof.

The dedicated host source package also resolves its own-digest circularity
through a pure fixed-template constructor. Exact emitted literal and committed
release pins require review and freezing BEFORE any privileged execution;
no dynamic caller-supplied digest creates authority. No bootstrap was executed.


## Host helper complexity STOP and unresolved oracle findings

C1-R-HOST-S stopped under its explicit complexity condition: the custom elevated
helper draft reached854 lines before bootstrap/disposal/tests, estimated above
1100 if completed. Retained fail-closed untracked source SHA256
`84937e4ebf8ace19a8ee618e7200074f2f00eaf387e0077eaba33058195a443a` is not
review-ready and must not execute. Only AST/locked Ruff ran. No privileged
operation, Docker/runtime, bootstrap or helper unit test occurred. Earlier
source release is suspended for this component. Root requested source-only
comparison of smaller fixed operations under the already trusted coordinator
versus provisioning in existing owned init/fixture processes; neither is released.
The goal is to remove a custom elevated framework, not redistribute its lines
or weaken secret custody, role exclusions, TLS, actual readers or closure.

Independent full-oracle interface review SHA256
`d026ad14ff4c4ddc66c973f9cfe407071984df3b008638c23f8c819351165220`
found five source-freeze gaps: parent/summary publisher projections, frozen JSON
versus codec dict/list inputs, raw event parsing ownership, boolean/numeric
comparison and lifecycle/fresh-snapshot provenance. The proposal remains
unreleased pending correction; no partial oracle can satisfy C1.

Actual parent source assigns its existing run_obj after commit rather than
rereading summary changes (agent_run.py565-577; expire_on_commit=False).
Summary publishes its separately loaded object after its own commit. Event
receive order is still not a global DB/process clock order. A workflow attempt's
process_id is the pool handle `process-N`, not an OS PID; public worker detail
reports a cached heartbeat PID and sets is_alive=True by inference. A future
actual-process witness must join real OS/container observation to that handle,
not turn either field into liveness proof. These are unresolved concrete gates,
not normalization opportunities.

## Reviewed smaller host design and committed-read acquisition

Root accepts [minimal host alternative A](rust-core-mvp-agent-host-interface.md)
within the existing trusted CI Docker-admin coordinator. Independent review
`c57438fe661b0c79d0f82003d2898b85b4313b87e464eca9092d18cabeba0474`
accepts the explicit removal of root-local framework/state, unchanged-codec
validation outside elevation, and stock deletion of the saved owned status
volume only after verified consumers/references are gone. Its discovered
prior-inode gap is closed by actual final FD identity/body in a bounded private
publisher receipt. [Literal source interface](rust-core-mvp-agent-host-literals.md)
was frozen after different revision2 review3f8ee97d for two-file source
work package C1-R-HOST-LITERAL-S. Its common validation prefix alone emitted
67x3=201 executable lines before operation bodies; independent8902d169 confirms
SOURCE STOP. Exact incomplete draftbd06 is archived mode0600 outside Git and
only its owned untracked source removed. No helper, further authoring or
privilege/runtime release. This construction failure is not an impossibility
proof for all implementations or the Rust strategy. The failed862-line draft84937
is preserved outside Git at the recorded mode0600 path, not executed or restored.
Secret/TLS/role/source/actual-reader/closure gates are unchanged; volume cleanup
never repairs failed evidence into acceptance. Alternative B fails actual
entrypoint/RO-material/preexisting-subpath/host-disposition prerequisites.

The [material-reader checkpoint](rust-core-mvp-agent-material-reader.md) retains
exact current source hashes, independent review and supported failure/repair
provenance. Acquisition reader078aaabd is committed unchanged in e3bed7f8;
units62908 fix three characterized fixture/assertion errors, preserving type
negatives and all pre-Step4 child exclusions. Run36978794042 FAILED11 units,
although quality/pre-PR/other lanes passed. New changed-candidate run36980564135
is SUCCESS:187 selected reader cases/12,774 total backend units pass, with
3 unrelated skips/35 deselections; quality/pre-PR/other required lanes pass.
Artifact11215402516 preserves exactsource/exit0/actualEMPTY. No actual PostgreSQL
observation or nominal acceptance is inferred.
The separately reviewed [read-acquisition supplement](rust-core-mvp-agent-read-acquisition.md)
releases only the SAME two source paths. It adds actual PostgreSQL16 own virtual
transaction identity before/after each readonly read and reader-internal real
BEGIN/COMMIT monotonic samples, with a conservative4096-byte reservation within
the unchanged1MiB/3-second caps. Independent interface reviewfbe0b69 accepted
it with explicit FAILED/no-snapshot identity-change, stateless repeated-identity
exposure and sampled-completion deadline checks. Source packetdab896c7 includes
those conditions. PostgreSQL metadata alone cannot order acquisition after real
closure; the actual consumer's awaited call/closed receipt joins remain gated.
No migrations, permanent XID allocation, connection acquisition/recovery,
domain writer, public credential authority or oracle acceptance is introduced.

These packages do not restart #1017's exhausted repair cycle. Actual installed
PG/OS/process/mount/privilege evidence, bounded immutable source/canonical
adapters, complete semantic oracle and genuine nominal closure remain required
before any Rust lifecycle or C2/C3 acceptance.


## Remaining adapter and actual-process source gates

Material transport/canonical adapter packetdce31021 has different design review
0ae2c2d7 ACCEPT WITH CONDITIONS. Preserve actual same isolated source-defined
synthetic application secret; no guessed fresh secret or normal-init change.
Presigned URLs currently leak through HTTPX INFO logging: the dedicated observer
must disable Python logging before dependency imports/use, without changing API,
worker or collector logs. Closed clients reject cookies/Set-Cookie and redirects,
use trust_env=False, bounded raw chunks and one absolute deadline including
actual response close; uncertain close retains owner and no objects. A206 must
cover the complete admitted object. Exact client/secret/source custody and
T/C typed acquisition seams remain pre-builder gates; no implementation release.

Actual-process packetd2c5484e has different review4267739e ACCEPT DESIGN but
STOP exact implementation release. Root selects diagnostic-only heartbeat1s
and validated synthetic Cove Login latency4s within unchanged60/120s limits,
not a benchmark/default change or guaranteed window. Cached is_alive is still
not OS liveness. Bounded native Redis GETRANGE, actual runner/host causal ordering
and retained ancestry comparisons need freezing before source authoring.
Neither conditional design nor the187 injected reader cases establishes full
nominal source/process/closure/authority. Narrow real PostgreSQL reader/schema
characterization may proceed independently; no domain acceptance follows.


## Real PostgreSQL reader prerequisite

[The narrow native-driver amendment](rust-core-mvp-agent-material-reader.md#native-driver-correction-and-actual-postgresql-characterization)
records actual locked asyncpg's distinct native UUID class. The test reader now
admits only the exact two native classes by identity, retains original objects
and rejects hostile class-equality subclasses; no public reference/auth/schema
behavior changes. Different review1a0ede47 accepts exact source5d5dac9f7,
including a225-line read-only PostgreSQL characterization file and corrected
native primary-key-only bind arguments for all32 fixed material queries.
Supported run36986398696 FAILED one of6 actual PostgreSQL cases;5 passed.
All191 selected reader/12,778 total units, quality and unchanged literal pre-PR
passed; artifact11217738787 verifies exact source/tree/main/exit0/actualEMPTY.
The all32 material test caught a real physical-column alias mismatch:
Solution's public attribute allow_outbound_access maps to existing physical
Alembic column global_repo_access. A narrow SELECT alias correction is under
independent review; no schema or policy change, test waiver or all32 acceptance. Fixed actual-record positives are
projection mechanics, not stored full lineage, postclosure freshness or nominal
acceptance. Host SOURCE STOP and exhausted #1017 repair disposition remain.


The narrow physical Solution alias repair has independent source review9a04e34e,
including the remaining32-group physical-column mapping audit. Exact corrected
readerfa4a4928 is pushed as d35451eebfbaee4c5b0246356955a1f0302147d5,
with unit/PG tests unchanged. New changed-candidate supported run37001683085,
including the unchanged pre-PR gate requested once, is pending. Prior failed
5d5 stays blocking historical evidence; no all32-runtime, nominal, authority or
full-MVP acceptance is claimed. Temporary Sol review capacity failures did not
waive independent review; a different Sol reviewer completed the source gate.
