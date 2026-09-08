# Integrate upstream through 0598020e3

Date: 2026-09-08. Status: reviewed implementation plan; integration source has not
been changed or tested. The operator requested review and planning for these ten
commits. This document does not authorize a production rollout.

## Scope and decision

Integrate the ten upstream commits after `67859ed15` through `0598020e3`, retaining
MTG's schema, execution, tenancy, deployment, and dependency improvements. Use one
integration PR with separately reviewable implementation commits and a final
ancestry-preserving merge. Account for already satisfied upstream changes without
downgrading packages or manufacturing edits. Exclude the frontend modernization
branch and later dependency PRs.

Pinned review inputs:

| Input | SHA |
| --- | --- |
| Current fork main | `cebcf40d0` (full SHA in evidence) |
| Last imported upstream revision | `67859ed158409b215ab8d030620917bec31d7ef6` |
| Selected upstream head | `0598020e32ea6367ecda69f548a53c00bb77bb6c` |
| Last integration on fork main | `57b43e023289a8d883dd7e1bbb3c1b17237dd84a`, PR #667 |

The selected upstream delta is 61 files, 2,683 insertions and 585 deletions.
[Machine-readable evidence](2026-09-08-upstream-integration-evidence.json) records
full SHAs, each commit's files, and both conflict inventories. Refresh the fork
base and repeat the rehearsal if main advances; keep the upstream target fixed.

Guidance reviewed: platform `AGENTS.md`, the
[fork repo policy](https://github.com/MTG-Thomas/bifrost-ops/blob/main/repo-policy/bifrost.md),
the infra upstream-contributor operating model, and
[prior integration PR #667](https://github.com/MTG-Thomas/bifrost/pull/667).
Keep MTG process policy in bifrost-ops. This plan concerns platform source
reconciliation. Older policy still names jackmusick/bifrost; the configured
upstream is gobifrost/bifrost. GitHub currently resolves the fork to
Midtown-Technology-Group/bifrost.

## Commit-by-commit disposition

| Upstream commit / PR | Actual change | Integration disposition |
| --- | --- | --- |
| `18507cfec` / [#704](https://github.com/gobifrost/bifrost/pull/704) | Sparse Chat event serialization preserves nested nulls and explicitly supplied null fields; public failures are sanitized on publish, replay, HTTP state, and client rendering. | Import together with all publisher call-site and regression-test changes. Preserve our durable Chat runtime, route continuity, activity interactions, and worker lifecycle. |
| `82e9949a9` / [#706](https://github.com/gobifrost/bifrost/pull/706) | Execution query aliases/validation, SDK continuation, AgentRun pagination, nonblank diagnostic errors, richer tool schemas and validation, Solution source inference. | Port in three reviewable units: history/SDK; diagnostic errors; schema/invocation. Schema portions require adaptation to our full-object storage contract. Do not apply this commit wholesale. |
| `50597d159` / [#676](https://github.com/gobifrost/bifrost/pull/676) | Node 26 image digest `4ebb5ac…` to `c075312…` in three client Dockerfiles. | Import digest changes; preserve our CI/build stages, nginx runtime, and existing Playwright image. No Node major-version change. |
| `f5e0fce54` / [#677](https://github.com/gobifrost/bifrost/pull/677) | Python 3.14 image digest `ce40764…` to `cae66f2…` in API production/development Dockerfiles. | Update both builder/runtime pins in both files; retain MTG stage names and Docker build structure. No Python major-version change. |
| `d1929a048` / [#681](https://github.com/gobifrost/bifrost/pull/681) | pragmatic-drag-and-drop 2.0.1 to 3.0.0. | Already superseded: fork manifest is `^3.1.0`. Preserve it and compatible lock entries; do not downgrade to 3.0.0. |
| `58d67f442` / [#696](https://github.com/gobifrost/bifrost/pull/696) | Lockfile-only js-yaml update; PR title describes 4.3.0 to 5.4.1. | Direct manifest and resolved root package already use 5.4.1 in fork. Reconcile any remaining transitive lock differences by consumer; do not replace the whole lockfile. |
| `493ea6c7d` / [#678](https://github.com/gobifrost/bifrost/pull/678) | pydantic-ai-slim 2.33.0 to 2.35.3; pydantic-ai-harness 0.24.0 to 0.27.0, with lock changes. | Import exact paired pins and required lock changes. Treat as an agent-runtime compatibility change, not routine low-risk maintenance: the harness remains pre-1.0. |
| `8f0032652` / [#680](https://github.com/gobifrost/bifrost/pull/680) | Three CodeQL action references move to 4.37.9 at `cdf488f…`. | All three refs already match. Preserve fork workflow structure. This PR does not actually require three distinct action upgrades. |
| `5e79257a6` / [#679](https://github.com/gobifrost/bifrost/pull/679) | Fourteen direct npm dependency updates. | Six targets already satisfied; import remaining eight manifest targets and minimal lock closure as detailed below. |
| `0598020e3` / [#707](https://github.com/gobifrost/bifrost/pull/707) | Generic execute_workflow validates the selected workflow's live schema; empty legacy registrations remain permissive; default gateway compatibility regression. | Integrate with #706's schema unit, using our canonical full-object adapter and retaining authorization and operation-receipt behavior. |

Remaining #679 targets: the five Tiptap packages (`extension-link`,
`extension-placeholder`, `markdown`, `react`, `starter-kit`) to `^3.30.5`,
`@xyflow/react` to `^12.11.5`, `@types/react-dom` to `^19.2.5`, and `typescript-eslint` to `^8.68.0`.
The other six are already satisfied: react-query, lucide-react,
testing-library/react, vitejs/plugin-react, happy-dom, and js-yaml.

## Reconciliation design

### 1. Keep the fork's schema contract

The fork's `WorkflowIndexer._extract_parameters_from_ast` returns a complete
Draft 2020-12 JSON Schema object. Upstream instead returns parameter records with
new `python_type` and `json_schema` fields. Our `tool_registry.py` already accepts
both stored shapes, deep-copies full schemas, and exposes a deliberately lossy
parameter projection for older UI/CLI DTOs. These contracts must survive.

Implementation:

1. Keep `workflow_parameters_to_json_schema` as the canonical conversion path.
   Extend its legacy-record conversion to respect an explicit `json_schema`,
   including the valid unconstrained schema `{}`. Preserve full-object keywords
   such as `$defs`, `$ref`, `anyOf`, `required`, and `additionalProperties`.
2. Add upstream's reusable argument validator, but avoid introducing a competing
   list-only schema builder as a second authority. Have agent tools, generic
   execution, MCP catalogs, and endpoint OpenAPI consume the canonical schema.
3. Scope the empty-registration concession to ambiguous legacy `[]`/missing
   metadata. An explicit object schema declaring zero arguments and
   `additionalProperties: false` must remain strict. Do not weaken all empty
   schemas to preserve legacy Solution behavior.
4. Bring over local Enum discovery and runtime annotation metadata while
   retaining the fork's nested schema support. Align AST and runtime inference
   for `Any`, explicit null, `Optional`, `T | None`, typed maps/lists, mixed
   Literals, and imported/unresolved annotations. Upstream code drops null union
   branches and uses `json_schema or {"type": "string"}`; do not copy these
   semantics into our full schema path. Unresolved types must not be presented
   as fully validated contracts.
5. Solution deploy should infer from the carried bundle's Python source through
   a non-writing indexer API, returning `dict | None`, not upstream's list type.
   Preserve cross-install UUID guards, roles, immutable candidate handling,
   Azure storage behavior, and release accountability/recovery. Parse failure
   must not silently replace a known schema with an empty/permissive one.
6. Preserve `@tool` indexing, deterministic UUID-based catalog names, catalog
   digests, durable revision reconciliation across replicas, and full-schema
   passthrough in `mcp_server/server.py`. Upstream's replacement registration
   loop predates those fork protections.
7. Add generic validation after authorized workflow resolution and before
   execution. Retain `has_scope_bypass`, platform-admin identity, caller org,
   permanent operation receipts, replay redaction, and no-redispatch guarantees.
   Validation failures must never invoke the workflow executor.

The shared module loader and Solution deploy file merge textually without
conflicts, but still require this semantic review. Likewise, tests asserting
list storage cannot be accepted simply because their patches apply.

### 2. History and SDK

Keep the fork's public `Executions` class and `executions = Executions` alias;
add upstream's list-compatible `ExecutionList` and continuation metadata.
Preserve existing positional arguments; new filters remain keyword-only.

Add snake_case aliases alongside current camelCase query names; preserve
workflow-ID precedence, tenant authorization, effective-timestamp keyset
ordering, legacy numeric cursors, lightweight summary responses, retry attempt
evidence, and the fork's History UI behavior. Invalid IDs/dates/booleans/cursors
and unknown parameters now return 422 rather than being ignored or restarting
pagination: audit platform/SDK/workspace consumers before enabling the strict
allowlist. Preserve legitimate fork-only query parameters if any are found.

AgentRun pagination adds an offset-derived `next_cursor`, not a new keyset
guarantee. Test page boundaries, exact-size final pages, malformed cursors,
filtering, root/delegated visibility, and authorization. Do not claim snapshot
consistency under concurrent insertion.

### 3. Chat and diagnostic errors

Import the typed ChatStreamChunk publisher signature and update every producer,
including fork-only producers. Preserve omitted versus explicitly null fields,
nested null tool results, and state reconstructed from retained events.

Keep useful internal diagnostic evidence while replacing public Chat failures
with safe copy. Add `format_exception_message` to engine/event paths without
removing secret scrubbing, retry classification, attempt/failure evidence,
release-pinned imports, queue admission, worker draining, or callback-failure
evidence. A nonblank fallback is not authorization to expose raw exceptions to
ordinary Chat users.

### 4. Dependencies and generated surfaces

Reconcile manifests first, then regenerate/reconcile only their dependency lock
closure using the repository's pinned toolchain. Compare every lock change;
avoid unrelated latest-version updates. Inspect the paired AI/harness changes
against BifrostToolset, streamed tool events, cancellation, usage accounting,
provider retries, and durable Chat reconstruction.

Regenerate OpenAPI types from the integrated API, SDK signature documentation
and mirrors through their existing generators. Refresh the contract fingerprint
from the resulting fork API after explicitly deciding whether the stricter
validation warrants a CLI/server contract-version bump. Never copy upstream's
fingerprint or replace fork-only DTOs with upstream generated files.

## Merge mechanics and measured conflicts

Both rehearsals used a temporary bare repository with read-only access to the
existing object store. No working tree or application source was merged.

| Rehearsal | Conflicting files |
| --- | ---: |
| Ordinary merge using actual common ancestor | 83 |
| Selected delta using imported `67859ed15` as explicit base | 17 |

The 17 are: two API Dockerfiles; `api/bifrost/executions.py`; the events and
executions routers; execution engine; workflow indexer; MCP gateway, server and
workflow tools; tool registry; four test files (execution cleanup, event
processor agents, OpenAPI endpoints, contract version); and both client package
files. Exact paths are in the evidence file. These are textual conflicts, not a
complete estimate of required changes.

PR #667 was squash-merged. Its reviewed integration commit `6d2cc5f8` has both
upstream and fork parents, but landed `57b43e023` has one parent. Their trees
also differ in 11 files (+97/-22), including final fixes and worker telemetry.
Do not assume the original merge tree equals the landed tree, and do not erase
those differences to restore ancestry.

Execution sequence:

1. Use an isolated JJ workspace based on refreshed fork main. Pin the selected
   upstream SHA. Record clean starting state and both repository roots.
2. Prepare the reconciled content from the imported-base delta in the units
   above. The rehearsal command is `git merge-tree --write-tree
   --merge-base=<imported-base> <fork-head> <upstream-head>` in a scratch object
   store; its conflicted tree is evidence, not an implementation candidate.
3. Establish a real integration merge with current fork main and selected
   upstream history as ancestors, using JJ. Resolve old-overlap conflicts
   against the already landed fork behavior and the reviewed delta. Preserve
   fork content outside the selected 61 paths except explicitly justified
   tests, generated artifacts, shared adapters, and this plan. Review every
   exception; do not blanket-select upstream or mark unreviewed code integrated
   with an ours-only merge.
4. Verify the integrated tree retains previous #667 resolutions and subsequent
   fork fixes. The 83-conflict natural rehearsal is the warning that an ordinary
   merge alone will revisit already integrated changes. Audit the final diff
   against current fork main, not merely conflict-marker removal.
5. Publish one PR after exact-candidate validation. Record the ten-commit
   disposition, preserved fork behavior, candidate SHA, and production notes.
   Use a merge commit when the PR is authorized to merge; squash/rebase would
   recreate the ancestry defect. Repository settings currently allow merge
   commits, but check branch rules at that time. If they prohibit it, do not
   silently substitute squash.
6. Verify both the prior fork head and `0598020e3` are ancestors of resulting
   main. Do not rewrite published main history. A source merge does not deploy.

Preserve the gobifrost-only `deploy-dev` guard and MTG Azure/infra image ownership.
Keep the fork's upstream-website documentation-gate exemption. These ten commits
contain no Alembic migrations; do not invent a merge revision. Verify the existing
fork Alembic head remains coherent.

## Validation and acceptance

Use the Docker-backed `./test.sh` lane. Inspect host capacity before heavy suites
and run only one comprehensive suite at a time. While iterating, select tests by
the resolved implementation and actual consumer closure.

| Area | Required proof |
| --- | --- |
| Chat | Upstream chat serialization/pubsub/run/consumer tests and client chat-runtime tests; real tool result containing null; timeout/failure, retained-event replay, refresh, cancellation, route transitions, activity click behavior. |
| History/SDK | Upstream execution query and AgentRun pagination E2E tests and SDK pagination unit tests; existing execution-history pagination and summary-payload tests; SDK class alias, both query spellings, 422 cases, tenant isolation, retry-attempt readback. |
| Schemas | Upstream indexer/type inference/tool registry tests adapted to full-object storage; existing workflow-indexer and MCP tool-schema tests; nested/null/Any/Enum/Literal/required/default/keyword-only cases; AST/runtime parity; no schema mutation or flattening. |
| Invocation | Upstream generic MCP, gateway and agent-runtime tests; invalid args never dispatch; legacy empty list remains compatible; explicit zero-argument object stays strict; operation receipt replay cannot redispatch; caller authorization preserved. |
| Catalogs | Existing `test_mcp_replica_catalog.py` and catalog-sync coverage; stable names/digests, replica refresh, schema-only changes, duplicate names and inaccessible tenants. |
| Solutions | Upstream deploy schema inference coverage plus fork Solution accountability/recovery tests; carried-source inference, cross-install collision rejection, preserved roles/ownership, parse-failure handling and catalog refresh after deploy. |
| Runtime/dependencies | Provider retry, cancellation, tool events and token/usage tests against updated AI libraries; pinned-image production builds; form editor rich text/link/markdown, dependency graph and drag/drop browser checks. |
| Generated/API | DTO parity, SDK mirrors, contract tripwire/version decision, generated client types, API Ruff/Pyright, client TypeScript/ESLint. |

Add focused regression cases for reconciliation risks rather than copying tests
that merely restate upstream's storage representation. Test explicit null and
mixed Literal values, unconstrained schemas, keyword-only/default semantics,
imported annotation limitations, schema-only catalog refresh, and strict query
validation against real existing callers. Any newly exposed inference weakness
must be fixed or have a bounded compatibility fallback before enforcing it.

Before opening/merging the integration PR, run `./test.sh pre-pr` on the exact
candidate and inspect the comprehensive CI plan. Dependency locks, Docker inputs,
contracts, and backend/client changes justify broad gates here. Known failures
need a durable disposition; do not skip, retry until green, or call upstream test
results proof of the fork integration.

Acceptance: all ten commits accounted for; no dependency downgrade; canonical
schema and MTG protections retained; contract artifacts regenerated; focused and
full candidate gates pass; reviewed parentage preserved; production release notes
describe operational behavior and compatibility limits.

## Release and rollback plan

After source integration and separate rollout authorization, publish/verify exact
fork images and let bifrost-infra pin the complete matching service set. Drain
admitted workflows through the existing worker lifecycle and avoid leaving mixed
runtime versions as the steady state. Verify independent health, Chat recovery,
read-only History queries, and harmless fixture tools through both relevant MCP
paths. Never replay ticket-writing workflows as verification.

Do not bulk-redeploy Solutions merely to obtain schema metadata. Existing
ambiguous empty-list registrations remain usable. A separately reviewed Solution
redeploy can backfill a richer schema from its immutable source; validate a small
representative install before widening that work. Catalog refresh and installed
schema readback must accompany it.

Retain previous exact image refs for infra rollback. No new database migration is
selected, but rollout is not stateless: schema metadata may change on reindex or
Solution deploy, and Chat events persist. Prove the prior fork's ability to read
new schema objects/events before calling image-only rollback sufficient; retain
the prior Solution candidate when opting an install into richer validation. Do
not erase schemas or bypass receipts to make rollback appear successful.

Suggested production notes for the implementation PR:

> Improves Chat failure handling, execution-history filtering and pagination,
> and validation of workflow tool inputs. Existing Solution registrations with
> missing input schemas remain compatible; richer validation is enabled when
> their reviewed source is redeployed. Updates selected runtime dependencies.
> Invalid history query parameters now return explicit errors. Rollout uses
> matching fork images; retain prior images and verify persisted-schema
> compatibility before rollback.

## Evidence limits

Completed: fresh upstream/fork fetch, commit and source review, dependency target
comparison, PR/settings readback, isolated merge-tree rehearsals, and conflict
inventory. No production reads/writes, application source changes, dependency
installation, browser exercise, or test suite execution were performed for this
plan. The frontend modernization remains outside scope.
