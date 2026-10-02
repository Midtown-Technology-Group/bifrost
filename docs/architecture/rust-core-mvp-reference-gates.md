# Rust core MVP: selected reference gates

Status: source-derived decision record; nominal Scenario B is blocked. This
record does not freeze the complete runtime contract or authorize an authority
change. Procedure: mtg-engineering-flow 2026-09-30.1. No merge or production
deployment is authorized.

## Source and evidence

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

Current fetched platform main remains `e58db4955ddd30177bd613f1d85b7e203ad7832a`; workspace main advanced to `9941427941587d253540942b714c5e2b7abfc410` (#1129). Its two-file delta from c185 changes Ninja alert reconciliation and its unit tests. Selected Cove sources and the static boundary checker are unchanged. The exact detached 9941427 checkout passes the workspace-owned AST audit: 2,107 boundary files, 1,984 standard authored files, zero forbidden imports and an empty allowlist. Workspace #1112 remains an ancestor; no authored code was imported or executed. The earlier c185 snapshot above remains historical evidence.

The [reference integration specification](rust-core-mvp-agent-reference-integration.md) now records private observer TLS, verified-reader mode0600 capability ownership, role-isolated status mounts, the dedicated host-status directory, named-only staged reset bypass and retained transient init custody. These are bounded reference-lane construction decisions, not public authorization changes, C2/C3 acceptance or a new #1017 repair cycle. No actual nominal model/tool/summary run, live vendor call, merge or deployment follows from this source review.
