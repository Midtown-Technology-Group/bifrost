# Code Mode evaluation: preflight and acceptance contract

Tracks [#1106](https://github.com/Midtown-Technology-Group/bifrost/issues/1106),
external [#1107](https://github.com/Midtown-Technology-Group/bifrost/issues/1107),
internal [#1108](https://github.com/Midtown-Technology-Group/bifrost/issues/1108),
and shared evaluation [#1109](https://github.com/Midtown-Technology-Group/bifrost/issues/1109).
Delivery lane: spike/proof. This is preflight evidence, not an implementation or
approval to expose an endpoint. Production remains disabled.

## Decision as of 2026-10-08

**NO-GO for enabling either facade in production.** The pinned frameworks cannot
install their Code Mode extras together. A narrowly scoped FastMCP candidate
resolves, but authorization, lifecycle, resource limits, and performance have
not been proved at runtime. The epic and its three spikes remain open.

Proceed with synthetic, test-only proofs once the designated VM is available.
Do not bypass the dependency conflict with `--no-deps`, substitute a custom
sandbox, upgrade the whole AI stack, or mount Code Mode over the native gateway.

Baseline: `02d1ca3d7d6c881699c5f637cab812d437bb9e8a`. Lockfile SHA-256:
`be76160e9eb3b3ba9ab0d9af9e77f311db3578cfaf02b7042d9697bd7c2feea1`.
Machine-readable package identities and resolver results are in
[code-mode-preflight.json](code-mode-preflight.json).

## Version compatibility

Read published wheel metadata and source, rather than assuming current docs
describe the installed release. Python 3.13.5 / pip 25.1.1 dependency dry runs
used an empty disposable venv on `codex-remote-02`; no platform packages were
installed or repository dependency files changed. These are resolver results,
not evidence from the Python 3.14 container that generated Bifrost's lockfile.

| Configuration | Observed result | Implication |
| --- | --- | --- |
| FastMCP slim `4.0.0b1[code-mode]`, Harness `0.27.0[code-mode]`, AI slim `2.35.3` | `ResolutionImpossible`: Monty `==0.0.17` versus `>=0.0.19` | Both extras cannot coexist under current pins. |
| FastMCP slim `4.0.11[client,server,code-mode]`, Harness `0.27.0[code-mode]`, AI slim `2.35.3`, MCP `2.0.0`, unconstrained transitives | Resolves Monty `0.0.21` | Candidate only; this would allow unrelated transitive drift. |
| Same candidate plus `fastmcp==4.0.11`, all existing lock versions constrained except FastMCP / slim | `ResolutionImpossible`: `uncalled-for >=0.4.0` versus lock `0.3.1` | A FastMCP-only change is insufficient. |
| Same constrained candidate, additionally replacing `uncalled-for` with exactly `0.4.0` | Resolves, including Monty `0.0.21` and its client/runtime packages | Small candidate to test in the real lockfile environment. Not a full platform dependency resolution. |
| Current Harness `0.54.0` metadata | Requires AI slim `==2.54.0`, Monty `>=1.0.0,<2` | Cannot retain AI slim `2.35.3`; unsuitable for this bounded spike. |

The constrained run resolves the selected frameworks and their transitive
closure. It does not install every Bifrost dependency, verify wheels on Python
3.14, or run `pip check` against the complete platform environment. A final
candidate must pass those checks before changing `requirements.lock`.

Upstream identities:
[FastMCP beta](https://pypi.org/pypi/fastmcp-slim/4.0.0b1/json),
[FastMCP candidate](https://pypi.org/pypi/fastmcp-slim/4.0.11/json),
[Harness pinned](https://pypi.org/pypi/pydantic-ai-harness/0.27.0/json),
[Harness current](https://pypi.org/pypi/pydantic-ai-harness/0.54.0/json).
General [Harness Code Mode docs](https://pydantic.dev/docs/ai/harness/code-mode/)
are navigation, not version-pinned evidence.

## Framework behavior found in versioned source

These findings are source inspection. Every runtime probe below is still pending.

**External:** `fastmcp/experimental/transforms/code_mode.py` in `4.0.0b1`
defines `CodeMode` and `MontySandboxProvider`. `transform_tools` replaces the
listed tools with discovery tools and `execute`; `get_tool` still passes native
names through. Thus discovery changes even if direct native calls remain
possible. Never attach this transform to the existing `/mcp` or agent mounts.
The default nested-call ceiling is 50 per `execute`; sandbox defaults are
30 seconds and 100,000,000 bytes. The transform retrieves the current catalog
for each nested call and calls `ctx.fastmcp.call_tool`. Its catalog bypass uses
a `ContextVar`; this suggests a viable context-preserving dispatch path but does
not prove HTTP auth/agent scope under fanout. Result unwrapping returns structured
content or joined text, so Bifrost's error envelopes and redaction need explicit
tests. Task negotiation is not explicitly handled by this transform.

`4.0.11` adds tracking and cancellation/joining of pending external callbacks
in `MontySandboxProvider.run`. Do not transfer cancellation conclusions between
versions: test cancellation with an outstanding host callback in both selected
environments, and check that no callback starts after execution closes.

**Internal:** Harness `0.27.0` exports `CodeMode`; its `_capability.py` wraps the
assembled toolset with `CodeModeToolset`. Explicitly unselected tools stay native.
Framework control, undiscovered deferred, native fallback, and other code tools
have special preservation rules. Nested calls pass through Pydantic AI's
`ToolManager.handle_call` with IDs such as `<parent>__1`. Denied calls raise in
the sandbox; unresolved approval/deferral becomes an error rather than a durable
approval continuation. Monty exceptions can become `ModelRetry` and include
exception text, which makes host-side redaction necessary. Default session
limits are 30 seconds and 256 MiB; resetting a session can renew those limits.
They do not establish a Bifrost run-wide CPU or wall budget. Keep `os_access`
and `mount` unset and do not supply filesystem, shell, or network bindings.

## Bifrost integration seams and unresolved hazards

- External identity comes from `_get_context_from_token` and
  `_get_runtime_context` in `api/src/services/mcp_server/server.py`; agent scope
  comes from `agent_scope.py`. Reuse these paths for every dispatch. The REST
  gateway's `execute_agent_tool` re-resolves agent-bound capabilities and owns
  operation receipt behavior. A generic gateway execution tool is **not**
  read-only merely because its own schema is stable.
- Internal schema validation and tool dispatch are in
  `api/src/services/agent_runtime/toolset.py`. `BifrostToolset` marks definitions
  sequential; wrapping it does not establish parallel fanout performance.
  Preserve those flags until their ownership requirements are understood.
- In `api/src/services/agent_executor.py`, `execute_runtime_tool` waits on
  `tool_call_ready[tool_call_id]`. The stream consumer populates that event and
  execution/message IDs on `FunctionToolCallEvent`. Harness nested IDs must
  produce the corresponding lifecycle evidence or the adapter may wait forever.
  This is a source-based deadlock hypothesis, **not** a reproduced bug.
- Use a test-only adapter to expose the exact lifecycle first. If an adapter is
  needed, centralize Bifrost's authorized per-call execution and persistence;
  do not fabricate outer model events or introduce internal HTTP loopback.
- Durable ownership, attempt leases, cancellation, operation receipts and
  approvals stay with Bifrost. Framework tracing and a Monty worker are not
  replacements for those controls.

## Shared dispatch acceptance contract

This describes a language-neutral host contract; it is not a second policy store.
The host binds principal, effective organization, allowed agent, run/attempt,
parent call, absolute deadline and cancellation before any generated code runs.
Generated arguments contain only an opaque tool reference and validated tool
arguments. They cannot select identity, tenant, privileges, execution owner or
approval state. Each nested invocation receives a distinct host-issued ID.

Before dispatch, the existing Bifrost authority must re-resolve the tool, check
caller/tenant/agent entitlement and a fixed, trusted read-only allowlist, verify
the current attempt lease, validate arguments and reserve host budgets. Absence
or staleness of any required context denies the call before touching a handler.
Do not infer read-only status from a tool name, model prompt or caller-supplied
metadata. Initial tools are deterministic synthetic fixtures only; arbitrary
workflows and integration calls remain excluded.

The host records authorization and terminal outcome with parent/nested IDs,
principal, tenant, agent, run and attempt correlation. Store no credentials,
generated source, raw sensitive arguments or exception secrets in audit events.
Existing error codes/redaction remain authoritative; a denial is not a successful
string result. Audit persistence failure must prevent dispatch, and every
started call must receive a success/error/denied/cancelled terminal disposition.

Proposed test budgets, to be enforced outside model code: 5 seconds total wall
time, 1 second per host call, 8 attempted calls across all snippets/restarts,
2 active calls, 64 KiB per result and 256 KiB aggregate output. Reserve attempted
calls before allocating host tasks; waiting for a concurrency slot counts
against the total deadline. A caught sandbox exception must not renew budgets.
Reject non-finite/invalid budget values during host configuration. Count discovery
and schema output toward output limits too. Do not truncate an error into success.

Separately configure Monty memory/execution limits. Those do not cap total host
callback allocations, output construction, host process memory or run-wide CPU.
Measure their enforcement in the chosen worker/runtime. If hard CPU/memory
containment cannot be demonstrated, require external process/container limits
before any rollout; record unsupported limits as failures, not passes.

## Required executable probes

| Boundary | Required observable result | Status |
| --- | --- | --- |
| Native transport | Unchanged `/mcp` and `/mcp/{agent_id}` discovery, names, schemas, direct calls and task negotiation with current and legacy clients | Not run |
| Identity and isolation | Missing/wrong audience/scope/tenant/agent rejected; concurrent principals never share context or results | Not run |
| Read-only policy | Unknown tool, write tool, generic workflow dispatch, forged tenant or privilege denied before handler | Not run |
| Attempt ownership | Stale attempt and cancellation deny new calls; no host dispatch after closure | Not run |
| Lifecycle | One correlated authorization and terminal event per attempted nested call; internal IDs never strand readiness waits | Not run |
| Errors and approvals | Structured/redacted host errors preserved; pending approval stays under Bifrost ownership; no automatic redispatch | Not run |
| Cancellation | Cancel during sandbox CPU and during queued/running host callback; all tasks/workers reclaimed with bounded teardown | Not run |
| Budgets | CPU loop, memory growth, large output/print, fanout burst, slow callback, swallowed budget exception and restart cannot escape host ceilings | Not run |
| Sandbox escapes | Environment, imports, filesystem read/write, network and dynamic execution denied without host side effects | Not run |
| Compatibility | Full candidate lock resolves and installs in supported image; `pip check`, scoped MCP/runtime tests and quality gates pass | Not run |

Do not mark these rows passed from source inspection. Add deterministic tests
at the relevant public seam in #1107–#1109 and retain the exposing failures.

## Comparative benchmark contract

Use the same synthetic datasets, allowed principal, model/version, settings,
prompt, correctness assertions and host dispatch budgets in all three modes.
The [fixtures](code-mode-benchmark-fixtures.json) contain inventory filtering,
parallel integration health and discovery/schema/read-only execution workloads,
plus a second tenant's sentinel data that must never appear in results.

Record model input/output tokens, model turns, native tool round trips, nested
host calls, end-to-end wall time, output bytes, correctness, error rate and
blocked security probes. Include discovery/schema overhead. Record framework,
Monty and platform source identities. Compare repeated runs under the same
declared conditions; label deterministic scripted transport measurements
separately from actual model runs. No model benchmark or speedup is claimed here.
Real integrations are optional and require separate safe fixture access.

## Reproduce dependency preflight

Create an empty disposable venv and use its `python -m pip` for the following
commands. A failed pinned probe is the expected incompatibility result:

```bash
python -m pip install --dry-run \
  'fastmcp-slim[code-mode]==4.0.0b1' \
  'pydantic-ai-harness[code-mode]==0.27.0' 'pydantic-ai-slim==2.35.3'
```

For the candidate, derive a temporary constraints file from every package's
`==` line in `requirements.lock`, removing continuation slashes, hashes and
extras. Omit only `fastmcp` and `fastmcp-slim`. The next command must fail with
the unchanged `uncalled-for==0.3.1` constraint, then resolve when that constraint
is replaced with exactly `uncalled-for==0.4.0`:

```bash
python -m pip install --dry-run --report /tmp/code-mode-resolution.json \
  --constraint /tmp/code-mode-constraints.txt \
  'fastmcp==4.0.11' 'fastmcp-slim[client,server,code-mode]==4.0.11' \
  'pydantic-ai-harness[code-mode]==0.27.0' \
  'pydantic-ai-slim==2.35.3' 'mcp==2.0.0'
```

Then test the full proposed lock in the supported Docker environment, retaining
unaffected versions and adding hashes for new Monty packages. Do not use a dry
run report as the deployed lockfile or change repo dependencies from these notes.

## Current verification and next step

Read-only Keeper status confirmed authentication and retrieved the designated
Proxmox credential into a child process. The request to
`pve-t340.netbird.cloud:8006` failed before authentication with DNS
`Name or service not known`. No guest identity or VM worktree could be verified.
No dev/test application stack was started; the pre-existing shared host stack
was preserved. The documentation-only `./test.sh pre-pr` gate passed; its
temporary test volumes were removed, with empty container/network/volume
readback for project `bifrost-test-6f65bd56`. No runtime, auth, cancellation, sandbox or model
benchmark tests ran, and no rollout or final GO assessment is justified.

Next: restore access to the designated test host, identify an available Bifrost
test VM and its resource ownership, then reproduce the full dependency candidate
and synthetic lifecycle probes there. Keep the external and internal proof PRs
independent and do not close #1106 from this preflight. Disablement/rollback for
any later proof removes only its opt-in mount/capability and restores the tested
dependency lock; existing MCP mounts and execution ownership stay intact.
