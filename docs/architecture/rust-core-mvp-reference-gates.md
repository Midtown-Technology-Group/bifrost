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

## Concrete decision needed before nominal B implementation

One option is a separate Python prerequisite correction, independently reviewed
and characterized before it becomes the parity baseline. Its bounded interface
would carry trusted original caller admin/provider/external/role attributes
through public agent admission, `AgentWorkflowCaller`, `execute_tool` and pending
dispatch. It must preserve caller organization separately from target/run
organization, default callerless authority to the existing explicit policy, and
never infer authority from transport admin or destination organization.

Required tests include provider non-superuser, ordinary tenant, superuser and
external principals; enqueue/execute/rerun/chat; foreign-org and revoked-grant
negatives; exact signed delegated caller/fence; unchanged capacity tool with
actual SDK and strictly synthetic Cove transport; cancellation; and credential
non-disclosure. This is a correction proposal, not approved implementation or
passing evidence. Alternatively, authoritative complete bindings for another
unchanged representative agent could remove this prerequisite. Until that
choice is approved, nominal Cove B remains blocked and C2/C3 cannot invent flags.

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
