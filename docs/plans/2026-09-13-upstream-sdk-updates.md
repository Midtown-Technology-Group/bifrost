# Upstream SDK update integration

Upstream `bbc882a57eb3f77e48e4aa584efd5064579054d4` (#742) landed while the
artifact-hardening follow-up was under validation. It is integrated as a
separate batch because it adds deployed SDK provenance, retained source,
in-place SDK rebuilds, and table invalidation semantics across API, CLI, and UI.

Normal merge `ae5bd50b4` preserves the upstream parent. The eight conflicts were
reconciled individually: retain Midtown's `react-router` imports and platform
job registrations, combine SDK status with existing solution entity counts and
logos, and retain the imports required by each implementation.

The new `application.sdk_update` definition uses Midtown's explicit interactive
platform-job operations policy. `20260913_merge_mtg_sdk` joins the existing
Midtown migration head and upstream SDK-provenance migration without rewriting
either parent chain.

The upstream web SDK contract advances to version 2 because batch table writes
now send `table_invalidated` rather than per-row events. Existing built apps
require an explicit SDK rebuild to consume those frames. This web SDK contract
is separate from the CLI/server contract (currently version 12).

Focused backend verification passed 252 tests covering SDK source archives,
builds, platform jobs, realtime invalidation, live application updates, solution
counts and round trips, and CLI contract/DTO/skill tripwires. API type generation
against this worktree's running API reproduced the merged generated types
without changes. API type checking and linting passed, as did scoped client
linting and migration-graph inspection. Client `npm run tsc` and `npm run lint`
also passed. The focused client run passed all 208 tests in 13 changed test
files with `./test.sh client unit <changed-test-files> --maxWorkers=1`, covering
SDK status and job handling, application/solution UI, and table realtime hooks.

Merge `f89f88a5` also brings in the preceding batch's Azure download-header
repair. Its 60 focused Azure, artifact, MCP, and chat-attachment tests passed
against this combined tree.

Focused browser verification and the exact
clean-commit full gate remain pending. Run the full gate after incorporating
the preceding batch's final merge into main; only one heavy local suite may
run at a time on this shared host.
