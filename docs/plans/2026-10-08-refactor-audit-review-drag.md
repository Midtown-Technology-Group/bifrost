# Refactor audit: review drag, drift, and unsafe boundaries

Date: 2026-10-08. Evidence head: `origin/main` at `17fb2be09` (checkout
branch `agent/platform-oss-builder/d7b132d553b2`). Source issue:
Midtown-Technology-Group/bifrost#310 (MIDT-189). No production behavior
was changed for this audit; the diff is this document only.

Method: read-only inspection of the current tree plus commit/file
statistics. Counts below are reproducible with the commands named in
each section. Findings distinguish language/tooling pain from
architecture pain from test/contract pain, as the issue requires.

## Top 5 refactor targets (ranked)

### 1. Execution-state duality: two attempt models, two delivery backends, two job worlds

Why it slows reviews: there is no single durable-execution vocabulary.
`api/src/models/orm/executions.py` with `services/execution/attempts.py`
maintains workflow-specific dispatch/claim/run evidence, while
`api/src/models/orm/execution_attempts.py` with
`services/execution_attempts.py` is a separate generalized runner-attempt
model over a distinct table. An agent fixing retry or claim semantics in
one model can leave the other inconsistent, and reviewers must hold both
vocabularies to tell which one a change affects. The same duality exists
one layer down: `services/work_delivery_store.py` implements PostgreSQL
`SKIP LOCKED` claims as the migration architecture, but
`src/config.py`, Compose interpolation, and `k8s/configmap.yaml` still
default `work_delivery_backend` to `rabbitmq`, so effective runtime
behavior depends on deployment config no reviewer sees in the diff.
Meanwhile durable non-workflow work is canonicalized on `PlatformJob`
(`docs/architecture/platform-jobs.md`), while workflow/agent execution
stays on the execution-worker infrastructure (`services/execution/`,
`worker/app.py`, `scheduler/`) — two job worlds with separate
retry/cancel/status semantics.

Classification: architecture-driven first, contract-driven second. The
language did not cause two tables; missing contracts let them coexist.

### 2. Type-checking enforcement gap: pyright runs in basic mode with core rules off

Why it slows reviews: the issue hypothesizes dynamic Python surfaces
that type checking cannot validate. The config confirms the stronger
claim — checking is disarmed even where types exist.
`api/pyrightconfig.json` sets `typeCheckingMode: basic` and disables
`reportArgumentType`, `reportCallIssue`, `reportOptionalMemberAccess`,
`reportOptionalSubscript`, `reportAttributeAccessIssue`,
`reportOperatorIssue`, plus all `reportUnknown*` rules and strict
inference. Reviewers therefore cannot rely on CI to catch wrong-argument,
None-deref, or unknown-member mistakes, and must re-derive call
contracts by reading. This is the cheapest pain to remove and the
highest-leverage first move (see recommendation).

Classification: language/tooling-driven (configuration, not Python
itself). Strict Python hardening is available without any rewrite.

### 3. Oversized solution-delivery services that also dominate churn

Why it slows reviews: the largest services are exactly the files agents
touch most, so every change arrives with a large blast radius. Sizes
(`wc -l`): `services/manifest_import.py` 3569,
`services/execution/process_pool.py` 2832,
`services/workspace_promotions.py` 2513, `services/solutions/deploy.py`
2413, `services/github_sync.py` 2285, `services/agent_executor.py` 2183.
In the last 500 commits the top-touched backend files are
`services/solutions/deploy.py` (13),
`services/solution_source_accountability.py` (12),
`services/solutions/github_source_delivery.py` and
`services/workspace_source_releases.py` (10 each), with the matching
test files churning alongside (`test_solution_app_deploy.py` 21).
Large file plus high churn plus multi-surface reach (REST, Solutions
flows, git delivery, manifest) means reviewers re-verify the same
invariants on every PR instead of relying on module boundaries.

Classification: architecture-driven (module size and ownership), with a
test/contract amplifier (tests churn with the code rather than pinning
it).

### 4. Four-surface mutation paths with direct DB access in the HTTP layer

Why it slows reviews: the same entity can be mutated through REST (81
router modules), MCP (`services/mcp_server/gateway.py` 1864 lines plus
~23 tool modules), CLI (`api/bifrost/commands/`, 20 commands), and
manifest import/export (`manifest_import.py` plus serialization), with
`docs/plans/2026-04-18-cli-mutation-surface-and-mcp-parity.md` recording
that parity is a known standing concern. MCP tools are documented as
thin REST wrappers, but a 29-module `repositories/` layer coexists with
direct database access in the HTTP layer: 46 of 81 router modules
contain `session.execute`/`db.execute`, and the pattern appears in 238
files under `api/src`. Tenant scoping has a home (`repositories/org_scoped.py`)
that routers are free to bypass, so org-scope correctness is verified
per-route by inspection instead of once at the layer boundary.
Parallel auth idioms compound this: user JWT (`CurrentUser`/`Context`
deps), engine bypass users, device-enrollment tokens (deliberately
outside the standard dependency, see `routers/devices.py`), and platform
superuser gates coexist, so a new route can pick the wrong idiom and
still look conventional.

Classification: architecture-driven. The repository layer exists; the
missing piece is the rule (and gate) that routers must use it.

### 5. Untyped trust-boundary payloads verified by after-the-fact tests

Why it slows reviews: `Dict[str, Any]`/`dict[str, Any]` concentrates at
exactly the boundaries where invariants matter most —
`services/solutions/deploy.py` (36 sites),
`services/mcp_server/gateway.py` (33),
`services/execution/async_executor.py` (29),
`services/execution/engine.py` (26),
`services/execution/process_pool.py` (25) — with `json.loads`/`dumps`
spread across auth, Redis, RabbitMQ, and worker-claim paths. Combined
with target 2, nothing mechanical checks these shapes, so behavior is
pinned only by tests written alongside or after the change; the churn
data in target 3 shows test files being edited in the same commits as
the code they cover. Contract-version tripwires exist for breaking API
changes (per repo guidance), but DTO parity between REST, MCP, CLI, and
generated OpenAPI types is maintained by convention plus the MCP parity
test, not by a shared contract source.

Classification: test/contract-driven, amplified by language/tooling
(target 2). Typing the hotspots in this section is what makes the
hardening in target 2 concrete.

## Recommended sequence

Accounted for open Rust-migration work: `origin/architecture/rust-backend-migration`
(RFC, status proposed, no subsystem ported), parity/evidence PRs
`test/workflow-domain-parity` (#1040) and `trust/runtime-evidence-vectors`
(#1020), and runtime-SDK credential PRs (#1024, #1026, #1037). The
frontier below does not duplicate or pre-empt them.

1. **Strict Python hardening first.** Raise pyright strictness on
   touched surfaces (starting with the section-5 hotspots), type the
   `Dict[str, Any]` concentrations, and add a lint gate requiring router
   DB access through `repositories/` (with `org_scoped` as the default).
   Small, reversible, no runtime change; directly attacks targets 2, 4,
   and 5. Suggested next issue: one pyright-hardening slice over
   `services/solutions/deploy.py` + `services/mcp_server/gateway.py`
   with the router-DB-access rule.
2. **Execution-contract work second.** Unify (or explicitly relate) the
   two attempt models and pin the delivery-backend default to the
   PostgreSQL architecture with a pre-cutover verification gate, as the
   Rust RFC itself requires. Suggested next issue: attempt-model
   contract decision plus `work_delivery_backend` default alignment.
3. **Defer typed-core extraction and the compiled worker spike** until 1
   and 2 land and the open parity/evidence PRs (#1040, #1020) settle.
   The spike frontier is already staffed; starting another one now
   multiplies vocabularies instead of reducing them. Revisit extraction
   only with contract-typed boundaries produced by steps 1–2.

## What was deliberately not changed

No source, migration, config, or test file was modified. All counts are
read-only observations at the evidence head above and will drift as
`main` moves; re-run the named greps before using them for sizing.
