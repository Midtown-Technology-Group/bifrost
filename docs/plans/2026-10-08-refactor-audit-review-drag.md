# Refactor audit: review drag, drift, and unsafe boundaries

Date: 2026-10-08. Evidence head: `origin/main` at `17fb2be09` (checkout
branch `agent/platform-oss-builder/d7b132d553b2`). Source issue:
Midtown-Technology-Group/bifrost#310 (MIDT-189). No production behavior
was changed for this audit; the diff is this document only.

Method: read-only inspection of the current tree plus commit/file
statistics. Every count states its scope and command in
"Reproducing the counts" at the end. Findings distinguish
language/tooling pain from architecture pain from test/contract pain,
as the issue requires.

Correction note (review feedback on v1): entity *field* parity across
CLI, MCP, and manifest is enforced by shared DTOs and tests, not by
convention — see target 4. The drift that remains is MCP-vs-REST
*behavior* divergence plus legacy REST import routes.

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
`api/src/config.py` (`default="rabbitmq"`, verified at
`config.py:120-124`) and the Kubernetes config still default
`work_delivery_backend` to `rabbitmq`, so effective runtime behavior
depends on deployment config no reviewer sees in the diff. Open PR
#1053 moves `.env.example` and the Compose files to `postgres` but
deliberately leaves `config.py` and Kubernetes untouched — the residual
follow-up is those two defaults plus the RFC's pre-cutover checks (see
recommendation 2).
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

### 4. MCP-vs-REST behavior divergence, legacy REST import routes, and direct DB access in routers

Why it slows reviews: entity *field* parity is the part that already
works. CLI, MCP, and manifest export read the same `XxxCreate` /
`XxxUpdate` Pydantic DTOs through `api/bifrost/dto_flags.py`, enforced
by `tests/unit/test_dto_flags.py`, the CLI-contract tripwire
(`tests/unit/test_contract_version.py`), MCP parity tests, and
skill-truth freshness (`docs/dev/agent-platform-rules.md`, "Keeping
CLI, MCP, and manifest in sync"). `.bifrost/` is export-only and the
old `bifrost export` / `bifrost import` commands were removed in favor
of Solutions — so manifest "import/export" is no longer a fourth live
mutation surface.

The drift that remains is behavioral. The MCP tools for `agents`,
`forms`, `tables`, `apps`, and `events` re-implement router logic and
have diverged — different permission models, missing side effects,
divergent validation — catalogued in
`docs/plans/2026-04-18-mcp-router-reconciliation.md` (status: Deferred).
New tools must be thin HTTP wrappers (`tools/roles.py`, `configs.py`,
`_http_bridge.py` pattern, enforced by
`tests/unit/test_mcp_thin_wrapper.py`), but the five legacy tools
predate that rule, so reviewers must diff tool behavior against router
behavior case by case. Separately, legacy REST bulk import/export
routes still exist (`routers/export_import.py`: `/import/knowledge`,
`/import/tables`, `/import/configs`, `/import/integrations`,
`/import/all`, plus `/export/*`) alongside the Solutions flow, so there
are two REST-side bulk paths to reason about. And a 29-module
`repositories/` layer coexists with direct database access in the HTTP
layer: 46 of 84 router modules contain `session.execute`/`db.execute`,
and the pattern appears in 238 files under `api/src`. Tenant scoping
has a home (`repositories/org_scoped.py`) that routers are free to
bypass, so org-scope correctness is verified per-route by inspection
instead of once at the layer boundary. Parallel auth idioms compound
this: user JWT (`CurrentUser`/`Context` deps), engine bypass users,
device-enrollment tokens (deliberately outside the standard dependency,
see `routers/devices.py`), and platform superuser gates coexist, so a
new route can pick the wrong idiom and still look conventional.

Classification: architecture-driven (legacy behavior duplication plus
unenforced layering), with the field-parity half already
test/contract-driven and working.

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
the code they cover. The contract-version tripwire
(`tests/unit/test_contract_version.py`) forces an explicit
breaking-vs-compatible decision on CLI/SDK-consumed DTO changes, so the
remaining gap is the untyped interior shapes in this section — not DTO
parity, which target 4 shows is already enforced.

Classification: test/contract-driven, amplified by language/tooling
(target 2). Typing the hotspots in this section is what makes the
hardening in target 2 concrete.

## Recommended sequence

Accounted for open Rust-migration and ops work:
`origin/architecture/rust-backend-migration` (RFC, status proposed, no
subsystem ported), parity/evidence PRs `test/workflow-domain-parity`
(#1040) and `rust/runtime-evidence-vectors` (#1020), runtime-SDK
credential PRs (#1024, #1026, #1037), and ops PR #1053 (OPEN), which
already moves `.env.example` and the Compose files to a PostgreSQL
work-delivery default while deliberately leaving `api/src/config.py`
and Kubernetes on RabbitMQ. The frontier below does not duplicate or
pre-empt them.

1. **Strict Python hardening first.** Raise pyright strictness on
   touched surfaces (starting with the section-5 hotspots), type the
   `Dict[str, Any]` concentrations, and add a lint gate requiring router
   DB access through `repositories/` (with `org_scoped` as the default).
   Small, reversible, no runtime change; directly attacks targets 2, 4,
   and 5. Suggested next issue: one pyright-hardening slice over
   `services/solutions/deploy.py` + `services/mcp_server/gateway.py`
   with the router-DB-access rule; follow with the already-sequenced
   `docs/plans/2026-04-18-mcp-router-reconciliation.md` work for the
   five legacy MCP tools (whose permission model wins, per entity) plus
   a keep-or-retire decision on the legacy REST import routes versus
   Solutions — no new parity mechanism is needed for entity fields.
2. **Execution-contract work second, scoped around #1053.** Unify (or
   explicitly relate) the two attempt models; for delivery backends,
   cover only what #1053 leaves open — the `api/src/config.py`
   `default="rabbitmq"` and the Kubernetes config — plus the RFC's own
   entry gates (read-only verification of effective deployed delivery
   settings, verified Python PostgreSQL baseline, PostgreSQL parity
   before worker cutover). Suggested next issue: attempt-model contract
   decision plus the remaining-default alignment and pre-cutover checks.
3. **Defer typed-core extraction and the compiled worker spike** until 1
   and 2 land and the open parity/evidence PRs (#1040, #1020) settle.
   The spike frontier is already staffed; starting another one now
   multiplies vocabularies instead of reducing them. Revisit extraction
   only with contract-typed boundaries produced by steps 1–2.

## Reproducing the counts

Run from the repo root at the evidence head. Scope notes matter: the
v1 draft undercounted routers by excluding `routers/platform/`.

- Router census (84 non-`__init__` modules: 80 top-level + 4 in
  `routers/platform/`):
  `find api/src/routers -name '*.py' | grep -v __init__ | wc -l`
- Direct DB access in routers (46 of 84):
  `find api/src/routers -name '*.py' | grep -v __init__ | xargs grep -lE 'session\.execute|db\.execute' | wc -l`
- Repo-wide pattern (238 files, includes the `repositories/` layer
  itself — it measures prevalence, not violations):
  `grep -rlE 'session\.execute|db\.execute' api/src --include='*.py' | wc -l`
- `Dict[str, Any]` hotspots, e.g.:
  `grep -rcE 'Dict\[str, Any\]|dict\[str, Any\]' api/src/services/solutions/deploy.py api/src/services/mcp_server/gateway.py api/src/services/execution/async_executor.py api/src/services/execution/engine.py api/src/services/execution/process_pool.py`
- Sizes: `wc -l` on the named files.
- Churn (last 500 commits on `main` at the evidence head):
  `git log --format=%H -500 | while read c; do git diff-tree --no-commit-id --name-only -r $c; done | sort | uniq -c | sort -rn | head -25`

## What was deliberately not changed

No source, migration, config, or test file was modified. All counts are
read-only observations at the evidence head above and will drift as
`main` moves; re-run the commands above before using them for sizing.
