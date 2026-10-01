# Bifrost platform guidance

This MTG fork tracks `gobifrost/bifrost`. Platform changes belong here (`api/`, `client/`, migrations, CLI/MCP); automation authoring belongs in `bifrost-workspace`. Follow [delivery lanes](docs/dev/delivery-lanes.md).

Read applicable nested guidance and the companion sections for the changed surface before editing; retain their requirements without loading unrelated procedures:

- Source, entities, DTOs or SDK: [file model](docs/dev/agent-platform-rules.md#file-operation-model), [manifest sync](docs/dev/agent-platform-rules.md#manifest-serialization--git-sync-integration-data), [SDK provenance](docs/dev/agent-platform-rules.md#v2-app-sdk-updates-and-retained-source), [CLI/MCP parity](docs/dev/agent-platform-rules.md#keeping-cli-mcp-and-manifest-in-sync).
- Backend/frontend code: [project rules](docs/dev/agent-platform-rules.md#project-specific-rules). Dependencies or merges: [maintenance](docs/dev/agent-platform-rules.md#dependency-and-merge-maintenance).
- Durable jobs: [shared job rules](docs/dev/agent-platform-rules.md#long-running-platform-jobs-critical). Upstream delivery: [fork boundaries](docs/dev/agent-platform-rules.md#read-first-persona-b).
- Tests, CI or PRs: [verification](docs/dev/agent-platform-rules.md#pre-completion-verification-required). Environment setup: [development](docs/dev/agent-platform-rules.md#development-environment-critical---read-first).

Pass exact headings to `python3 docs/dev/read-guidance.py platform` (or `claude`)
to read their complete sections in one call; omit headings only to discover
sections whose names are unknown. For example,
client changes need `"Project-Specific Rules"`, `"Pre-Completion Verification (REQUIRED)"`
and, when developing live behavior, `"Development Environment (CRITICAL - READ FIRST)"`.
Read additional sections when the changed surface requires them. The reader
includes subsections and fenced examples; it does not replace nested guidance,
referenced skills or their required checks. Reuse sections already read while
their source is unchanged. On Windows use `python`.

`CLAUDE.md` remains the tool-native playbook; consult its applicable sections
when this guide or a selected reference directs you there. Shared routing comes
from `mtg-engineering-flow` without overriding repository gates.

## Critical boundaries

- Preserve existing work. Keep handlers thin, Pydantic contracts canonical, and MCP tools thin REST wrappers. Do not introduce unrequested fallbacks.
- `.bifrost/` is export-only: mutate entities through CLI/MCP and distribute them through Solutions. Removed export/import commands are not an operating path.
- S3/RepoStorage owns source content; Redis module reads are Python-only. Preserve natural-key upserts, user configuration, OAuth mappings, portable-manifest field separation, and sanitized retained SDK source.
- Durable non-workflow work uses shared `PlatformJob`; extend its registry, policies, status and notification transports rather than adding parallel job systems. Read [platform jobs](docs/architecture/platform-jobs.md).
- Keep CLI, MCP, manifest serialization and generated OpenAPI types aligned. DTO parity and contract-version tripwires require an explicit breaking-versus-compatible decision.
- Preserve MTG's Azure deployment boundary: upstream DigitalOcean `deploy-dev` stays guarded by `github.repository == 'gobifrost/bifrost'`. Upstream website freshness is not an MTG release gate.
- Never expose secrets. Treat auth, tenant isolation, execution, migrations, manifest round-trips and audit logging as sensitive.

## Verification and delivery

Use the Linux Docker lane on the dedicated Bifrost test VMs at `pve-t340.netbird.cloud`, or CI. Do not substitute legacy `pve`, host pytest, or Windows host services. Retrieve access from Keeper at use time; report unavailable access rather than changing targets. Report VM/worktree identity.

For platform behavior or UI changes, develop against the worktree's running application before treating automated tests as proof:

1. Record the exact host, worktree, Compose project and whether its stack/volumes predate this task. Inspect `./debug.sh status`; if this worktree has no running stack, boot it with `./debug.sh` (default `up`). Use the printed worktree URL and credentials, never fixed historical login values.
2. Reproduce and exercise the change through the real browser or API, using hot reload and `./debug.sh logs api` to inspect failures. Verify success, relevant failure paths and observed behavior at that endpoint. `./debug.sh fixtures` seeds and runs real local scheduler workloads only when that behavior needs them.
3. Run scoped `./test.sh` tests, API quality through `./test.sh quality api`, and applicable client checks. The automated test environment is distinct from the live development stack; tests do not replace behavioral proof. API changes regenerate types with `npm run generate:types` in `client/` against the running worktree API.

Pure documentation/static changes need no live stack. Preserve pre-existing/shared stacks and volumes; do not reset, restart broadly, or deploy production to obtain development proof. Before final issue/PR handoff, clean up disposable debug resources created for this task using the [debug lifecycle](docs/dev/agent-platform-rules.md#debug-environment-ownership-and-cleanup). Report verified teardown or an explicit retained owner/reason; never leave a task-created stack running silently. Report the worktree endpoint and observed behavior without exposing credentials.

Before opening or queueing a PR, commit a clean candidate containing current `origin/main` and run `./test.sh pre-pr`. Preserve required CI, candidate provenance and merge-queue authorization. Never mask instability with retries, skips or longer timeouts. Known failures need a durable blocking disposition. Report actual checks, deferred suites, failures and runtime evidence separately.
