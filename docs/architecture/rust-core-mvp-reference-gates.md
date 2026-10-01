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
