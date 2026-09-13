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

The focused browser run passed all eight checks (including setup and MCP
settings prerequisites): `./test.sh client e2e
e2e/applications-sdk-update.admin.spec.ts
e2e/solutions-sdk-update.admin.spec.ts
e2e/policies-app-realtime.admin.spec.ts`. This covers desktop/mobile SDK-update
controls, solution update status, and live table subscriptions. The SDK-update
UI specs use explicit API/WebSocket fixtures; the earlier backend SDK-update
E2E tests exercise the live durable job boundary.

The exact clean-commit full gate remains pending. Run the full gate after incorporating
the preceding batch's final merge into main; only one heavy local suite may
run at a time on this shared host.

The first full gate exposed a missing execution-operations inventory entry for
`application.sdk_update`. The inventory now records its actual 20-minute,
single-attempt, single-concurrency, 512 MiB-headroom policy. The registry
inventory regression remains enabled; the corrected commit requires a new
exact-commit gate.

The corrected `3d5da4511` passed the complete local gate: 3,099 client tests,
10,103 backend unit tests, 1,898 backend E2E tests, 163 browser checks, quality
checks, and production image/runtime validation. PR #711's Sonar analysis then
identified synchronous file operations in async retained-source streaming.
Those paths now use AnyIO's async file API, matching solution source storage.
A regression verifies archive opening, reading, writing, and closing occur off
the event-loop thread. All 28 focused storage, CLI, job, and live SDK-update
tests passed, as did API Pyright and Ruff. The repaired commit requires its own
complete gate.
